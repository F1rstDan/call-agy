#!/usr/bin/env python3
"""Host-agnostic wrapper around the official Antigravity CLI headless mode.

This script intentionally shells out to `agy`; it does not read OAuth credentials or
call Antigravity service endpoints directly.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, NamedTuple

STREAM_INPUT_SAFE_BYTES = 60 * 1024
DEFAULT_WATCHDOG_GRACE_SECONDS = 30.0
TERMINATION_GRACE_SECONDS = 5.0
MAX_RECEIPT_RESPONSE_CHARS = 64 * 1024
TOKEN_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "thinking_tokens",
    "cache_read_tokens",
    "total_tokens",
)

SECRET_PATTERNS = [
    (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=:-]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(sk-[A-Za-z0-9_-]{6})[A-Za-z0-9_-]+\b"), r"\1...[REDACTED]"),
    (
        re.compile(
            r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|authorization)\s*[:=]\s*)\S+"
        ),
        r"\1[REDACTED]",
    ),
]

AUTH_REQUIRED_PATTERN = re.compile(r"\bauthentication\s+(?:is\s+)?required\b", re.IGNORECASE)
AUTH_FAILED_PATTERN = re.compile(r"\bauthentication\s+(?:failed|timed\s+out)\b", re.IGNORECASE)
NOT_LOGGED_IN_PATTERN = re.compile(r"\bnot\s+logged\s+(?:in|into)\s+antigravity\b", re.IGNORECASE)
HEADLESS_PERMISSION_PATTERN = re.compile(
    r"(?:headless mode cannot prompt|required the \"?command\"? permission|"
    r"permission check failed|user denied)",
    re.IGNORECASE,
)
DURATION_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)(ms|s|m|h)$", re.IGNORECASE)
UNSUPPORTED_FLAG_PATTERNS = (
    "unknown flag",
    "unknown option",
    "unrecognized option",
    "flag provided but not defined",
)
SAFE_CONVERSATION_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def eprint(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def redact(text: str) -> str:
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def markdown_code_block(text: str) -> str:
    """Render text in a fence longer than any backtick run it contains."""
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    suffix = "" if text.endswith("\n") else "\n"
    return f"{fence}text\n{text}{suffix}{fence}"


def install_command() -> str:
    if os.name == "nt":
        return "irm https://antigravity.google/cli/install.ps1 | iex"
    return "curl -fsSL https://antigravity.google/cli/install.sh | bash"


def agy_not_found_error(raw: str) -> str:
    return (
        f"AGY_NOT_FOUND: Antigravity CLI executable '{raw}' was not found on the current PATH "
        "or in the official default install location. A sandbox may have an incomplete PATH.\n"
        "If agy is installed elsewhere, retry with --agy-binary <absolute-path> or set "
        "CALL_AGY_BINARY. Install only when no local executable exists:\n"
        f"  {install_command()}\n"
        "Official guide: https://www.agy.dev/docs/cli/getting-started/"
    )


def actionable_failure(text: str) -> str | None:
    if (
        AUTH_REQUIRED_PATTERN.search(text)
        or AUTH_FAILED_PATTERN.search(text)
        or NOT_LOGGED_IN_PATTERN.search(text)
    ):
        return (
            "AUTH_REQUIRED: Open a terminal, run `agy`, sign in, then retry call-agy."
        )
    lowered = text.lower()
    normalized = lowered.replace("\\", "/")
    if HEADLESS_PERMISSION_PATTERN.search(text):
        return (
            "HEADLESS_PERMISSION_BLOCKED: agy reached a tool that requires an interactive "
            "permission decision, but headless mode cannot prompt. Configure a scoped "
            "permissions.allow rule for that tool, or explicitly authorize the trusted-workspace "
            "all-tool preset. Do not add --dangerously-skip-permissions silently."
        )
    if (
        ".gemini/antigravity-cli" in normalized
        and ("access is denied" in lowered or "permission denied" in lowered)
    ):
        return (
            "HOST_SANDBOX_BLOCKED: agy started, but the host sandbox denied access to its local "
            "state under ~/.gemini/antigravity-cli. Request host-level access to that directory "
            "and retry the same task. Antigravity --sandbox and --dangerously-skip-permissions "
            "cannot override the host sandbox."
        )
    if (
        any(pattern in lowered for pattern in UNSUPPORTED_FLAG_PATTERNS)
        and ("input-format" in lowered or "output-format" in lowered or "print-timeout" in lowered)
    ):
        return (
            "AGY_VERSION_UNSUPPORTED: This agy build does not support the required headless flags. "
            "Run `agy update` or reinstall the current CLI, then retry. call-agy requires agy 1.1.15+."
        )
    return None


def with_actionable_failure(result: dict[str, Any], diagnostics: list[str]) -> dict[str, Any]:
    original = str(result.get("error") or "").strip()
    combined = "\n".join(part for part in [original, *diagnostics] if part)
    hint = actionable_failure(combined)
    if not hint:
        return result
    updated = dict(result)
    updated["error"] = f"{hint}\nOriginal diagnostic: {original or combined}"
    return updated


def default_agy_candidates() -> list[pathlib.Path]:
    home = pathlib.Path.home()
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        candidates = []
        if local_app_data:
            candidates.append(pathlib.Path(local_app_data) / "agy" / "bin" / "agy.exe")
        candidates.append(home / "AppData" / "Local" / "agy" / "bin" / "agy.exe")
    else:
        candidates = [home / ".local" / "bin" / "agy"]
    return list(dict.fromkeys(candidates))


def discover_default_agy() -> str | None:
    for path in default_agy_candidates():
        try:
            if path.is_file():
                return str(path.resolve())
        except OSError:
            continue
    return None


def resolve_executable(raw: str) -> str:
    candidate = os.path.expandvars(os.path.expanduser(raw))
    if os.path.sep in candidate or (os.path.altsep and os.path.altsep in candidate):
        p = pathlib.Path(candidate)
        if not p.is_file():
            raise RuntimeError(agy_not_found_error(raw))
        return str(p.resolve())
    resolved = shutil.which(candidate)
    if not resolved and candidate.lower() in {"agy", "agy.exe"}:
        resolved = discover_default_agy()
        if resolved:
            eprint(f"[call-agy] agy is not on PATH; using default install location: {resolved}")
    if not resolved:
        raise RuntimeError(agy_not_found_error(raw))
    return resolved


def agy_state_dir(home: pathlib.Path | None = None) -> pathlib.Path:
    return (home or pathlib.Path.home()) / ".gemini" / "antigravity-cli"


def probe_writable_directory(path: pathlib.Path, *, label: str) -> None:
    """Fail before model usage when a required runtime directory is not writable."""
    if not path.is_dir():
        raise RuntimeError(
            f"AGY_STATE_UNAVAILABLE: Required {label} directory does not exist: {path}. "
            "Run `agy` interactively once to initialize and authenticate it."
        )
    probe = path / f".call-agy-write-probe-{os.getpid()}-{secrets.token_hex(6)}"
    try:
        with probe.open("x", encoding="utf-8") as handle:
            handle.write("call-agy preflight\n")
        probe.unlink()
    except OSError as exc:
        try:
            probe.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(
            "HOST_SANDBOX_BLOCKED: call-agy cannot write Antigravity's required local "
            f"{label} directory: {path}. Grant the host process write access to "
            "~/.gemini/antigravity-cli or run this bounded delegation with the required "
            "host-level access. Antigravity --mode, --sandbox, and "
            f"--dangerously-skip-permissions cannot override the host sandbox. ({exc})"
        ) from exc


def permission_summary(state_dir: pathlib.Path) -> dict[str, Any]:
    summary: dict[str, Any] = {"settings_present": False, "allow_count": 0}
    settings_path = state_dir / "settings.json"
    if not settings_path.is_file():
        return summary
    summary["settings_present"] = True
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        summary["settings_readable"] = False
        return summary
    summary["settings_readable"] = True
    permissions = data.get("permissions") if isinstance(data, dict) else None
    allow = permissions.get("allow") if isinstance(permissions, dict) else None
    summary["allow_count"] = len(allow) if isinstance(allow, list) else 0
    summary["tool_permission"] = str(data.get("toolPermission") or "request-review")
    return summary


def parse_duration_seconds(raw: str) -> float:
    match = DURATION_PATTERN.fullmatch(raw.strip())
    if not match:
        raise RuntimeError(
            f"Invalid duration '{raw}'. Use a positive duration such as 90s, 10m, or 1h."
        )
    value = float(match.group(1))
    if value <= 0:
        raise RuntimeError(f"Duration must be positive: {raw}")
    unit = match.group(2).lower()
    multipliers = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    return value * multipliers[unit]


def wrapper_timeout_seconds(print_timeout: str, explicit: str | None) -> float:
    if explicit:
        return parse_duration_seconds(explicit)
    return parse_duration_seconds(print_timeout) + DEFAULT_WATCHDOG_GRACE_SECONDS


def receipt_path_for(turn_id: str) -> pathlib.Path:
    path = temp_root() / ".staging" / f"{turn_id}-receipt.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class RunReceipt:
    """A small atomically updated artifact that survives wrapper or host interruption."""

    def __init__(
        self,
        path: pathlib.Path,
        *,
        turn_id: str,
        workspace: pathlib.Path,
        mode: str | None,
        watchdog_seconds: float,
    ) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {
            "turn_id": turn_id,
            "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "workspace": str(workspace),
            "mode": mode or "default",
            "watchdog_seconds": watchdog_seconds,
            "state": "STARTING",
            "conversation_id": "",
            "last_event": "receipt_created",
            "last_diagnostic": "",
            "partial_response": "",
            "tool_counts": {},
            "final_output_path": "",
            "error": "",
        }
        self._write_locked()

    def update(self, **changes: Any) -> None:
        with self._lock:
            self._data.update(changes)
            self._write_locked()

    def _write_locked(self) -> None:
        partial = redact(str(self._data.get("partial_response") or ""))
        truncated = False
        if len(partial) > MAX_RECEIPT_RESPONSE_CHARS:
            partial = partial[-MAX_RECEIPT_RESPONSE_CHARS:]
            truncated = True
        tools = self._data.get("tool_counts") or {}
        lines = [
            "# call-agy run receipt",
            "",
            f"- state: `{self._data['state']}`",
            f"- turn_id: `{self._data['turn_id']}`",
            f"- started_at: `{self._data['started_at']}`",
            f"- workspace: `{self._data['workspace']}`",
            f"- mode: `{self._data['mode']}`",
            f"- watchdog_seconds: `{self._data['watchdog_seconds']:.1f}`",
        ]
        if self._data.get("conversation_id"):
            lines.append(f"- conversation_id: `{self._data['conversation_id']}`")
        lines.append(f"- last_event: `{self._data['last_event']}`")
        if tools:
            lines += ["", "## Completed tools", ""]
            for name, count in sorted(tools.items()):
                lines.append(f"- `{name}` ×{count}")
        if partial:
            lines += ["", "## Partial response", ""]
            if truncated:
                lines.append("_Only the last 64 KiB is retained in this crash receipt._\n")
            lines.append(partial)
        if self._data.get("last_diagnostic"):
            lines += ["", "## Last diagnostic", "", redact(str(self._data["last_diagnostic"]))]
        if self._data.get("error"):
            lines += ["", "## Error", "", redact(str(self._data["error"]))]
        if self._data.get("final_output_path"):
            lines += ["", "## Final handoff", "", str(self._data["final_output_path"])]
        lines.append("")
        temporary = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        )
        temporary.write_text("\n".join(lines), encoding="utf-8")
        os.replace(temporary, self.path)


def normalize_workspace(raw: str) -> pathlib.Path:
    p = pathlib.Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
    if not p.is_dir():
        raise RuntimeError(f"Workspace does not exist or is not a directory: {p}")
    return p


def normalize_add_dir(raw: str) -> pathlib.Path:
    p = pathlib.Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
    if not p.is_dir():
        raise RuntimeError(f"Additional directory does not exist or is not a directory: {p}")
    return p


def accessible_dirs(workspace: pathlib.Path, raw_dirs: list[str]) -> list[str]:
    """Expose the primary workspace plus explicit external directories once each."""
    directories = [workspace, *(normalize_add_dir(raw) for raw in raw_dirs)]
    unique: list[str] = []
    seen: set[str] = set()
    for directory in directories:
        resolved = str(directory.resolve())
        key = os.path.normcase(resolved)
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def normalize_hint(workspace: pathlib.Path, raw: str) -> pathlib.Path:
    cleaned = raw.strip()
    # Remove common line suffixes: path#L12 and path:12-20.
    cleaned = re.sub(r"#L\d+$", "", cleaned)
    cleaned = re.sub(r":\d+(?:-\d+)?$", "", cleaned)
    p = pathlib.Path(os.path.expandvars(os.path.expanduser(cleaned)))
    if not p.is_absolute():
        p = workspace / p
    return p.resolve(strict=False)


def read_task(args: argparse.Namespace) -> str:
    choices = [value for value in (args.task_positional, args.task) if value]
    if len(choices) > 1:
        raise RuntimeError("Pass the task either positionally or with --task, not both.")
    if choices:
        task = choices[0]
    elif not sys.stdin.isatty():
        task = sys.stdin.read()
    else:
        task = ""
    if not task.strip():
        raise RuntimeError("Task is empty. Pass a positional task, --task, or stdin.")
    return task


def build_prompt(
    task: str,
    workspace: pathlib.Path,
    hints: list[str],
    add_dirs: list[str] | None = None,
    mode: str | None = None,
) -> str:
    parts = [task]
    accessible_dirs = [pathlib.Path(raw) for raw in (add_dirs or [])]

    contract = [
        "",
        f"Workspace: {workspace}",
        "- For a text-only or connectivity task, reply directly without inspecting files or running tools.",
        "- Report changed workspace paths relative to this workspace.",
        "- Treat additional accessible directories as context unless the task explicitly targets one; report an explicit external target by absolute path.",
    ]
    if mode == "accept-edits":
        contract.extend([
            "- Perform the task and create or edit its deliverables in this workspace.",
            "- Use file editing tools for substantive file contents. Keep shell commands concise and use them for build, test, lint, or inspection steps.",
        ])
    elif mode == "plan":
        contract.extend([
            "- This is a planning/read-only posture: do not create, edit, delete, or rename workspace files.",
            "- Prefer native read-only file tools for inspection. Do not use a shell command merely to list or read files.",
            "- Return the plan or findings in the final response rather than writing a deliverable file.",
        ])
    else:
        contract.extend([
            "- Do not modify workspace files unless the task explicitly authorizes the change.",
            "- Use file editing tools only when the task authorizes edits. Keep shell commands concise and use them for build, test, lint, or inspection steps.",
        ])
    if accessible_dirs:
        contract.append("- Additional accessible directories:")
        contract.extend(f"  - {directory}" for directory in accessible_dirs)
    parts.append("\n".join(contract))

    if hints:
        lines = ["", "Priority paths (inspect these first if relevant):"]
        for raw in hints:
            p = normalize_hint(workspace, raw)
            state = "exists" if p.exists() else "missing"
            lines.append(f"- {p} ({state})")
        parts.append("\n".join(lines))
    parts.append(
        "\nAt the end, return a concise handoff covering: outcome, files changed (if any), "
        "verification performed, and any blockers or remaining risks."
    )
    return "\n".join(parts)


def temp_root() -> pathlib.Path:
    root = pathlib.Path(tempfile.gettempdir()) / "call-agy"
    root.mkdir(parents=True, exist_ok=True)
    return root


def create_turn_id() -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{os.getpid()}-{secrets.token_hex(8)}"


def big_prompt_dir() -> pathlib.Path:
    path = temp_root() / ".big-prompt"
    path.mkdir(parents=True, exist_ok=True)
    return path


def conversation_key(conversation_id: str) -> str:
    if (
        SAFE_CONVERSATION_KEY.fullmatch(conversation_id)
        and conversation_id not in {".", ".."}
        and conversation_id.split(".", 1)[0].upper() not in WINDOWS_RESERVED_NAMES
    ):
        return conversation_id
    digest = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()
    return f"conversation-{digest}"


def conversation_dir_for(conversation_id: str) -> pathlib.Path:
    path = temp_root() / conversation_key(conversation_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def finalize_artifact_paths(
    turn_id: str,
    conversation_id: str,
    prompt_file_path: pathlib.Path | None,
) -> tuple[pathlib.Path, pathlib.Path | None]:
    destination = conversation_dir_for(conversation_id) if conversation_id else temp_root()
    grouped_prompt = prompt_file_path
    if prompt_file_path:
        target = destination / f"{turn_id}-prompt.txt"
        try:
            prompt_file_path.replace(target)
        except OSError as exc:
            eprint(
                f"[call-agy] WARNING: could not finalize temporary artifact paths: {exc}"
            )
            return temp_root() / f"{turn_id}-handoff.md", prompt_file_path
        grouped_prompt = target

    return destination / f"{turn_id}-handoff.md", grouped_prompt


def output_path_for(
    args: argparse.Namespace,
    workspace: pathlib.Path,
    default_path: pathlib.Path | None,
) -> pathlib.Path:
    if args.output:
        p = pathlib.Path(os.path.expandvars(os.path.expanduser(args.output)))
        if not p.is_absolute():
            p = workspace / p
        p = p.resolve(strict=False)
    else:
        if default_path is None:
            raise RuntimeError("Internal error: default handoff path was not prepared.")
        p = default_path
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def raw_path_for(raw: str | None, workspace: pathlib.Path) -> pathlib.Path | None:
    if not raw:
        return None
    p = pathlib.Path(os.path.expandvars(os.path.expanduser(raw)))
    if not p.is_absolute():
        p = workspace / p
    p = p.resolve(strict=False)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def build_command(args: argparse.Namespace, executable: str) -> list[str]:
    cmd = [
        executable,
        "--print=",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
    ]
    for directory in args.add_dirs:
        cmd += ["--add-dir", directory]
    if args.conversation:
        cmd += ["--conversation", args.conversation]
    elif args.continue_last:
        cmd += ["--continue"]
    if args.model:
        cmd += ["--model", args.model]
    if args.effort:
        cmd += ["--effort", args.effort]
    if args.agent:
        cmd += ["--agent", args.agent]
    if args.timeout:
        cmd += ["--print-timeout", args.timeout]
    if args.mode:
        cmd += ["--mode", args.mode]
    if args.sandbox:
        cmd += ["--sandbox"]
    if args.dangerously_skip_permissions:
        cmd += ["--dangerously-skip-permissions"]
    return cmd


def dry_run_shape(cmd: list[str]) -> str:
    return json.dumps(cmd, ensure_ascii=False)


def prompt_event(prompt: str) -> str:
    return json.dumps(
        {"event": "user", "message": {"content": prompt}},
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"


def prompt_event_size(prompt: str) -> int:
    return len(prompt_event(prompt).encode("utf-8"))


def materialize_prompt(prompt: str, turn_id: str | None = None) -> pathlib.Path:
    prompt_path = big_prompt_dir() / f"{turn_id or create_turn_id()}-prompt.txt"
    with prompt_path.open("x", encoding="utf-8") as handle:
        handle.write(prompt)
    return prompt_path


def prompt_file_instruction(prompt_path: pathlib.Path) -> str:
    return (
        "Read the complete delegated task from this UTF-8 file, then carry it out: "
        f"{prompt_path}"
    )


def is_empty_zero_usage_success(result: dict[str, Any]) -> bool:
    if str(result.get("status") or "") != "SUCCESS":
        return False
    if str(result.get("response") or "").strip():
        return False
    usage = result.get("usage")
    if not isinstance(usage, dict):
        return False
    present_usage = [usage[key] for key in TOKEN_USAGE_KEYS if key in usage]
    if not present_usage or any(value != 0 for value in present_usage):
        return False
    no_turns = "num_turns" in result and result.get("num_turns") == 0
    no_duration = "duration_seconds" in result and result.get("duration_seconds") == 0
    return no_turns or no_duration


def is_retryable_pre_model_error(
    result: dict[str, Any], saw_tool_step: bool, partial_response: str = ""
) -> bool:
    """Match the opaque transient failure observed before a model or tool ran."""
    if partial_response.strip() or saw_tool_step or str(result.get("status") or "") != "ERROR":
        return False
    if str(result.get("response") or "").strip():
        return False
    usage = result.get("usage")
    if not isinstance(usage, dict):
        return False
    present_usage = [usage[key] for key in TOKEN_USAGE_KEYS if key in usage]
    if not present_usage or any(value != 0 for value in present_usage):
        return False
    return str(result.get("error") or "").strip() == "Agent execution terminated due to error."


def empty_success_error(serialized_prompt_bytes: int) -> str:
    return (
        "Antigravity returned SUCCESS with an empty response and zero token usage before "
        "running a model. "
        f"The original serialized stdin message was {serialized_prompt_bytes} bytes. "
        "推测提示词可能超过约 60KB。"
    )


def normalize_terminal_result(
    result: dict[str, Any], partial_response: str, serialized_prompt_bytes: int
) -> dict[str, Any]:
    """Require a usable final handoff while salvaging streamed response text."""
    updated = dict(result)
    response = str(updated.get("response") or "")
    partial = partial_response.strip()
    if response.strip():
        return updated
    if partial:
        if str(updated.get("status") or "") == "SUCCESS":
            updated["response"] = partial_response.rstrip()
            updated["response_source"] = "stream-json text_delta recovery"
            updated["wrapper_status"] = "RECOVERED_STREAM_RESPONSE"
        else:
            updated["partial_response"] = partial_response.rstrip()
            updated.setdefault("wrapper_status", "PARTIAL_NO_FINAL_RESPONSE")
        return updated
    if str(updated.get("status") or "") == "SUCCESS":
        zero_usage_empty = is_empty_zero_usage_success(updated)
        updated["agy_status"] = "SUCCESS"
        updated["status"] = "ERROR"
        updated["wrapper_status"] = "NO_FINAL_RESPONSE"
        if zero_usage_empty:
            updated["error"] = empty_success_error(serialized_prompt_bytes)
        else:
            updated["error"] = (
                "Antigravity reported SUCCESS but returned no final response. Tool activity or "
                "token usage does not satisfy call-agy's handoff contract."
            )
    return updated


def send_prompt(pipe: Any, prompt: str, errors: list[str]) -> None:
    try:
        pipe.write(prompt_event(prompt))
        pipe.flush()
    except (BrokenPipeError, OSError) as exc:
        errors.append(redact(str(exc)))
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def drain_stderr(pipe: Any, collected: list[str], receipt: RunReceipt | None = None) -> None:
    try:
        for line in pipe:
            safe = redact(line.rstrip("\r\n"))
            collected.append(safe)
            if safe:
                eprint(f"[agy:stderr] {safe}")
                if receipt:
                    receipt.update(last_event="stderr", last_diagnostic=safe)
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def progress_from_event(event: dict[str, Any], seen_tools: set[tuple[int, str]], tool_counts: collections.Counter[str]) -> None:
    if event.get("event") != "step_update":
        return
    step = event.get("step_update") or {}
    if not isinstance(step, dict):
        return
    step_type = str(step.get("step_type") or "")
    state = str(step.get("state") or "")
    step_index = int(step.get("step_index") or 0)

    if step_type == "tool" and state == "DONE":
        tool_name = str(step.get("tool_name") or (step.get("tool_info") or {}).get("name") or "tool")
        key = (step_index, tool_name)
        if key not in seen_tools:
            seen_tools.add(key)
            tool_counts[tool_name] += 1
            eprint(f"[agy] tool: {tool_name}")

    subagent_info = step.get("subagent_info")
    if state == "DONE" and isinstance(subagent_info, dict):
        subagents = subagent_info.get("subagents")
        if isinstance(subagents, list) and subagents:
            eprint(f"[agy] subagents: {len(subagents)}")


def update_session_metadata(source: Any, metadata: dict[str, str]) -> None:
    """Collect model/effort metadata when the CLI exposes it in stream-json."""
    if not isinstance(source, dict):
        return

    aliases = {
        "model": ("model", "model_slug", "model_name"),
        "effort": ("effort", "reasoning_effort", "thinking_level", "thinking"),
        "permission_mode": ("permission_mode",),
    }
    for canonical, keys in aliases.items():
        for key in keys:
            value = source.get(key)
            if value is not None and str(value).strip():
                metadata[canonical] = str(value)
                break

    for nested_key in ("metadata", "session", "config", "model_info"):
        nested = source.get(nested_key)
        if isinstance(nested, dict):
            update_session_metadata(nested, metadata)


class InvocationResult(NamedTuple):
    return_code: int
    conversation_id: str
    terminal_result: dict[str, Any]
    tool_counts: collections.Counter[str]
    session_metadata: dict[str, str]
    elapsed: float
    saw_tool_step: bool
    diagnostics: list[str]
    partial_response: str
    last_event: str
    timed_out: bool


def invoke_once(
    cmd: list[str],
    workspace: pathlib.Path,
    prompt: str,
    raw_path: pathlib.Path | None,
    *,
    append_raw: bool = False,
    watchdog_seconds: float | None = None,
    receipt: RunReceipt | None = None,
) -> InvocationResult:
    stderr_lines: list[str] = []
    stdin_errors: list[str] = []
    tool_counts: collections.Counter[str] = collections.Counter()
    seen_tools: set[tuple[int, str]] = set()
    session_metadata: dict[str, str] = {}
    conversation_id = ""
    terminal_result: dict[str, Any] | None = None
    saw_tool_step = False
    partial_fragments: list[str] = []
    last_event = "launching"
    timed_out = threading.Event()
    watchdog_stop = threading.Event()
    raw_handle = raw_path.open("a" if append_raw else "w", encoding="utf-8") if raw_path else None

    started = time.monotonic()
    if receipt:
        receipt.update(state="LAUNCHING", last_event=last_event)
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(workspace),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
    except OSError as exc:
        if raw_handle:
            raw_handle.close()
        elapsed = time.monotonic() - started
        error = f"Failed to start Antigravity CLI: {exc}"
        if receipt:
            receipt.update(state="ERROR", last_event="spawn_failed", error=error)
        return InvocationResult(
            return_code=1,
            conversation_id="",
            terminal_result={
                "status": "ERROR",
                "wrapper_status": "SPAWN_FAILED",
                "response": "",
                "error": error,
            },
            tool_counts=tool_counts,
            session_metadata=session_metadata,
            elapsed=elapsed,
            saw_tool_step=False,
            diagnostics=[],
            partial_response="",
            last_event="spawn_failed",
            timed_out=False,
        )

    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None
    stdin_thread = threading.Thread(
        target=send_prompt,
        args=(proc.stdin, prompt, stdin_errors),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=drain_stderr, args=(proc.stderr, stderr_lines, receipt), daemon=True
    )

    def watchdog() -> None:
        if watchdog_seconds is None or watchdog_stop.wait(watchdog_seconds):
            return
        timed_out.set()
        if receipt:
            receipt.update(
                state="TERMINATING",
                last_event="wrapper_watchdog_timeout",
                error=f"Wrapper watchdog expired after {watchdog_seconds:.1f}s.",
            )
        eprint(f"[call-agy] wrapper watchdog expired after {watchdog_seconds:.1f}s; terminating agy")
        try:
            proc.terminate()
            proc.wait(timeout=TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
        except OSError:
            pass

    stdin_thread.start()
    stderr_thread.start()
    watchdog_thread = threading.Thread(target=watchdog, daemon=True)
    watchdog_thread.start()

    try:
        for raw_line in proc.stdout:
            line = raw_line.rstrip("\r\n")
            if not line:
                continue
            if raw_handle:
                raw_handle.write(line + "\n")
                raw_handle.flush()
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                eprint("[call-agy] ignored non-JSON stdout line from stream-json mode")
                continue
            if not isinstance(event, dict):
                continue
            event_name = str(event.get("event") or "unknown")
            last_event = event_name
            if event.get("event") == "init":
                conversation_id = str(event.get("conversation_id") or conversation_id)
                update_session_metadata(event.get("init"), session_metadata)
                eprint(f"[agy] conversation: {conversation_id or '(pending)'}")
                last_event = "init"
            elif event.get("event") == "result":
                payload = event.get("result")
                if isinstance(payload, dict):
                    terminal_result = payload
                    conversation_id = str(payload.get("conversation_id") or conversation_id)
                    update_session_metadata(payload, session_metadata)
                    last_event = "result"
            elif event.get("event") == "step_update":
                step = event.get("step_update")
                if isinstance(step, dict):
                    step_type = str(step.get("step_type") or "unknown")
                    step_state = str(step.get("state") or "unknown")
                    last_event = f"step_update:{step_type}:{step_state}"
                    if step_type == "tool":
                        saw_tool_step = True
                    if step_type == "agent_response":
                        delta = step.get("text_delta")
                        if isinstance(delta, str) and delta:
                            partial_fragments.append(delta)
            progress_from_event(event, seen_tools, tool_counts)
            if receipt:
                receipt.update(
                    state="STREAMING" if terminal_result is None else "FINALIZING",
                    conversation_id=conversation_id,
                    last_event=last_event,
                    partial_response="".join(partial_fragments),
                    tool_counts=dict(tool_counts),
                )
    finally:
        proc.stdout.close()
        return_code = proc.wait()
        watchdog_stop.set()
        watchdog_thread.join(timeout=2)
        stdin_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        if raw_handle:
            raw_handle.close()

    elapsed = time.monotonic() - started
    partial_response = "".join(partial_fragments)
    if timed_out.is_set():
        native_status = str((terminal_result or {}).get("status") or "")
        terminal_response = str((terminal_result or {}).get("response") or "")
        if terminal_response.strip():
            partial_response = terminal_response
        terminal_result = {
            "status": "ERROR",
            "wrapper_status": "TIMEOUT",
            "response": "",
            "error": f"Wrapper watchdog timed out after {watchdog_seconds:.1f}s.",
        }
        if native_status:
            terminal_result["agy_status"] = native_status
        return_code = 124
        last_event = "timeout"
    elif terminal_result is None:
        diagnostics = [*stdin_errors, *stderr_lines[-20:]]
        tail = "\n".join(diagnostics)
        wrapper_status = "NO_TERMINAL_RESULT"
        hint = actionable_failure(tail)
        detail = f"\nRecent diagnostics:\n{tail}" if tail else ""
        error = hint or f"Antigravity CLI returned no terminal result event (exit {return_code})."
        error += detail
        terminal_result = {
            "status": "ERROR",
            "wrapper_status": wrapper_status,
            "response": "",
            "error": error,
        }
        last_event = wrapper_status.lower()

    if receipt:
        receipt.update(
            state="AGY_FINISHED",
            conversation_id=conversation_id,
            last_event=last_event,
            partial_response=partial_response,
            tool_counts=dict(tool_counts),
            error=str(terminal_result.get("error") or ""),
        )

    return InvocationResult(
        return_code=return_code,
        conversation_id=conversation_id,
        terminal_result=terminal_result,
        tool_counts=tool_counts,
        session_metadata=session_metadata,
        elapsed=elapsed,
        saw_tool_step=saw_tool_step,
        diagnostics=[*stdin_errors, *stderr_lines[-20:]],
        partial_response=partial_response,
        last_event=last_event,
        timed_out=timed_out.is_set(),
    )


def write_markdown(
    path: pathlib.Path,
    result: dict[str, Any],
    prompt: str,
    conversation_id: str,
    tool_counts: collections.Counter[str],
    session_metadata: dict[str, str],
    elapsed: float,
    args: argparse.Namespace,
    prompt_file_path: pathlib.Path | None,
    receipt_path: pathlib.Path | None = None,
    watchdog_seconds: float | None = None,
    attempts: int = 1,
    previous_conversation_ids: list[str] | None = None,
) -> None:
    response = str(result.get("response") or "").rstrip()
    partial_response = str(result.get("partial_response") or "").rstrip()
    status = str(result.get("status") or "UNKNOWN")
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}

    lines: list[str] = ["# Antigravity handoff", ""]
    if response:
        lines += ["## Result", "", response]
    elif partial_response:
        lines += [
            "## Partial result",
            "",
            "_Antigravity did not return a final response. The text below was recovered from stream-json response events._",
            "",
            partial_response,
        ]
    else:
        lines += [
            "## Result unavailable",
            "",
            "Antigravity did not return a final response body.",
        ]
    lines += [
        "",
        "## Prompt sent to Antigravity",
        "",
        markdown_code_block(prompt),
        "",
        "## Run",
        "",
    ]
    lines.append(f"- status: `{status}`")
    if result.get("agy_status"):
        lines.append(f"- agy_status: `{result['agy_status']}`")
    if result.get("wrapper_status"):
        lines.append(f"- wrapper_status: `{result['wrapper_status']}`")
    if result.get("response_source"):
        lines.append(f"- response_source: `{result['response_source']}`")
    if conversation_id:
        lines.append(f"- conversation_id: `{conversation_id}`")
    if attempts > 1:
        lines.append(f"- attempts: `{attempts}`")
        for previous_id in previous_conversation_ids or []:
            lines.append(f"- previous_attempt_conversation_id: `{previous_id}`")
    model = session_metadata.get("model") or args.model
    effort = session_metadata.get("effort") or args.effort
    if model:
        lines.append(f"- model: `{model}`")
    if effort:
        lines.append(f"- effort: `{effort}`")
    if args.agent:
        lines.append(f"- agent: `{args.agent}`")
    if args.mode:
        lines.append(f"- mode: `{args.mode}`")
    if session_metadata.get("permission_mode"):
        lines.append(f"- permission_mode: `{session_metadata['permission_mode']}`")
    if prompt_file_path:
        lines.append(f"- prompt_file_path: `{prompt_file_path}`")
    if receipt_path:
        lines.append(f"- receipt_path: `{receipt_path}`")
    if watchdog_seconds is not None:
        lines.append(f"- wrapper_watchdog: `{watchdog_seconds:.1f}s`")
    lines.append(f"- elapsed: `{elapsed:.1f}s`")

    if tool_counts:
        lines += ["", "## Tools used", ""]
        for name, count in sorted(tool_counts.items()):
            lines.append(f"- `{name}` ×{count}")

    if usage:
        lines += ["", "## Usage", ""]
        for key in ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens"):
            if key in usage:
                lines.append(f"- {key}: `{usage[key]}`")

    error = result.get("error")
    if error:
        lines += ["", "## Error", "", redact(str(error))]

    lines += [
        "",
        "---",
        "Generated by `call-agy` via the official local `agy` CLI. Raw tool arguments/outputs are omitted by default.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    if args.conversation and args.continue_last:
        raise RuntimeError("Use either --conversation or --continue, not both.")
    if args.dangerously_skip_permissions:
        eprint("[call-agy] WARNING: --dangerously-skip-permissions auto-approves all agy tool calls for this run.")

    executable = resolve_executable(args.agy_binary)
    workspace = normalize_workspace(args.workspace)
    external_dirs = [str(normalize_add_dir(raw)) for raw in args.add_dirs]
    args.add_dirs = accessible_dirs(workspace, external_dirs)
    task = read_task(args)
    prompt = build_prompt(task, workspace, args.files, external_dirs, args.mode)
    serialized_prompt_bytes = prompt_event_size(prompt)
    watchdog_seconds = wrapper_timeout_seconds(args.timeout, args.wrapper_timeout)

    if args.dry_run:
        cmd = build_command(args, executable)
        print(f"cwd={workspace}")
        print(f"command={dry_run_shape(cmd)}")
        print(f"serialized_prompt_bytes={serialized_prompt_bytes}")
        transport = "system-temp-file" if serialized_prompt_bytes > STREAM_INPUT_SAFE_BYTES else "stdin"
        print(f"prompt_transport={transport}")
        print(f"wrapper_watchdog_seconds={watchdog_seconds:.1f}")
        print(f"recommended_host_timeout_seconds={watchdog_seconds + DEFAULT_WATCHDOG_GRACE_SECONDS:.1f}")
        return 0

    turn_id = create_turn_id()
    receipt_path = receipt_path_for(turn_id)
    receipt = RunReceipt(
        receipt_path,
        turn_id=turn_id,
        workspace=workspace,
        mode=args.mode,
        watchdog_seconds=watchdog_seconds,
    )
    print(f"receipt_path={receipt_path}", flush=True)

    try:
        state_dir = agy_state_dir()
        probe_writable_directory(state_dir, label="state")
        permission_info = permission_summary(state_dir)
        receipt.update(
            state="PREFLIGHT_OK",
            last_event="state_write_probe_ok",
            permission_allow_count=permission_info.get("allow_count", 0),
        )
        tool_permission = str(permission_info.get("tool_permission") or "request-review")
        if (
            not args.dangerously_skip_permissions
            and permission_info.get("allow_count", 0) == 0
            and tool_permission not in {"proceed-in-sandbox", "always-proceed"}
        ):
            warning = (
                "HEADLESS_PERMISSION_RISK: no scoped permissions.allow rules were found; "
                "shell commands may be soft-denied in headless mode."
            )
            eprint(f"[call-agy] WARNING: {warning}")
            receipt.update(last_event="permission_preflight_warning", last_diagnostic=warning)
    except RuntimeError as exc:
        terminal_result = {
            "status": "ERROR",
            "wrapper_status": "PRECHECK_FAILED",
            "response": "",
            "error": str(exc),
        }
        default_handoff_path, _ = finalize_artifact_paths(turn_id, "", None)
        handoff_path = output_path_for(args, workspace, default_handoff_path)
        write_markdown(
            handoff_path,
            terminal_result,
            prompt,
            "",
            collections.Counter(),
            {},
            0.0,
            args,
            None,
            receipt_path,
            watchdog_seconds,
        )
        receipt.update(
            state="ERROR",
            last_event="preflight_failed",
            error=str(exc),
            final_output_path=str(handoff_path),
        )
        print(f"output_path={handoff_path}")
        print("elapsed=0.0s")
        print("status=ERROR")
        eprint(f"[call-agy] failed before model invocation: {redact(str(exc))}")
        return 1

    prompt_file_path: pathlib.Path | None = None
    prompt_to_send = prompt
    if serialized_prompt_bytes > STREAM_INPUT_SAFE_BYTES:
        prompt_file_path = materialize_prompt(prompt, turn_id)
        prompt_to_send = prompt_file_instruction(prompt_file_path)
        args.add_dirs = accessible_dirs(
            workspace,
            [*external_dirs, str(prompt_file_path.parent)],
        )
        eprint(
            f"[call-agy] prompt is {serialized_prompt_bytes} serialized bytes; "
            f"using prompt file: {prompt_file_path}"
        )

    cmd = build_command(args, executable)

    raw_path = raw_path_for(args.raw_output, workspace)

    def invoke_for_run(*, append_raw: bool = False) -> InvocationResult:
        try:
            return invoke_once(
                cmd,
                workspace,
                prompt_to_send,
                raw_path,
                append_raw=append_raw,
                watchdog_seconds=watchdog_seconds,
                receipt=receipt,
            )
        except Exception:
            if prompt_file_path:
                _, retained_prompt = finalize_artifact_paths(
                    turn_id, "", prompt_file_path
                )
                if retained_prompt:
                    eprint(f"[call-agy] retained prompt file: {retained_prompt}")
            raise

    invocation = invoke_for_run()
    invocation = invocation._replace(
        terminal_result=with_actionable_failure(
            invocation.terminal_result,
            invocation.diagnostics,
        )
    )
    attempts = 1
    previous_conversation_ids: list[str] = []
    elapsed = invocation.elapsed

    if (
        is_retryable_pre_model_error(
            invocation.terminal_result,
            invocation.saw_tool_step,
            invocation.partial_response,
        )
        and not args.conversation
        and not args.continue_last
    ):
        eprint(
            "[call-agy] transient pre-model error with zero usage and no tool steps; "
            "retrying once with the same task and settings"
        )
        if invocation.conversation_id:
            previous_conversation_ids.append(invocation.conversation_id)
        receipt.update(state="RETRYING", last_event="automatic_retry")
        invocation = invoke_for_run(append_raw=raw_path is not None)
        invocation = invocation._replace(
            terminal_result=with_actionable_failure(
                invocation.terminal_result,
                invocation.diagnostics,
            )
        )
        attempts = 2
        elapsed += invocation.elapsed

    return_code = invocation.return_code
    conversation_id = invocation.conversation_id
    terminal_result = invocation.terminal_result
    tool_counts = invocation.tool_counts
    session_metadata = invocation.session_metadata

    terminal_result = normalize_terminal_result(
        terminal_result,
        invocation.partial_response,
        serialized_prompt_bytes,
    )

    status = str(terminal_result.get("status") or "UNKNOWN")
    default_handoff_path: pathlib.Path | None = None
    if not args.output or prompt_file_path:
        default_handoff_path, prompt_file_path = finalize_artifact_paths(
            turn_id,
            conversation_id or str(args.conversation or ""),
            prompt_file_path,
        )
    handoff_path = output_path_for(args, workspace, default_handoff_path)
    write_markdown(
        handoff_path,
        terminal_result,
        prompt,
        conversation_id,
        tool_counts,
        session_metadata,
        elapsed,
        args,
        prompt_file_path,
        receipt_path,
        watchdog_seconds,
        attempts,
        previous_conversation_ids,
    )

    receipt.update(
        state="COMPLETE" if status == "SUCCESS" else "ERROR",
        conversation_id=conversation_id,
        last_event=invocation.last_event,
        partial_response=invocation.partial_response,
        tool_counts=dict(tool_counts),
        error=str(terminal_result.get("error") or ""),
        final_output_path=str(handoff_path),
    )

    if conversation_id:
        print(f"conversation_id={conversation_id}")
    print(f"output_path={handoff_path}")
    if raw_path:
        print(f"raw_output_path={raw_path}")
    if prompt_file_path:
        print(f"prompt_file_path={prompt_file_path}")
    if attempts > 1:
        print(f"attempts={attempts}")
    print(f"elapsed={elapsed:.1f}s")
    print(f"status={status}")

    if return_code != 0 or status != "SUCCESS":
        err = redact(str(terminal_result.get("error") or "Antigravity did not finish successfully."))
        eprint(f"[call-agy] failed: status={status}, exit={return_code}: {err}")
        return return_code if return_code != 0 else 2
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="call_agy.py",
        description="Delegate one bounded task to the official local Antigravity CLI (agy) and capture a compact handoff.",
    )
    p.add_argument("task_positional", nargs="?", help="Task text (or use --task / stdin)")
    p.add_argument("-t", "--task", help="Task text")
    p.add_argument("-w", "--workspace", default=os.getcwd(), help="Workspace directory (default: cwd)")
    p.add_argument("-f", "--file", dest="files", action="append", default=[], help="Priority path hint; repeatable")
    p.add_argument("--add-dir", dest="add_dirs", action="append", default=[], help="Additional directory to expose to agy; repeatable")
    p.add_argument("--conversation", help="Resume a specific Antigravity conversation ID")
    p.add_argument("-c", "--continue", dest="continue_last", action="store_true", help="Continue the most recent conversation for this workspace")
    p.add_argument("--model", help="Model slug from `agy models`")
    p.add_argument("--effort", choices=("low", "medium", "high"), help="Reasoning effort")
    p.add_argument("--agent", help="Agent name from `agy agents`")
    p.add_argument("--timeout", default="10m", help="agy --print-timeout value (default: 10m)")
    p.add_argument(
        "--wrapper-timeout",
        help="Hard wrapper watchdog duration; default is --timeout plus 30s",
    )
    p.add_argument("--mode", choices=("accept-edits", "plan"), help="agy execution mode; use accept-edits for authorized file changes")
    p.add_argument("--sandbox", action="store_true", help="Enable agy terminal sandbox restrictions")
    p.add_argument(
        "--dangerously-skip-permissions",
        dest="dangerously_skip_permissions",
        action="store_true",
        help="DANGEROUS: auto-approve all agy tool calls",
    )
    p.add_argument("-o", "--output", help="Markdown handoff path")
    p.add_argument("--raw-output", help="Optional raw NDJSON capture path (may contain sensitive tool details)")
    p.add_argument("--agy-binary", default=os.environ.get("CALL_AGY_BINARY", "agy"), help="agy executable path/name")
    p.add_argument("--dry-run", action="store_true", help="Print command shape without running; task text is not placed in the command")
    return p


def main() -> int:
    try:
        return run(parser().parse_args())
    except KeyboardInterrupt:
        eprint("[call-agy] interrupted")
        return 130
    except RuntimeError as exc:
        eprint(f"[call-agy] ERROR: {redact(str(exc))}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
