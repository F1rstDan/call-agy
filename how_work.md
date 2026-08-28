# call-agy 工作原理与技术参考

本文档面向希望深入了解 `call-agy` 实现原理、命令行底层参数、安全边界与故障排查机制的开发者与技术读者。

---

## 目录

- [1. 工作原理与协作闭环](#1-工作原理与协作闭环)
  - [1.1 架构与委托流程](#11-架构与委托流程)
  - [1.2 跨平台启动脚本](#12-跨平台启动脚本)
  - [1.3 长提示词与 stdin 传输机制](#13-长提示词与-stdin-传输机制)
  - [1.4 标准执行输出与回执系统](#14-标准执行输出与回执系统)
- [2. 多轮会话接续](#2-多轮会话接续)
- [3. CLI 常用参数一览](#3-cli-常用参数一览)
- [4. 权限、CLI 边界与安全模型](#4-权限cli-边界与安全模型)
- [5. 故障排查 (Troubleshooting)](#5-故障排查-troubleshooting)
- [6. 非设计目标 (Non-Goals)](#6-非设计目标-non-goals)

---

## 1. 工作原理与协作闭环

### 1.1 架构与委托流程

`call-agy` 实现了严谨的 Agent 间协作闭环：

<p align="center">
  <img src="assets/readme/workflow-zh-cn.svg" alt="call-agy 架构与任务委托流程" width="100%">
</p>

整个执行流程包含五个阶段：

1. **宿主编排 (Host Orchestration)**：调度 AI Agent 明确任务目标、模型选择（`--model`）、验收标准与工作区边界，并提供可选的优先级参考文件（`--file`）。
2. **协议桥接 (Protocol Bridge)**：`call_agy.py` 标准化路径参数并调用 `agy --print= --input-format stream-json --output-format stream-json`，通过标准输入流式传递组装提示词。
3. **授权执行 (Authorized Execution)**：Antigravity 在指定工作区内运行。`--mode plan` 表达只读意图，`--mode accept-edits` 允许已授权的编辑；无头命令执行另由 Antigravity 权限规则或显式自动批准参数控制。
4. **结构化交付 (Structured Handoff)**：`call-agy` 捕获终端结果与执行指标，过滤冗余日志，生成紧凑的 Markdown 移交报告并保存至系统临时目录。
5. **宿主审查 (Host Verification)**：调度 Agent 读取移交报告并审查工作区实际代码 Diff，验证无误后向用户汇报或继续后续多轮任务。

### 1.2 跨平台启动脚本

`call-agy` 提供了针对不同操作系统的原生启动入口，无需安装第三方 Python 包依赖：

#### POSIX Shell (macOS / Linux)
```bash
sh ./scripts/call_agy.sh "分析当前仓库的核心请求处理流程并进行总结" --workspace .
```

#### Windows PowerShell
```powershell
& ".\scripts\call_agy.ps1" --task "分析当前仓库的核心请求处理流程并进行总结" --workspace .
```
> **注意**：请在当前 PowerShell 进程中直接调用启动脚本；嵌套 `powershell -Command` 会增加不必要的引号解析层级。

#### 跨平台 Python 3
```bash
python3 scripts/call_agy.py "分析当前仓库的核心请求处理流程并进行总结" --workspace .
```

### 1.3 长提示词与 stdin 传输机制

对于复杂任务或长篇规范，推荐通过 `stdin` 管道传入提示词：

```powershell
@'
分析当前仓库。
说明核心请求流程，并根据代码逐项验证结论。
'@ | .\scripts\call_agy.ps1 --workspace .
```

**传输机制说明**：
- **60 KiB 以内**：提示词通过内存流直通 `stdin` 传递给 `agy` 进程。
- **超过 60 KiB**：超长提示词会自动安全暂存至 `%TEMP%/call-agy/.big-prompt/` 下每轮独立的私有目录，仅向本轮 Antigravity 暴露，并在运行结束后与最终移交报告归组，避免跨进程命令行参数长度限制与泄漏风险。

### 1.4 标准执行输出与回执系统

任务完成后，`call-agy` 会向标准输出打印紧凑的键值元数据：

```text
receipt_path=<system-temp>/call-agy/.staging/<turn-id>-receipt.md
conversation_id=<conversation-id>
output_path=<system-temp>/call-agy/<conversation-id>/<turn-id>-handoff.md
elapsed=14.2s
status=SUCCESS
```

**回执与移交设计**：
- `receipt_path` 在启动 `agy` 之前即行输出。即使宿主在最终交付生成前异常中断或杀死了 Wrapper，该暂存回执仍可用于恢复中断前的最后事件、部分响应文本与诊断信息，避免任务失败时退化为误导性的“空成功”。
- 调度 Agent 优先读取 `output_path`；若其缺失则回退读取 `receipt_path`，并在接纳修改前审查实际工作区代码变更。
- `conversation_id` 是后续接续会话的全局确定性唯一标识。

---

## 2. 多轮会话接续

若后续任务依赖上一轮的上下文（例如基于分析结果继续编写测试或重构代码），可传入返回的 `conversation_id` 进行无缝恢复：

```bash
sh ./scripts/call_agy.sh \
  "基于上面的分析结果，为边界异常场景补充单元测试" \
  --workspace . \
  --conversation "<conversation-id>"
```

> **最佳实践提示**：虽然 `-c, --continue` 可以快速恢复工作区最近一次活跃会话，但在多任务并发或严谨的 Agent 编排场景下，强烈建议使用 `--conversation <id>`，以确保调度的上下文确定性与可重复性。

---

## 3. CLI 常用参数一览

| 参数 | 说明 |
| :--- | :--- |
| `-w, --workspace <path>` | **Wrapper 专用**：设置进程工作目录，并自动授权原生 `agy` 访问（默认：当前目录）。 |
| `-f, --file <path>` | **Wrapper 专用**：向提示词追加优先级文件路径提示（可重复指定）。 |
| `--add-dir <path>` | 额外向 Antigravity 授予工作区外部目录的访问权限（可重复指定）。 |
| `--conversation <id>` | 恢复指定的 Antigravity 会话 ID。 |
| `-c, --continue` | 恢复工作区内最近一次活跃的 Antigravity 会话。 |
| `--model <slug>` | 显式指定模型 Slug（可从 `agy models` 中获取；默认使用 CLI 全局配置）。 |
| `--effort <low\|medium\|high>` | 设定模型推理思考强度。 |
| `--agent <name>` | 指定内置或自定义 Agent 身份（可从 `agy agents` 中获取）。 |
| `--timeout <duration>` | AGY 原生 `--print-timeout` 总上限（默认：`2h`）。 |
| `--idle-timeout <duration>` | 连续未收到有效 `init`/`step_update` 事件多久后告警（默认：`10m`）。 |
| `--idle-grace <duration>` | 告警后继续静默多久才终止（默认：`5m`）。 |
| `--wrapper-timeout <duration>` | Wrapper 硬看门狗；默认比 `--timeout` 多 30 秒，必须大于 `--timeout`，并应小于宿主超时。 |
| `--mode <accept-edits\|plan>` | 执行模式；在明确授权修改代码时请使用 `accept-edits`。 |
| `--sandbox` | 启用 Antigravity 终端沙箱限制。 |
| `--dangerously-skip-permissions` | **原生 `agy` 高危参数**：自动批准全部工具调用。受信任工作区修改预设默认使用。 |
| `-o, --output <path>` | 自定义 Markdown 移交报告保存路径。 |
| `--raw-output <path>` | 调试专用：捕获原始 NDJSON 事件流。 |
| `--force` | 允许显式输出路径覆盖已有文件；否则自动选择并报告带唯一后缀的新文件名。 |
| `--agy-binary <path>` | 覆盖 `agy` 可执行文件路径或名称。 |
| `--dry-run` | 仅打印命令结构、序列化提示词大小与选定传输方式，不执行任务。 |

---

## 4. 权限、CLI 边界与安全模型

- **基于官方文档的子 Agent 委托**：`call-agy` 仅通过官方无头模式调用本地 `agy` CLI。它不是模型代理（Shim），不提取 OAuth 访问凭据，也不提供任何未经认证的外部网络端点。
- **零凭据暴露与安全隔离**：Skill 本身无需也绝不读取、存储任何 API 密钥或 OAuth 凭据，完全依赖官方 CLI 管理的本地用户会话。
- **四层权限相互独立**：宿主文件系统策略、Antigravity 终端沙箱、mode 意图和 Antigravity 工具权限是四个独立控制层；放开其中一层不会覆盖其他层。
- **状态目录预检**：在消耗模型 Token 前，Wrapper 会在 `~/.gemini/antigravity-cli` 执行可逆写入探针；当持久化不可用时直接以 `HOST_SANDBOX_BLOCKED` 或 `AGY_STATE_UNAVAILABLE` 显式失败，避免无效计费。
- **受信任工作区修改预设**：用户授权在受信任工作区修改代码时，宿主流程首调使用 `--mode accept-edits --sandbox --dangerously-skip-permissions` 自动批准工具调用；`--sandbox` 会约束终端执行，但不能让任意写入变得无害。
- **只读规划**：分析任务使用 `--mode plan --sandbox`。无头运行中的 shell 命令仍可能需要细粒度规则；提示词会优先要求使用原生只读文件工具，以减少无谓的权限拒绝。
- **显式工作目录指令**：每次委托都会要求 Antigravity 直接在 `--workspace` 绝对路径下工作，并使用原生文件编辑工具写入内容，避免 Windows 下的长内联 shell 命令。
- **外部目录边界隔离**：`--file` 仅作为优先级提示，无法越权访问工作区外的文件；如需访问外部模块，必须显式声明 `--add-dir <path>`。

---

## 5. 故障排查 (Troubleshooting)

当委托执行失败（非零退出码或非 `SUCCESS` 状态）时，宿主 Agent 与使用者可按以下步骤排查与恢复：

### 1. 诊断原因

读取 stderr 与 Markdown 移交报告中的 Error 诊断区块：

- **`AGY_NOT_FOUND`** ➔ 检查 PATH 环境变量，或通过 `--agy-binary <path>` 指定 CLI 绝对路径。
- **`AUTH_REQUIRED`** ➔ 在终端运行 `agy` 完成交互式登录，然后重试。
- **`HOST_SANDBOX_BLOCKED`** ➔ 请求宿主层开放 `~/.gemini/antigravity-cli` 的文件读写权限。
- **`HEADLESS_PERMISSION_BLOCKED`** ➔ 添加范围最小的 Antigravity allow 规则；仅在用户已授权时使用显式自动批准。
- **`AGY_VERSION_UNSUPPORTED`** ➔ 当前 CLI 不支持所需的无头参数；运行 `agy update` 或重新安装最新版本。
- **未生成 `output_path`** ➔ 读取已输出的 `receipt_path`，其中保留中断前的最后事件、部分响应与诊断信息。

### 2. 安全恢复

- **外部文件边界** ➔ 使用 `--add-dir <path>` 补充目标所在的外部目录。
- **安全模式下 shell 权限软拒绝** ➔ 配置细粒度 Antigravity 命令规则，或与用户确认是否调整权限。
- **静默告警** ➔ 观察 stderr 单行进度或本地回执；任何有效流事件都会重新计时，宽限期后仍静默则以 `IDLE_TIMEOUT` 保留部分证据并终止进程树。
- **总超时中断** ➔ 先读取部分交付与同会话恢复建议；只有额外一轮 Token 成本值得时才显式续接。`--wrapper-timeout` 必须大于 `--timeout`，宿主超时再高于两者。
- **会话失效** ➔ 移除 `--conversation` 参数重新开启新会话。

### 3. 单次重试机制

针对由于偶发性空错误导致的零 Token 失败，Wrapper 会自动重试一次（输出 `attempts=2`）；此时请勿发起第三次重试。

---

## 6. 非设计目标 (Non-Goals)

`call-agy` 专注于**任务级子 Agent 委托**，坚决不做以下行为：

- ❌ 作为模型提供方代理（Shim）或封装 OpenAI 兼容的 API 端点。
- ❌ 提取、保存或复用 Antigravity 的 OAuth 访问凭据或 Token。
- ❌ 绕过 CLI 直接请求 Google/Antigravity 内部后端接口。
- ❌ 将宿主用户的每一轮常规对话无条件转发代理给 Antigravity。
- ❌ 在缺少用户显式授权时擅自变更 Antigravity 的本地权限与沙箱策略。
