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
import signal
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
RESULT_EXIT_GRACE_SECONDS = 5.0
DEFAULT_PROGRESS_REPORT_INTERVAL_SECONDS = 60.0
PROGRESS_TAIL_CHARS = 20
MAX_RECEIPT_RESPONSE_CHARS = 64 * 1024
STDERR_RING_LINES = 200
MIN_AGY_VERSION = (1, 1, 15)
RUNTIME_STATE_DIR_NAMES = (
    "brain",
    "cache",
    "conversations",
    "crashes",
    "knowledge",
    "log",
    "presence",
    "scratch",
)
TOKEN_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "thinking_tokens",
    "cache_read_tokens",
    "total_tokens",
)

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
    native_status = str(result.get("status") or "UNKNOWN")
    response = str(result.get("response") or "")
    if native_status == "SUCCESS" and response.strip() and not hint.startswith(
        "HEADLESS_PERMISSION_BLOCKED:"
    ):
        # Startup diagnostics can mention a transient auth/keyring problem before AGY recovers.
        # Do not override a usable successful response unless a tool was actually soft-denied.
        return result
    updated = dict(result)
    updated["error"] = f"{hint}\nOriginal diagnostic: {original or combined}"
    if native_status == "SUCCESS" and hint.startswith("HEADLESS_PERMISSION_BLOCKED:"):
        updated["agy_status"] = "SUCCESS"
        updated["status"] = "ERROR"
        updated["wrapper_status"] = "HEADLESS_PERMISSION_BLOCKED"
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


def verify_agy_version(executable: str) -> str:
    """Validate the local CLI without starting a model turn."""
    try:
        completed = subprocess.run(
            [executable, "--version"],
            cwd=str(pathlib.Path.home()),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"AGY_VERSION_CHECK_FAILED: Could not query `agy --version`: {exc}") from exc
    combined = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", combined)
    if completed.returncode != 0 or not match:
        raise RuntimeError(
            "AGY_VERSION_CHECK_FAILED: `agy --version` did not return a semantic version. "
            f"exit={completed.returncode}; output={combined or '(empty)'}"
        )
    version = tuple(int(part) for part in match.groups())
    if version < MIN_AGY_VERSION:
        required = ".".join(str(part) for part in MIN_AGY_VERSION)
        raise RuntimeError(
            f"AGY_VERSION_UNSUPPORTED: Found {match.group(0)}; call-agy requires {required}+ "
            "for structured stream-json I/O. Run `agy update` or reinstall."
        )
    return match.group(0)


def agy_state_dir(home: pathlib.Path | None = None) -> pathlib.Path:
    return (home or pathlib.Path.home()) / ".gemini" / "antigravity-cli"


