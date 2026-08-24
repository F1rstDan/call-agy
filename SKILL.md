---
name: call-agy
description: "Delegate a bounded task to the locally authenticated Google Antigravity CLI (`agy`) and return a host-verified handoff. Use when the user explicitly asks to call Antigravity, `agy`, or `$call-agy` for coding, review, analysis, testing, or verification. Exclude informational questions and generic subagent requests."
---

# call-agy

Use the official local `agy` CLI as a bounded sub-agent. The calling Agent remains the task owner: it scopes the request, reviews the handoff and workspace, verifies material claims, and reports the combined result.

## Router Rules

- Trigger when the user explicitly asks to call `$call-agy`, `agy`, or Antigravity. Explicit invocation wins even when the task is trivial.
- Also trigger when an authorized host policy calls for a specifically Antigravity-backed implementation, debugging, review, test, analysis, or verification pass.
- Do not trigger for installation/docs/pricing questions, unsolicited trivial work, or generic “use a subagent” requests that do not identify Antigravity.
- Do not delegate when the host policy or user forbids cross-agent work.
- One invocation owns one focused goal with observable completion criteria. Split unrelated work before delegating.
- Keep the CLI default model, effort, and agent unless the user explicitly pins one. Never guess or silently substitute a slug.

## Compact Workflow

1. For a smoke test or direct text reply, skip repository discovery and file hints. Otherwise read enough local context to state the goal, completion criteria, constraints, expected verification, workspace, and up to six priority paths. For broad inspection, tell AGY to keep exploration proportional, avoid unbounded recursion, and deliver gathered evidence before time expires; do not impose fixed exclusions that could hide an explicit target.
2. Separate execution intent from authorization. Use `plan` only for planning/read-only work, `accept-edits` for authorized changes, and scoped permission rules for commands. Announce the trusted-workspace preset before using it.
3. Invoke the OS-native launcher once. Use `--task` for a short request, stdin for a multiline request, and `--file` for priority paths.
4. On `status=SUCCESS`, read the reported `output_path`, inspect any actual diff, and run host-side verification proportional to risk.
5. On failure, follow [Permissions and recovery](references/permissions-and-recovery.md): diagnose, make one safe local correction, and retry the same task at most once.
6. Return the substantive result, verification, limitations, and any known process-artifact paths.

## Fast Path

The canonical implementation is `scripts/call_agy.py`; use the native launcher from this Skill directory.

On Windows, stay in the current PowerShell process and invoke the launcher directly with `&`. Do not start a nested `powershell -Command`. Short tasks use `--task`; the wrapper stages large-prompt fallbacks under system temp and groups them by conversation after the run.

Explicit connectivity smoke test (no repository discovery):

```powershell
& "<skill-directory>\scripts\call_agy.ps1" `
  --task "Reply exactly: Antigravity is connected. Do not inspect files or run tools." `
  --workspace "<absolute-workspace>" --timeout 90s
```

Read-only or planning task that needs repository tools:

```powershell
& ".\scripts\call_agy.ps1" --task "Review the request flow and cite relevant files" `
  --workspace . --mode plan --sandbox
```

`plan` is an execution intent, not a host permission grant. In headless mode, shell commands
still need scoped `permissions.allow` rules. The wrapper prefers native read-only file tools
for plan tasks and reports `HEADLESS_PERMISSION_BLOCKED` rather than silently escalating.

```bash
sh ./scripts/call_agy.sh "Review the request flow and cite relevant files" --workspace .
```

Authorized edits in a trusted workspace use the success-first preset selected by this Skill:

```powershell
.\scripts\call_agy.ps1 "Fix the bug and run the focused tests" `
  --workspace . --file "src/example.ts" --file "tests/example.test.ts" `
  --mode accept-edits --sandbox --dangerously-skip-permissions
```

Before that preset, tell the user that `--dangerously-skip-permissions` changes Antigravity to all-tool auto-approval. `--sandbox` constrains terminal execution; it does not make arbitrary file changes harmless. Use the preset only for user-authorized changes in a trusted workspace and prompt.

If the user requests safe, conservative, or no-bypass behavior, use:

```powershell
.\scripts\call_agy.ps1 "Fix the bug and report any blocked command" `
  --workspace . --mode accept-edits --sandbox
```

Preserve that safe posture during recovery. Never add the dangerous flag silently.

Before model invocation, the wrapper verifies the AGY version and reversibly probes the state
root plus existing runtime subdirectories under `~/.gemini/antigravity-cli`.
If the host sandbox blocks that state directory, it emits a failure handoff without consuming
model tokens. It also prints `receipt_path` before launching AGY; this incrementally updated
artifact preserves the conversation ID, last event, completed tool counts, diagnostics, and
streamed response text if the wrapper or host interrupts the run.

## Delegation Contract

Give Antigravity:

1. one concrete goal and what counts as done
2. relevant constraints and explicit non-goals
3. verification commands or evidence expected
4. the absolute workspace plus only useful `--file` hints
5. the requested final handoff: changes/findings, verification, and unresolved limits

`--file` is a priority hint, not an access grant. If an explicit target is outside the workspace, pass its containing directory with `--add-dir`. For long or multiline tasks, pipe stdin and close it after the complete request.

## Result and Verification

A successful wrapper run reports at least:

```text
conversation_id=<id>
output_path=<absolute-markdown-path>
elapsed=<seconds>s
status=SUCCESS
```

Every non-dry run prints `receipt_path=<absolute-markdown-path>` before starting AGY. The wrapper
waits on AGY's stream rather than polling: valid `init` and `step_update` events renew its activity
deadline. Defaults are a 2-hour AGY total ceiling, an idle warning after 10 minutes, and termination
after another 5 silent minutes. The host Agent may override these before launch with `--timeout`,
`--idle-timeout`, and `--idle-grace`. Keep the host process timeout above the wrapper watchdog.

Progress uses compact stderr lines plus the receipt: tool state changes and, at most once per minute,
partial-response character count, timestamp, and a 20-character tail. Treat these as observation for
host judgment, not automatic proof of a text loop. A terminal `result` completes the turn; AGY then
gets 5 seconds to exit before process-tree cleanup.

For the narrow transient failure `ERROR` + empty response + zero token usage + no tool step, the wrapper repeats the same fresh invocation once. It reports `attempts=2`; this consumes the single retry budget, so the host must not launch a third attempt.

- Read the handoff only after a successful run.
- An empty final response is never treated as success. Streamed `agent_response.text_delta`
  text is recovered when available; otherwise the handoff says that no final response was received.
- On timeout or an incomplete terminal result with a conversation ID, read the handoff's
  suggested recovery prompt. The wrapper never launches that extra turn automatically.
- Treat the handoff as evidence to review, not as automatic truth or task completion.
- For edits, inspect the real workspace/diff and rerun relevant tests from the host.
- Resume only when prior Antigravity context helps, using `--conversation <id>` rather than workspace-global `--continue`.
- The standard handoff includes the complete assembled prompt delegated to Antigravity. List `prompt_file_path`, `raw_output_path`, or host-created prompt intermediates only when they exist.
- Treat `--raw-output` and every reported artifact path as potentially sensitive.

## Progressive References

- Read [CLI reference](references/cli-reference.md) for setup, multiline/large prompts, model or session overrides, options, and handoff fields.
- Read [Permissions and recovery](references/permissions-and-recovery.md) before mutations, external-directory access, permission changes, or retrying a failed run.
