# M06 服务器安装、实验与跨会话交接手册

适用日期：2026-10-19。操作教程见[第 6 课](../tutorials/06-qwen-finetuning.md)。本文件用于另一个会话在 Ubuntu 24.04 / RTX 4090D 24GB / 128GB 内存服务器安装和验收；Windows 文档会话不安装依赖、不启动模型或服务。

M04/M05 已由 PR #3 合并，M06 尚待发布。开始前确认交付的 M06 Git SHA 与服务器实际 HEAD 一致；不要盲目在旧 main 上运行，也不要覆盖已有服务器本地修改。后续发布状态以交付记录为准。

## 1. 固定输入与待确认事实

| 项目 | 要求 |
|---|---|
| Python | 独立 3.11 环境 `.venv-m06`，不用 M01 的 3.12 环境 |
| Factory | v0.9.4 / `95ac3f2373b82662c1bd855c284d3379e6a763d3`，工作区无意外修改 |
| 底座 | `Qwen/Qwen3-4B-Instruct-2507` |
| 模型 revision | `cdbee75f17c01a7cc42f958dc650907174af0554` |
| 模板 | `qwen3_nothink` |
| 候选包 | 仓库 `requirements/m06-constraints.txt`，不是完整依赖锁或 GPU 认证 |
| torch 来源 | 官方 cu124 索引先装 2.6.0 三件套，再装其余依赖 |
| 数据 | 已自动验证、已人工复核的 M05 bundle，记录两种证据 |
| 训练入口 | CLI；WebUI 只做回环访问与参数预览 |
| 验证分区 | 固定 validation；不重新随机切分、不使用 test 选模型 |

“CUDA 13”来源待确认：分别报告 `nvidia-smi`、`nvcc --version`（若存在）、`torch.version.cuda`。原 Ollama `qwen3.8:27b` 的真实身份未知，本课不用它训练，也不推断它与 HF 模型的对应关系。

## 2. 按顺序执行的关卡

1. 核对项目 HEAD、未提交修改、服务器硬件和磁盘，确认 M06 文件已交付。
2. 用已安装 Python 3.11 创建新 venv；缺少 3.11 时按教程官方 uv 备用路线处理，不替换系统 Python。
3. 固定 Factory SHA；官方 cu124 安装 torch；同时 `-r` 和 `-c` 使用 constraints 并 editable 安装 Factory/项目。
4. `pip check` 成功后保存 `pip freeze --all`；记录真实 Python 补丁版本与路径。
5. 用新输出路径执行 preflight；失败保留 JSON，不继续下载/加载 4B 试训练。
6. 按固定 repo/revision 执行 `snapshot_download` 到明确 local_dir，保存成功下载记录。
7. 重新 validate M05，检查独立人工审核结论，prepare 新 smoke run。
8. verify 输入，检查 64/32 数量，执行真实 tokenizer 长度检查。
9. 可选 WebUI：只绑定 127.0.0.1，SSH 隧道访问，预览与配置对照后关闭。
10. 同一训练命令块先拒绝已有 adapter/log，再以 pipefail + tee 执行 10 步 smoke，保存实际退出码。
11. smoke 证据完整后 prepare 新 sft run；336/96、3 epoch，预计 126 更新步，实际以日志为准。
12. 检查有限损失、评估与保存、adapter 及训练状态，整理本手册报告模板交接 M07。

所有正式命令与变量初始化以教程为准。不要把这份清单当作可绕过前置检查的独立脚本；必要步骤非零退出立即停止，保留现场。

## 3. 工具接口速查

以下是参数形状，`NEW_*` 和 `*_DIR` 是说明性占位值，执行时替换成教程已定义的绝对路径。

```text
python -m enterprise_ai.training prepare
  --data M05_DIR --model-dir HF_MODEL_DIR
  --run-dir NEW_RUN_DIR --profile smoke|sft
  --baseline-cases M04_CASES_FILE

python -m enterprise_ai.training verify --run-dir RUN_DIR

python -m enterprise_ai.training_checks preflight
  --factory-dir FACTORY_DIR --output NEW_REPORT_FILE

python -m enterprise_ai.training_checks token-lengths
  --run-dir RUN_DIR --output NEW_REPORT_FILE
```

