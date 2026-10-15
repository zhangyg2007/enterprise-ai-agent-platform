# 第 2 课：生成企业样例数据、导入并验证权限

本课对应 M02。你将把 CRM、HCM、SCM 三类业务数据连成一个可查询的实验数据库，完成三项业务问答：客户累计订单金额、本人年假余额、指定产品各仓库可用库存。最后使用真实数据库验证权限和数据一致性。

**本文所有操作命令都在 Ubuntu 服务器执行。** 先完成[第 1 课](01-ubuntu-platform.md)的服务器验收，确认本批文件已经发布到指定仓库的 `main`，且 `python -m enterprise_ai.db health` 成功。本课不安装 PyTorch，不改动现有 Ollama、驱动或 CUDA。

本教程描述预期行为，没有代替你在服务器执行。代码在本地通过某项检查，不等于服务器上的 PostgreSQL 已通过验证。

## 1. 本课数据如何模拟企业

两个租户代表两个独立组织，分别为 `tenant_id=1` 和 `tenant_id=2`。它们可以都有客户 1、员工 1、产品 1，因此完整身份必须包含租户编号。

| 领域 | 本课要回答的问题 | 主要数据 |
|---|---|---|
| CRM，客户关系 | 客户 1 有多少订单，总金额多少？ | 客户、销售订单、订单明细 |
| HCM，人力资源 | 当前登录员工 2026 年还有多少假期？ | 员工、部门、年假余额、请假申请 |
| SCM，供应链 | 产品 1 在各仓库有多少可用库存？ | 产品、供应商、仓库、库存、采购订单 |

客户负责人关联员工；销售明细关联产品；产品关联供应商；库存把产品与仓库连接起来。这些关系让后续智能体可以查实际数据，而无需把动态事实记在模型参数里。字段、主外键和关系图见[数据字典](../data-dictionary.md)。

本批数据完全合成，姓名使用 `Employee 1-1` 之类标签，邮箱使用 `example.com`。年假额度、价格、采购成本比例、供应商交期都是教学设定，不是任何企业的真实制度或商业建议。固定业务时点为 **2026-10-15**；电脑日期变了，本批样例的时点不会自动变化。

## 2. 数据规模与可复现性

`tiny` 每个租户包含 20 个客户、30 名员工、40 个产品、100 张销售订单、30 张采购订单和 5 个供应商。每租户还有 4 个部门、2 个仓库，每名员工有一份 2026 年余额和 3 条申请，每个产品在两个仓库各有一条库存快照。

| 表 | tiny，两租户合计 | standard，两租户合计 |
|---|---:|---:|
| `core.tenants` | 2 | 2 |
| `hcm.departments` | 8 | 8 |
| `hcm.employees` | 60 | 1,000 |
| `hcm.leave_balances` | 60 | 1,000 |
| `hcm.leave_requests` | 180 | 3,000 |
| `scm.suppliers` | 10 | 100 |
| `scm.products` | 80 | 4,000 |
| `scm.warehouses` | 4 | 4 |
| `scm.inventory` | 160 | 8,000 |
| `crm.customers` | 40 | 2,000 |
| `crm.sales_orders` | 200 | 40,000 |
| `crm.sales_order_items` | 401 | 79,856 |
| `scm.purchase_orders` | 60 | 10,000 |
| `scm.purchase_order_items` | 121 | 20,083 |
| **总计** | **1,386** | **169,053** |

上表是本批生成器、`seed=42` 的本地内存生成与校验结果，**不是服务器导入结果**。销售和采购订单各有随机的 1–3 行明细；改变种子或生成器版本，明细行数和总行数可能改变。服务器运行时，以命令输出的 `rows` 和实际提交号为准，不硬编码某个摘要值作为跨版本标准。

同一代码版本、profile 和 seed 用于重复生成同一数据集。输出的 `sha256` 是对规范化 JSON 内容计算的摘要；`generate` 和 `validate` 对同一文件应报告相同摘要。它不同于对含缩进、换行的原始文件直接运行 `sha256sum` 的结果。摘要用于识别数据集，不是密码、签名或权限凭证。

## 3. 生成并检查 tiny 数据集

### 3.1 回到项目环境

```bash
cd "$HOME/projects/enterprise-ai-agent-platform"
. .venv/bin/activate
git status --short --branch
git log -1 --format='%h %s'
sudo docker compose ps
python -m enterprise_ai.db health
```

