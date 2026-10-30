# 企业 AI Agent 课程总状态

更新时间：2026-10-29（Asia/Shanghai）。结论：**第0–13课均有教材和本地实验，但整个课程尚不能认定为完全完成或服务器验收通过。** 本表把文件、实验实现、实际验收和GitHub发布分开；证据见[完整性审核](../validation/2026-10-29-m10-m13-audit.md)及[本轮接续](2026-10-29-m10-m13-closure.md)。

| 模块 | 教材/本地内容 | 待完成或待实测 |
|---|---|---|
| M00 环境盘点 | [第0课](../tutorials/00-server-inventory.md)提供只读检查 | Ubuntu驱动、CUDA、工具链、实际Ollama身份待记录 |
| M01 平台基础 | [第1课](../tutorials/01-ubuntu-platform.md)与配置 | Ubuntu安装和服务健康待学习者验收 |
| M02 企业数据 | [第2课](../tutorials/02-enterprise-data.md)、合成数据、数据库结构 | 真实PG加载、隔离、查询和对账待验收 |
| M03 数据治理 | [第3课](../tutorials/03-data-governance.md)、本地治理实验 | Ubuntu实操/样本检查待验收；不是实际企业CDC |
| M04 模型基线 | [第4课](../tutorials/04-model-baseline.md)、公开题集和调用工具 | 现有Ollama模型身份、请求和质量待实测 |
| M05 微调数据 | [第5课](../tutorials/05-finetune-data.md)、480条合成标注与分组切分 | 原计划2000–5000条扩展、工具调用数据专题和人工审核未完成 |
| M06 微调 | [第6课](../tutorials/06-qwen-finetuning.md)、候选配置与检查 | GPU兼容、真实冒烟/训练和指标待验收 |
| M07 评估与服务 | [第7课](../tutorials/07-evaluation-serving.md)、对照/导出/服务探针 | 真实训练资产、模型效果、导出与服务回归待验收 |
| M08 文档 | [第8课](../tutorials/08-document-engineering.md)、文档生成/解析/ACL | Ubuntu三格式、真实文档解析和抽查待验收 |
| M09 RAG | [第9课](../tutorials/09-enterprise-rag.md)、检索和引用 | 真实BGE、PostgreSQL/pgvector、RLS/HNSW及召回待验收 |
| M10 编排 | [第10课](../tutorials/10-langgraph-basics.md)、受控只读图；本轮新增高层和异步合成示例 | 示例使用脚本化模型；真实Ollama/数据库/RAG仍待验收 |
| M11 业务 | [第11课](../tutorials/11-enterprise-workflows.md)、三业务审批/幂等；格式2 SQLite受保护实验绑定引用并拒绝旧写入口 | 旧格式和PG没有该门禁；信任本地进程与文件所有者，跨库非原子，真实审批/PG/ERP未验收 |
| M12 API与运维 | [第12课](../tutorials/12-api-operations.md)、服务/备份；补线程摘要、租户指标及格式2三库备份 | 尚无制度感知HTTP业务入口；文档及索引须另备份，日志与账本非同一事务；生产SSO/TLS、真实PG/systemd及恢复待实现或验收 |
| M13 FDE交付 | [第13课](../tutorials/13-fde-delivery.md)、七份模板；本轮新增M09引用冻结及四组采集候选 | 业务事实由操作者提供且未自动核验；真实模型四组采集、人工评分、客户演示和签字待执行 |

## 本轮已做与验证边界

- 合成三案例：真实M09索引与M11持久图串联；引用、隔离、拒绝、幂等、恶意文档、制度变化和恢复有本地定向测试。演示中的经理与数据是模拟，真实模型模式也未在服务器运行。
- 高层Agent：使用真实LangChain `create_agent`与`invoke/ainvoke`，模型响应是脚本替身，只提供本人只读工具。
- 四组采集：冻结相同题集、事实和检索引用，保存原始回答/错误及时间；截断、超时保留在分母。真实M07 release、GPU、分数和人工评审尚未运行。
- 进程内指标按可信租户隔离，业务线程可用摘要与HTTP记录关联；摘要不是加密，日志与业务事务并非原子。
- 本轮独立review已发现并修复模拟审批参数、失败恢复、设备选择、冲突恢复和冻结文件大小等问题；最终全量验证与复核结果以接续记录为准。
- 2026-10-29制度到期修复后的本地全量回归：**334项通过、347.278秒**；原始日志保存在被忽略的`reports/local/guarded-expiry-final-full.txt`。2026-10-30重新独立复核，55项定向通过且未发现可行动回归；真实服务器未验收。

## 发布与下一步

截至2026-10-30核查，GitHub远端main提供第0–10课，M11在草稿PR #9；M12–M13及本轮补充整理于`codex/m12-m13-delivery`候选。实际发布和合并状态应以GitHub为准，不能将草稿PR视为已合并。

优先完成：将制度门禁扩展到正式PG和HTTP服务；设计跨系统版本一致性与恢复；核验业务快照来源并完成四组真实采集；按服务器手册逐项执行PG/RLS/checkpoint、GPU与模型验收；补M05数据扩展及人工审核。待相应质量门禁通过并确认GitHub可用，再同步已验证版本；不默认合并或部署。
