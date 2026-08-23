# CLI Reference

Load this reference for setup, non-default flags, multiline or large prompts, session continuation, and handoff details.

## Requirements

- Official Antigravity CLI `1.1.15+`. The wrapper checks `PATH`, then the official default location (`%LOCALAPPDATA%/agy/bin/agy.exe` on Windows or `~/.local/bin/agy` on macOS/Linux); use `--agy-binary` for a custom location.
- One prior interactive `agy` login. Headless mode uses locally cached credentials; this Skill does not need an API key.
- Python 3.10+ for `scripts/call_agy.py`.
- A host Agent that can read `SKILL.md` and run a local process.

The wrapper calls the official structured interface:

```text
agy --print= --input-format stream-json --output-format stream-json
```

## Entry Points

- Windows: `scripts/call_agy.ps1`
- macOS/Linux: `scripts/call_agy.sh`
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

The wrapper measures the final UTF-8 stream-json message. Up to 60 KiB is sent directly by stdin. Above that limit it writes the complete task as a unique `<turn-id>-prompt.txt` file under `%TEMP%/call-agy/.big-prompt/`, exposes that shared pending directory to `agy`, then moves the file beside the handoff in `%TEMP%/call-agy/<conversation-id>/` and reports its final `prompt_file_path`.

## Workspace and Priority Paths

```bash
./scripts/call_agy.sh \
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
./scripts/call_agy.sh \
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
| `--timeout <duration>` | Print timeout such as `10m` or `90s`; wrapper default `10m`. |
| `--mode <accept-edits|plan>` | Antigravity execution mode. |
| `--sandbox` | Enable terminal sandbox restrictions. |
| `--dangerously-skip-permissions` | Auto-approve every Antigravity tool call; high risk. |
| `-o, --output <path>` | Markdown handoff destination. |
| `--raw-output <path>` | Optional sensitive raw NDJSON capture for debugging. |
| `--agy-binary <path-or-name>` | Override the `agy` executable. |
| `--dry-run` | Print command shape, serialized prompt size, and transport without running Antigravity. |

Task text may be the first positional argument, `--task`, or stdin. It is sent to native `agy` through stdin rather than placed on the native command line.

## Handoff

The standard Markdown handoff includes the complete assembled prompt delegated to Antigravity, the final response, conversation ID, terminal status, explicitly selected model/effort when available, requested agent override, compact tool counts, token usage when reported, and elapsed time. Unreported model, effort, and CLI version fields are omitted. If the wrapper recovered from its narrow automatic pre-model retry, it also records `attempts: 2` and the previous attempt's conversation ID.

Raw tool arguments and outputs are omitted by default. A standard stdout receipt contains `conversation_id`, `output_path`, `elapsed`, and `status`; it may additionally report `attempts=2`, `prompt_file_path`, or `raw_output_path`. Without `--output`, turns from the same conversation share `%TEMP%/call-agy/<conversation-id>/` and use unique `<turn-id>-handoff.md` filenames. Artifacts without a conversation ID remain directly under `%TEMP%/call-agy/`; `conversation_id`, not a temporary file, is the resume handle.