记录提交号。如果健康检查失败，先回到第 1 课排错，不跳过检查直接导入。

### 3.2 首次生成

```bash
python -m enterprise_ai.data generate --profile tiny --seed 42 --output data/generated/tiny.json
```

预期命令成功，输出 JSON 摘要，包含 `sha256`、`profile`、`seed` 和各表 `rows`。输出文件保存在 `data/generated/tiny.json`；父目录不存在时由程序创建。

生成器使用独立随机数生成器和固定日期，在内存创建数据并先校验，再写入文件。它使用“新建文件”模式：目标文件已存在会拒绝覆盖。重新开始一个实验时先验证已有文件；需要比较种子时另起文件名，不删除原始证据。

### 3.3 独立校验已有文件

```bash
python -m enterprise_ai.data validate data/generated/tiny.json
```

预期成功并返回与首次生成一致的摘要和行数。`validate` 只检查文件，不连接或写入数据库。它检查的内容包括：

- 顶层结构、14 张业务表的字段集合、字段类型和主键唯一性。
- 两个租户的完整身份与同租户外键；例如订单不能引用另一个租户独有的客户。
- 正金额、正数量、合法状态和日期；库存预留量不超过在库量。
- 每张订单有 1–3 个从 1 开始连续编号的明细；采购明细产品属于订单供应商。
- 已使用年假等于 `approved` 申请的天数合计，`pending` 与 `rejected` 不扣减；员工有对应年度余额。
- 邮箱为合成的 `example.com` 地址；同租户邮箱和 SKU 唯一。

格式通过不等于具备真实业务有效性。例如，这里不计算法定节假日、工龄年假规则或汇率。字段边界和数据库约束的差异见数据字典。

### 3.4 保存校验摘要

验收报告放入已被 Git 忽略的 `reports/local/`，完整生成文件也不提交到仓库：

```bash
umask 077
mkdir -p reports/local
git check-ignore data/generated/tiny.json reports/local/
python -m enterprise_ai.data validate data/generated/tiny.json > reports/local/m02-data-validation.json
```

确认上一条退出码为 0 后再查看报告。生成文件和报告本次虽是合成数据，也应养成将数据与代码分开的习惯；不要把真实企业数据替换进样例后直接上传。

## 4. 导入数据库，并验证重复导入行为

### 4.1 首次导入

```bash
python -m enterprise_ai.db load data/generated/tiny.json
```

预期首次成功输出 `status: loaded`、数据集 `digest` 和各表 `counts`。导入器以管理员身份工作，适用于本教学数据库；业务读者查询使用其他角色。

导入过程先校验文件，在一个事务中取得实验导入锁，再检查 14 张业务表为空；随后按依赖顺序导入，逐表比较实际数据与文件内容，最后写入 `lab.seed_runs` 标记。若中途失败，事务回滚，避免留下部分新数据。管理员应单独运行这套实验，不让其他程序同时修改表。

这里使用固定的表名白名单和数据库驱动处理标识符、数据值，没有让模型决定写入任意表。遇到错误先检查错误类型与私有日志，不使用手工删除业务表、修改摘要记录的方式绕过保护。

### 4.2 再次导入同一文件

```bash
python -m enterprise_ai.db load data/generated/tiny.json
```

预期 `status: unchanged`。这表示数据集摘要匹配，且数据库中 **14 张业务表的全部实际内容** 与文件一致；它不是只比较行数，也不是看到一次导入标记就直接跳过。

如果更换 seed、profile 或修改文件内容后再次导入，会拒绝混入不同数据集。如果数据库被人为改动，即使原摘要标记还在，也会因实际内容不同而拒绝。保留现场，按第 8 节恢复，不删除卷“重试”。

## 5. 使用两种读者身份做三项查询

```bash
python -m enterprise_ai.db demo --reader a
python -m enterprise_ai.db demo --reader b
```

`a` 使用数据库登录角色 `lab_a_reader`，映射到租户 1、员工 1；`b` 使用 `lab_b_reader`，映射到租户 2、员工 1。CLI 的 `--reader` 是教学时选择数据库凭证的开关，不是生产应用中允许用户随意切换身份的机制。

输出是 JSON，顶层有 `reader`、`customer_1`、`my_leave_2026`、`product_1`。查询返回的元组序列在 JSON 中变成二维数组：最外层数组表示多行，内层数组表示一行，字段按下表顺序排列。金额与天数使用字符串，例如 `"20.00"`，用于保留十进制精度。

