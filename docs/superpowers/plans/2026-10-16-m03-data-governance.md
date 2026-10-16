# M03 Data Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 提供能演示清洗、隔离、去重、增量和删除传播的数据治理实验及中文教程。

**Architecture:** 独立模块 governance.py 管理标准库 SQLite 事务与不可变文件快照；governance_demo.py 创建可审查合成事件及验收。现有 M02 数据契约保持不变。

**Tech Stack:** Python 3.11–3.13 标准库，sqlite3、hashlib、json、unittest。

## 文件和步骤

- [x] T1 tests/test_governance.py：先写失败测试。生成 tiny dataset，调用 run_batch(seed, input, workspace)，断言首批 applied、同批 unchanged、同事件 duplicate、不同事件同版本 conflict、低版本 stale、delete 去除服务记录且 tombstone 防旧事件复活。执行 `python -B -m unittest tests.test_governance -v` 确认缺实现而失败。
- [x] T2 src/enterprise_ai/governance.py：实现严格事件解析、固定 seed 引用、SQLite schema 与事务、raw 保存、CURRENT 快照发布、排他工作目录锁和 CLI run/inspect。非法行隔离；失败不提前提交。实现后运行 T1，补充事务失败回滚、导出失败重跑恢复、跨租户、重复键、非法数值和输出摘要测试。
- [x] T3 src/enterprise_ai/governance_demo.py：提供 `python -m enterprise_ai.governance_demo create --seed PATH --output DIR`，排他创建输入目录；`verify --seed PATH` 在临时目录重放实验并断言报告和最终39客户。测试 demo 命令与验收入口。
- [x] T4 docs/tutorials/03-data-governance.md：按冻结 CLI 与事件契约撰写教程、输出数值、故障恢复、练习及答案；更新 README/ROADMAP 导航与范围说明。文档独立代理完成，只改教程。
- [x] T5 完整 unittest、AST、文档链接和 Bash 语法检查；本地仅既有 Python 和内置 SQLite 临时文件测试，不安装依赖或启动 PostgreSQL 服务。验证报告保存 docs/validation/2026-10-16-m03.md。
- [x] T6 独立只读审阅代码与文档；修复后定向复测。保存本地提交，保持本批与 M01–M02 的发布记录分离。

## 关键接口与断言

`run_batch(seed_path: Path, input_path: Path, workspace: Path) -> dict` 返回批次计数和 unchanged；`inspect_workspace(workspace: Path) -> dict` 校验 CURRENT 指向 manifest/clean/service/report 的内容摘要并返回读取结果。CLI 成功退出0；格式、引用、版本冲突计入 quarantine 后批次仍成功；文件级错误退出1，报告不声称数据全合格。

测试最小示例：事件 A upsert 客户(1,1)v1；事件 B delete (1,1)v2；事件 C upsert (1,1)v1 → stale；事件 D upsert (2,1)v1 → 不影响租户1。最终仅租户2客户保留。JSON 精确重复去重，bool version 必须隔离。不同 seed 重用 workspace 必须拒绝。

范围自查：满足路线图 M03 的可重复 ETL、质量报告、数据清单、坏数据隔离与删除传播；不引入真实 CDC、调度服务或未授权服务器操作。
