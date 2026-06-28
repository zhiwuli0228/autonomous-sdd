# C++ 比赛定制版 Autonomous SDD 设计文档

## 1. 目标

当前分支不是通用开发器，而是面向固定赛题的无人值守 SDD 开发器。默认目标固定为：

- 改造目标 C++ 打包项目，支持通过参数传入可变长度的压头定制内容；
- 定制后仍可正常解包；
- 编译入口保持不变；
- 原有打包工具原参数保持兼容；
- 交付一个可调用的 skill，支持 THX 相关处理与压头查看；
- 基于工程结构给出合理测试并完成验证。

允许在启动时一次性传入外部任务文本，但如果未传入，则直接使用上述默认赛题目标。

## 2. 设计原则

- 默认行为直接服务固定赛题，不再依赖泛化 sample 目标。
- 无人值守优先，不能要求中途补充需求。
- 目标冻结优先，启动后所有阶段围绕同一份冻结目标执行。
- 验证优先，任何实现都必须覆盖兼容性、解包、技能和测试要求。
- 约束内扩展，预留未来格式校验工具接入点，但不让该工具替代核心验证。

## 3. Runner 需要承担的职责

### 3.1 启动时冻结唯一有效目标

Runner 在 `compete` 或 `rehearse-recovery` 启动时解析：

- `--task` 文件
- `--task` 行内文本
- 未提供 `--task` 时的内置默认赛题目标

解析结果写入：

- `.sdd/runtime/competition-objective.json`

该文件至少包含：

- `effective_objective`
- `frozen_goal`
- `competition_constraints`
- `required_acceptance_invariants`
- `tooling_integration_constraints`
- `source`
- `branch_default_used`

### 3.2 生命周期阶段必须读取冻结目标

所有阶段 packet 中必须带出：

- `frozen_goal`
- `competition_constraints`
- `required_acceptance_invariants`
- `tooling_integration_constraints`

并将 `.sdd/runtime/competition-objective.json` 加入 `required_reads`。

### 3.3 Prompt 强制执行比赛边界

阶段 Prompt 必须明确：

- 冻结目标必须遵守；
- 约束项必须全部满足；
- skill 交付和验证不是可选项；
- 未来格式工具只作为辅助证据，不替代核心验证。

## 4. 各阶段输出应该围绕什么写

### brainstorm

必须明确：

- 赛题目标
- 兼容性边界
- 可变长度压头处理风险
- skill 交付边界

### proposal

必须明确：

- pack 侧新增参数
- unpack 侧长度感知解析
- 兼容性保持策略
- skill 交付范围

### specs

至少覆盖：

- 自定义压头参数化
- 可变长度压头
- 定制包正常解包
- 原参数兼容

### design

必须说明：

- 头部结构怎么兼容扩展
- 长度字段如何编码/解析
- 旧包和新包如何共存
- skill 如何调用工具
- 测试如何证明要求满足

### tasks

任务必须最少覆盖三块：

1. pack/format 改造
2. unpack/compat 回归
3. skill 与端到端验证

### verify

验证报告至少覆盖：

- 可变长度压头
- 解包正确性
- 原 CLI 兼容
- skill 可用性

## 5. skill 设计边界

Runner 不负责代替目标项目实现 skill 逻辑，但必须把 skill 交付作为强制目标，并在模板、packet、任务和验证中固定下来。

建议交付的 skill 能力：

- 打包 THX/HWX 文件
- 解包 THX/HWX 文件
- 查看压头字段
- 在存在自定义压头内容时输出解析结果

## 6. 为什么这次改造是必要的

如果不把赛题目标固化进 runner：

- agent 会继续输出泛化 sample 文档；
- skill 交付会被遗漏；
- 兼容性和解包验证可能被弱化；
- 无人值守运行时容易偏题。

这次改造的收益是：从启动到验证，全流程默认朝着同一个固定赛题收敛。

## 7. 后续开发建议

下一步应继续做两类增强：

1. 在 `apply` 阶段增加赛题验收项检查，缺少 skill/兼容性/测试证据时直接阻断；
2. 增加针对 packet、fixture、默认目标和 skill 交付要求的更多自动化测试。
