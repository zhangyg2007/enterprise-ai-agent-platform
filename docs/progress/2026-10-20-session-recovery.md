# 会话恢复与 GitHub 同步检查点

检查日期：2026-10-20（北京时间）。这是恢复记录，不是 M07 完成交付声明。

## 工作位置与远端状态

- 仓库：`zhangyg2007/enterprise-ai-agent-platform`。
- 开发目录：`D:/newWorkspace/chatgpt_projects/EnterpriseAIAgentPlan/.worktrees/m03`。
- 当前开发分支：`codex/m07-evaluation-serving`。
- 检查时项目根目录 main 仍停在 `fa25a40`（M03）；不能以该目录推断后续成果丢失。
- GitHub main 为 `aa7cfd5424d55e5b947945db820db6c7e1367c0c`，已包含 M04/M05 和用户新上传的 plan.txt、Filelist.txt。
- M01/M02、M03、M04/M05 分别经 PR #1、#2、#3 合并。
- M06 本地提交与 GitHub `codex/m06-qwen-training` 均为 `88d9799ffde5e4ca03b8bc935e508dbbbd99220f`；PR #4 仍 OPEN，未合并 main。
- 检查时 M07 分支还没有远端分支，也没有超出 M06 的本地提交。

## 找回的 M07 文件

以下四个文件检查时全部为未跟踪文件，本检查点保存其原始内容：

1. `docs/superpowers/specs/2026-10-20-m07-evaluation-serving-design.md`
2. `docs/superpowers/plans/2026-10-20-m07-evaluation-serving.md`
3. `tests/test_model_delivery.py`
4. `tests/test_model_api.py`

`model_delivery.py`、`model_eval.py`、`model_api.py`、评估器测试、第 7 课和 M07 运行手册尚未存在。实施计划 T1–T3 均未勾选。不能把测试草稿当作已实现功能。

## 本次验证

- 既有 M01–M06 标准库测试：130 项通过，33.536 秒。通过 unittest discover 收集后，仅排除了上述两份新 M07 测试。
- 首次在受限沙箱运行时，临时目录触发 PermissionError；在放宽执行限制后复验得到上述结果。没有修改业务代码以绕过错误。
- `PYTHONPATH=src python -m unittest discover -s tests -p "test_model*.py"`：14 个失败、1 个导入错误。delivery 未实现；API 模块无法导入。因此完整测试集目前不通过。
- M06 既有验证记录包含独立审核及修复经过；本次没有重新进行代码审核。
- Ubuntu 依赖安装、真实权重加载、GPU 训练、模型效果评估及服务验收仍待服务器执行，不能由 130 项离线测试代替。

## Git 忽略的本地资料

`reports/local/` 仍保留 M04–M06 测试日志、M05 合成数据输出、M06 集成试验输出、结构用伪权重和上游源码检查资料。这些文件按现有规则被忽略，未随本检查点上传；它们不是训练完成的真实模型。不要强制上传整个 reports 或把伪权重当作训练成果。

项目根目录的 plan.txt 和 Filelist.txt 虽显示为未跟踪，但已在 GitHub main 上传并完成逐字节回读验证。

## 接续位置与同步约定

课程编号是 M00–M13，共 14 个模块。首个未完成开发模块为 M07，从现有设计与 T1–T3 计划接续，不重复生成已验证的 M01–M06。

用户要求每次完成任务、创建相关文件后及时同步 GitHub。后续每个可验证任务应保存进度、验证命令与结果，提交并推送开发分支，再核对远端提交。中断保存使用明确的 WIP 检查点，写清失败测试及未完成内容；只有实际通过验证并完成相应审核的任务才能标记完成。同步开发分支不等于合并 PR 或发布。

本检查点只保存恢复材料，没有实现 M07，也没有合并 PR #4。
