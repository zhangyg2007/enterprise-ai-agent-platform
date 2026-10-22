# 企业 AI Agent 开发实战

面向具备 Python 编程、Linux 与 Hadoop 基础的学习者，用同一家虚构企业贯穿数据工程、Qwen 微调、企业知识库、LangGraph 1.x 应用开发和 FDE 项目交付。

**当前课程批次：0.1.4.0，当前分支已提供 M01–M07 代码与教程。发布状态以 GitHub PR 为准，Ubuntu 服务器验收待执行。** 已提供数据库配置、14 张业务表、两租户合成数据、文件事件治理、Ollama评估工具、480条微调样本及单卡 Qwen 训练准备与检查工具、同底座评估/CPU 导出护栏及本机 API 探针；未运行真实模型或完成 Ubuntu 集成验证。

## 工作方式

- Windows 本地：编写代码、配置模板和中文教程，进行静态检查，通过 GitHub 同步。
- Ubuntu 服务器：由学习者执行安装、数据加载、模型训练、应用运行和实验验收。
- 服务器信息：Ubuntu 24.04 Server amd64、128GB 内存、RTX 4090D 24GB；用户报告 CUDA 13，具体驱动、Toolkit、运行时分别核对。
- 已有模型：Ollama 中的 `qwen3.8:27b`，按用户提供的名称记录，模型实际架构、量化格式和来源尚待检查。
- 目标仓库：https://github.com/zhangyg2007/enterprise-ai-agent-platform 。当前为公开仓库，仅提供合成教学数据。

## 从这里开始

1. 阅读 [课程路线与模块交付计划](docs/ROADMAP.md)。
2. 阅读 [已确认的平台设计](docs/superpowers/specs/2026-10-15-enterprise-ai-platform-design.md)。
3. 在服务器执行 [第 0 课：只读环境检查](docs/tutorials/00-server-inventory.md)，确认真实配置。
4. 取得包含 M01–M02 的版本后，按 [第 1 课：Ubuntu 基础平台](docs/tutorials/01-ubuntu-platform.md) 安装与启动。
5. 按 [第 2 课：企业数据库与合成数据](docs/tutorials/02-enterprise-data.md) 生成数据、查询并验收。
6. 取得包含 M03 的版本后，按 [第 3 课：数据治理](docs/tutorials/03-data-governance.md) 学习清洗、增量、坏数据隔离与删除传播。本课不依赖 PostgreSQL 已启动，但需要 M02 的 tiny 数据文件。
7. 取得包含 M04 的代码后，按 [第 4 课：模型调用与基线](docs/tutorials/04-model-baseline.md) 核对已有 Ollama 模型，执行两题冒烟与 16 题公开开发集评估；本课不依赖数据库服务。
8. 取得包含 M05 的代码后，按 [第 5 课：微调数据工程](docs/tutorials/05-finetune-data.md) 生成480条标注，核对分组切分、两种导出格式与人工复核表；本课不安装训练环境。

9. 取得包含 M06 的代码后，按 [第 6 课：Qwen 单卡微调](docs/tutorials/06-qwen-finetuning.md) 准备独立训练环境、固定模型与数据快照，先做小规模试跑，再训练正式 adapter。安装与实测问题可使用[跨会话交接手册](docs/runbooks/m06-server-handoff.md)。

10. 阅读 [第 7 课：评估、导出与模型服务](docs/tutorials/07-evaluation-serving.md)，完成同精度对照、导出回归和本机服务探针；填写 [M07 交付记录](docs/runbooks/m07-model-delivery.md)。

