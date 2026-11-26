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

Use the launcher native to the host OS. Paths in examples are relative to the Skill directory. For read-only repository analysis, pass both flags shown here.

```powershell
& ".\scripts\call_agy.ps1" --task "Review the request flow and cite relevant files" --workspace . --mode plan --sandbox
```

```sh
sh ./scripts/call_agy.sh --task "Review the request flow and cite relevant files" --workspace . --mode plan --sandbox
```

## Multiline and Large Tasks

Prefer stdin for long or multiline prompts:

```powershell
@'
Inspect this repository.
Explain the request flow and verify every claim against the code.
'@ | .\scripts\call_agy.ps1 --workspace .
```

Close stdin after sending the complete task. If the host cannot pipe stdin, create a prompt intermediate in the system temporary directory and report its path.

The wrapper measures the final UTF-8 stream-json message. Up to 60 KiB is sent directly by stdin. Above that limit it writes the complete task under the system temp directory in `call-agy/.big-prompt/<turn-id>/`, exposes the turn's staging directory to `agy`, then groups the prompt file with the conversation artifacts.

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
Concurrent explicit resumes of one ID fail early with `CONVERSATION_BUSY`. The OS releases the lock when the wrapper exits.

## Model, Effort, and Agent

Omit `--model`, `--effort`, and `--agent` by default. Only add them when the user explicitly requests a choice. Discover local values with:

```bash
agy models
agy agents
```

Never guess a slug or replace an unavailable pinned model without the user's direction.

To check the current model manually, run `agy -p "/model" --output-format json`.

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
| `--idle-grace <duration>` | Optionally terminate if silence continues for this long after the idle warning. Default is warning only. |
| `--wrapper-timeout <duration>` | Hard process watchdog; defaults to `--timeout` plus 30 seconds and must be greater than `--timeout`. |
| `--mode <accept-edits|plan>` | Antigravity execution mode. |
| `--sandbox` | Enable terminal sandbox restrictions. |
| `--dangerously-skip-permissions` | Auto-approve every Antigravity tool call; high risk. |
| `-o, --output <path>` | Markdown handoff destination. |
| `--raw-output <path>` | Optional sensitive raw NDJSON capture for debugging. |
| `--force` | Allow explicit `--output` or `--raw-output` paths to overwrite existing files. Without it, a unique suffix is selected and reported. |
| `--agy-binary <path-or-name>` | Override the `agy` executable. |
| `--dry-run` | Check `agy --version` and print command shape, prompt size, and transport. Skips the conversation lock, artifacts, and model turn. |

Task text may be the first positional argument, `--task`, or stdin. It is sent to native `agy` through stdin rather than placed on the native command line.

## Handoff

The standard Markdown handoff includes the complete assembled prompt delegated to Antigravity, the final or recovered partial response, effective conversation ID, native and wrapper status, process exit code, model and source, effort when available, requested agent override, compact tool counts, AGY-reported usage, and elapsed time. Usage and duration counters may be cumulative on resumed conversations; do not add cache reads to totals or infer billing. If the wrapper performs its narrow automatic pre-model retry, it records each attempt boundary and the previous conversation ID.

`model_source` describes the evidence for `model`. The wrapper uses the first available source below without running a separate model query.

| Source | Meaning |
|---|---|
| `stream` | Model metadata returned by AGY for this invocation. |
| `transcript` | Model AGY recorded for this turn's last completed response step. The wrapper reads at most 256 KiB from the end of the native conversation transcript. |
| `requested` | Explicit `--model` argument. |
| `settings` | Saved model preference from the existing preflight read, when no model, effort, or agent override was supplied. |
| `unavailable` | Model reported as `unknown`. |

After initial executable and output checks, the wrapper prints `receipt_path` for the interruption receipt. On finalization it prints `output_path`, `elapsed`, `status`, and `conversation_id` if available. Optional fields include `attempts=2`, `prompt_file_path`, `raw_output_path`, and `recovery_conversation_id`. Without `--output`, handoff files have unique turn names under the system temp directory in `call-agy/<conversation-id>/`, or directly in `call-agy/` when no conversation was created. Use the conversation ID for resuming.

Key progress goes to stderr and the receipt. While the process runs, a status line every minute reports elapsed time, the last valid stream event, and time without valid events. Partial-response progress includes the character count, timestamp, and last 20 characters. See [timeout coordination](permissions-and-recovery.md#timeout-coordination) for stream activity and process cleanup.

When explicit `--output` or `--raw-output` already exists, call-agy preserves it and selects a unique suffixed filename. Pass `--force` only when replacement is intentional. The two options must not resolve to the same file. Wrapper-created output files are independent of AGY `--mode plan`; plan governs AGY behavior, not the wrapper's own handoff writes.
