# Autonomous SDD

Autonomous SDD 是一个面向受限内部开发环境的“一键式、无人值守”软件交付编排器。

它将以下能力组合为一条完整交付流水线：

- 使用 OpenSpec 管理需求和能力规格；
- 使用内置 Superspec 工作流约束工件顺序与阶段门禁；
- 使用 OpenCode Agent 作为阶段执行环境；
- 使用确定性的 Python Runner 管理状态、阶段交接、Git 检查点、规则校验、恢复与归档。

当前版本的对外交付形态是 OpenCode Agent，而不是仓库内捆绑 Skill。使用者只需要提供目标项目和任务文件。Autonomous SDD 会自动完成需求分析、规格设计、任务分解、代码实现、独立审查、验证、OpenSpec 归档、复盘和最终交付报告。

## 交付形态

- 用户入口：`.opencode/agents/autonomous-sdd.md`
- 内部阶段执行：`.opencode/agents/sdd-stage.md`
- 状态机与恢复：`.sdd/bin/sdd.py` 及其内置 `autonomous_sdd` 运行时
- Skill 使用方式：运行时只做能力路由和审计，不负责安装或复制 Skill

也就是说，这个仓库现在是“Agent + Runner”的托管框架。项目级或公司级 Skill 由宿主环境预置，Runner 通过 `.sdd/config.yaml` 里的 `skill_routing` 把能力映射到候选 Skill。

## 核心特性

- 对外只有一个主 Agent 入口
- 启动后不需要人工参与
- 单工作区、单写入者，不使用 Git worktree
- 每个阶段或实现任务使用独立的 OpenCode 会话
- 默认使用 OpenCode 环境中已经配置的模型
- 运行状态持久化，不依赖聊天上下文保存记忆
- 每个阶段通过后生成交接记录和 Git 检查点
- 自动保护公共 API、依赖文件、策略文件、Schema 和修改范围
- 重试次数受限，无法继续时安全进入 `BLOCKED`
- 自动同步 OpenSpec 主规格并归档 Change
- 同时提供 Windows 和 Unix 入口
- 支持项目级或宿主级 Skill 热插拔

## 环境要求

执行机器需要提前安装：

- Python 3.10 或更高版本
- Git
- OpenCode
- OpenSpec CLI
- 目标项目自身需要的构建和测试工具

可以使用以下命令检查本地环境：

```powershell
python --version
git --version
opencode --version
openspec.cmd --version
```

OpenCode 需要提前配置好可用模型。Autonomous SDD 默认不会自行指定或切换模型。

## 快速开始

首先准备一个 UTF-8 编码的任务文件，例如 `task.md`：

```markdown
实现指定的任务调度行为。

约束：

- 不允许修改公共 API；
- 不允许新增依赖；
- 保持现有架构边界；
- 为正常场景和边界场景补充自动化测试。
```

然后初始化目标项目：

```powershell
python scripts\sdd.py init E:\path\to\project
```

随后在目标项目里选择 `autonomous-sdd` Agent，并把任务内容直接发给 Agent。

如果要从仓库侧直接启动完整流程，也可以执行：

```powershell
python scripts\sdd.py compete --project E:\path\to\project --task-file E:\path\to\task.md --profile generic-hosted
```

目标 Git 仓库在执行前必须处于干净状态。

## 本地演练模式

在调用真实模型之前，可以先使用确定性演练执行器验证整个控制系统：

```powershell
python scripts\sdd.py rehearse-recovery --project E:\path\to\project --task-file E:\path\to\task.md --profile generic-hosted
```

演练模式不会调用模型，但会实际完成全部生命周期：

```text
项目探测
→ 自动安装交付框架
→ 冻结项目基线
→ 需求探索
→ Change Proposal
→ 能力规格
→ 技术设计
→ 任务和执行计划
→ 实现任务循环
→ 独立代码审查
→ 完整验证
→ Finalize
→ 归档和主规格同步
→ 复盘
→ CLOSED 交付报告
```

演练过程会创建 Git 提交和交付工件，因此建议在项目的临时克隆或测试仓库中执行。

## Unix 入口

正式执行：

```sh
python scripts/sdd.py compete --project /path/to/project --task-file /path/to/task.md --profile generic-hosted
```

演练模式：

```sh
python scripts/sdd.py rehearse-recovery --project /path/to/project --task-file /path/to/task.md --profile generic-hosted
```

## 一键命令会自动完成什么

Runner 会自动执行以下操作：

1. 检查目标 Git 仓库是否干净；
2. 识别 Maven、Gradle、npm、pnpm、Python、Go、Rust 或通用项目；
3. 安装 OpenCode Agent 模板、OpenSpec Schema、模板、策略和运行入口；
4. 自动配置目标项目的测试命令；
5. 提交比赛交付框架；
6. 冻结策略、Schema、依赖文件和受保护 API 基线；
7. 创建受控的 OpenSpec Change；
8. 为每个生命周期阶段启动新的 OpenCode Agent 会话；
9. 在每次状态推进前运行确定性门禁；
10. 为每个通过验证的阶段生成 Git 提交；
11. 同步已经实现的能力规格并归档 Change；
12. 生成复盘和最终交付报告。

## 执行结果

成功完成时输出：