第 0–7 课已撰写。M04–M05 已在 [PR #3](https://github.com/zhangyg2007/enterprise-ai-agent-platform/pull/3) 合并；M06 是否已进入 main，请查看对应 PR 和代码文件，不能仅凭 VERSION 判断。M07 位于开发分支，合并状态以 PR 为准。M08–M13 尚待补齐。文件用途见 [Filelist.txt](Filelist.txt)，M07 进度见 [执行记录](docs/progress/2026-10-20-m07-execution.md)。

## 本批文件导航

| 文件 | 作用 |
|---|---|
| [compose.yaml](compose.yaml) | PostgreSQL 16 + pgvector 0.8.2，固定镜像摘要，仅绑定回环地址 |
| [初始化 SQL](infra/postgres/init/10-schema.sql) | 业务表、组合外键、只读权限与行级隔离 |
| [数据生成器](src/enterprise_ai/data.py) | tiny / standard 两档、固定种子、严格校验、SHA256 摘要 |
| [数据库工具](src/enterprise_ai/db.py) | health / load / demo / verify；导入事务与幂等检查 |
| [数据字典](docs/data-dictionary.md) | 实体、字段、关系和业务口径 |
| [M01–M02 验证记录](docs/validation/2026-10-15-m01-m02.md) | 已执行检查和未执行的服务器验证 |
| [M03 治理工具](src/enterprise_ai/governance.py) | 严格事件校验、隔离区、SQLite 事务、不可变快照 |
| [M03 示例与验收](src/enterprise_ai/governance_demo.py) | 两批合成事件、独立临时目录重放验收 |
| [M03 验证记录](docs/validation/2026-10-16-m03.md) | 文件 ETL 测试与独立审阅结果 |
| [M04 Ollama 适配器](src/enterprise_ai/ollama.py) | 模型清单、非流式请求、超时与协议校验 |
| [M04 基线入口](src/enterprise_ai/baseline.py) | 严格字段校验、固定提示词、inventory / evaluate |
| [M04 公开教学题集](data/samples/m04-intents.jsonl) | 16 道 CRM/HCM/SCM、缺信息与拒答题 |
| [M04 验证记录](docs/validation/2026-10-17-m04.md) | 离线测试与服务器待验收边界 |
| [M05 数据工具](src/enterprise_ai/finetune_data.py) | 分组生成、泄漏检查、双格式导出和验证 |
| [M05 数据卡](docs/data-cards/m05-intents.md) | 来源、标注、数量、适用范围与限制 |
| [M05 验证记录](docs/validation/2026-10-17-m05.md) | 真实文件生成、离线测试与独立审核 |
| [M01–M05 工作总结](docs/progress/2026-10-17-m01-m05-summary.md) | 已交付内容、问题修复、发布状态与下一步 |
| [M06 训练配置](configs/training/m06-smoke.yaml) / [正式配置](configs/training/m06-sft.yaml) | 固定 4B 底座、4bit QLoRA、10 步试跑与 3 epoch 实验 |
| [M06 版本来源](configs/training/m06-versions.json) / [核心约束](requirements/m06-constraints.txt) | 固定源码、模型 revision 与候选依赖；不是 GPU 兼容认证 |
| [M06 准备器](src/enterprise_ai/training.py) / [检查器](src/enterprise_ai/training_checks.py) | 隔离数据快照、配置摘要、GPU 小探针及真实模板长度检查 |
| [M06 验证记录](docs/validation/2026-10-19-m06.md) | 离线验证证据、独立审核与服务器待验收清单 |

本项目是教学平台。数据库管理员仅用于初始化、导入和验收；三条查询示例使用受限读者账号。尚无 HTTP 认证、生产用户体系或正式写入流程。

## 服务器上的实验顺序

完成第 1 课安装和配置后，在 Ubuntu 仓库根目录、已激活的虚拟环境中执行：

```bash
python -m enterprise_ai.db health
python -m enterprise_ai.data generate --profile tiny --seed 42 --output data/generated/tiny.json
python -m enterprise_ai.data validate data/generated/tiny.json
python -m enterprise_ai.db load data/generated/tiny.json
python -m enterprise_ai.db demo --reader a
python -m enterprise_ai.db demo --reader b
python -m enterprise_ai.db verify data/generated/tiny.json
```

生成器拒绝覆盖已有文件；已有 JSON 时直接 validate。相同数据集重复 load 会校验数据库当前内容并返回 unchanged；不同数据集会被拒绝，不自动清空现有数据库。服务器运行失败时先按教程定位，不删除卷重试。

无数据库测试使用 `python -m unittest discover -v`。它验证生成器和配置逻辑，不能代替上面的真实数据库 verify。

## 学习主线

企业模拟数据 → 数据清洗与质量检查 → 模型调用与评估基线 → LLaMA-Factory 微调 → 文档处理与知识库 → LangGraph 工作流 → CRM/HCM/SCM 应用 → 部署与 FDE 交付。

三种能力有明确分工：

| 能力 | 负责的问题 | 示例 |
|---|---|---|
| SQL / 业务工具 | 当前业务事实与确定性计算 | 库存、订单金额、本人假期余额 |
| RAG 知识库 | 带来源和版本的文档知识 | 报价政策、员工制度、采购流程 |
| SFT 微调 | 任务行为与输出习惯 | 意图分类、字段提取、结构化输出 |

最终示例为销售报价助手、员工服务助手、采购补货助手。首版聚焦可复现的单机教学平台，生产化能力作为后续模块逐步添加。

## 验证原则

- 分开记录：本地静态检查、服务器集成测试、GPU 实验、真实模型质量评估。
- 每章提供操作位置、前置条件、命令、预期结果、常见错误、恢复办法和实验题。
- 测试未执行标注“未执行”；计划中的指标标注“目标”；不以模拟测试替代模型实测。
- 模型权重、训练输出、数据库卷、真实业务数据与凭证不进入 Git。
- 当前检查结果见 [M01–M02 验证记录](docs/validation/2026-10-15-m01-m02.md)；设计阶段的历史记录见 [首批文档验证记录](docs/validation/2026-10-15-planning-review.md)。

## M07 文件导航

| 文件 | 用途 |
| --- | --- |
| [交付准备与导出](src/enterprise_ai/model_delivery.py) | 固定来源、资产指纹、推理配置、新目录 CPU 导出 |
| [评估与比较](src/enterprise_ai/model_eval.py) | 完整题集记录、指标重算、同精度实验比较 |
| [API 探针](src/enterprise_ai/model_api.py) | 本机鉴权、模型别名与单题响应契约检查 |
| [M07 验证记录](docs/validation/2026-10-20-m07.md) | 离线测试、审核与服务器待验收边界 |
