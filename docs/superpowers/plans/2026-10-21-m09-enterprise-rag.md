# M09 Enterprise RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Inline TDD; one independent final review.

**Goal:** 把M08文档状态连接到可追溯、可失效、带权限与引用的企业检索教程。
**Architecture:** 当前SQLite快照→固定embedding索引包→Python基线/pgvector→中文关键词与RRF/重排→摘录引用与评估。
**Tech Stack:** Python标准库，候选torch2.8.0/transformers4.57.1/psycopg3.2.10，Postgres16/pgvector0.8.2。
**Spec:** ../specs/2026-10-21-m09-enterprise-rag-design.md

## Global Constraints

- 延续可信CLI与Ubuntu实测分离；本机不下载大模型、不安装/启动平台服务。
- M08旧head更新/删除后旧索引拒绝使用；角色/租户/日期过滤早于证据输出和重排。
- 1024维有限归一化向量、固定身份；fixture不能冒充模型质量或导入真实pgvector空间。
- 每阶段提交含Filelist与验证ledger，远端SHA回读确认后才称同步。

## Review Focus

- 旧包/伪部分导出/未来文档不能绕过当前源摘要与权限。
- 模型identity一致但资产发生变化、维度异常/NaN/零向量必须拒绝。
- PostgreSQL管理员误作读者、RLS上下文自报、同generation篡改不得放行。
- 近似过滤不足与实际执行计划不一致不得报告为精确或已用HNSW。
- 异常题不得从评估分母消失，引用不得引入未检索文本。

### T1 索引与离线检索

Files: rag_models.py、rag_index.py、rag_search.py；test_rag_index.py、test_rag_search.py；configs/retrieval/m09-models.json、requirements/m09-rag.txt；ledger。
Interfaces: snapshot(state,tenant)->dict；build_index(state,tenant,generation,encoder,output)->dict；load_index(path)->dict；search(index,state,query,roles,as_of,mode,k,candidates,encoder,reranker)->dict；evidence_answer(result)->dict。

- [x] RED：完整current快照/墓碑/空租户、SHA与chunk_id篡改、模型空间、来源变更前后、权限与日期、向量/关键词/RRF/引用、重排输出校验。
- [x] 实现固定模型适配（延迟导入）与显式fixture、完整包校验、新输出保护、CLI索引/搜索。
- [x] GREEN：定向单测与30篇离线检索演示；模型实测边界写明。
- [ ] 提交T1和Filelist并同步。

### T2 PostgreSQL检索契约

Files: rag_pg.py；infra/postgres/migrations/020-m09-rag.sql；test_rag_pg.py。
Interfaces: load(settings,collection,index,state)；retrieve(settings,reader,collection,index,state,query,roles,as_of,method,encoder)。消费T1受检索引与embedding接口。

- [ ] RED：参数绑定、模型/fixture拒绝、身份与版本检查、同代冲突、exact/HNSW配置与诊断、全量替换事务。
- [x] 实现独立schema/RLS、管理员导入与受限reader查询、CLI和真实服务器核对入口。
- [ ] GREEN：可执行SQL契约测试；不宣称真实数据库已测。提交同步并更新Filelist。

### T3 评估、演示与教程

Files: rag_eval.py、test_rag_eval.py、data/samples/m09-retrieval.json；教程09、runbook、数据卡、validation；README/ROADMAP/CHANGELOG/VERSION/Filelist。
Interfaces: evaluate(index,state,cases,encoder,output,mode)->dict；run_demo(output)->dict；CLI eval/demo。

- [x] RED→GREEN：doc级Recall/MRR、拒绝/失败分母、引用绑定、更新删除导致旧包失败及重建恢复。
- [x] 编写Ubuntu真实模型/迁移/检索/比较步骤与报告模板；离线不冒充实测。
- [x] 完成全量验证、静态命令与链接、独立代码审阅和回归修复。
- [ ] 同步全部成果与Filelist，回读远端并创建依赖M08的草稿PR。

## 2026-10-22 本地候选收尾

T1已提交4373618。T2/T3本地实现及教材已提供，217项全量回归通过，独立review无Critical/Important，两项Minor已修订并定向复验。未勾选的提交/同步/PR项是有意保留：本次未执行发布流程；真实数据库与BGE按runbook在Ubuntu验收，不把本地契约测试冒充真实通过。T2原始RED在旧会话记录，本轮从已有实现接续，未重演删除实现的RED。
