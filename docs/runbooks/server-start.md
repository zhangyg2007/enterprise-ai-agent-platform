# 服务器实操起步：从main取得第0–10课

当前学习分支：`main`，课程批次`0.1.7.0`。M06–M10的PR #4–#8已依次合并，默认分支包含第0–10课。历史章节分支保留用于复现；学习时记录实际commit，常规更新统一使用main。发布与验证证据见[主线发布记录](../progress/2026-10-22-main-publication.md)。

## 1. 首次获取

在Ubuntu选定的项目父目录执行；目标子目录必须不存在。用户自己的绝对路径保存在本地项目记忆，此公开手册不记录它。

```bash
git clone --branch main --single-branch \
  https://github.com/zhangyg2007/enterprise-ai-agent-platform.git \
  enterprise-ai-agent-platform
cd enterprise-ai-agent-platform
git branch --show-current
git rev-parse HEAD
cat VERSION
test -f docs/tutorials/06-qwen-finetuning.md
test -f docs/tutorials/10-langgraph-basics.md
test -f src/enterprise_ai/agent_cli.py
test -f plan.txt
```

预期分支名如上，VERSION为0.1.7.0，四个文件检查成功。若目录已存在，不在里面再克隆一个嵌套仓库，使用下一节。

## 2. 已有仓库更新（包括旧M09/M10单分支克隆）

先进入已有项目根目录。检查本地修改并保留`.env`、模型和实验数据；以下命令不清理或重置任何文件。

```bash
git status --short
git remote -v
```

若有已修改/未跟踪文件，先停在此处，与实操辅导会话逐项判断备份或提交方式，避免分支切换覆盖学习记录。干净工作区才继续。旧single-branch克隆需要补充main的fetch映射；以下步骤也适用于已有main的仓库：

```bash
git remote set-branches --add origin main
git fetch origin \
  refs/heads/main:refs/remotes/origin/main
if git show-ref --verify --quiet refs/heads/main; then
  git switch main
else
  git switch --track origin/main
fi
git merge --ff-only origin/main
git rev-parse HEAD
cat VERSION
test -f docs/tutorials/06-qwen-finetuning.md
test -f docs/tutorials/10-langgraph-basics.md
test -f src/enterprise_ai/agent_cli.py
test -f plan.txt
```

如果不是fast-forward，保留错误与分支状态，停止操作；不要force-push、reset或删除目录来“修复”。

## 3. 学习顺序与环境

1. 阅读[总计划](../../plan.txt)与[课程路线](../ROADMAP.md)，再执行[第0课](../tutorials/00-server-inventory.md)只读盘点。
2. 跟随[第1课](../tutorials/01-ubuntu-platform.md)建立基础环境，再顺序学习数据、治理、模型与训练；不要求一天全部完成。
3. 项目当前声明Python`>=3.11,<3.14`。先确认`command -v python3`、`python3 --version`和可用解释器；若现有默认版本不在范围内，停止照抄第1课的通用`python3 -m venv`命令。在辅导中确认独立Python3.12应用环境与Python3.11训练环境，不替换系统Python或改变既有推理环境。
4. 想先验证知识库流程，可直接从[第9课第2节](../tutorials/09-enterprise-rag.md#2-先跑不下载模型的最小实验)做Markdown离线实验；真实模型和数据库步骤在其后。
5. 依赖安装、下载、迁移与GPU训练是服务器验收步骤，本地回归通过不替代它们。填写对应章节runbook，并记录命令、退出码、脱敏输出、commit和下一步。

文件用途见[Filelist](../../Filelist.txt)。M09实操结果填写[验收表](m09-rag-handoff.md)，M10填写[验收表](m10-agent-handoff.md)。M11实验仍在独立草稿PR，不要求学习者切到未完成章节。用户环境记忆、`.env`、权重与原始实验日志不提交公开仓库。