prepare 只创建新的 run，导出 train.yaml、data/train.json、data/validation.json、data/dataset_info.json 和 manifest；不创建 adapter、不训练。报告输出也拒绝覆盖；重试使用新编号，不删除旧报告。

| 返回结果 | 精确含义 |
|---|---|
| manifest.status=prepared | 准备器完成并校验了输入，不是训练已完成 |
| verify 退出 0 | 准备文件符合摘要、计数、当前模板和路径；不验证 adapter |
| preflight passed | 核心版本（含 Gradio）、实际导入 Factory 路径/版本与源码状态、GPU/BF16、微型 NF4 前反向检查通过 |
| token-lengths passed | 报告记录的输入字节快照在本地真实 tokenizer + 固定 LF 模板下无超限或空答案 |
| train 退出 0 | 还需结合日志、实际步数与产物核对训练结果 |

模型结构检查不能证明权重真实性；微型 NF4 probe 不能代替 4B 模型验收；长度检查不能代替 Factory loader 验收。报告中必须分层陈述。

## 4. 必须保留的证据

| 类别 | 保存内容 |
|---|---|
| 代码 | 项目 SHA、Factory SHA、工作区是否修改、配置模板变更说明 |
| 环境 | 系统/GPU/驱动、Python 路径与版本、pip check、freeze、preflight |
| 模型 | repo/revision/local_dir、下载实际结果、模型加载日志 |
| 数据 | M05 manifest、validate 结果、人工报告位置与结论 |
| 输入 | 每个 run 的 manifest、train.yaml、data 三文件、token-lengths JSON |
| 运行 | 完整 train.log、train.exit-code、起止时间、实际步数、采样显存和耗时 |
| 产物 | adapter 配置与权重、checkpoint/训练状态/结果文件清单 |
| 异常 | 首次错误前后日志、失败退出码、失败报告和保留目录 |

本地 reports 和生成数据可能被 Git 忽略，提交文档不会同步服务器实验材料。按你的备份位置另行保存，不把大权重提交到 Git。分享日志前检查 token、私有域名中的凭证、个人路径与业务敏感信息；用一致占位符脱敏以保留路径之间的关系。

不要发送 `.env`、HF token、SSH 私钥、代理账号密码、完整 `env` 输出，也不要把凭证写入任何报告模板。若失败日志含凭证，只提供脱敏副本并在服务器按凭证流程处理。

## 5. 失败后的处理边界

安装失败：保存解析日志，记录失败命令与退出码，核对候选版本。不得移除 constraints、强行忽略依赖或安装无关加速包来掩盖问题。

下载中断：保留 local_dir 和日志。核对同一 repo/revision 后由安装会话恢复；已有目录不是自动可信来源，不能把别的模型复制进去绕过准备器。

OOM/参数实验：不手改 prepared 的 train.yaml。保留失败 run 和原配置，修改有版本记录的模板，prepare 新 run，重新 verify/token-lengths/smoke。模板变化后旧 run 用对应旧代码复核。

中断恢复：先确认旧训练进程状态；已有 adapter/checkpoint 时首次训练保护块会拒绝。v0.9.4 上游可能自动恢复 checkpoint，不能以“未写 resume 参数”断言从头开始。恢复前核对模型、数据、配置、环境、checkpoint 完整性、优化器/调度器状态与目标步数，单独制定恢复方案。不得直接删除 guard 或清空目录。

NaN/Inf、数据数量不符、来源不明、权限或认证错误：停止验收并报告。不得把“有 adapter 文件”写成实验成功；不得把失败记录改为 passed。

## 6. 可复制的跨会话任务说明

