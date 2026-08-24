# CLI Reference

Load this reference for setup, non-default flags, multiline or large prompts, session continuation, and handoff details.

## Requirements

- Official Antigravity CLI `1.1.15+`. The wrapper checks `PATH`, then the official default location (`%LOCALAPPDATA%/agy/bin/agy.exe` on Windows or `~/.local/bin/agy` on macOS/Linux), and runs `agy --version` before a model turn; use `--agy-binary` for a custom location.
- One prior interactive `agy` login. Headless mode uses locally cached credentials; this Skill does not need an API key.
- Host-level write access to `~/.gemini/antigravity-cli`; the wrapper probes this before model invocation because AGY needs it for settings, cache, conversations, and runtime artifacts.
- Python 3.10+ for `scripts/call_agy.py`.
- A host Agent that can read `SKILL.md` and run a local process.

The wrapper calls the official structured interface:

```text
agy --print= --input-format stream-json --output-format stream-json
```

## Entry Points

- Windows: `scripts/call_agy.ps1` (selects Python 3.10+ and uses UTF-8 for native stdin/stdout)
- macOS/Linux: `sh scripts/call_agy.sh` (works even when the installed file lacks an executable mode bit)
- Portable: `python3 scripts/call_agy.py`

Use the launcher native to the host OS. Paths in examples are relative to the Skill directory.

## Multiline and Large Tasks

Prefer stdin for long or multiline prompts:

```powershell
@'
Inspect this repository.
Explain the request flow and verify every claim against the code.
'@ | .\scripts\call_agy.ps1 --workspace .
```

Close stdin after sending the complete task. If the host cannot pipe stdin, create a prompt intermediate in the system temporary directory and report its path.

The wrapper measures the final UTF-8 stream-json message. Up to 60 KiB is sent directly by stdin. Above that limit it writes the complete task inside a private `%TEMP%/call-agy/.big-prompt/<turn-id>/` directory, exposes only that turn directory to `agy`, then moves the file beside the handoff in `%TEMP%/call-agy/<conversation-id>/` and removes the empty staging directory.

## Workspace and Priority Paths

```bash
sh ./scripts/call_agy.sh \
  "Fix the reported bug, run the relevant tests, and summarize the change" \
  --workspace "/path/to/repo" \
  --file "src/server.ts" \
  --file "tests/server.test.ts"
```

- `--workspace` sets the process working directory and forwards it through native `--add-dir`.
- `--file` appends a priority hint to the prompt; repeat up to six useful paths. It grants no access.
- `--add-dir` exposes an additional directory to Antigravity. Use it when an explicit target is outside the workspace.

## Sessions

Resume the exact prior conversation only when its context helps:

```bash
sh ./scripts/call_agy.sh \
  "Now add the missing regression test" \
  --workspace "/path/to/repo" \
  --conversation "<conversation-id>"
```

Prefer `--conversation <id>` over `--continue`; the latter depends on the most recent workspace conversation and can select the wrong thread.

## Model, Effort, and Agent

Omit `--model`, `--effort`, and `--agent` by default. Only add them when the user explicitly requests a choice. Discover local values with:

```bash
agy models
agy agents
```

Never guess a slug or replace an unavailable pinned model without the user's direction.

## Options

| Option | Meaning |
|---|---|
| `-w, --workspace <path>` | Wrapper working directory and primary accessible directory; default is current directory. |
| `-f, --file <path>` | Priority prompt hint; repeatable and not an access grant. |
| `--add-dir <path>` | Additional accessible directory; repeatable. |
| `--conversation <id>` | Resume an exact conversation. |
| `-c, --continue` | Resume the most recent conversation for the workspace. |
| `--model <slug>` | Explicit model from `agy models`. |
| `--effort <low|medium|high>` | Explicit reasoning effort. |
| `--agent <name>` | Explicit agent from `agy agents`. |
| `--timeout <duration>` | Native AGY total print-timeout ceiling; default `2h`. |
| `--idle-timeout <duration>` | Warn after this long without a valid `init` or `step_update`; default `10m`. |
| `--idle-grace <duration>` | Terminate if valid stream activity does not resume during this grace; default `5m`. |
| `--wrapper-timeout <duration>` | Hard process watchdog; defaults to `--timeout` plus 30 seconds and must be greater than `--timeout`. |
| `--mode <accept-edits|plan>` | Antigravity execution mode. |
| `--sandbox` | Enable terminal sandbox restrictions. |
| `--dangerously-skip-permissions` | Auto-approve every Antigravity tool call; high risk. |
| `-o, --output <path>` | Markdown handoff destination. |
| `--raw-output <path>` | Optional sensitive raw NDJSON capture for debugging. |
| `--force` | Allow explicit `--output` or `--raw-output` paths to overwrite existing files. Without it, a unique suffix is selected and reported. |
| `--agy-binary <path-or-name>` | Override the `agy` executable. |
| `--dry-run` | Print command shape, serialized prompt size, and transport without running Antigravity. |

Task text may be the first positional argument, `--task`, or stdin. It is sent to native `agy` through stdin rather than placed on the native command line.

## Handoff

The standard Markdown handoff includes the complete assembled prompt delegated to Antigravity, the final or recovered partial response, effective conversation ID, native and wrapper status, process exit code, explicitly selected model/effort when available, requested agent override, compact tool counts, AGY-reported usage, and elapsed time. Usage and duration counters may be cumulative on resumed conversations; do not add cache reads to totals or infer billing. If the wrapper performs its narrow automatic pre-model retry, it records each attempt boundary and the previous conversation ID.

Every non-dry run first prints `receipt_path` for a best-effort atomically updated crash/interruption receipt, then prints the final `conversation_id`, `output_path`, `elapsed`, and `status` when finalization completes. It may additionally report `attempts=2`, `prompt_file_path`, `raw_output_path`, or `recovery_conversation_id`. A timeout/incomplete result includes a suggested resume prompt but never starts that extra model turn automatically. Without `--output`, turns from the same conversation share `%TEMP%/call-agy/<conversation-id>/` and use unique `<turn-id>-handoff.md` filenames. Artifacts without a conversation ID remain directly under `%TEMP%/call-agy/`; `conversation_id`, not a temporary file, is the resume handle.

The wrapper consumes AGY events continuously. Valid `init` and `step_update` events renew the activity deadline; stderr and malformed output do not. Key progress is emitted as compact stderr lines and persisted in the receipt. Partial-response progress is limited to once per 60 seconds and contains the cumulative character count, timestamp, and a 20-character single-line tail. This is observation for host-Agent judgment, not automatic repetition detection. A valid terminal `result` ends stream consumption; AGY receives 5 seconds to exit before bounded process-tree cleanup.

When explicit `--output` or `--raw-output` already exists, call-agy preserves it and selects a unique suffixed filename. Pass `--force` only when replacement is intentional. The two options must not resolve to the same file. Wrapper-created output files are independent of AGY `--mode plan`; plan governs AGY behavior, not the wrapper's own handoff writes.
