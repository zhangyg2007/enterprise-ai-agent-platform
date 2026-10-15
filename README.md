# 企业 AI Agent 开发实战

面向具备 Python 编程、Linux 与 Hadoop 基础的学习者，用同一家虚构企业贯穿数据工程、Qwen 微调、企业知识库、LangGraph 1.x 应用开发和 FDE 项目交付。

**当前阶段：M01–M02 代码与教程已撰写，服务器验收待执行。** 已提供数据库配置、14 张业务表、两租户合成数据生成器和查询/检查入口；未部署应用、运行 GPU 训练或完成服务器集成验证。

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
4. 等本批代码发布后，按 [第 1 课：Ubuntu 基础平台](docs/tutorials/01-ubuntu-platform.md) 安装与启动。
5. 按 [第 2 课：企业数据库与合成数据](docs/tutorials/02-enterprise-data.md) 生成数据、查询并验收。

第 0–2 课已撰写。M03–M13 随后续模块补齐，当前不提供这些模块的运行入口。

## 本批文件导航

| 文件 | 作用 |
|---|---|
| [compose.yaml](compose.yaml) | PostgreSQL 16 + pgvector 0.8.2，固定镜像摘要，仅绑定回环地址 |
| [初始化 SQL](infra/postgres/init/10-schema.sql) | 业务表、组合外键、只读权限与行级隔离 |
| [数据生成器](src/enterprise_ai/data.py) | tiny / standard 两档、固定种子、严格校验、SHA256 摘要 |
| [数据库工具](src/enterprise_ai/db.py) | health / load / demo / verify；导入事务与幂等检查 |
| [数据字典](docs/data-dictionary.md) | 实体、字段、关系和业务口径 |
| [M01–M02 验证记录](docs/validation/2026-10-15-m01-m02.md) | 已执行检查和未执行的服务器验证 |

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
