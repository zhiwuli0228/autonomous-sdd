# C++ 打包工具 Skill 设计文档

## 1. 定位

这个 skill 不是通用代码开发 skill，而是给后续 agent 操作目标 C++ 打包工具用的专用 skill。

目标：

- 降低较弱 agent 误用 CLI 的概率；
- 让 agent 能直接完成打包、解包、压头查看；
- 为验证自定义压头功能提供稳定入口。

## 2. 对 skill 的最低要求

skill 至少提供以下能力：

- `pack`：按原工具能力打包文件或目录
- `pack-with-header`：带自定义压头内容打包
- `unpack`：解包 THX/HWX 文件
- `inspect-header`：查看压头基础字段和自定义内容

## 3. skill 输入/输出设计

### pack

输入：

- 输入路径
- 输出路径
- 原有兼容参数

输出：

- 产物路径
- 调用命令
- 退出码

### pack-with-header

输入：

- 输入路径
- 输出路径
- 自定义压头内容

输出：

- 产物路径
- 实际传入的头部参数
- 退出码

### unpack

输入：

- 包路径
- 输出目录

输出：

- 解包目录
- 退出码

### inspect-header

输入：

- 包路径

输出：

- 固定压头字段
- 自定义压头长度
- 自定义压头内容

## 4. Agent 使用规则

skill 应强制 agent 遵循以下规则：

- 原参数流程和新参数流程必须分开验证；
- 先 inspect-header，再决定是否需要 unpack；
- 遇到解析失败时保留原始产物并输出错误；
- 不允许 agent 自己猜测头格式，必须以工具真实输出为准。

## 5. 建议文件布局

建议后续在目标项目中落地为：

```text
skill/
  cpp-unitool-header/
    SKILL.md
    examples/
    expected-output/
```

## 6. 验收标准

skill 交付完成后，至少证明：

- 能操作原始 CLI 成功打包；
- 能带自定义压头成功打包；
- 能查看定制压头信息；
- 能正常解包定制产物；
- 输出信息足够让 agent 做自动验证。
