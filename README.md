# call-agy

<p align="center">
  <strong>English</strong> | <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  A host-agnostic Agent Skill for <strong>task-level delegation to the official Google Antigravity CLI (<code>agy</code>)</strong> in headless mode.
</p>

<p align="center">
  <img src="assets/readme/hero.svg" alt="call-agy — delegate bounded tasks to the official Antigravity CLI from your Agent chat" width="100%">
</p>

<p align="center">
  <a href="https://github.com/F1rstDan/call-agy/releases"><img src="https://img.shields.io/github/v/release/F1rstDan/call-agy?style=flat-square&color=blue" alt="GitHub Release"></a>
  <a href="https://github.com/F1rstDan/call-agy/blob/main/LICENSE"><img src="https://img.shields.io/github/license/F1rstDan/call-agy?style=flat-square" alt="License"></a>
  <a href="https://github.com/F1rstDan/call-agy/commits/main"><img src="https://img.shields.io/github/last-commit/F1rstDan/call-agy?style=flat-square" alt="Last Commit"></a>
</p>

---

## Key Highlights

- Use Antigravity as a second agent in your usual coding chat, such as Codex or DeepSeek Harness. You can hand it a specific task and keep working in the same conversation.
- **Zero API Keys & Zero Setup Hassle**: Get started instantly. Simply authenticate once via the official `agy` terminal CLI; no API keys, tokens, or secrets to configure or manage.
- **Outsource Complex Tasks with Structured Handoffs**: Delegate demanding code exploration, bug investigations, or test writing to Antigravity. Its Markdown handoff records the result and which model was used. For edits, the calling agent checks the actual changes and tests.
- **Transparent & Secure Sub-Agent Boundary**: Built strictly on the official headless CLI interface. Never extracts OAuth credentials, never exposes external proxy ports, and respects explicit security boundaries.

<p align="center">
  <img src="assets/readme/flow-eli5.svg" alt="How call-agy works in one prompt — host Agent forwards to official agy headless mode, agy executes, returns handoff" width="100%">
</p>

*Ask for an architecture walkthrough, an independent review, or a focused fix in the chat you already use. Your agent sends the task to `agy` and brings the result back.*

---

## Quick Start

### 1. Prerequisites (3 Simple Steps)

1. **Install Antigravity CLI**: Ensure the official `agy` command-line tool is installed.
2. **Authenticate (One-time)**: Run `agy` once in your terminal to complete interactive login (no API keys needed).
3. **Install this Skill**:
   ```bash
   npx skills add F1rstDan/call-agy
   ```

### 2. Use Directly in Your Host Agent

Once installed, simply prompt your host Agent with natural language to delegate tasks.

**You can say:**

> **Scenario 1: Deep Architecture & Flow Analysis**
> ```text
> Use $call-agy to analyze the core request lifecycle in this repository and summarize key architectural findings.
> ```
> *Sub-agent analyzes the codebase in read-only mode and returns a structured analysis report.*

> **Scenario 2: Root-Cause Debugging & Verified Fixes**
> ```text
> Use $call-agy to investigate and fix this error. Pass tests/test_api.py and relevant source files as priority paths, then run tests to verify the fix.
> ```
> *Antigravity investigates the failure, applies the fix, and reports its tests. Your agent checks the diff and the results.*

> **Scenario 3: Code Review & Optimization**
> ```text
> Have Antigravity review this module for concurrency safety and edge conditions, providing concrete optimization recommendations.
> ```
> *Leverages frontier reasoning capabilities for thorough, high-effort code reviews.*

> **Scenario 4: Multi-Turn Test Generation**
> ```text
> Based on the previous analysis, use $call-agy to write unit tests covering all boundary edge cases.
> ```
> *Seamlessly continues the session context across multiple turns without losing state.*

---

## Installation & Setup

### Option 1: `npx skills` (Recommended)

```bash
npx skills add F1rstDan/call-agy
```

Update:
```bash
npx skills update call-agy
```

### Option 2: `git clone`

<details>
<summary>Click to expand Git installation</summary>

```bash
# macOS / Linux
git clone https://github.com/F1rstDan/call-agy.git ~/.agents/skills/call-agy

# Windows (PowerShell)
git clone https://github.com/F1rstDan/call-agy.git "$env:USERPROFILE\.agents\skills\call-agy"
```

Update:
```bash
git -C ~/.agents/skills/call-agy pull
```

</details>

---

## Requirements

- **Antigravity CLI**: Official `agy` binary (v1.1.15+, latest recommended)
- **Authentication**: Logged in via interactive terminal `agy` once (no API keys)
- **Python**: Python 3.10+
- **Host Agent**: Any AI Agent supporting skills (Codex, DeepSeek Harness, etc.)

---

## Technical Reference & Internals

<p align="center">
  <img src="assets/readme/workflow.svg" alt="call-agy architecture and task delegation flow" width="100%">
</p>

For internal execution mechanics, CLI flags, security models, or troubleshooting, see:

👉 **[How It Works & Technical Reference (how_work.md)](how_work.md)**

---

## License & Notice

Licensed under the MIT License. See [LICENSE](LICENSE) and [NOTICE](NOTICE) for details.
