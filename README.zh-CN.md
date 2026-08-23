# call-agy

<p align="center">
  <a href="README.md">English</a> | <strong>简体中文</strong>
</p>

<p align="center">
  专为官方 Google Antigravity CLI (<code>agy</code>) 无头模式设计的<strong>宿主无关（Host-Agnostic）AI Agent 任务级委托 Skill</strong>。
</p>

<p align="center">
  <img src="assets/readme/hero-zh-cn.svg" alt="call-agy 让你在 Agent 对话中直接把任务派给 agy-cli" width="100%">
</p>

---

## 核心特性

- **在常用 Agent 中直接体验新模型**：直接在日常使用的 AI Agent 宿主中调用（如 Codex、DeepSeek Harness），将 `agy` 作为专属子 Agent 调度，无需切换工具与工作流即可尝试和评测 Antigravity 上的新模型。
- **上手零成本，无需 API Key**：开箱即用。只要本地完成过一次官方 CLI 登录（直接运行 `agy`），该 Skill 即可被直接调用，全程无需向 Skill 或宿主 Agent 配置或提供任何 API 密钥。
- **基于官方文档接口的任务级委托**：通过官方无头 CLI 将本地 `agy` 作为子 Agent 使用，而非模型层代理（Shim）；不提取 OAuth 访问凭据，也不暴露 OpenAI 兼容端点。受信任工作区预设会单独披露，因为它会使用官方提供的全工具权限绕过参数。
- **任务级 Agent 委托**：调度 Agent 始终作为主编排者，将有明确边界的代码探索、实现、调试、重构、评审或测试任务委托给 Antigravity 独立完成。
- **结构化 Markdown 移交报告**：实时流式解析官方 `stream-json` 事件，自动过滤高噪冗余信息，生成包含实际委托的完整组装提示词、最终结果、工具调用频次、Token 统计与会话元数据的紧凑交付报告。
- **长提示词直接输入**：不超过 60 KiB 的 stream-json 消息直接通过 stdin 发送；更大的序列化提示词改用明确报告路径的临时文件，运行结束后再与会话 handoff 归组。
- **可恢复的多轮会话**：提取并返回确定性的 `conversation_id`，支持后续任务基于历史上下文无缝接续，避免上下文漂移。
- **原生全平台支持**：提供零额外依赖的 POSIX Shell (`call_agy.sh`)、Windows PowerShell (`call_agy.ps1`) 与跨平台 Python (`call_agy.py`) 启动脚本。
- **受信任工作区可靠修改**：用户授权在受信任工作区修改时，首调使用 `--mode accept-edits --sandbox --dangerously-skip-permissions`。这是成功率优先预设，会自动批准 Antigravity 的全部工具调用；用户明确要求安全模式时保留权限检查。

---

## 工作机制

`call-agy` 实现了严谨的 Agent 间协作生命周期：

<p align="center">
  <img src="assets/readme/workflow-zh-cn.svg" alt="call-agy 架构与任务委托流程" width="100%">
</p>

1. **宿主编排与模型探索**：调度 AI Agent（如 Codex、DeepSeek Harness）明确任务目标、模型选择（`--model`）、验收标准与工作区边界，并提供可选的优先级参考文件（`--file`）。
2. **协议桥接**：`call_agy.py` 标准化路径参数并执行 `agy --print= --input-format stream-json --output-format stream-json`，通过 stdin 发送组装后的提示词。
3. **自主执行**：Antigravity 作为子 Agent 在工作区内按所选权限姿态运行。成功率优先预设会自动批准其工具调用；安全模式保留本地权限检查。
4. **结构化交付**：`call-agy` 捕获终端结果与运行时指标，脱敏并按会话归组到系统临时目录。
5. **宿主审查**：调度 Agent 读取移交报告并审查工作区代码 Diff，验证通过后向用户汇报或继续后续多轮任务。

---

## 环境要求

