# C++ 比赛分支最小使用说明

## 1. 这个分支的定位

这个分支不是通用 autonomous-sdd，而是面向固定 C++ 赛题的定制分支。

默认目标就是：

- 自定义压头
- 可变长度解析
- 解包正确性
- 原 CLI 兼容
- 编译入口不变
- skill 交付
- 测试验证

## 2. 如何启动

如果直接使用默认赛题目标：

```powershell
python scripts\sdd.py compete --project <target-project> --executor opencode
```

如果需要在启动时一次性传入外部任务文本：

```powershell
python scripts\sdd.py compete --project <target-project> --task <task-file-or-inline-text> --executor opencode
```

注意：

- `--task` 只在启动时生效；
- 启动后目标被冻结；
- 中途不应再补充需求。

## 3. 如何把任务交给其他 agent

建议把以下三份材料一起给执行 agent：

1. 目标项目代码
2. [cpp_competition_execution_prompt.md](E:/tmp/autonomous-sdd/docs/cpp_competition_execution_prompt.md)
3. 当前运行生成的 `.sdd/runtime/task-packet.json`

如果当前处于 `apply` 阶段，务必强调：

- 只做当前 `task_id`
- 必须遵守 `current_task_contract`
- 必须写真实 `requirement_evidence`

## 4. 当前 Runner 已经会自动卡什么

Runner 当前会自动阻断以下问题：

- 没有覆盖固定赛题目标
- 缺少 skill 相关证据
- 缺少解包/兼容性/压头覆盖
- `plan.md` 没有覆盖全部任务
- `plan.md` 写得太空泛
- `plan.md` 承诺的主题没有在最终证据里兑现
- `plan.md` 承诺的实现/测试目标没有落到真实 evidence 文件

## 5. 推荐的实际使用方式

最稳妥的方式不是让弱 agent 自己理解整套框架，而是：

1. 启动 Runner；
2. 读取当前 `.sdd/runtime/task-packet.json`；
3. 把 packet 和执行 prompt 一起交给 agent；
4. 让 agent 只完成当前任务；
5. 回来由 Runner gate。

这样最符合当前分支的设计边界。