def probe_writable_directory(
    path: pathlib.Path,
    *,
    label: str,
    error_code: str = "AGY_STATE_UNAVAILABLE",
) -> None:
    """Fail before model usage when a required runtime directory is not writable."""
    if not path.is_dir():
        raise RuntimeError(
            f"{error_code}: Required {label} directory does not exist: {path}."
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
        if label.startswith("state"):
            message = (
                "HOST_SANDBOX_BLOCKED: call-agy cannot write Antigravity's required local "
                f"{label} directory: {path}. Grant the host process write access to "
                "~/.gemini/antigravity-cli. Antigravity --mode, --sandbox, and "
                "--dangerously-skip-permissions cannot override the host sandbox."
            )
        else:
            message = f"{error_code}: call-agy cannot write required {label} directory: {path}."
        raise RuntimeError(f"{message} ({exc})") from exc


def probe_agy_state(state_dir: pathlib.Path) -> list[str]:
    """Probe the state root and existing runtime subdirectories used during a turn."""
    probe_writable_directory(state_dir, label="state")
    probed = [str(state_dir)]
    for name in RUNTIME_STATE_DIR_NAMES:
        child = state_dir / name
        if child.is_dir():
            probe_writable_directory(child, label=f"state/{name}")
            probed.append(str(child))
    return probed


def permission_summary(state_dir: pathlib.Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "settings_present": False,
        "allow_count": 0,
        "ask_count": 0,
        "deny_count": 0,
        "command_allow_count": 0,
        "command_ask_count": 0,
        "command_deny_count": 0,
        "tool_permission": "request-review",
    }
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
    ask = permissions.get("ask") if isinstance(permissions, dict) else None
    deny = permissions.get("deny") if isinstance(permissions, dict) else None
    allow_rules = [str(rule) for rule in allow] if isinstance(allow, list) else []
    summary["allow_count"] = len(allow_rules)
    summary["ask_count"] = len(ask) if isinstance(ask, list) else 0
    summary["deny_count"] = len(deny) if isinstance(deny, list) else 0
    summary["command_allow_count"] = sum(
        1 for rule in allow_rules if rule.strip().lower().startswith("command(")
    )
    ask_rules = [str(rule) for rule in ask] if isinstance(ask, list) else []
    deny_rules = [str(rule) for rule in deny] if isinstance(deny, list) else []
    summary["command_ask_count"] = sum(
        1 for rule in ask_rules if rule.strip().lower().startswith("command(")
    )
    summary["command_deny_count"] = sum(
        1 for rule in deny_rules if rule.strip().lower().startswith("command(")
    )
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
    print_seconds = parse_duration_seconds(print_timeout)
    if explicit:
        explicit_seconds = parse_duration_seconds(explicit)
        if explicit_seconds <= print_seconds:
            raise RuntimeError(
                "INVALID_TIMEOUT_ORDER: --wrapper-timeout must be greater than --timeout so "
                "AGY can emit its terminal result before the wrapper watchdog fires."
            )
        if explicit_seconds < print_seconds + DEFAULT_WATCHDOG_GRACE_SECONDS:
            eprint(
                "[call-agy] WARNING: --wrapper-timeout leaves less than the recommended "
                f"{DEFAULT_WATCHDOG_GRACE_SECONDS:.0f}s finalization grace."
            )
        return explicit_seconds
    return print_seconds + DEFAULT_WATCHDOG_GRACE_SECONDS


def receipt_path_for(turn_id: str) -> pathlib.Path:
    path = temp_root() / ".staging" / f"{turn_id}-receipt.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def atomic_write_text(path: pathlib.Path, text: str) -> None:
    """Write a UTF-8 artifact atomically in its destination directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


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
        idle_timeout_seconds: float = 600.0,
        idle_grace_seconds: float = 300.0,
    ) -> None:
        self.path = path
        self._lock = threading.Lock()
        self.available = True
        self._warned = False
        self._data: dict[str, Any] = {
            "turn_id": turn_id,
            "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "workspace": str(workspace),
            "mode": mode or "default",
            "watchdog_seconds": watchdog_seconds,
            "idle_timeout_seconds": idle_timeout_seconds,
            "idle_grace_seconds": idle_grace_seconds,
            "state": "STARTING",
            "conversation_id": "",
            "last_event": "receipt_created",
            "last_diagnostic": "",
            "partial_response": "",
            "tool_counts": {},
            "attempts": [],
            "final_output_path": "",
            "error": "",
        }
        self._try_write_locked()

    def update(self, **changes: Any) -> bool:
        with self._lock:
            self._data.update(changes)
            return self._try_write_locked()

    def record_attempt(self, attempt: int, **details: Any) -> bool:
        with self._lock:
            attempts = list(self._data.get("attempts") or [])
            entry = {"attempt": attempt, **details}
            attempts.append(entry)
            self._data["attempts"] = attempts
            return self._try_write_locked()

    def _try_write_locked(self) -> bool:
        if not self.available:
            return False
        try:
            self._write_locked()
            return True
        except OSError as exc:
            self.available = False
            if not self._warned:
                self._warned = True
                eprint(
                    f"[call-agy] WARNING: receipt updates disabled because {self.path} "
                    f"is not writable: {exc}"
                )
            return False

    def _write_locked(self) -> None:
        partial = str(self._data.get("partial_response") or "")
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
            f"- idle_warning_seconds: `{self._data['idle_timeout_seconds']:.1f}`",
            f"- idle_grace_seconds: `{self._data['idle_grace_seconds']:.1f}`",
        ]
        if self._data.get("conversation_id"):
            lines.append(f"- conversation_id: `{self._data['conversation_id']}`")
        lines.append(f"- last_event: `{self._data['last_event']}`")
        if self._data.get("agy_version"):
            lines.append(f"- agy_version: `{self._data['agy_version']}`")
        if self._data.get("tool_permission"):
            lines.append(f"- observed_tool_permission: `{self._data['tool_permission']}`")
        for key in (
            "allow_count",
            "command_allow_count",
            "ask_count",
            "command_ask_count",
            "deny_count",
            "command_deny_count",
        ):
            if key in self._data:
                lines.append(f"- observed_{key}: `{self._data[key]}`")
        if self._data.get("active_tool"):
            lines.append(f"- active_tool: `{self._data['active_tool']}`")
        if tools:
            lines += ["", "## Completed tools", ""]
            for name, count in sorted(tools.items()):
                lines.append(f"- `{name}` ×{count}")
        if partial:
            lines += ["", "## Partial response", ""]
            if truncated:
                lines.append("_Only the last 64 KiB is retained in this crash receipt._\n")
            lines.append(partial)
        attempts = self._data.get("attempts") or []
        if attempts:
            lines += ["", "## Attempts", ""]
            for item in attempts:
                detail = ", ".join(
                    f"{key}={value}" for key, value in item.items() if key != "attempt" and value not in (None, "", {})
                )
                lines.append(f"- attempt {item.get('attempt')}: {detail or 'recorded'}")
        if self._data.get("last_diagnostic"):
            lines += ["", "## Last diagnostic", "", str(self._data["last_diagnostic"])]
        if self._data.get("error"):
            lines += ["", "## Error", "", str(self._data["error"])]
        if self._data.get("final_output_path"):
            lines += ["", "## Final handoff", "", str(self._data["final_output_path"])]
        lines.append("")
        atomic_write_text(self.path, "\n".join(lines))


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
    parts.append(
        "Keep inspection proportional to the task: avoid unbounded recursive enumeration, "
        "and if remaining time is limited, stop exploring and deliver the evidence already gathered."
    )
    return "\n".join(parts)


def temp_root() -> pathlib.Path:
    root = pathlib.Path(tempfile.gettempdir()) / "call-agy"
    root.mkdir(parents=True, exist_ok=True)
    return root


def create_turn_id() -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{os.getpid()}-{secrets.token_hex(8)}"


def big_prompt_dir(turn_id: str) -> pathlib.Path:
    path = temp_root() / ".big-prompt" / turn_id
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
        prompt_parent = prompt_file_path.parent
        target = destination / f"{turn_id}-prompt.txt"
        try:
            prompt_file_path.replace(target)
            try:
                prompt_parent.rmdir()
            except OSError:
                pass
        except OSError as exc:
            eprint(
                f"[call-agy] WARNING: could not finalize temporary artifact paths: {exc}"
            )
            return temp_root() / f"{turn_id}-handoff.md", prompt_file_path
        grouped_prompt = target

    return destination / f"{turn_id}-handoff.md", grouped_prompt


def resolved_output_path(raw: str, workspace: pathlib.Path) -> pathlib.Path:
    path = pathlib.Path(os.path.expandvars(os.path.expanduser(raw)))
    if not path.is_absolute():
        path = workspace / path
    return path.resolve(strict=False)


def select_explicit_output_path(
    raw: str,
    workspace: pathlib.Path,
    turn_id: str,
    *,
    force: bool,
    label: str,
) -> pathlib.Path:
    path = resolved_output_path(raw, workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    probe_writable_directory(
        path.parent,
        label=f"{label} parent",
        error_code="OUTPUT_UNAVAILABLE",
    )
    if force or not path.exists():
        return path
    candidate = path.with_name(f"{path.stem}-{turn_id}{path.suffix}")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.stem}-{turn_id}-{counter}{path.suffix}")
        counter += 1
    eprint(
        f"[call-agy] WARNING: {label} already exists; preserving it and using {candidate}"
    )
    return candidate


def output_path_for(
    args: argparse.Namespace,
    workspace: pathlib.Path,
    default_path: pathlib.Path | None,
) -> pathlib.Path:
    if args.output:
        p = resolved_output_path(args.output, workspace)
    else:
        if default_path is None:
            raise RuntimeError("Internal error: default handoff path was not prepared.")
        p = default_path
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def raw_path_for(raw: str | None, workspace: pathlib.Path) -> pathlib.Path | None:
    if not raw:
        return None
    p = resolved_output_path(raw, workspace)
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
    actual_turn_id = turn_id or create_turn_id()
    prompt_path = big_prompt_dir(actual_turn_id) / "prompt.txt"
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


def empty_success_error(
    serialized_prompt_bytes: int,
    *,
    sent_prompt_bytes: int | None = None,
    prompt_transport: str = "stdin",
) -> str:
    return (
        "Antigravity returned SUCCESS with an empty response and zero token usage before "
        "running a model. "
        f"Original serialized task size: {serialized_prompt_bytes} bytes; "
        f"transport: {prompt_transport}; serialized message sent to AGY: "
        f"{sent_prompt_bytes if sent_prompt_bytes is not None else serialized_prompt_bytes} bytes."
    )


def normalize_terminal_result(
    result: dict[str, Any],
    partial_response: str,
    serialized_prompt_bytes: int,
    *,
    return_code: int = 0,
    sent_prompt_bytes: int | None = None,
    prompt_transport: str = "stdin",
    post_result_cleanup: bool = False,
) -> dict[str, Any]:
    """Combine native status, process exit, and streamed text into one wrapper truth."""
    updated = dict(result)
    native_status = str(updated.get("status") or "UNKNOWN")
    response = str(updated.get("response") or "")
    partial = partial_response.strip()

    if return_code != 0 and native_status == "SUCCESS" and post_result_cleanup:
        updated["process_cleanup"] = (
            "AGY emitted a terminal result but did not exit within "
            f"{RESULT_EXIT_GRACE_SECONDS:.0f} seconds."
        )

    if return_code != 0 and native_status == "SUCCESS" and not post_result_cleanup:
        updated["agy_status"] = native_status
        updated["status"] = "ERROR"
        updated["wrapper_status"] = "PROCESS_EXIT_MISMATCH"
        updated["response"] = ""
        if response.strip():
            updated["partial_response"] = response.rstrip()
        elif partial:
            updated["partial_response"] = partial_response.rstrip()
        updated["error"] = (
            f"Antigravity reported SUCCESS, but its process exited with code {return_code}. "
            "The response is preserved as partial evidence rather than a successful result."
        )
        return updated

    if native_status != "SUCCESS" and response.strip():
        updated["response"] = ""
        updated["partial_response"] = response.rstrip()
        updated.setdefault("wrapper_status", "PARTIAL_NON_SUCCESS")
        return updated

    if response.strip() and native_status == "SUCCESS":
        if post_result_cleanup:
            updated["wrapper_status"] = "SUCCESS_WITH_POST_RESULT_CLEANUP"
        return updated
    if partial:
        if native_status == "SUCCESS":
            updated["response"] = partial_response.rstrip()
            updated["response_source"] = "stream-json text_delta recovery"
            updated["wrapper_status"] = "RECOVERED_STREAM_RESPONSE"
        else:
            updated["partial_response"] = partial_response.rstrip()
            updated.setdefault("wrapper_status", "PARTIAL_NO_FINAL_RESPONSE")
        return updated
    if native_status == "SUCCESS":
        zero_usage_empty = is_empty_zero_usage_success(updated)
        updated["agy_status"] = "SUCCESS"
        updated["status"] = "ERROR"
        updated["wrapper_status"] = "NO_FINAL_RESPONSE"
        if zero_usage_empty:
            updated["error"] = empty_success_error(
                serialized_prompt_bytes,
                sent_prompt_bytes=sent_prompt_bytes,
                prompt_transport=prompt_transport,
            )
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
        errors.append(str(exc))
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def drain_stderr(pipe: Any, collected: Any, receipt: RunReceipt | None = None) -> None:
    try:
        for line in pipe:
            diagnostic = line.rstrip("\r\n")
            collected.append(diagnostic)
            if diagnostic:
                eprint(f"[agy:stderr] {diagnostic}")
                if receipt:
                    receipt.update(last_event="stderr", last_diagnostic=diagnostic)
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def terminate_process_tree(proc: subprocess.Popen[str], *, reason: str) -> None:
    """Boundedly terminate AGY and the tool processes it started."""
    if proc.poll() is not None:
        return
    eprint(f"[call-agy] terminating agy process tree: {reason}")
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            proc.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            pass
    else:
        for process_signal in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, process_signal)
            except (OSError, ProcessLookupError):
                pass
            try:
                proc.wait(timeout=TERMINATION_GRACE_SECONDS)
                return
            except subprocess.TimeoutExpired:
                continue
    try:
        proc.kill()
    except OSError:
        pass


def bounded_process_wait(proc: subprocess.Popen[str]) -> int:
    try:
        return proc.wait(timeout=TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        terminate_process_tree(proc, reason="process did not exit after stdout closed")
        try:
            return proc.wait(timeout=TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            return 124


def progress_from_event(
    event: dict[str, Any],
    seen_tools: set[tuple[int, str]],
    active_tools: set[tuple[int, str]],
    tool_counts: collections.Counter[str],
) -> None:
    if event.get("event") != "step_update":
        return
    step = event.get("step_update") or {}
    if not isinstance(step, dict):
        return
    step_type = str(step.get("step_type") or "")
    state = str(step.get("state") or "")
    step_index = int(step.get("step_index") or 0)

    if step_type == "tool":
        tool_name = str(step.get("tool_name") or (step.get("tool_info") or {}).get("name") or "tool")
        key = (step_index, tool_name)
        if state == "ACTIVE" and key not in active_tools:
            active_tools.add(key)
            eprint(f"[agy] tool active: {tool_name}")
        elif state == "DONE":
            active_tools.discard(key)
            if key not in seen_tools:
                seen_tools.add(key)
                tool_counts[tool_name] += 1
                eprint(f"[agy] tool done: {tool_name}")

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
    post_result_cleanup: bool = False


def invoke_once(
    cmd: list[str],
    workspace: pathlib.Path,
    prompt: str,
    raw_path: pathlib.Path | None,
    *,
    append_raw: bool = False,
    watchdog_seconds: float | None = None,
    idle_timeout_seconds: float = 600.0,
    idle_grace_seconds: float = 300.0,
    receipt: RunReceipt | None = None,
    attempt: int = 1,
) -> InvocationResult:
    stderr_lines: collections.deque[str] = collections.deque(maxlen=STDERR_RING_LINES)
    stdin_errors: list[str] = []
    tool_counts: collections.Counter[str] = collections.Counter()
    seen_tools: set[tuple[int, str]] = set()
    active_tools: set[tuple[int, str]] = set()
    session_metadata: dict[str, str] = {}
    conversation_id = ""
    terminal_result: dict[str, Any] | None = None
    saw_tool_step = False
    partial_fragments: list[str] = []
    last_event = "launching"
    timed_out = threading.Event()
    watchdog_stop = threading.Event()
    watchdog_wakeup = threading.Event()
    activity_lock = threading.Lock()
    idle_warned = False
    timeout_details: dict[str, str] = {}
    post_result_cleanup = False
    last_response_report_at = 0.0
    raw_handle = raw_path.open("a" if append_raw else "w", encoding="utf-8") if raw_path else None
    if raw_handle:
        raw_handle.write(json.dumps({"event": "call_agy_attempt", "attempt": attempt}) + "\n")
        raw_handle.flush()

    started = time.monotonic()
    last_activity = started

    def note_stream_activity() -> None:
        nonlocal last_activity, idle_warned
        with activity_lock:
            resumed = idle_warned
            last_activity = time.monotonic()
            idle_warned = False
        watchdog_wakeup.set()
        if resumed:
            eprint("[agy] valid stream activity resumed after idle warning")
            if receipt:
                receipt.update(
                    state="STREAMING",
                    last_event="stream_activity_resumed",
                    last_diagnostic="",
                )
    if receipt:
        receipt.update(state="LAUNCHING", last_event=last_event)
    try:
        popen_options: dict[str, Any] = {}
        if os.name == "nt":
            popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_options["start_new_session"] = True
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
            **popen_options,
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
            post_result_cleanup=False,
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
        nonlocal idle_warned
        absolute_deadline = started + watchdog_seconds if watchdog_seconds is not None else None
        while not watchdog_stop.is_set():
            now = time.monotonic()
            action = ""
            message = ""
            wait_seconds = 1.0
            with activity_lock:
                idle_warning_deadline = last_activity + idle_timeout_seconds
                idle_termination_deadline = idle_warning_deadline + idle_grace_seconds
                deadlines = [idle_termination_deadline]
                if not idle_warned:
                    deadlines.append(idle_warning_deadline)
                if absolute_deadline is not None:
                    deadlines.append(absolute_deadline)
                wait_seconds = max(0.05, min(deadlines) - now)

                if absolute_deadline is not None and now >= absolute_deadline:
                    action = "TIMEOUT"
                    message = f"Wrapper hard limit expired after {watchdog_seconds:.1f}s."
                elif now >= idle_termination_deadline:
                    action = "IDLE_TIMEOUT"
                    message = (
                        "No valid AGY stream activity was received for "
                        f"{idle_timeout_seconds + idle_grace_seconds:.1f}s "
                        f"({idle_timeout_seconds:.1f}s idle threshold plus "
                        f"{idle_grace_seconds:.1f}s grace)."
                    )
                elif now >= idle_warning_deadline and not idle_warned:
                    idle_warned = True
                    action = "IDLE_WARNING"
                    message = (
                        f"No valid AGY stream activity for {idle_timeout_seconds:.1f}s; "
                        f"waiting {idle_grace_seconds:.1f}s grace before termination."
                    )
                    wait_seconds = max(0.05, idle_termination_deadline - now)

            if action == "IDLE_WARNING":
                eprint(f"[call-agy] WARNING: {message}")
                if receipt:
                    receipt.update(
                        state="IDLE_WARNING",
                        last_event="idle_warning",
                        last_diagnostic=message,
                    )
                continue
            if action in {"TIMEOUT", "IDLE_TIMEOUT"}:
                timeout_details["wrapper_status"] = action
                timeout_details["message"] = message
                timed_out.set()
                if receipt:
                    receipt.update(
                        state="TERMINATING",
                        last_event=action.lower(),
                        error=message,
                    )
                eprint(f"[call-agy] {message} Terminating AGY process tree.")
                terminate_process_tree(proc, reason=action.lower())
                return

            watchdog_wakeup.wait(wait_seconds)
            watchdog_wakeup.clear()

    stdin_thread.start()
    stderr_thread.start()
    watchdog_thread = threading.Thread(target=watchdog, daemon=True)
    watchdog_thread.start()

    malformed_stdout: collections.deque[str] = collections.deque(maxlen=5)
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
                malformed_stdout.append(line[:500])
                if receipt:
                    receipt.update(
                        last_event="malformed_stdout",
                        last_diagnostic=f"Non-JSON stdout: {line[:500]}",
                    )
                continue
            if not isinstance(event, dict):
                continue
            event_name = str(event.get("event") or "unknown")
            if (
                event_name == "init" and isinstance(event.get("init"), dict)
            ) or (
                event_name == "step_update" and isinstance(event.get("step_update"), dict)
            ):
                note_stream_activity()
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
                    eprint(f"[agy] result: {payload.get('status') or 'UNKNOWN'}")
            elif event.get("event") == "step_update":
                step = event.get("step_update")
                if isinstance(step, dict):
                    step_type = str(step.get("step_type") or "unknown")
                    step_state = str(step.get("state") or "unknown")
                    last_event = f"step_update:{step_type}:{step_state}"
                    if step_type == "tool":
                        saw_tool_step = True
                        tool_name = str(
                            step.get("tool_name")
                            or (step.get("tool_info") or {}).get("name")
                            or "tool"
                        )
                        if receipt:
                            receipt.update(
                                active_tool=(
                                    f"{tool_name} (step {step.get('step_index', '?')})"
                                    if step_state == "ACTIVE"
                                    else ""
                                )
                            )
                    if step_type == "agent_response":
                        delta = step.get("text_delta")
                        if isinstance(delta, str) and delta:
                            partial_fragments.append(delta)
                            now = time.monotonic()
                            if (
                                last_response_report_at == 0.0
                                or now - last_response_report_at
                                >= DEFAULT_PROGRESS_REPORT_INTERVAL_SECONDS
                            ):
                                response_so_far = "".join(partial_fragments)
                                compact = re.sub(r"\s+", " ", response_so_far).strip()
                                tail = compact[-PROGRESS_TAIL_CHARS:]
                                updated_at = dt.datetime.now().astimezone().isoformat(timespec="seconds")
                                eprint(
                                    "[agy] response active: "
                                    f"chars={len(response_so_far)}, updated={updated_at}, "
                                    f"tail={json.dumps(tail, ensure_ascii=False)}"
                                )
                                last_response_report_at = now
            progress_from_event(event, seen_tools, active_tools, tool_counts)
            if receipt:
                receipt.update(
                    state="STREAMING" if terminal_result is None else "FINALIZING",
                    conversation_id=conversation_id,
                    last_event=last_event,
                    partial_response="".join(partial_fragments),
                    tool_counts=dict(tool_counts),
                )
            if event_name == "result" and terminal_result is not None:
                break
    except BaseException:
        terminate_process_tree(proc, reason="wrapper interrupted while reading AGY output")
        raise
    finally:
        proc.stdout.close()
        if terminal_result is not None:
            watchdog_stop.set()
            watchdog_wakeup.set()
            watchdog_thread.join(timeout=2)
            try:
                return_code = proc.wait(timeout=RESULT_EXIT_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                post_result_cleanup = True
                eprint(
                    "[call-agy] AGY emitted a terminal result but did not exit within "
                    f"{RESULT_EXIT_GRACE_SECONDS:.0f}s; cleaning up its process tree"
                )
                terminate_process_tree(proc, reason="post-result exit grace expired")
                try:
                    return_code = proc.wait(timeout=TERMINATION_GRACE_SECONDS)
                except subprocess.TimeoutExpired:
                    return_code = 124
        else:
            return_code = bounded_process_wait(proc)
            watchdog_stop.set()
            watchdog_wakeup.set()
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
            "wrapper_status": timeout_details.get("wrapper_status") or "TIMEOUT",
            "response": "",
            "error": timeout_details.get("message") or "Wrapper timeout expired.",
        }
        if native_status:
            terminal_result["agy_status"] = native_status
        return_code = 124
        last_event = str(terminal_result["wrapper_status"]).lower()
    elif terminal_result is None:
        diagnostics = [*stdin_errors, *list(stderr_lines)[-20:], *malformed_stdout]
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
        diagnostics=[*stdin_errors, *list(stderr_lines)[-20:], *malformed_stdout],
        partial_response=partial_response,
        last_event=last_event,
        timed_out=timed_out.is_set(),
        post_result_cleanup=post_result_cleanup,
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
    if response and status == "SUCCESS":
        lines += ["## Result", "", response]
    elif partial_response or response:
        recovered = partial_response or response
        lines += [
            "## Partial result",
            "",
            "_The run was not successful. Text received before or with the failure is preserved below as partial evidence._",
            "",
            recovered,
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
    if "process_exit_code" in result:
        lines.append(f"- process_exit_code: `{result['process_exit_code']}`")
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
    lines.append(f"- agy_total_timeout: `{args.timeout}`")
    lines.append(f"- idle_warning: `{getattr(args, 'idle_timeout', '10m')}`")
    lines.append(f"- idle_grace: `{getattr(args, 'idle_grace', '5m')}`")
    lines.append(f"- elapsed: `{elapsed:.1f}s`")
    if "duration_seconds" in result:
        lines.append(f"- agy_cumulative_duration_seconds: `{result['duration_seconds']}`")
    if "num_turns" in result:
        lines.append(f"- agy_cumulative_num_turns: `{result['num_turns']}`")

    if tool_counts:
        lines += ["", "## Tools used", ""]
        for name, count in sorted(tool_counts.items()):
            lines.append(f"- `{name}` ×{count}")

    attempt_records = result.get("attempt_records")
    if isinstance(attempt_records, list) and attempt_records:
        lines += ["", "## Attempt history", ""]
        for record in attempt_records:
            if not isinstance(record, dict):
                continue
            summary = [
                f"native_status={record.get('native_status', 'UNKNOWN')}",
                f"exit={record.get('exit_code', '?')}",
                f"elapsed={record.get('elapsed_seconds', '?')}s",
            ]
            if record.get("conversation_id"):
                summary.append(f"conversation_id={record['conversation_id']}")
            if record.get("wrapper_status"):
                summary.append(f"wrapper_status={record['wrapper_status']}")
            if record.get("final_wrapper_status") and record.get("final_wrapper_status") != record.get(
                "wrapper_status"
            ):
                summary.append(f"final_wrapper_status={record['final_wrapper_status']}")
            if record.get("tool_counts"):
                summary.append(f"tools={record['tool_counts']}")
            lines.append(f"- attempt {record.get('attempt', '?')}: " + ", ".join(summary))

    if usage:
        lines += [
            "",
            "## Usage reported by AGY",
            "",
            "_These are native AGY counters. On resumed conversations they may be cumulative; do not add cache_read_tokens to total_tokens or infer billing without provider evidence._",
            "",
        ]
        for key in ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens"):
            if key in usage:
                lines.append(f"- {key}: `{usage[key]}`")

    error = result.get("error")
    if error:
        lines += ["", "## Error", "", str(error)]

    if result.get("process_cleanup"):
        lines += ["", "## Process cleanup", "", str(result["process_cleanup"])]

    if result.get("recovery_prompt") and result.get("recovery_conversation_id"):
        lines += [
            "",
            "## Suggested recovery",
            "",
            f"- Resume conversation: `{result['recovery_conversation_id']}`",
            "- Suggested next task:",
            "",
            markdown_code_block(str(result["recovery_prompt"])),
            "",
            "This is a suggestion only; call-agy did not spend tokens on an automatic recovery turn.",
        ]

    lines += [
        "",
        "---",
        "Generated by `call-agy` via the official local `agy` CLI. Raw tool arguments/outputs are omitted by default.",
        "",
    ]
    atomic_write_text(path, "\n".join(lines))


def write_markdown_resilient(
    preferred_path: pathlib.Path,
    turn_id: str,
    *args: Any,
    **kwargs: Any,
) -> pathlib.Path:
    try:
        write_markdown(preferred_path, *args, **kwargs)
        return preferred_path
    except OSError as exc:
        fallback = temp_root() / f"{turn_id}-handoff-fallback.md"
        eprint(
            f"[call-agy] WARNING: could not write handoff to {preferred_path}: {exc}; "
            f"using fallback {fallback}"
        )
        try:
            write_markdown(fallback, *args, **kwargs)
        except OSError as fallback_exc:
            raise RuntimeError(
                "HANDOFF_WRITE_FAILED: model execution finished, but neither the requested "
                f"handoff nor fallback was writable. primary={exc}; fallback={fallback_exc}"
            ) from fallback_exc
        return fallback


def add_recovery_suggestion(result: dict[str, Any], conversation_id: str) -> dict[str, Any]:
    wrapper_status = str(result.get("wrapper_status") or "")
    if not conversation_id or wrapper_status not in {
        "TIMEOUT",
        "IDLE_TIMEOUT",
        "NO_TERMINAL_RESULT",
        "NO_FINAL_RESPONSE",
        "PROCESS_EXIT_MISMATCH",
    }:
        return result
    updated = dict(result)
    updated["recovery_conversation_id"] = conversation_id
    updated["recovery_prompt"] = (
        "Stop further exploration. Based only on evidence already gathered in this conversation, "
        "return the best concise handoff now: outcome, files changed, verification, blockers, and "
        "remaining uncertainty. Do not rerun broad searches or repeat completed work."
    )
    return updated


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class ConversationLock:
    """Prevent concurrent explicit resumes of one AGY conversation."""

    def __init__(self, conversation_id: str | None) -> None:
        self.path: pathlib.Path | None = None
        if conversation_id:
            lock_dir = temp_root() / ".locks"
            lock_dir.mkdir(parents=True, exist_ok=True)
            self.path = lock_dir / f"{conversation_key(conversation_id)}.lock"

    def __enter__(self) -> "ConversationLock":
        if self.path is None:
            return self
        for _ in range(2):
            try:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(f"{os.getpid()}\n")
                return self
            except FileExistsError:
                try:
                    owner = int(self.path.read_text(encoding="utf-8").strip())
                except (OSError, ValueError):
                    owner = -1
                if process_is_running(owner):
                    raise RuntimeError(
                        "CONVERSATION_BUSY: another call-agy process is already resuming this "
                        f"conversation (pid={owner})."
                    )
                try:
                    self.path.unlink()
                except OSError as exc:
                    raise RuntimeError(f"CONVERSATION_LOCK_FAILED: {exc}") from exc
        raise RuntimeError("CONVERSATION_LOCK_FAILED: could not acquire conversation lock.")

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.path:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                eprint(f"[call-agy] WARNING: could not remove conversation lock {self.path}")


def run_locked(args: argparse.Namespace) -> int:
    executable = resolve_executable(args.agy_binary)
    workspace = normalize_workspace(args.workspace)
    external_dirs = [str(normalize_add_dir(raw)) for raw in args.add_dirs]
    args.add_dirs = accessible_dirs(workspace, external_dirs)
    task = read_task(args)
    prompt = build_prompt(task, workspace, args.files, external_dirs, args.mode)
    serialized_prompt_bytes = prompt_event_size(prompt)
    print_timeout_seconds = parse_duration_seconds(args.timeout)
    idle_timeout_seconds = parse_duration_seconds(getattr(args, "idle_timeout", "10m"))
    idle_grace_seconds = parse_duration_seconds(getattr(args, "idle_grace", "5m"))
    watchdog_seconds = wrapper_timeout_seconds(args.timeout, args.wrapper_timeout)
    agy_version = verify_agy_version(executable)

    if args.dry_run:
        cmd = build_command(args, executable)
        print(f"cwd={workspace}")
        print(f"command={dry_run_shape(cmd)}")
        print(f"agy_version={agy_version}")
        print(f"serialized_prompt_bytes={serialized_prompt_bytes}")
        transport = "system-temp-file" if serialized_prompt_bytes > STREAM_INPUT_SAFE_BYTES else "stdin"
        print(f"prompt_transport={transport}")
        print(f"agy_total_timeout_seconds={print_timeout_seconds:.1f}")
        print(f"idle_warning_seconds={idle_timeout_seconds:.1f}")
        print(f"idle_grace_seconds={idle_grace_seconds:.1f}")
        print(f"wrapper_watchdog_seconds={watchdog_seconds:.1f}")
        print(f"recommended_host_timeout_seconds={watchdog_seconds + DEFAULT_WATCHDOG_GRACE_SECONDS:.1f}")
        return 0

    turn_id = create_turn_id()
    probe_writable_directory(temp_root(), label="temporary artifact", error_code="OUTPUT_UNAVAILABLE")
    if args.output:
        args.output = str(
            select_explicit_output_path(
                args.output, workspace, turn_id, force=args.force, label="handoff output"
            )
        )
    if args.raw_output:
        args.raw_output = str(
            select_explicit_output_path(
                args.raw_output, workspace, turn_id, force=args.force, label="raw output"
            )
        )
    if args.output and args.raw_output:
        if os.path.normcase(str(resolved_output_path(args.output, workspace))) == os.path.normcase(
            str(resolved_output_path(args.raw_output, workspace))
        ):
            raise RuntimeError(
                "OUTPUT_PATH_CONFLICT: --output and --raw-output must use different files."
            )
    raw_path = raw_path_for(args.raw_output, workspace)

    receipt_path = receipt_path_for(turn_id)
    receipt = RunReceipt(
        receipt_path,
        turn_id=turn_id,
        workspace=workspace,
        mode=args.mode,
        watchdog_seconds=watchdog_seconds,
        idle_timeout_seconds=idle_timeout_seconds,
        idle_grace_seconds=idle_grace_seconds,
    )
    print(f"receipt_path={receipt_path}", flush=True)

    try:
        state_dir = agy_state_dir()
        probed_state_paths = probe_agy_state(state_dir)
        permission_info = permission_summary(state_dir)
        tool_permission = str(permission_info.get("tool_permission") or "request-review")
        receipt.update(
            state="PREFLIGHT_OK",
            last_event="state_write_probe_ok",
            agy_version=agy_version,
            probed_state_path_count=len(probed_state_paths),
            tool_permission=tool_permission,
            allow_count=permission_info.get("allow_count", 0),
            command_allow_count=permission_info.get("command_allow_count", 0),
            ask_count=permission_info.get("ask_count", 0),
            command_ask_count=permission_info.get("command_ask_count", 0),
            deny_count=permission_info.get("deny_count", 0),
            command_deny_count=permission_info.get("command_deny_count", 0),
        )
        if (
            not args.dangerously_skip_permissions
            and tool_permission not in {"proceed-in-sandbox", "always-proceed"}
        ):
            command_allow_count = permission_info.get("command_allow_count", 0)
            if command_allow_count:
                warning = (
                    "HEADLESS_PERMISSION_RISK: command allow rules were observed, but this "
                    "preflight cannot prove that the commands required by this task are allowed; "
                    "ask/deny rules take precedence and unmatched commands may still be soft-denied."
                )
            else:
                warning = (
                    "HEADLESS_PERMISSION_RISK: no command(...) rule was observed under "
                    "permissions.allow; shell commands may be soft-denied in headless mode. "
                    "Other allow rules do not prove command authorization."
                )
            eprint(f"[call-agy] WARNING: {warning}")
            receipt.update(last_event="permission_preflight_warning", last_diagnostic=warning)
    except RuntimeError as exc:
        terminal_result = {
            "status": "ERROR",
            "wrapper_status": "PRECHECK_FAILED",
            "response": "",
            "error": str(exc),
            "process_exit_code": 1,
        }
        default_handoff_path, _ = finalize_artifact_paths(turn_id, "", None)
        preferred_path = output_path_for(args, workspace, default_handoff_path)
        handoff_path = write_markdown_resilient(
            preferred_path,
            turn_id,
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
        eprint(f"[call-agy] failed before model invocation: {exc}")
        return 1

    prompt_file_path: pathlib.Path | None = None
    prompt_to_send = prompt
    prompt_transport = "stdin"
    if serialized_prompt_bytes > STREAM_INPUT_SAFE_BYTES:
        prompt_file_path = materialize_prompt(prompt, turn_id)
        prompt_to_send = prompt_file_instruction(prompt_file_path)
        prompt_transport = "per-turn-system-temp-file"
        args.add_dirs = accessible_dirs(workspace, [*external_dirs, str(prompt_file_path.parent)])
        eprint(
            f"[call-agy] prompt is {serialized_prompt_bytes} serialized bytes; "
            f"using isolated prompt file: {prompt_file_path}"
        )
    sent_prompt_bytes = prompt_event_size(prompt_to_send)
    cmd = build_command(args, executable)

    def invoke_for_run(attempt: int, *, append_raw: bool = False) -> InvocationResult:
        try:
            return invoke_once(
                cmd,
                workspace,
                prompt_to_send,
                raw_path,
                append_raw=append_raw,
                watchdog_seconds=watchdog_seconds,
                idle_timeout_seconds=idle_timeout_seconds,
                idle_grace_seconds=idle_grace_seconds,
                receipt=receipt,
                attempt=attempt,
            )
        except BaseException:
            if prompt_file_path and prompt_file_path.exists():
                _, retained_prompt = finalize_artifact_paths(turn_id, "", prompt_file_path)
                if retained_prompt:
                    eprint(f"[call-agy] retained prompt file: {retained_prompt}")
            raise

    attempt_records: list[dict[str, Any]] = []

    def record_invocation(attempt: int, invocation: InvocationResult) -> None:
        record = {
            "attempt": attempt,
            "conversation_id": invocation.conversation_id,
            "native_status": str(invocation.terminal_result.get("status") or "UNKNOWN"),
            "wrapper_status": str(invocation.terminal_result.get("wrapper_status") or ""),
            "exit_code": invocation.return_code,
            "elapsed_seconds": round(invocation.elapsed, 3),
            "tool_counts": dict(invocation.tool_counts),
        }
        attempt_records.append(record)
        receipt.record_attempt(attempt, **{key: value for key, value in record.items() if key != "attempt"})

    invocation = invoke_for_run(1)
    invocation = invocation._replace(
        terminal_result=with_actionable_failure(invocation.terminal_result, invocation.diagnostics)
    )
    record_invocation(1, invocation)
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
        invocation = invoke_for_run(2, append_raw=raw_path is not None)
        invocation = invocation._replace(
            terminal_result=with_actionable_failure(invocation.terminal_result, invocation.diagnostics)
        )
        record_invocation(2, invocation)
        attempts = 2
        elapsed += invocation.elapsed

    return_code = invocation.return_code
    emitted_conversation_id = invocation.conversation_id
    requested_conversation_id = str(args.conversation or "")
    conversation_id = emitted_conversation_id or requested_conversation_id
    terminal_result = normalize_terminal_result(
        invocation.terminal_result,
        invocation.partial_response,
        serialized_prompt_bytes,
        return_code=return_code,
        sent_prompt_bytes=sent_prompt_bytes,
        prompt_transport=prompt_transport,
        post_result_cleanup=invocation.post_result_cleanup,
    )
    terminal_result["process_exit_code"] = return_code
    terminal_result["attempt_records"] = attempt_records

    if requested_conversation_id and emitted_conversation_id and requested_conversation_id != emitted_conversation_id:
        native_response = str(terminal_result.get("response") or "")
        terminal_result.update(
            {
                "agy_status": str(terminal_result.get("status") or "UNKNOWN"),
                "status": "ERROR",
                "wrapper_status": "CONVERSATION_ID_MISMATCH",
                "response": "",
                "error": (
                    "Requested conversation ID did not match the ID emitted by AGY: "
                    f"requested={requested_conversation_id}, emitted={emitted_conversation_id}."
                ),
            }
        )
        if native_response:
            terminal_result["partial_response"] = native_response
        return_code = return_code or 2

    terminal_result = add_recovery_suggestion(terminal_result, conversation_id)
    if attempt_records:
        attempt_records[-1]["final_wrapper_status"] = str(
            terminal_result.get("wrapper_status") or ""
        )
    status = str(terminal_result.get("status") or "UNKNOWN")
    completed_successfully = status == "SUCCESS" and (
        return_code == 0 or invocation.post_result_cleanup
    )
    default_handoff_path: pathlib.Path | None = None
    if not args.output or prompt_file_path:
        default_handoff_path, prompt_file_path = finalize_artifact_paths(
            turn_id,
            conversation_id,
            prompt_file_path,
        )
    preferred_path = output_path_for(args, workspace, default_handoff_path)
    handoff_path = write_markdown_resilient(
        preferred_path,
        turn_id,
        terminal_result,
        prompt,
        conversation_id,
        invocation.tool_counts,
        invocation.session_metadata,
        elapsed,
        args,
        prompt_file_path,
        receipt_path,
        watchdog_seconds,
        attempts,
        previous_conversation_ids,
    )

    receipt_partial = str(terminal_result.get("partial_response") or invocation.partial_response)
    receipt.update(
        state="COMPLETE" if completed_successfully else "ERROR",
        conversation_id=conversation_id,
        last_event=invocation.last_event,
        partial_response=receipt_partial,
        tool_counts=dict(invocation.tool_counts),
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
    if terminal_result.get("recovery_conversation_id"):
        print(f"recovery_conversation_id={terminal_result['recovery_conversation_id']}")
    print(f"elapsed={elapsed:.1f}s")
    print(f"status={status}")

    if not completed_successfully:
        err = str(terminal_result.get("error") or "Antigravity did not finish successfully.")
        eprint(f"[call-agy] failed: status={status}, exit={return_code}: {err}")
        return return_code if return_code != 0 else 2
    return 0


def run(args: argparse.Namespace) -> int:
    if args.conversation and args.continue_last:
        raise RuntimeError("Use either --conversation or --continue, not both.")
    if args.continue_last:
        eprint(
            "[call-agy] WARNING: --continue selects workspace-global recent state and is not "
            "deterministic; prefer --conversation <id> when the exact session matters."
        )
    if args.dangerously_skip_permissions:
        eprint(
            "[call-agy] WARNING: --dangerously-skip-permissions auto-approves all agy tool "
            "calls for this run."
        )
    with ConversationLock(args.conversation):
        return run_locked(args)


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
    p.add_argument("--timeout", default="2h", help="AGY total --print-timeout ceiling (default: 2h)")
    p.add_argument(
        "--idle-timeout",
        default="10m",
        help="Warn after this long without a valid AGY stream event (default: 10m)",
    )
    p.add_argument(
        "--idle-grace",
        default="5m",
        help="Terminate if stream silence continues this long after the warning (default: 5m)",
    )
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
    p.add_argument(
        "--force",
        action="store_true",
        help="Allow explicit --output/--raw-output paths to overwrite existing files",
    )
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
        eprint(f"[call-agy] ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