- **Antigravity CLI**：官方 `agy` 命令行工具（需 **1.1.15+** 以支持结构化 `stream-json` 输入与输出；推荐使用最新版）。
- **本地认证**：只需在终端运行一次官方 `agy` 完成常规登录。**无需配置任何 API 密钥或环境变量**。
- **Python 环境**：Python 3.10+（可通过 `python`、`python3` 或 `py` 调用）。
- **宿主 Agent**：任何能够读取 `SKILL.md` 并执行本地进程的 AI Agent 环境，如 Codex、DeepSeek Harness。

> 官方 Antigravity 无头模式文档：https://antigravity.google/docs/cli/headless/

---

## 安装与配置

将 `call-agy` 仓库放置或链接到宿主 Agent 扫描的 Skill 目录中：

仓库发布并验证后可使用：`npx skills add <repository-url> --skill call-agy`。本次本地优化没有发布或验证该远程路径；当前安装方式是下述本地链接。

### POSIX (macOS / Linux)

```bash
export AGENT_SKILLS_DIR="/path/to/your/agent/skills"
mkdir -p "$AGENT_SKILLS_DIR"
ln -s "/absolute/path/to/call-agy" "$AGENT_SKILLS_DIR/call-agy"
```

### Windows (PowerShell)

```powershell
$SkillsDir = "$env:USERPROFILE\.gemini\skills"
$Repo = "C:\path\to\call-agy"
New-Item -ItemType Directory -Force -Path $SkillsDir
New-Item -ItemType Junction -Path "$SkillsDir\call-agy" -Target $Repo
```

---

## 快速上手

### 1. 完成本地认证（仅需一次，零 API Key 配置）

```bash
agy
```

*在终端运行一次 `agy` 完成登录。无需配置 API Key。*

### 2. 执行委托任务

#### macOS / Linux (POSIX Shell)

```bash
./scripts/call_agy.sh "分析当前仓库的核心请求处理流程并进行总结" --workspace .
```

#### Windows (PowerShell)

```powershell
& ".\scripts\call_agy.ps1" --task "分析当前仓库的核心请求处理流程并进行总结" --workspace .
```

请在当前 PowerShell 进程中直接调用启动脚本；嵌套 `powershell -Command` 会额外增加一层引号解析，且没有必要。

#### 跨平台 (Python 3)

```bash
python3 scripts/call_agy.py "分析当前仓库的核心请求处理流程并进行总结" --workspace .
```

长提示词或多行任务通过 stdin 直接传入：

```powershell
@'
分析当前仓库。
说明核心请求流程，并根据代码逐项验证结论。
'@ | .\scripts\call_agy.ps1 --workspace .
```

启动脚本把输入传给 wrapper，wrapper 再通过 stream-json stdin 交给原生 `agy`。安全线按最终 UTF-8 NDJSON 消息计算，为 60 KiB；超过后，wrapper 会先把完整提示词以唯一文件名放入 `%TEMP%/call-agy/.big-prompt/`，运行结束后再移动到会话 handoff 所在目录，并明确输出最终 `prompt_file_path`。如果宿主无法提供进程 stdin，可把自己的中转提示词放在系统临时目录，再把文件内容通过管道传给启动脚本。

### 3. 标准执行输出

任务完成后，`call-agy` 会向标准输出打印紧凑的元数据：

```text
conversation_id=<conversation-id>
output_path=<system-temp>/call-agy/<conversation-id>/<turn-id>-handoff.md
elapsed=14.2s
status=SUCCESS
```

如果全新调用在模型和工具均未开始前命中严格限定的零 Token 空错误，wrapper 会用完全相同的任务与设置自动重试一次，并额外输出 `attempts=2`。这次自动重试会消耗唯一的重试额度。

调度 Agent 读取 `output_path` 文件获取结构化报告，并在接纳结果前审查实际的工作区变更。同一会话的默认 handoff 共用一个临时目录，并以唯一轮次前缀区分文件；恢复上下文以 `conversation_id` 为准。显式 `--output` 仍可覆盖保存位置。

