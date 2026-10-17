# M03 数据治理设计

日期：2026-10-16。依据：已确认课程路线的 M03，以及用户“确认发布，并继续下一部分”。本章细化已授权范围，不新增服务或本地环境。

## 选择与边界

采用 Python 标准库 + SQLite 本地文件事务，模拟 CRM 客户变更事件的数据治理。相比 PostgreSQL staging，无需先完成服务器数据库验收；相比 Airflow/Spark，不引入调度或分布式依赖。SQLite 只保存本章 ETL 状态，不替换 M02 的 PostgreSQL 业务库。本章在 Ubuntu 操作，本地使用现有 Python 做临时文件测试。

数据流：M02 tiny JSON → 合成客户事件 JSONL → 不可变 raw 批次 → SQLite 去重/校验/版本判断 → clean 客户快照和 service 租户汇总 → 质量报告与文件摘要。所有实验输出在 data/generated 下，不提交真实业务数据。

## 契约

事件字段固定：event_id、tenant_id、customer_id、version、op、payload。event_id 为全源唯一字符串；实体键是 (tenant_id, customer_id)，version 是每实体递增正整数，不以最大时间戳过滤迟到数据。op 为 upsert 或 delete；upsert payload 仅 name 和 owner_employee_id，delete payload 必须 null。租户和员工引用来自 M02 快照（固定摘要），清洗只去掉名称首尾空白并拒绝空名称、控制字符、超长文本。版本与 ID 拒绝 bool/浮点/字符串隐式转换。

同 event_id 与相同规范 JSON 为重复；同 ID 不同内容隔离为冲突。同实体当前版本不同事件 ID 也隔离为版本冲突（即使业务值相同）；更低版本为 stale，不覆盖最新状态。先检查事件 ID，再比较实体版本。delete 保留 tombstone 和版本，删除会传播到 clean/service；更高版本 upsert 可以恢复。不存在实体的合法 delete 仍保留 tombstone。

事件 ID 登记以结构及租户/员工引用校验通过为前提；即使因当前版本冲突而隔离，该 ID 仍登记，不能换内容重用。相同有效事件再次出现计 duplicate（原隔离记录保留）。未通过格式/引用校验的行只有隔离记录；改正数据建议使用新事件 ID，避免混淆审计。

非法行（含非法 JSON）进入 quarantine，保留源文件摘要、行号、错误码和原始文本；其他合法行继续处理。非法记录也计入 report，不能静默丢弃。有限文件大小和单行长度，超限整批失败且不推进状态。输入必须 UTF-8，可接受 CRLF；拒绝重复 JSON 键、NaN/Infinity。批次身份采用原始字节 SHA256，文件名不构成身份。

## 持久化与恢复

SQLite 中 batches、events、entities、quarantine 保存导入凭据、合法事件审计、当前状态和坏数据。BEGIN IMMEDIATE 串行化写入；事件、实体、隔离记录、批次统计同一事务提交。事务失败不留半批记录。批次重复直接返回原统计并标记 unchanged，不重复计数。每次运行核实固定 seed 摘要与 pipeline schema 版本，不自动迁移不匹配的实验目录。

raw 文件通过排他创建保存并核实摘要。raw 保存先于事务，异常可能留下未处理原件，重新执行可安全恢复。SQLite 提交和文件发布不是同一事务：导出写到新的不可变快照目录，包含 manifest 与哈希，最后原子替换 CURRENT.json 指针；失败后旧指针仍可用，重跑已提交批次会重新导出恢复。读者只从 CURRENT.json 读取完整快照。单工作目录只允许一个 CLI 进程，通过排他锁文件拒绝并发；崩溃残留锁需确认进程已停止后人工处理。此模式不保证网络文件系统或断电时的持久性。

## 验收

提供 demo 命令从 tiny 创建两个输入批次：初始40客户 + 空白清洗 + 重复 + 非法租户/员工/JSON；增量含更新、删除、迟到、冲突与重复。两个租户 ID 相同的客户互不覆盖。verify 在临时目录重放、重跑，核对删除传播、摘要、报告和幂等性；测试模拟处理异常回滚与导出异常后的恢复。

教程包含执行命令、精确预期、数据层说明、事务与指针提交边界、隐私清理注意、排障和练习答案。不得把这套合成文件事件宣称为生产 CDC 或 PostgreSQL 同步；对接真实源系统、删除后隐私擦除、权限访问、调度和规模优化留作扩展。
