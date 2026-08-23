# call-agy

<p align="center">
  <strong>English</strong> | <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  A host-agnostic Agent Skill for <strong>task-level delegation to the official Google Antigravity CLI (<code>agy</code>)</strong> in headless mode.
</p>

<p align="center">
  <img src="assets/readme/workflow.svg" alt="call-agy Architecture and Task Delegation Flow" width="100%">
</p>

---

## Key Highlights

- **Explore New Models in Your Favorite Host Agent**: Call Antigravity directly from the AI coding agents you already use (e.g., Codex and DeepSeek Harness). Treat `agy` as a specialized sub-agent to test-drive and evaluate new models without switching tools or disrupting your primary workflow.
- **Zero-Cost Onboarding & Zero API Keys**: Get started in seconds. As long as you have completed a standard one-time interactive login via `agy`, the Skill is ready to use—no API keys or environment variables need to be provided to the Skill or host Agent.
- **Documented CLI Boundary**: Designed around task-level sub-agent delegation through the official headless CLI rather than a model-provider shim. It never extracts OAuth credentials or exposes an OpenAI-compatible proxy endpoint. The trusted-workspace preset is disclosed separately because it deliberately uses Antigravity's documented all-tool permission bypass.
- **Task-Level Agent Delegation**: The calling Agent remains the primary orchestrator while delegating bounded coding, exploration, debugging, review, or verification tasks to Antigravity.
- **Structured Markdown Handoffs**: Automatically streams and parses official `stream-json` events into a clean, noise-reduced Markdown handoff containing the exact assembled delegated prompt, final results, tool usage counts, token statistics, and session metadata.
- **Direct Long-Prompt Input**: Sends stream-json messages up to 60 KiB directly through stdin. Larger serialized prompts use a clearly reported temporary file that is grouped with the conversation handoff after the run.
- **Resumable Context**: Captures and reports deterministic `conversation_id`s, allowing natural multi-turn follow-ups without context drift.
- **Cross-Platform Ready**: Includes zero-dependency native launchers for POSIX (`call_agy.sh`), Windows PowerShell (`call_agy.ps1`), and universal Python (`call_agy.py`).
- **Reliable Trusted-Workspace Edits**: Authorized changes in a trusted workspace use `--mode accept-edits --sandbox --dangerously-skip-permissions` on the first call. This success-first preset auto-approves every Antigravity tool call; an explicit safe-mode request keeps permission checks active.

---

## How It Works

`call-agy` implements an Agent-to-Agent collaboration lifecycle:

1. **Host Orchestration & Model Selection**: The host AI Agent (e.g., Codex or DeepSeek Harness) formulates a bounded task or model-exploration run, setting parameters such as target model (`--model`), completion criteria, workspace boundaries, and optional priority files (`--file`).
2. **CLI Bridge**: `call_agy.py` normalizes paths and executes `agy --print= --input-format stream-json --output-format stream-json`, sending the assembled prompt through stdin.
3. **Autonomous Execution**: Antigravity executes inside the workspace using the selected permission posture. The success-first trusted preset auto-approves Antigravity tool calls; safe mode preserves configured permission checks.
4. **Structured Handoff**: `call-agy` captures the terminal state, extracts metadata (omitting noisy tool outputs), and groups compact reports by conversation under the system temporary directory.
5. **Host Verification**: The calling Agent reviews the handoff and inspects actual workspace diffs before accepting changes or continuing the session.

---

## Requirements

- **Antigravity CLI**: Official `agy` binary (version **1.1.15+** required for structured `stream-json` input and output; latest version recommended).
- **Authentication**: Authenticate once via an interactive `agy` session (`agy`). **No API keys or secrets required**.
- **Python**: Python 3.10+ available on PATH (or via `py`/`python3`).
- **Host Agent**: Any AI Agent environment capable of loading `SKILL.md` and running local processes, such as Codex or DeepSeek Harness.

> Official Antigravity headless docs: https://antigravity.google/docs/cli/headless/

---

## Installation & Setup

Place or link the `call-agy` repository into the Skill directory scanned by your Agent host:

Published-repository form, after a repository URL has been published and verified: `npx skills add <repository-url> --skill call-agy`. This local optimization did not publish or verify that path; the current installation is the local link described below.

### POSIX (macOS / Linux)

```bash
export AGENT_SKILLS_DIR="/path/to/your/agent/skills"
mkdir -p "$AGENT_SKILLS_DIR"
ln -s "/absolute/path/to/call-agy" "$AGENT_SKILLS_DIR/call-agy"
```