标准 handoff 包含委托给 Antigravity 的完整组装提示词、执行结果与运行元数据。

---

## 多轮会话接续

若后续任务依赖上一轮的上下文，可传入返回的 `conversation_id` 进行无缝恢复：

```bash
./scripts/call_agy.sh \
  "基于上面的分析结果，为边界异常场景补充单元测试" \
  --workspace . \
  --conversation "<conversation-id>"
```

> **提示**：虽然 `-c, --continue` 可以恢复当前工作区最近一次会话，但在多会话并发或复杂编排场景下，强烈建议使用 `--conversation <id>` 确保调度的确定性。

---

## 典型 Agent 提示词示例

**你可以直接这样说 / You can say:**

在向宿主 Agent 发出指令时（例如体验新模型或委托独立任务），可采用如下提示词模式：

```text
使用 $call-agy 并指定最新模型（--model <slug>）调查当前仓库中的这个报错。
将失败的测试文件和关键源码作为优先级路径（--file）传入。
让 Antigravity 作为子 Agent 独立实现修复并运行测试验证，随后审查其移交报告和代码 Diff 再向我汇报。
```

---

## CLI 常用参数一览

| 参数 | 说明 |
| :--- | :--- |
| `-w, --workspace <path>` | wrapper 专用：设置进程工作目录，并自动把该目录加入原生 `agy` 的工作区访问范围（默认当前目录）。原生 `agy` 没有 `--workspace` 参数。 |
| `-f, --file <path>` | wrapper 专用：把优先路径提示追加到提示词（可重复指定）。它不授予访问权限；原生 `agy` 没有 `--file` 参数。 |
| `--add-dir <path>` | 额外向 Antigravity 暴露工作区外部的目录（可重复指定）。 |
| `--conversation <id>` | 恢复指定的 Antigravity 会话 ID。 |
| `-c, --continue` | 恢复工作区内最近一次活跃的 Antigravity 会话。 |
| `--model <slug>` | 显式指定模型 Slug（需从 `agy models` 中获取；默认使用 CLI 配置）。 |
| `--effort <low\|medium\|high>` | 设定推理思考强度。 |
| `--agent <name>` | 指定内置或自定义 Agent 身份（需从 `agy agents` 中获取）。 |
| `--timeout <duration>` | 运行超时时间，例如 `10m`, `30m`（默认：`10m`）。 |
| `--mode <accept-edits\|plan>` | 执行模式；在明确授权修改代码时请使用 `accept-edits`。 |
| `--sandbox` | 启用 Antigravity 终端沙箱限制。 |
| `--dangerously-skip-permissions` | **原生 `agy` 高危参数**：自动批准所有工具调用。受信任工作区修改预设默认传入；用户明确要求安全模式时省略。 |
| `-o, --output <path>` | 自定义 Markdown 移交报告保存路径。 |
| `--raw-output <path>` | 调试专用：捕获原始 NDJSON 数据流（可能包含敏感工具细节）。 |
| `--agy-binary <path>` | 覆盖 `agy` 可执行文件路径或名称。 |
| `--dry-run` | 打印命令结构、序列化提示词大小与选定传输方式，不执行任务，也不创建兜底提示词文件。 |

---

## 权限、CLI 边界与安全模型