### 5.1 客户累计订单：`customer_1`

每行四个字段：

| 位置，从 1 开始 | 字段 | 含义 |
|---|---|---|
| 1 | `id` | 当前租户的客户编号，本 demo 固定 1 |
| 2 | `name` | 合成客户名称 |
| 3 | `order_count` | 该客户的订单张数 |
| 4 | `order_amount` | 所有订单明细的数量乘单价后合计，十进制字符串 |

SQL 与源码 `src/enterprise_ai/queries.py` 一致；下面展示驱动参数占位符，不直接粘贴进 `psql` 执行：

```sql
WITH totals AS (
    SELECT tenant_id, order_id, SUM(quantity * unit_price) AS amount
    FROM crm.sales_order_items GROUP BY tenant_id, order_id
)
SELECT c.id, c.name, COUNT(o.id) AS order_count,
       COALESCE(SUM(t.amount), 0)::numeric(16,2) AS order_amount
FROM crm.customers c
LEFT JOIN crm.sales_orders o ON (o.tenant_id, o.customer_id) = (c.tenant_id, c.id)
LEFT JOIN totals t ON (t.tenant_id, t.order_id) = (o.tenant_id, o.id)
WHERE c.id = %s GROUP BY c.id, c.name
```

先把明细聚合为“一张订单一行”，再计数订单。如果把订单直接连接三行明细后 `COUNT(o.id)`，同一订单就会被数三次。两个 `LEFT JOIN` 保留没有订单的客户；此时订单数为 0，`COALESCE` 将空金额转为 0。

该指标包含 `open` 与 `completed` 两种状态，不等于已收款收入，也未处理退货、税额、折扣或币种。金额来自订单明细的单价快照；即使未来产品目录价变化，历史订单金额也不应跟着变。

### 5.2 本人年假：`my_leave_2026`

每行五个字段依次为：`employee_id`（员工编号）、`year`（2026）、`entitled_days`（额度）、`used_days`（已使用）、`remaining_days`（剩余额度）。后三项为精确十进制字符串。

```sql
SELECT employee_id, year, entitled_days, used_days,
       entitled_days - used_days AS remaining_days
FROM hcm.leave_balances
WHERE employee_id = auth.current_employee() AND year = %s
```

员工编号从数据库维护的当前身份取得，不由自然语言请求任意指定。余额使用已批准申请合计；待审批和已拒绝申请保留为业务记录，但不扣减本样例余额。比如额度 20 天、已批准使用 3 天，剩余是 17 天。这个示例用于解释公式，不代表你此次生成的员工 1 一定用了 3 天。

### 5.3 库存快照：`product_1`

每行九个字段，按仓库编号排序：

| 位置 | 字段 | 含义 |
|---|---|---|
| 1 | `product_id` | 产品编号，demo 固定 1 |
| 2 | `sku` | 当前租户的产品编码 |
| 3 | `warehouse_id` | 仓库编号 |
| 4 | `unit` | 数量单位，样例为 `piece` |
| 5 | `on_hand` | 在库数量 |
| 6 | `reserved` | 已预留数量 |
| 7 | `available` | 可用量，`on_hand - reserved` |
| 8 | `reorder_point` | 补货点 |
| 9 | `snapshot_gap` | 快照缺口，`max(补货点 - 可用量, 0)` |

```sql
SELECT i.product_id, p.sku, i.warehouse_id, p.unit,
       i.on_hand, i.reserved, i.on_hand - i.reserved AS available,
       i.reorder_point,
       GREATEST(i.reorder_point - (i.on_hand - i.reserved), 0) AS snapshot_gap
FROM scm.inventory i
JOIN scm.products p ON (p.tenant_id, p.id) = (i.tenant_id, i.product_id)
WHERE i.product_id = %s ORDER BY i.warehouse_id
```

生成器特意让产品 1 在两个仓库的在库量为 0，便于观察缺货分支。`snapshot_gap` 只描述当前快照与补货点的差值，未扣除采购在途、未考虑未来需求、供应交期或跨仓调拨，不能直接作为采购建议。采购订单的 `received` 状态也不自动推动本课库存变化；库存流水和写入工作流在 M11 扩展。

### 5.4 参数和十进制数的意义