```text
RESULT=CLOSED
REPORT=<project>\.sdd\delivery-report.md
```

主要交付工件包括：

```text
.sdd/delivery-report.md
.sdd/changes/<change>/handoffs/
.sdd/evidence/
openspec/specs/
openspec/changes/archive/
```

历史上为固定 C++ 赛题定制过一套分支方案，相关设计文档仍保留在 `docs/` 中，主要用于回溯背景，不代表当前主线交付仍以竞赛 Skill 为中心：

- `docs/competition_submission_package_design.md`
- `docs/competition_submission_prompt.md`
- `docs/cpp_competition_goal_freeze_design.md`
- `docs/cpp_competition_goal_freeze_prompt.md`
- `docs/competition_objective_input_and_freeze_design.md`
- `docs/competition_design_traceability_design.md`
- `docs/competition_development_trace_design.md`
- `docs/cpp_competition_autonomous_design.md`
- `docs/cpp_tool_skill_design.md`
- `docs/cpp_competition_execution_prompt.md`
- `docs/cpp_competition_operator_guide.md`

运行时状态保存在 `.sdd/runtime/`，默认不会提交到 Git。

如果系统无法在安全边界内继续执行，会返回非零退出码，并记录：

```text
status: blocked
blocking_reason: <具体原因>
last_verified_commit: <最后一个安全检查点>
```

## 默认安全策略

内置策略默认禁止：

- 启动后的人工干预；
- Git worktree 和多个并行写入者；
- 修改依赖清单；
- 修改策略、基线、Runner 和 Superspec Schema；
- 修改评分器、评测代码和官方测试数据；
- 修改常见的 `api` 和 `contract` 包；
- 超出自动识别的源码和测试目录进行修改。

项目特有规则可以定义在 `.sdd/policy/` 下。正式一键执行默认采用保守策略。

## 模型选择

`.sdd/config.yaml` 默认配置为：

```json
{
  "model": null
}
```

当 `model` 为 `null` 时，Runner 不会传递 `--model` 参数，OpenCode 会使用当前执行环境已经配置的默认模型。

只有在能够保证模型标识稳定时，才建议配置显式模型：

```json
{
  "model": "provider/model-id"
}
```

## 上下文与恢复机制

Autonomous SDD 不将聊天历史视为可靠状态源。

系统通过以下文件维持执行记忆：

```text
.sdd/runtime/state.json
.sdd/runtime/current-handoff.json
.sdd/runtime/task-packet.json
.sdd/runtime/execution-journal.jsonl
```

每次 Agent 调用只接收当前阶段所需的任务包、约束和相关工件。即使 OpenCode 压缩上下文或重新启动会话，Runner 也可以根据持久化状态继续执行。

## OpenSpec 和 Superspec

项目内置 `autonomous-superspec` Schema，生命周期为：

```text
brainstorm
→ proposal
→ specs
→ design
→ tasks
→ plan
→ apply
→ review
→ verify
→ finalize
→ archive
→ retrospective
→ closed
```

OpenSpec 负责描述需要交付的行为，Superspec 负责约束工件依赖，Runner 负责实际状态推进和机器门禁。

## 底层命令

正常比赛应优先使用一键入口。以下命令主要用于开发、调试和故障排查：

```powershell
python scripts\sdd.py init E:\path\to\project
.\sdd.cmd doctor
.\sdd.cmd baseline
.\sdd.cmd start <change-id> "<objective>"
.\sdd.cmd run
.\sdd.cmd status
.\sdd.cmd recover
.\sdd.cmd autorecover --retry-seconds 10
.\sdd.cmd rehearse-recovery --task "<objective>" --retry-seconds 10 --json-out .sdd\runtime\rehearsal-summary.json --artifacts-dir .sdd\runtime\rehearsal-artifacts
```

普通比赛执行不需要手工调用这些命令。

## 本地验证

验证 Runner 和 Skill：

```powershell
python -m unittest discover -s tests -v
python -m py_compile scripts\sdd.py tests\test_runner.py
```

自动化测试覆盖：

- 从普通 Git 仓库自动安装并开始执行；
- 一键完成完整生命周期；
- 策略文件篡改检测；
- 受保护 API 修改检测；
- 重试次数耗尽后的安全阻塞；
- OpenSpec 归档和主规格同步。
- 命令超时后完整回收子进程树；
- Runner 按稳定任务 ID 托管勾选并拒绝无实质变更。
- 支持在中断后自动回滚到最后验证检查点并续跑。

## 当前状态

当前版本为 `0.3.0`。

该版本已经通过确定性的完整生命周期测试。任务阶段只执行快速编译门禁，
全量项目测试集中在 `verify` 阶段执行一次，避免大型测试集在多个阶段重复运行。
任务分解限制为 3–20 个顶层任务，Runner 校验 `task_id` 并负责完成标记；
命令超时会终止整个进程树，避免 OpenCode、Maven 或测试进程残留。
Runner 还会从实际变更的测试文件生成针对性测试命令，不接受 Agent 自报的
“测试已通过”作为完成依据。回执必须提供严格的需求、实现和测试映射，
未满足需求或无法独立验证时不会勾选任务。
下一阶段验证重点是目标 OpenCode 环境中的真实模型执行效果、失败恢复质量和复杂项目适配能力。
