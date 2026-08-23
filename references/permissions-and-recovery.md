# Permissions and Recovery

Read this reference before any delegated mutation, external-directory access, permission posture change, or failure retry.

## Permission Postures

### Read-only or mutation not authorized

Do not pass the mutation preset. Use the user's configured Antigravity rules. A read-only prompt is an instruction, not a filesystem enforcement guarantee, so do not claim stronger isolation than the environment provides.

### Authorized trusted-workspace mutation: success-first preset

Use on the first call only when all are true:

- the user authorized file creation or modification
- the workspace and prompt are trusted
- the delegated task is bounded
- the host announces the posture before running

```text
--mode accept-edits --sandbox --dangerously-skip-permissions
```

This is deliberately success-first. `--mode accept-edits` auto-approves file edits; `--dangerously-skip-permissions` changes Antigravity to all-tool auto-approval, including commands, writes, and network-capable tools; `--sandbox` restricts terminal execution but does not neutralize arbitrary writes or every external effect.

Do not describe this posture as “not bypassing permissions.” The flag is a permission bypass, even though it is an official documented CLI option.

### User-requested safe mutation

When the user asks for safe mode, conservative permissions, or no bypass:

```text
--mode accept-edits --sandbox
```

Keep permission checks active. Prefer user-configured scoped `permissions.allow` rules for commands. If a required tool is soft-denied, report the needed scoped rule or ask before changing posture. Never add `--dangerously-skip-permissions` silently during recovery.

## Workspace Boundaries

- `--workspace` is the delegated working directory and is forwarded to native `--add-dir`.
- `--file` only tells Antigravity what to inspect.
- For an explicit target outside the workspace, add the containing directory with `--add-dir`.
- Treat additional directories as context unless the task explicitly targets an absolute path there.
- Inspect actual diffs after any mutation; Antigravity's handoff is not authoritative.

## Recovery

When the wrapper exits non-zero or reports a non-`SUCCESS` status:

1. Read stderr and any handoff error block.
2. Classify the failure before changing anything.
3. Apply one local, safe correction that preserves user intent.
4. Retry the same delegated task once.
5. If the corrected retry fails or needs new authority, stop and ask the user rather than completing the original task in the host Agent.

If the launcher prints no `output_path`, inspect its classified stderr first. Handle `AGY_NOT_FOUND`, `AUTH_REQUIRED`, or `AGY_VERSION_UNSUPPORTED` using the actions below; otherwise correct the native shell invocation before diagnosing `agy`. On Windows, use one PowerShell process and invoke `call_agy.ps1` directly with `&`.

The wrapper automatically performs its one retry only for the narrow opaque pre-model signature: `ERROR`, empty response, zero token usage, and no tool step. It preserves the task and all settings. When stdout or the handoff reports `attempts=2`, the retry budget is spent; do not launch a third attempt. Permission failures, tool-started failures, resumed conversations, and specific diagnostics are never auto-retried.

### On-demand onboarding failures

The wrapper reports these onboarding failures:

- `AGY_NOT_FOUND`: show the command for the current system, ask the user to open a new terminal, then retry.
  - Windows PowerShell: `irm https://antigravity.google/cli/install.ps1 | iex`
  - macOS/Linux: `curl -fsSL https://antigravity.google/cli/install.sh | bash`
- `AUTH_REQUIRED`: ask the user to run `agy` in a terminal to sign in, then retry.
- `AGY_VERSION_UNSUPPORTED`: run `agy update` or reinstall the current CLI; call-agy requires `agy 1.1.15+`.

| Failure | Safe correction |
|---|---|
| `AGY_NOT_FOUND` | Use the current-platform command above; if already installed, correct PATH or use `--agy-binary`. |
| `AGY_VERSION_UNSUPPORTED` | Run `agy update` or reinstall; structured stdin requires `1.1.15+`. |
| `AUTH_REQUIRED` | Run `agy` in a terminal to sign in, then retry. |
| External file inaccessible | Add the containing directory with `--add-dir`. |
| Unknown pinned model/agent | Inspect `agy models` / `agy agents`; do not substitute. |
| Stale conversation | Retry fresh without `--conversation`. |
| Bounded timeout | Increase `--timeout` proportionally once. |
| Safe-mode command soft-denied | Report a scoped allow rule or request permission; do not escalate silently. |
| No `output_path` and no classified onboarding error | Correct the OS-native command and quoting; this is not yet an `agy` failure. |
| Opaque zero-token error before tools | Let the wrapper retry once unchanged; do not switch model or permission posture. |
| Non-success terminal status | Preserve status and diagnostics; do not present the task as complete. |

A native `SUCCESS` with an empty response, zero usage, and no executed turn is converted to wrapper failure. Report the serialized prompt size; the diagnostic ends with `推测提示词可能超过约 60KB。`

## No Host Takeover on Delegation Failure

Recovery repairs the requested Antigravity delegation chain. It does not authorize the host Agent to silently perform the original task itself or switch to another Skill. If authentication, broader access, a permission change, or a second retry is required, explain the cause and wait for the user's decision.

## Sensitive Artifacts

- Standard handoffs include the exact assembled delegated prompt but omit raw tool arguments and output. Treat the handoff as potentially sensitive when the task prompt contains private data.
- Treat `--raw-output` files as potentially sensitive.
- Report any wrapper-created `prompt_file_path`; do not delete it without user authorization.
- Report host-created prompt intermediates when they exist.
