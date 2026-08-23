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

1. For a smoke test or direct text reply, skip repository discovery and file hints. Otherwise read enough local context to state the goal, completion criteria, constraints, expected verification, workspace, and up to six priority paths.
2. Choose the permission posture below. Announce the trusted-workspace preset before using it.
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
  --workspace "<absolute-workspace>" --mode plan --sandbox --timeout 90s
```

Read-only or analysis task:

```powershell
& ".\scripts\call_agy.ps1" --task "Review the request flow and cite relevant files" --workspace .
```

```bash
./scripts/call_agy.sh "Review the request flow and cite relevant files" --workspace .
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

For the narrow transient failure `ERROR` + empty response + zero token usage + no tool step, the wrapper repeats the same fresh invocation once. It reports `attempts=2`; this consumes the single retry budget, so the host must not launch a third attempt.

- Read the handoff only after a successful run.
- Treat the handoff as evidence to review, not as automatic truth or task completion.
- For edits, inspect the real workspace/diff and rerun relevant tests from the host.
- Resume only when prior Antigravity context helps, using `--conversation <id>` rather than workspace-global `--continue`.
- The standard handoff includes the complete assembled prompt delegated to Antigravity. List `prompt_file_path`, `raw_output_path`, or host-created prompt intermediates only when they exist.

## Progressive References

- Read [CLI reference](references/cli-reference.md) for setup, multiline/large prompts, model or session overrides, options, and handoff fields.
- Read [Permissions and recovery](references/permissions-and-recovery.md) before mutations, external-directory access, permission changes, or retrying a failed run.
