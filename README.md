# Issue Materials Review

把这个项目克隆到一个新目录，在 agent 中打开该目录，然后提供两项输入：
**材料路径**和**目标库名称**。Agent 读取项目的 AGENTS.md，使用项目自带的工具
整理证据、复现、检索已有 issue/PR，最后启动供人工审阅和逐字段复制的页面。

```bash
git clone <仓库地址或本地源仓库路径> <新的工作区目录>
cd <新的工作区目录>
```

打开这个工作区后，可以这样提出任务：

> 材料在 /path/to/materials，目标库是 pytorch。请按本工作区的 AGENTS.md
> 整理 issue 材料，完成复现和已有 issue/PR 核对，提供人工审阅页面。

不需要把项目安装到全局 skill 目录。保留的 SKILL.md 是项目内的完整流程说明，
由 AGENTS.md 引导读取；具体操作使用当前克隆中的 scripts/、assets/ 和 references/。

## 工作区内保存什么

| 位置 | 内容 |
| --- | --- |
| AGENTS.md、SKILL.md、references/ | Agent 的入口、工作流和证据要求 |
| scripts/ | 模板解析、复现记录、后台检索、字段校验及审阅服务 |
| assets/ | 固定前端、布局校验基准和已有 PT/TF 模板缓存 |
| runs/ | 每次任务的输入记录、冻结材料、运行证据和最终报告包 |
| .local/state/review.sqlite3 | 当前工作区的选中项与复制进度 |
| tests/ | 使用合成数据和本地服务的回归检查 |

任务产物、运行状态和本地凭据不会随 Git 克隆。一个新克隆从空任务和空审阅进度开始。
原始材料可以放在工作区以外；agent 读取它们，并将使用到的证据冻结到任务目录。

GitHub 检索队列默认共用本机状态位置，以串行控制同一账户的请求额度。
不同克隆分别处理各自任务，仍然共用这一个队列，不要为加速而另建并行队列。
使用现有个人访问令牌；令牌保留在本地环境变量或已有 GitHub 登录中，不进入项目文件。

## 环境与页面

使用本地已有 Python。服务、SQLite、缓存导出和检索器使用标准库；验证 YAML
快照时需要一个已有且包含 PyYAML 的解释器。缺少匹配环境时，agent 应主动询问，
不会为了运行这个项目强行安装或升级目标库。工具所用的 Python 与缺陷复现环境分别选择。

Agent 会创建新任务，按官方模板准备字段，并启动本地服务。人在浏览器里选择报告、
打开官方填写页、逐字段复制即可。模板、原始日志和内部任务状态留在证据记录中。
更换库或材料继续使用同一套前端，不需要让 LLM 改页面。项目不会代替人提交 issue。

在 WSL 中启动服务后，Windows Edge 可以打开返回的 localhost 地址。
已有端口被占用时使用另一个可用端口，避免影响其他工作区。

## 检查与详细说明

以下命令供 agent 或维护者使用；把 python 换成本地已有的解释器：

```bash
python scripts/workspace.py paths
python scripts/layout_guard.py check
python -m unittest discover -s tests -v
```

完整流程见 [SKILL.md](SKILL.md)，后台检索见
[检索队列说明](references/retrieval-queue.md)，字段组装见
[字段填写说明](references/filling.md)，服务与进度见
[审阅服务说明](references/service.md)。
