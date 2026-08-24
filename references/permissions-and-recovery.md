# Permissions and Recovery

Read this reference before any delegated mutation, external-directory access, permission posture change, or failure retry.

## Permission Postures

Keep four layers separate:

1. the host sandbox decides whether the `agy` process can write its own state
2. Antigravity permission rules decide whether tools such as `run_command` are auto-approved
3. `--mode` expresses planning versus editing intent
4. `--sandbox` contains terminal commands launched by Antigravity

Neither `--mode` nor Antigravity's `--sandbox` can grant the host process access to
`~/.gemini/antigravity-cli`.

### Read-only or mutation not authorized

Use `--mode plan` when the required outcome is a plan or read-only investigation. The
wrapper asks Antigravity to prefer native read-only file tools, but shell commands remain
subject to the user's configured rules. A read-only prompt is an instruction, not a
filesystem enforcement guarantee, so do not claim stronger isolation than the environment provides.

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

The wrapper prints `receipt_path` before launching Antigravity. Read that receipt when the
host kills the process before stdout contains a final `output_path`; it is updated atomically
with the conversation ID, last event, tool counts, diagnostics, and recovered response text.

If the launcher prints no `output_path`, inspect its classified stderr first. Handle `AGY_NOT_FOUND`, `AUTH_REQUIRED`, `HOST_SANDBOX_BLOCKED`, or `AGY_VERSION_UNSUPPORTED` using the actions below; otherwise correct the native shell invocation before diagnosing `agy`. On Windows, use one PowerShell process and invoke `call_agy.ps1` directly with `&`.

The wrapper automatically performs its one retry only for the narrow opaque pre-model signature: `ERROR`, empty response, zero token usage, and no tool step. It preserves the task and all settings. When stdout or the handoff reports `attempts=2`, the retry budget is spent; do not launch a third attempt. Permission failures, tool-started failures, resumed conversations, and specific diagnostics are never auto-retried.

### On-demand onboarding failures

The wrapper reports these onboarding failures:

- `AGY_NOT_FOUND`: the wrapper already checked `PATH` and the official default install location. A sandbox may hide a custom installation; if the user says agy is installed elsewhere, locate it and retry once with `--agy-binary <absolute-path>`. Install only when no local executable exists.
  - Windows PowerShell: `irm https://antigravity.google/cli/install.ps1 | iex`
  - macOS/Linux: `curl -fsSL https://antigravity.google/cli/install.sh | bash`
- `AUTH_REQUIRED`: ask the user to run `agy` in a terminal to sign in, then retry.
- `HOST_SANDBOX_BLOCKED`: agy was found and started, but the host sandbox denied its state directory under `~/.gemini/antigravity-cli`. Request host-level access to that directory and retry the same task. Antigravity's `--sandbox` and `--dangerously-skip-permissions` do not override the host sandbox.
- `HEADLESS_PERMISSION_BLOCKED`: a tool required an interactive decision that headless mode could not obtain. Add the narrow required `permissions.allow` rule or explicitly authorize the trusted-workspace preset.
- `AGY_VERSION_UNSUPPORTED`: run `agy update` or reinstall the current CLI; call-agy requires `agy 1.1.15+`.

| Failure | Safe correction |
|---|---|
| `AGY_NOT_FOUND` | Retry a custom installation by absolute `--agy-binary` path; use the install command only when no executable exists. |
| `AGY_VERSION_UNSUPPORTED` | Run `agy update` or reinstall; structured stdin requires `1.1.15+`. |
| `AUTH_REQUIRED` | Run `agy` in a terminal to sign in, then retry. |
| `HOST_SANDBOX_BLOCKED` | Request host-level access to `~/.gemini/antigravity-cli`, then retry the same task. |
| `HEADLESS_PERMISSION_BLOCKED` | Add the narrow required `permissions.allow` rule or request explicit authorization; do not silently escalate. |
| External file inaccessible | Add the containing directory with `--add-dir`. |
| Unknown pinned model/agent | Inspect `agy models` / `agy agents`; do not substitute. |
| Stale conversation | Retry fresh without `--conversation`. |
| Bounded timeout | Increase `--timeout` proportionally once. |
| Safe-mode command soft-denied | Report a scoped allow rule or request permission; do not escalate silently. |
| No `output_path` and no classified onboarding error | Correct the OS-native command and quoting; this is not yet an `agy` failure. |
| Opaque zero-token error before tools | Let the wrapper retry once unchanged; do not switch model or permission posture. |
| Non-success terminal status | Preserve status and diagnostics; do not present the task as complete. |
| `wrapper_status=TIMEOUT` | Read the partial receipt/handoff, then increase the bounded timeout once only if the task still merits it. |
| `wrapper_status=NO_TERMINAL_RESULT` | Use the recovered stream text and diagnostics; do not claim a completed response. |
| `wrapper_status=NO_FINAL_RESPONSE` | Treat the run as failed even if native AGY reported `SUCCESS`. |

Any native `SUCCESS` with an empty final response is converted to wrapper failure unless a
usable response can be reconstructed from stream-json `text_delta` events. A zero-usage,
zero-turn case additionally reports the serialized prompt size and possible large-input cause.

## Timeout Coordination

`--timeout` remains Antigravity's print timeout. The wrapper watchdog defaults to that value
plus 30 seconds, giving AGY time to emit its terminal result. `--wrapper-timeout` can override
the hard watchdog. The host process timeout must exceed the wrapper watchdog; `--dry-run`
prints a recommended host timeout. On expiry, the wrapper terminates AGY, preserves partial
stream text, and writes `wrapper_status=TIMEOUT`.

## No Host Takeover on Delegation Failure

Recovery repairs the requested Antigravity delegation chain. It does not authorize the host Agent to silently perform the original task itself or switch to another Skill. If authentication, broader access, a permission change, or a second retry is required, explain the cause and wait for the user's decision.

## Sensitive Artifacts

- Standard handoffs include the exact assembled delegated prompt but omit raw tool arguments and output. Treat the handoff as potentially sensitive when the task prompt contains private data.
- Treat `--raw-output` files as potentially sensitive.
- Report any wrapper-created `prompt_file_path`; do not delete it without user authorization.
- Report host-created prompt intermediates when they exist.