### Windows (PowerShell)

```powershell
$SkillsDir = "$env:USERPROFILE\.gemini\skills"
$Repo = "C:\path\to\call-agy"
New-Item -ItemType Directory -Force -Path $SkillsDir
New-Item -ItemType Junction -Path "$SkillsDir\call-agy" -Target $Repo
```

---

## Quick Start

### 1. Authenticate Antigravity (One-time, Zero API Key Setup)

```bash
agy
```

*Run `agy` once in a terminal to sign in. No API key is needed.*

### 2. Run a Delegated Task

#### macOS / Linux (POSIX Shell)

```bash
./scripts/call_agy.sh "Explain the main request flow in this repository" --workspace .
```

#### Windows (PowerShell)

```powershell
& ".\scripts\call_agy.ps1" --task "Explain the main request flow in this repository" --workspace .
```

Use the launcher directly in the current PowerShell process. A nested `powershell -Command` adds a second quoting layer and is unnecessary.

#### Universal (Python 3)

```bash
python3 scripts/call_agy.py "Explain the main request flow in this repository" --workspace .
```

For a long or multiline task, send the task through stdin:

```powershell
@'
Inspect this repository.
Explain the request flow and verify every claim against the code.
'@ | .\scripts\call_agy.ps1 --workspace .
```

The launcher passes this input to the wrapper, and the wrapper sends it to native `agy` through stream-json stdin. The safe limit is 60 KiB measured on the final UTF-8 NDJSON message. Above that limit, the wrapper stages the complete prompt as a uniquely named file under `%TEMP%/call-agy/.big-prompt/`, then moves it beside the conversation handoff and reports its final `prompt_file_path`. If a host cannot supply process stdin, it can place its own intermediate prompt in the system temporary directory and pipe that file into the launcher.

### 3. Execution Output

Upon completion, `call-agy` outputs compact metadata to stdout:

```text
conversation_id=<conversation-id>
output_path=<system-temp>/call-agy/<conversation-id>/<turn-id>-handoff.md
elapsed=14.2s
status=SUCCESS
```

If a fresh invocation fails before any model or tool work with the narrow opaque zero-token error, the wrapper retries the identical invocation once and also prints `attempts=2`. That automatic retry consumes the retry budget.

The calling Agent reads `output_path` and verifies any repository changes. Default handoffs from the same conversation share one temporary directory and use unique turn-prefixed filenames; `conversation_id` is the durable handle for resuming context. An explicit `--output` still overrides the destination.

The standard handoff contains the complete assembled prompt delegated to Antigravity, along with the result and run metadata.

---

## Resuming Conversations

To continue a previous Antigravity session with full context, pass the exact `conversation_id`:

```bash
./scripts/call_agy.sh \
  "Now add unit tests for the edge cases identified above" \
  --workspace . \
  --conversation "<conversation-id>"
```

> **Note**: While `-c, --continue` resumes the latest workspace session, `--conversation <id>` is strongly recommended for deterministic multi-session agent workflows.

---

## Typical Agent Prompt Pattern

**You can say / 你可以直接这样说:**

When prompting your host Agent to delegate to Antigravity (e.g. to test a new model or delegate a focused task):

```text
Use $call-agy to investigate this bug in the current repository with the latest model (--model <slug>).
Provide the failing test and relevant source files as priority paths (--file).
Let Antigravity implement and verify the fix as a sub-agent, then review its handoff and diff before reporting back.
```

---

## Command-Line Options

| Option | Description |
| :--- | :--- |
| `-w, --workspace <path>` | Wrapper-only: set the process working directory and automatically add it to native `agy` workspace access (default: current directory). Native `agy` has no `--workspace` flag. |
| `-f, --file <path>` | Wrapper-only: append a priority path hint to the prompt (repeatable). It grants no access; native `agy` has no `--file` flag. |
| `--add-dir <path>` | Grant Antigravity access to an external directory outside workspace (repeatable). |
| `--conversation <id>` | Resume a specific Antigravity conversation by ID. |
| `-c, --continue` | Resume the most recent conversation in the workspace. |
| `--model <slug>` | Explicit model override from `agy models` (defaults to CLI config). |
| `--effort <low\|medium\|high>` | Reasoning effort level. |
| `--agent <name>` | Custom or built-in agent persona from `agy agents`. |
| `--timeout <duration>` | Print timeout duration, e.g., `10m`, `30m` (default: `10m`). |
| `--mode <accept-edits\|plan>` | Execution mode; use `accept-edits` for authorized file modifications. |
| `--sandbox` | Enforce Antigravity terminal sandbox restrictions. |
| `--dangerously-skip-permissions` | **Dangerous native `agy` flag**: auto-approve all tool calls. The trusted-workspace mutation preset supplies it unless the user requests safe mode. |
| `-o, --output <path>` | Custom Markdown handoff output destination. |
| `--raw-output <path>` | Debugging: capture raw NDJSON stream (may contain sensitive tool details). |
| `--agy-binary <path>` | Custom `agy` executable path or binary name. |
| `--dry-run` | Print the command shape, serialized prompt size, and selected transport without executing or creating a fallback prompt file. |