```text
请在 Ubuntu 24.04 / RTX 4090D 24GB / 128GB 服务器继续 M06 安装与验收。
遵循 docs/tutorials/06-qwen-finetuning.md 和本运行手册，逐关执行、失败停。
先确认含 M06 的项目 SHA 与服务器工作区状态，不覆盖已有修改。
使用独立 Python 3.11；Factory 固定 v0.9.4 / 95ac3f2373b82662c1bd855c284d3379e6a763d3。
HF 底座固定 Qwen/Qwen3-4B-Instruct-2507 / cdbee75f17c01a7cc42f958dc650907174af0554。
核心版本以 requirements/m06-constraints.txt 为准，属于待实测候选组合。
先装官方 torch 2.6.0 cu124，再同时 -r/-c 安装核心包与 editable 项目。
先保存 pip check/freeze 和 preflight，再固定 revision 下载模型。
M05 数据通过自动校验并确认人工复核后，先做 64/32 的 10 步 smoke。
smoke 通过才新建 336/96 的正式 sft run；预计 126 步但记录实际值。
每次首次训练同一命令块检查 adapter 和日志不存在；pipefail 保留真实退出码。
WebUI 只回环 + SSH 预览；正式训练使用准备器生成的 CLI 配置。
原 CUDA 13 来源及 Ollama qwen3.8:27b 身份待核对，不据此替换训练模型。
请回传下面的报告。只报告实际执行结果；不要发送 .env、token 或未脱敏日志。
```

## 7. 可复制的结果报告

```text
M06 服务器实验报告
报告日期/时区：
执行人或会话：
当前结论：未执行 / 安装失败 / preflight失败 / smoke失败 / smoke通过 / sft完成待M07

【代码与环境】
项目 Git SHA：
项目工作区修改：无 / 有（说明）
Factory Git SHA 与工作区状态：
Ubuntu 版本：
GPU 型号/总显存：
驱动版本与 nvidia-smi CUDA 显示：
nvcc 是否存在及版本：
Python 可执行路径/完整版本/来源：
torch.__version__ / torch.version.cuda：
constraints 文件版本：
pip check 实际退出码与摘要：
pip-freeze-all 文件位置：
preflight 文件位置/status/errors：
实际导入 Factory 版本/路径是否匹配指定 checkout、tracked_clean：
Gradio 实际版本（如使用 UI）：

【模型与数据】
HF repo/revision：
本地模型路径（可脱敏但保持一致）：
下载命令、退出码、下载记录位置：
M05 bundle 位置及 manifest 摘要：
M05 validate 实际命令/退出码：
M05 人工复核报告位置、结论、未决项：

【每个 run 分别填写：smoke / sft】
run 路径/profile：
prepare/verify 实际命令、退出码：
manifest 与 train.yaml 位置：
模型路径、数据路径、输出路径是否一致：
token-lengths status、train/validation count/min/max：
token-lengths checked_artifacts 与本次输入快照摘要：
over_cutoff_indices / empty_answer_indices：
实际启动命令（凭证已脱敏）：
首次启动时 adapter/log 是否不存在：
实际加载的 train/validation 数量：
实际更新步数/epoch：
train.exit-code：
train.log 位置：
起止时间/耗时：
显存最大观察值及采样方式：
训练 loss 和各次 eval_loss（填实际值）：
是否出现 NaN/Inf/OOM/截断/过滤：
adapter 与 checkpoint 文件清单：
是否成功加载 adapter 做生成（未做则写待M07）：

【失败或变更】
首个失败步骤、命令、退出码：
脱敏错误片段（包含错误前后的必要上下文）：
保留的失败目录和报告：
是否修改模板/依赖/数据及原因：
新版本/新run 与旧实验的关系：
是否存在恢复训练需求（尚未执行则明确）：

【证据与下一步】
已执行且有证据的关卡：
未执行/无法执行的关卡及原因：
证据附件或受控存放位置：
尚待核对的问题：
下一步：修复环境 / 重做新smoke / 新sft / M07同底座对照
确认未包含 .env、token、私钥或代理凭证：是 / 尚待脱敏
```

只有完成实际 SFT 且证据齐全，才写“本次训练完成”；仍需 M07 的同底座生成对照才能讨论业务收益。代码侧检查、源码核对和文档准备不计为服务器训练验收。
