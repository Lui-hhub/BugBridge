# BugBridge

[English](README.md) · 简体中文

BugBridge 帮助 coding agent 复现问题、核对已有 issue/PR，整理出供你审阅和复制的
issue 或评论草稿。

## 快速开始

1. **克隆仓库。**

   ```bash
   git clone https://github.com/Lui-hhub/BugBridge.git
   cd BugBridge
   ```

2. **在支持读取 [AGENTS.md](AGENTS.md) 的 coding agent 中打开这个目录。**
   提供**材料路径**和**目标库／项目名称**，例如：

   > 材料在 /path/to/materials，目标库是 pytorch。请按本工作区的 AGENTS.md
   > 整理 issue 材料，完成复现和已有 issue/PR 核对，提供本地审阅地址和到期时间。

   脚本、模板和审阅页面都随仓库提供，无需安装全局 skill。使用已有的 Python 3.10+；
   校验 YAML 模板时需要已有且包含 PyYAML 的解释器。复现使用与目标库匹配的现有环境。
   GitHub 检索优先使用本地已配置的个人访问令牌（PAT）。缺少环境或认证时，agent 会
   主动询问；令牌保存在本地配置中。

3. **打开返回的 localhost 地址。**
   审阅报告，打开对应的官方 issue 页面，逐字段复制。由你决定提交内容并完成发布。
   使用 WSL 时，可以在 Windows Edge 中打开这个地址。

   每个审阅服务在**启动 3 小时后自动关闭**，访问页面不会续期。Agent 会同时提供到期
   时间。关闭后文件和复制进度仍然保留，需要继续时让 agent 重新开启即可。

任务产物保存在 `runs/`，选中项和复制进度保存在 `.local/state/review.sqlite3`，
原始材料保持不变。处理多个库时，逐库提供材料路径；每个库独立创建任务和审阅地址，
GitHub 请求共用机器级队列。

## 更多说明

- [完整工作流](SKILL.md)
- [审阅服务与进度保存](references/service.md)
- [贡献与检查](CONTRIBUTING.md)

## 开源许可

BugBridge 的原创代码与文档采用 [MIT 许可](LICENSE)。随项目保存的上游模板
保留各自许可，详见[第三方来源与许可说明](THIRD_PARTY_NOTICES.md)。
