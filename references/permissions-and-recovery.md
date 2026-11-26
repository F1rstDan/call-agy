# Permissions and Recovery

Read this reference before delegated edits, external directory access, permission changes, or failure recovery.

## Permission postures

The host sandbox controls access to the AGY state directory. AGY uses `--sandbox` to contain terminal execution, `--mode` to express task intent, and tool rules or explicit overrides to decide approval.

### Read-only work

Use `--mode plan --sandbox` for planning and read-only repository analysis. The wrapper asks AGY to prefer native read-only file tools. Headless shell commands still need scoped authorization. See the [launcher examples](cli-reference.md#entry-points).

### Authorized edits

For a bounded task with authorized edits and a trusted workspace and prompt, announce all-tool auto-approval before using the following first-call preset.

```text
--mode accept-edits --sandbox --dangerously-skip-permissions
```

This preset bypasses AGY approval for commands, writes, and network-capable tools. The terminal sandbox constrains terminal execution only.

For safe or no-bypass requests, use `--mode accept-edits --sandbox` and keep command rules scoped. Preserve the permission posture during recovery. Request authorization before changing it.

### Workspace boundaries

`--workspace` sets the working directory and forwards it to AGY. `--file` supplies priority hints. For an explicitly authorized external file, pass the smallest parent directory you can expose with `--add-dir` and the exact file path with `--file`. Keep `--mode plan --sandbox` for read-only work and request native file tools. AGY receives the directory as an additional workspace root, so choose its scope carefully.

In headless mode, actions that need an interactive approval may be denied. When a supported file tool still lacks access, the host can stage just the authorized read-only input in the workspace if it can read it and the user permits sharing it with AGY. To edit the original or use a command that needs approval, use a narrowly scoped AGY permission rule with the user's authorization. The host sandbox and any deny rules still apply. If access remains unavailable, report the specific path and operation that were blocked.

## Recovery

1. Read stderr and the handoff at `output_path`. If no handoff path appears, read any `receipt_path` for the last event, diagnostics, tool counts, and partial text.
2. Use the failure evidence below to pick a correction. Preflight counts describe configured permission rules. Deny and ask rules take precedence over allow rules.
3. Stop if stdout reports `attempts=2` or the receipt records `automatic_retry: true`. Otherwise make at most one corrected launcher call for the same task when authorized and the failure evidence supports a correction. Stop if it fails. Ask the user before changing authority, switching delegation, or making another attempt.

Each fresh launcher call can repeat its native invocation once after the opaque `Agent execution terminated due to error.` result with zero reported usage, no response, and no tool step. It repeats the same task and settings. Resumed conversations and classified failures such as permission denials do not qualify.

| Failure | Safe correction |
|---|---|
| `AGY_NOT_FOUND` | The wrapper checked `PATH` and the default install location. Use an absolute `--agy-binary` path for a custom installation. If missing, use the [official installer](#official-install-commands) after authorization. |
| `AGY_VERSION_UNSUPPORTED` | Run `agy update` or reinstall after authorization. Structured stdin requires `1.1.15+`. |
| `AUTH_REQUIRED` | Ask the user to sign in by running `agy` in a terminal. |
| `HOST_SANDBOX_BLOCKED` or `AGY_STATE_UNAVAILABLE` | Check the state directory under `~/.gemini/antigravity-cli`. Request the required host-level access if writes are blocked. |
| `HEADLESS_PERMISSION_BLOCKED` or a soft-denied command | Use the narrow required `permissions.allow` rule after authorization. |
| `CONVERSATION_BUSY` | Let the active wrapper finish before explicitly resuming the same ID. |
| External file inaccessible | Check the [workspace boundaries](#workspace-boundaries) and preserve the approved scope. |
| Unknown pinned model or agent | Inspect `agy models` or `agy agents` and preserve the requested choice. |
| Stale conversation | Start fresh only if the task can proceed without the previous context. |
| `TIMEOUT` or `IDLE_TIMEOUT` | Review the last progress and any partial result. Choose a proportional timeout adjustment or use the suggested resume prompt when the existing context helps. |
| `NO_TERMINAL_RESULT`, `NO_FINAL_RESPONSE`, or `PROCESS_EXIT_MISMATCH` | Use any recovered text and error details as partial evidence. An empty result is a wrapper error unless the stream supplied usable response text. |
| No artifact path and no classified error | Check launcher invocation and quoting. In PowerShell, invoke the launcher directly with `&`. |

### Official install commands

- Windows PowerShell uses `irm https://antigravity.google/cli/install.ps1 | iex`.
- macOS and Linux use `curl -fsSL https://antigravity.google/cli/install.sh | bash`.

## Timeout coordination

`--timeout` sets AGY's total print timeout and defaults to 2 hours. Keep this window for tasks with unknown duration. Valid `init` and `step_update` payloads renew the idle timer. The wrapper reports liveness every minute and warns after `--idle-timeout` (default 10 minutes). Silence stops AGY only if the caller explicitly supplied an `--idle-grace` and that grace expires.

The latest event describes what AGY exposed so far. An active tool is evidence that AGY launched it. A quiet stream can occur while the model is working or while the CLI is waiting. Relay the last observed step and wait on the same process while it is alive.

The wrapper's hard watchdog defaults to `--timeout` plus 30 seconds. Keep an explicit `--wrapper-timeout` above `--timeout` and the host timeout above both. After a terminal `result`, the wrapper gives AGY 5 seconds to exit before cleanup and preserves any usable result.

A timeout or incomplete turn can leave partial evidence and a suggested prompt for an explicit resume. The wrapper leaves that decision to the host.

## Sensitive artifacts

The standard handoff includes the exact delegated prompt. Raw NDJSON can include tool arguments and outputs. Treat both as sensitive. Report wrapper-created `prompt_file_path` and any host-created intermediates that exist. Preserve them unless the user authorizes deletion.
