# M08 Document Engineering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Follow TDD and independent review.

**Goal:** 交付可追溯、可更新与删除、带权限元数据的企业文档处理教程。

**Architecture:** 生成器→受检源字节→解析结构块→确定性切块→SQLite版本事务→按租户/角色/有效期导出；M09再做embedding和检索。

**Tech Stack:** Python标准库；pypdf 6.10.0、python-docx 1.2.0、reportlab 4.4.9可选格式依赖。

**Spec:** ../specs/2026-10-21-m08-document-engineering-design.md

## Global Constraints

- 30/300/1000篇合成数据，两个租户，三个领域；不得宣称真实制度或知识覆盖。
- 本地仅用现有运行库，不安装平台服务/GPU；真实Ubuntu验收单列。
- source字节有界且摘要校验，catalog显式delete；失败全批回滚，输出独占。
- 每次可验证任务提交与GitHub同步必须包含Filelist新增文件说明和进度证据。

## Review Focus

- 源路径/符号链接/ZIP成员逃逸：读取前拒绝，禁止获取外部资源。
- ACL与有效期只改元数据：仍须新版本并撤销旧块，不能被内容相同误判幂等。
- 解析失败/空页/不支持的DOCX内容：明确失败，禁止静默发布不完整索引。
- 部分批次成功后另一项失败：完整事务回滚，不能误删其他租户或回退历史。
- 解析器/切块配置改变：pipeline_id变化，同版本不得当作原实验重跑。

### T1 样例与解析

Files: src/enterprise_ai/document_samples.py、document_parse.py；tests/test_document_samples.py、test_document_parse.py；requirements/m08-documents.txt；data/samples/documents/三个Markdown；docs/data-cards/m08-documents.md。

Interfaces: generate_corpus(output,count=30,formats='mixed')->dict；parse_document(raw,suffix)->list[dict]。字段与限制按Spec。

- [x] RED：生成目录保护/30篇元数据/两租户/确定性；解析MD围栏、UTF8/NUL、真实PDF/DOCX、空/坏/超限文档。
- [x] 实现纯标准库MD路径及延迟导入的格式库路径；文档说明不支持布局。
- [x] GREEN：定向核心测试与预置依赖格式测试；PDF视觉通过，DOCX缺少LibreOffice版面未验收，已明确记录。
- [x] 提交并同步T1与Filelist：3ba1ae6。

### T2 切块与版本流水线

Files: src/enterprise_ai/document_pipeline.py；tests/test_document_pipeline.py。

Interfaces: chunk_segments(metadata,segments,max_chars=400,overlap=40)；ingest(catalog_path,source_root,state_path,report_path,max_chars=400,overlap=40)；export_chunks(state_path,output_path,tenant_id,roles,as_of)。

- [x] RED：chunk覆盖/边界/稳定ID；摘要快照/路径拒绝；重跑、冲突/旧版本、更新/删除；ACL/时间/租户隔离；失败批次原子性与报告；不同pipeline_id拒绝幂等。
- [x] 实现schema严格验证、SQLite版本/head/chunks事务与独占导出。
- [x] GREEN：定向测试、30篇三格式真实集成（只用预置库），保留报告。
- [x] 提交并同步T2与Filelist：aa6c479。

### T3 增量实验与教程

Files: src/enterprise_ai/document_demo.py；tests/test_document_demo.py；docs/tutorials/08-document-engineering.md；docs/runbooks/m08-document-handoff.md；docs/validation/2026-10-21-m08.md；进度ledger与导航/版本。

- [x] RED→GREEN：30篇基线、幂等、变更内容/ACL、删除、坏摘要回滚与租户角色导出断言。
- [x] 完成第8课、质量模板和M09契约说明；更新Filelist/README/ROADMAP/CHANGELOG/VERSION。
- [x] 全量195项通过、格式集成、文档语法链接检查；独立审阅唯一P2按解析/事务回归RED→GREEN修复，无延期Minor。
- [ ] 同步最终提交与Filelist，回读GitHub SHA；创建依赖M07的草稿PR，不合并/部署。