- **基于官方文档接口的子 Agent 委托**：`call-agy` 仅通过官方文档支持的无头模式调用本地 `agy` CLI。它**不是**模型代理（Shim），不提取 OAuth 访问凭据，也不提供 OpenAI 兼容端点。该架构本身不构成对法律、组织制度或安全策略合规性的保证。
- **零凭据暴露与安全隔离**：Skill 本身无需也绝不读取、存储任何 API 密钥或 OAuth 凭据，完全依赖官方 CLI 管理的本地用户会话。
- **明确的权限姿态**：安全模式遵循本地 Antigravity 权限规则；成功率优先预设则会显式改变该姿态，不能把两者混为一谈。
- **受信任工作区修改预设**：用户授权在受信任工作区修改时，宿主流程先告知风险，再首调使用 `--mode accept-edits --sandbox --dangerously-skip-permissions`。高危参数会自动批准全部 Antigravity 工具调用（包括写入和命令执行）；`--sandbox` 约束终端执行，但不能让任意写入变得无害。
- **安全修改覆盖**：用户明确要求安全模式或保守权限时，宿主改用 `--mode accept-edits --sandbox`，保留权限检查。
- **工作目录指令**：每次委托都会要求 Antigravity 直接在 `--workspace` 指定的绝对路径工作，并使用文件编辑工具写入大段内容，避免 Windows 下的超长内联 shell 命令。
- **外部目录隔离**：`--file` 仅作为优先级提示，无法越权访问工作区外的文件；如需访问外部模块，必须显式声明 `--add-dir <path>`。
- **防止敏感信息泄露**：标准 Markdown 移交报告默认丢弃工具调用的原始入参和原始输出，仅保留结构化统计数据。

---

## 故障排查 (Troubleshooting)

当委托执行失败（非零退出码或非 `SUCCESS` 状态）时，宿主 Agent 应执行以下自愈逻辑：

1. **诊断原因**：读取 stderr 与 Markdown 移交报告中的 Error 诊断区块。
   - `AGY_NOT_FOUND` -> wrapper 已检查 `PATH` 和官方默认安装位置。如果 agy 安装在其他位置，使用其绝对路径通过 `--agy-binary` 重试；确认本机没有可执行文件后再安装。
   - `AUTH_REQUIRED` -> 在终端运行 `agy` 完成登录，然后重试。
   - `HOST_SANDBOX_BLOCKED` -> 请求宿主层开放 `~/.gemini/antigravity-cli` 后重试同一任务；仅调整 Antigravity 自身权限参数无法取得该访问权。
   - `AGY_VERSION_UNSUPPORTED` 表示当前 CLI 不支持所需的无头参数；运行 `agy update` 或重新安装。
   - 没有 `output_path` 且没有上述分类错误，才按 wrapper 未完整启动处理，先修正原生 shell 调用。
2. **本地安全修复**：
   - 外部文件边界 -> 使用 `--add-dir` 补充包含目标的目录。
   - 用户明确要求的安全模式发生 shell 权限软拒绝 -> 配置细粒度 Antigravity 命令规则，或询问用户是否调整权限姿态。
   - 超时中断 -> 适当延长 `--timeout 20m`。
   - 会话失效 -> 移除 `--conversation` 重新开启新会话。
3. **单次重试**：使用修复后的参数重试一次委托任务。
4. **向用户求助**：若需要用户介入，展示分类后的修复动作；不得索取凭据，也不得未经验证擅自降级接管。

wrapper 只会为 `ERROR` + 空响应 + Token 用量全零 + 没有工具步骤这一严格签名自动消耗该重试。看到 `attempts=2` 后，不得发起第三次调用，也不要为追逐同一空错误而擅自切换模型或权限姿态。

如果原生 `agy` 返回 `SUCCESS`，但响应为空、Token 用量全为零且没有实际执行回合，wrapper 会把它报告为失败。诊断会包含序列化提示词大小，并在末尾提示：推测提示词可能超过约 60KB。

---

## 非设计目标 (Non-Goals)

`call-agy` 专注于**任务级子 Agent 委托**，坚决不做以下行为：

- ❌ 作为模型提供方代理（Shim）或封装 OpenAI 兼容的 API 端点。
- ❌ 提取、保存或复用 Antigravity 的 OAuth 访问凭据或 Token。
- ❌ 绕过 CLI 直接请求 Google/Antigravity 内部后端接口。
- ❌ 将宿主用户的每一轮常规对话无条件转发代理给 Antigravity。
- ❌ 在缺少用户显式授权时改变 Antigravity 的本地权限、沙箱或账户安全策略。

---

## 开源协议

本项目采用 MIT 许可证开源。详情请参阅 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。