源码通过 `conn.execute(CUSTOMER, (1,))` 传递客户编号；SQL 中的 `%s` 交由驱动绑定，不使用字符串拼接或 f-string 填值。不要给 `%s` 手动加引号。[Psycopg 参数绑定说明](https://www.psycopg.org/psycopg3/docs/basic/params.html)

JSON 中的金额是固定两位小数的字符串；数据库使用 `numeric`；Python 核对使用 `Decimal`，避免二进制浮点数的表示误差。可以单独观察：

```bash
python -c "from decimal import Decimal; print(Decimal('0.10') + Decimal('0.20'))"
```

预期结果为 `0.30`。从字符串构造 `Decimal`，不要先转成 `float` 再转换。[Python Decimal 说明](https://docs.python.org/3.12/library/decimal.html)

## 6. 权限来自数据库登录身份

`auth.reader_context` 保存数据库角色与租户、员工的对应关系。`auth.current_tenant()` 和 `auth.current_employee()` 使用 **`session_user`** 查这张受保护的映射表。普通读者不能直接读取或修改映射；设置自定义会话变量 `app.tenant_id` 不会改变它。

14 张业务表启用行级安全策略（RLS）。普通业务数据按租户过滤；员工、年假余额和请假申请还附加“只能看到本人”的限制。两个条件同时生效，所以读者既不能跨租户，也不能查看同租户其他员工的这些 HCM 记录。

读者只有业务表的 `SELECT` 权限，不是表所有者，没有超级用户、绕过 RLS 或创建角色的能力。默认只读事务是额外保护；即便取消该会话默认值，写入权限仍应被拒绝。管理员拥有更高权限，不能拿管理员查询结果证明普通用户隔离正确。RLS 的角色与所有者行为见 [PostgreSQL 行级安全说明](https://www.postgresql.org/docs/16/ddl-rowsecurity.html)。

这只是教学身份映射：不包含企业单点登录、用户生命周期、连接池身份传递、完整岗位权限或审计系统。CRM 客户和 SCM 数据在同租户读者之间可见，不按客户负责人做额外过滤；HCM 隐私策略不能被误解为整个系统已有生产级认证授权。

## 7. 在真实数据库执行验收

```bash
python -m enterprise_ai.db verify data/generated/tiny.json
```

预期退出码为 0，返回 `status: passed`、`checks` 列表和 `dataset_digest`。这是服务器实际运行后才可记录的结果；不要直接复制预期文本作为验收证据。

当前检查列表逐项含义：

| 检查名 | 实际验证内容 |
|---|---|
| `health_and_schema` | 可连接 PostgreSQL，存在 pgvector，schema 版本为 1 |
| `content_and_idempotent_import` | 已导入指定数据集、全表内容一致，重复导入返回 `unchanged` |
| `a_isolation_privacy_queries_and_write_denial` | A 读者的身份、租户隔离、HCM 本人可见、三项查询计算、拒绝写入和拒绝读身份映射 |
| `b_isolation_privacy_queries_and_write_denial` | B 读者的同类检查 |
| `cross_tenant_foreign_key_and_probe_rollback` | 使用 B 独有的临时客户编号，尝试让 A 的订单引用它，应被复合外键拒绝；探针回滚后数据仍一致 |

核对金额使用从数据文件重新计算的 `Decimal` 结果，同时核对订单张数、年假差额和各仓库可用库存。权限检查使用两种真实数据库登录角色，且测试会话租户变量不能伪造身份。

`verify` 并非纯只读扫描：它会尝试权限应拒绝的更新，并用管理员事务插入临时外键探针后回滚。不要与其他写入任务并行执行；它不会首次导入数据，也不是任意生产数据库的安全扫描器。验证范围针对本课固定样例和已实现的断言，不代表覆盖所有业务流程。

保存本次实际验收报告，可以使用：

```bash
umask 077
mkdir -p reports/local
if python -m enterprise_ai.db verify data/generated/tiny.json > reports/local/m02-db-verification.json 2> reports/local/m02-db-verification.err; then
  cat reports/local/m02-db-verification.json
else
  printf '验收失败，请在服务器私下查看 reports/local/m02-db-verification.err。\n'
fi
git rev-parse HEAD > reports/local/m02-code-commit.txt
```

保留错误文件和报告；失败时 JSON 文件可能为空，不能以文件存在判断成功。这里只输出通过时的结构化报告，不公开原始数据库日志。

## 8. 错误与恢复

| 错误或现象 | 处理方式 |
|---|---|
| 生成时报文件已存在 | 运行 `validate` 检查原文件；不同实验使用新文件名，不覆盖 |
| 校验报字段、状态或关联错误 | 保留问题文件用于定位；以当前代码另行生成新文件，对照数据字典排查 |
| `Different dataset already loaded` | 当前库已有另一摘要；找回原文件，或准备独立实验数据库与卷；不强行覆盖 |
| `Table is not empty` | 库里已有未登记的数据；先备份并查来源，不手工清表绕过导入保护 |
| `Database differs from dataset` | 标记与实际内容不一致；保留当前库、原数据文件和备份，确认是否有人修改数据 |
| `verify` 提示先导入 | 验收不会替你首次导入；确认文件正确后先执行 `load` |
| `demo` 为空或读者认证失败 | 检查是否已导入、旧卷密码是否匹配，以及当前使用的代码版本；先运行 `health` |
| 权限/外键检查失败 | 停止进入下一课，记录检查名和提交号；不要通过放宽权限来让测试“通过” |
| 连接、模块或端口错误 | 回到第 1 课检查虚拟环境、数据库状态和 `.env` |

恢复前按第 1 课备份。需要恢复数据库时先在隔离环境演练并核对内容与权限，不在当前库试错。改种子不需要删除现有数据库；本课只要求完整验收一个 `tiny` 数据集。

## 9. 选修：standard 数据与性能观察

完成 tiny 验收后，可以仅生成和校验较大文件：

```bash
python -m enterprise_ai.data generate --profile standard --seed 42 --output data/generated/standard.json
python -m enterprise_ai.data validate data/generated/standard.json
```

`standard` 每租户为 1,000 个客户、500 名员工、2,000 个产品、20,000 张销售订单、5,000 张采购订单和 50 个供应商。不要把它导入已经装有 tiny 的同一个数据库；导入器会拒绝。

性能选修需要另外规划独立数据库与卷、端口和配置。隔离后再记录生成耗时、文件大小、导入耗时、三项查询耗时和服务器资源使用。比较前保持提交号、种子、硬件及缓存条件一致；不要把 tiny 查询很快视为大规模性能结论。当前加载器与全表一致性检查会在内存持有数据，属于教学实现，不是生产大规模流式导入方案。

## 10. 验收反馈模板

```text
执行日期 / 项目提交号：
Ubuntu Python / PostgreSQL / pgvector 版本：
profile / seed / as_of：
generate 是否成功：
validate 是否成功 / sha256：
各表行数 / 总行数：
首次 load 状态：
第二次 load 状态：
demo a 客户订单张数/金额、本人假期、两仓可用库存：
demo b 对应摘要：
verify 状态 / checks 列表：
验收报告路径：
未执行或失败项及错误摘要：
```

完成标准：服务器文件校验成功；首次导入成功且重复导入保持不变；能解释三项查询字段；真实数据库验收全部成功；报告保存且不含密码。任何项目未执行，就标记未执行，不补写成功结果。

## 11. 练习与参考答案

**练习 1：** 一张订单有三行明细，直接连接明细后数订单编号，可能得到什么错误？

答案：会把一张订单算成三张。当前 SQL 先按 `(tenant_id, order_id)` 汇总明细，再计订单；订单计数与金额汇总处于正确粒度。

**练习 2：** A、B 租户都有客户 1，只按 `customer_id=1` 关联订单为什么不够？

答案：客户编号只在租户内唯一，必须把租户一起用于连接与外键。RLS 控制可见行，复合外键控制合法关联，两者解决不同问题。

**练习 3：** 同一数据文件导入两次，第二次为什么还检查全部内容？

答案：有人可能在首次导入后修改数据库而没有修改摘要标记。比较实际数据可以发现这种漂移；仅凭标记或行数会遗漏值被修改的情况。

**练习 4：** 员工有 20 天额度，批准 4 天、待审批 2 天、拒绝 1 天，样例剩余多少？

答案：16 天。只扣已批准的 4 天；这不代表其他企业必须采用相同规则。

**练习 5：** 某仓在库 12、预留 7、补货点 10，快照可用量和缺口是多少？能直接采购这个缺口吗？

答案：可用量 5，缺口 5。还需考虑在途、需求和交期等条件，本课不自动采购。

**练习 6：** 为什么不能把客户订单金额、实时库存和员工假期余额放进微调数据，指望模型以后直接回答？

答案：这些事实会变化，且受租户与个人权限限制。应通过经授权的工具实时查询；微调可学习任务格式、工具选择等稳定行为，真实敏感信息还需要独立的数据治理。
