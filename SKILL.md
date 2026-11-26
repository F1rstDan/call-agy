---
name: call-agy
description: "Delegate a bounded task to the local Antigravity CLI and return a host-verified handoff. Use when the user or host policy explicitly asks Antigravity, agy, or $call-agy to perform a task. Do not use for informational questions or generic subagent requests."
---

# call-agy

The calling Agent owns the task. Use the official local CLI, verify material claims, and report the result. Respect any restriction on delegation.

## Dispatch

1. Define one goal and what counts as done. For a direct text reply or connectivity test, skip repository discovery and file hints. Otherwise gather the workspace, up to six useful priority paths, constraints, and expected verification. Keep broad exploration proportional to the goal.
2. Choose the execution posture. For planning or file analysis without edits, use `--mode plan --sandbox`. For edits or a permission change, consult [Permissions and recovery](references/permissions-and-recovery.md#permission-postures) and keep approvals scoped.
3. Run the launcher directly. Use `--task` for short input and stdin for multiline input. Keep the CLI's model, effort, and agent defaults unless the user pins a choice. For an explicitly authorized external file, pass its smallest suitable parent directory with `--add-dir` and the absolute file with `--file`. `--add-dir` exposes a directory to AGY, subject to host and AGY permissions.

## Launchers

Invoke the Windows launcher directly in the current PowerShell session.

```powershell
& "<skill-directory>\scripts\call_agy.ps1" --task "<task>" --workspace "<workspace>"
```

On macOS and Linux, use the shell launcher.

```sh
sh "<skill-directory>/scripts/call_agy.sh" --task "<task>" --workspace "<workspace>"
```

For ordinary calls, go from this file straight to the launcher. A connectivity test asks for a short reply without tools. Keep the default 2-hour total window unless the task needs an explicit deadline. See the [CLI reference](references/cli-reference.md) for long input, session continuation, or overrides.

Keep one process session alive while AGY runs. When the host supports asynchronous execution, wait on that same session with intervals of up to a minute and keep its timeout above the wrapper watchdog. If the run takes longer, relay the latest observed step or say AGY is still running and the stream is quiet. A quiet stream alone gives no evidence about model thinking or a stall. Follow the [timeout and recovery guidance](references/permissions-and-recovery.md#timeout-coordination) if the process ends early.

## Verify and recover

1. Read the handoff at `output_path` whenever present. If no handoff path is available, use stderr and any `receipt_path`. The receipt can preserve the last event, errors, and partial response after an interruption.
2. On `status=SUCCESS`, check the result against the request. For a direct reply, read the handoff once and return the requested text. For changed files or material claims, verify the relevant diff, tests, or evidence and report the outcome. The handoff already records the model and source. Treat artifacts as potentially sensitive.
3. On failure, use the partial evidence and follow [recovery](references/permissions-and-recovery.md#recovery). A reported `attempts=2` or receipt with `automatic_retry` set means the wrapper used its retry. Otherwise make at most one corrected launcher call when the evidence supports it. Keep the original task with Antigravity unless the user changes the delegation.

For follow-up work that needs prior context, use `--conversation <id>`.