---

## Permissions, CLI Boundary & Safety Model

- **Documented Sub-Agent Delegation**: `call-agy` invokes the official `agy` CLI in documented headless mode. It is **not** a model-provider shim, does not extract OAuth tokens, and does not expose proxy endpoints. This architecture does not by itself establish legal, organizational, or security-policy compliance.
- **Zero Credential Exposure**: The Skill never asks for, reads, or stores API keys or OAuth credentials. It relies entirely on the local user session managed by the official binary.
- **Scoped Permissions**: Headless `agy` operates under the user's local Antigravity permission policy.
- **Trusted-Workspace Mutation Preset**: When the user authorizes changes in a trusted workspace, the host workflow announces and uses `--mode accept-edits --sandbox --dangerously-skip-permissions` on the first call. The dangerous flag changes Antigravity to all-tool auto-approval, including writes and command execution; `--sandbox` constrains terminal execution but does not make arbitrary writes harmless.
- **Safe Mutation Override**: When the user explicitly requests safe or conservative permissions, the host uses `--mode accept-edits --sandbox` and keeps permission checks active.
- **Working Directory Instruction**: Every delegated prompt tells Antigravity to work directly in the absolute `--workspace` path and routes substantive file content through file editing tools instead of long inline shell commands.
- **External Dependencies**: Use `--add-dir <path>` when code references external repositories or shared libraries outside `--workspace`.
- **Sensitive Output Filtering**: Raw tool outputs and arguments are excluded from standard Markdown handoffs to prevent token leakage. Use `--raw-output` only for local debugging.

---

## Troubleshooting

If delegation fails (non-zero exit or non-`SUCCESS` status):

1. **Diagnose**: Inspect stderr and the Markdown error block.
   - `AGY_NOT_FOUND` -> The wrapper already checked `PATH` and the official default install location. If agy is installed elsewhere, retry with its absolute `--agy-binary` path; install only when no local executable exists.
   - `AUTH_REQUIRED` -> Run `agy` in a terminal to sign in, then retry.
   - `HOST_SANDBOX_BLOCKED` -> Request host-level access to `~/.gemini/antigravity-cli`, then retry the same task; changing Antigravity permission flags alone cannot grant that access.
   - `AGY_VERSION_UNSUPPORTED` means the installed CLI rejected required headless flags; run `agy update` or reinstall it.
   - No `output_path` and no classified onboarding error means the wrapper did not finish starting; correct the native shell command before diagnosing `agy`.
2. **Local Safe Repair**:
   - External file boundary -> Add the containing directory with `--add-dir`.
   - Shell permission soft-denial during an explicitly requested safe run -> Use a scoped Antigravity command rule or ask the user whether to change the permission posture.
   - Timeout -> Adjust `--timeout 20m`.
   - Stale session -> Re-run fresh without `--conversation`.
3. **Single Retry**: Attempt the task once with corrected flags.
4. **Escalate**: If user intervention is needed, show the classified corrective action without requesting credentials or making unverified assumptions.

The wrapper itself spends that single retry only for `ERROR` + empty response + zero token usage + no tool step. If it reports `attempts=2`, do not run a third attempt or change model/permissions to chase the same opaque error.

A native `SUCCESS` with an empty response, zero token usage, and no executed turn is reported as a wrapper failure. Its diagnostic includes the serialized prompt size and ends with `推测提示词可能超过约 60KB。`

---

## Non-Goals

`call-agy` is strictly designed for **bounded task delegation** and intentionally does **not**:

- ❌ Act as a model provider shim or expose OpenAI-compatible API proxy endpoints.
- ❌ Extract, store, or reuse Antigravity OAuth tokens or credentials.
- ❌ Call internal Google/Antigravity backend APIs directly.
- ❌ Route every host LLM chat turn through Antigravity.
- ❌ Change local Antigravity permission policies, sandboxes, or account controls without the user's explicit authorization.

---

## License & Notice

Licensed under the MIT License. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for details.
