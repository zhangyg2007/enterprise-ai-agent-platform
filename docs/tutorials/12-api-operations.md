# 第12课：把审批图交付成可访问、可恢复的 API

前置：完成[第11课](11-enterprise-workflows.md)的本地三案例。目标是让客户端只能提交业务请求，由服务器确定身份、权限和可执行操作，并能在服务重启或恢复备份后继续处理同一请求。

本章是单进程教学服务，支持 SQLite 合成实验与独立 PostgreSQL 工作流库两个后端。模型不在 HTTP 请求中自由生成写操作；这里复用 M11 的确定性审批图。正式企业 SSO、公开 TLS 入口、分布式限流和高可用尚未提供。真实 PG/Ubuntu 验收与本地测试分别记录。

## 1. 为什么 CLI 不能直接当企业 API

M11 CLI 的 `--actor employee --tenant 1` 由实验者指定，用于理解权限契约。若 HTTP 也接受这些字段，任何用户都可能自称经理。本章改为：请求携带随机 Bearer 凭证 → 服务端查 SHA256 绑定 → 确定身份 → M11 检查线程归属和独立审批 → 才读取 checkpoint 和执行业务事务。

| 层 | 职责 | 不承担的保证 |
|---|---|---|
| HTTP边界 | 8 KiB请求体上限、严格字段、401/403、每凭证60次/分钟 | 不能替代TLS与企业身份系统 |
| 应用身份 | fixture映射或固定libpq service对应数据库登录 | 客户端不能指定租户/角色/数据库 |
| M11工作流 | 独立经理审批、幂等、资源版本、操作锁、持久回执 | checkpoint本身不保证业务只执行一次 |
| 运维 | 请求关联号、就绪检查、备份/恢复、版本回滚 | 本地测试不证明Ubuntu服务已部署 |

## 2. 获取代码与独立环境

执行位置：Ubuntu 仓库根目录。M12/M13 位于 `codex/m12-m13-delivery` 本地开发候选；它包含 M11，main目前仅第0–10课。2026-10-28用户暂缓GitHub权限，本次内容尚未同步远端。**下方两条Git命令仅在后续确认分支已推送后执行**；当前可在本地开发目录阅读、验证，不要因远端缺少该分支判断文件丢失。只抓取main的旧clone先参考[服务器起步](../runbooks/server-start.md)。

```bash
git fetch origin codex/m12-m13-delivery
git switch --track origin/codex/m12-m13-delivery
python3.12 -m venv .venv-m12
source .venv-m12/bin/activate
python -m pip install -r requirements/m12-service.txt
python -m pip install --no-deps -e .
python -m pip check
python -m unittest tests.test_service_api tests.test_workflow_backup tests.test_service_process -v
```

分支已存在时使用 `git switch codex/m12-m13-delivery`，再在无本地修改时 `git pull --ff-only`。不要覆盖训练环境；本项目支持 Python 3.11–3.13，本课本地验证解释器为3.12，其他版本另行验证。

## 3. 创建实验数据与凭证

以下路径都必须是新目录。原lab保留，避免一次新练习改掉上一课的恢复证据。

```bash
mkdir -p data/generated reports/local .secrets
python -m enterprise_ai.workflow_cli init --lab data/generated/m12-lab
python -m enterprise_ai.service_config init-fixture \
  --directory .secrets/m12 --lab data/generated/m12-lab
export M12_CONFIG="$PWD/.secrets/m12/service.json"
python -m uvicorn enterprise_ai.service_api:app_from_env --factory \
  --host 127.0.0.1 --port 8012 --workers 1 --no-access-log \
  --log-config configs/service/m12-logging.json
```

`service.json` 保存token摘要与身份绑定，`credentials.json` 保存8个随机凭证供本机实验。Linux文件权限为600，父目录700；Windows应限制当前用户ACL。两份文件均在Git忽略目录中。不要上传截图或把凭证贴进报告。服务启动不自动创建业务库、迁移表或补造身份。

另开终端、激活同一环境，在同一仓库运行：

```bash
python scripts/m12_verify_http.py --base-url http://127.0.0.1:8012 \
  --credentials .secrets/m12/credentials.json --output reports/local/m12-http-01.json
```

预期先输出 `status=starting` 与原请求的 `thread_id`，最后输出 `status=passed, requests=12`。实际依次检查存活、未认证拒绝、就绪、创建/重复草案、跨租户拒绝、自批拒绝、两次经理批准、查看状态、普通用户被拒绝读取计数器及经理读取计数器。该脚本只允许fixture与回环HTTP，成功运行消耗租户1员工1的3天余额；重复整段练习应创建新lab，否则余额不是初始状态。单次传输失败后用最先输出的thread_id查看原线程，不要换新幂等键盲目重跑；失败时不会输出凭证或原始异常。

从远程电脑访问使用 SSH 隧道：`ssh -L 8012:127.0.0.1:8012 user@server`，客户端仍访问本机回环地址。服务绑定保持127.0.0.1。

## 4. 自己写一个客户端

在服务器同一环境执行以下Python。凭证从私有文件读取，不能放进URL、命令历史或源码常量。

```python
import json
from pathlib import Path
import httpx

tokens = json.loads(Path('.secrets/m12/credentials.json').read_text())
with httpx.Client(base_url='http://127.0.0.1:8012', trust_env=False, timeout=10) as client:
    response = client.post('/v1/threads',
        headers={'Authorization': 'Bearer ' + tokens['t1_sales']},
        json={'thread_id': 'quote-http-01', 'idempotency_key': 'quote-key-01',
              'case': 'quote', 'arguments': {'customer_id': 1, 'product_id': 1,
                       'quantity': 2, 'discount_percent': '5.00'}})
    response.raise_for_status()
    print(response.json()['status'], response.headers['x-request-id'])
```

预期 `pending` 和一个关联号。报价尚未生效。经理使用 `POST /v1/threads/quote-http-01/decision`，正文 `{"approved":true}`，凭证改为 `t1_manager` 后才执行；`false` 为拒绝。采购补货使用 `case=replenish, arguments={"product_id":1}` 以及 `t1_procurement` 凭证。仍然遵守第11课“补货创建在途量，不直接增加现货”的口径。

| 路径 | 功能 | 身份要求 |
|---|---|---|
| GET /health/live | 进程存活 | 无；不透露数据库配置 |
| GET /health/ready | 后端元数据/锁检查，PG还检查checkpoint身份和表 | 有效凭证；不等于所有业务流程验收 |
| POST /v1/threads | 创建或重放同一草案 | 对应业务角色 |
| GET /v1/threads/{id} | 查看状态 | 同租户拥有者或经理 |
| POST /v1/threads/{id}/decision | 批准/拒绝 | 同租户独立经理 |
| POST /v1/threads/{id}/retry | 恢复已作决定的失败执行 | 同租户独立经理；不能代替审批 |
| GET /v1/metrics | 当前进程、当前租户已确认身份的请求/错误/拒绝计数 | 同租户经理；不含未认证或身份未解析请求，不代表全局监控 |

错误：401无有效凭证；403无权限；409输入不符合业务契约、状态冲突或锁忙（锁忙含Retry-After）；413正文过大；422字段模型不符；429触发每分钟限制；503后端异常。收到503后不能假定业务未提交：用同线程查询状态，经理按原决定retry。

## 5. 观察、审计与故障

每个HTTP响应包含服务端生成的 `X-Request-ID`。服务验证身份和线程格式后，在JSON日志记录UTC时间、关联号、可信租户/用户、固定操作名、状态码、耗时和`thread_digest`。摘要为`SHA256(租户编号 + ':' + 线程ID)`：在受控环境对M11业务审计中的tenant/request_id计算相同摘要，可关联创建、拒绝、审批和重放；原始thread、Bearer、请求正文、原始URL或堆栈不写入日志。`--no-access-log` 避免另一路访问日志记录查询参数。摘要不是加密，易猜线程ID可被枚举，日志仍须受控；业务审计与HTTP日志是独立记录，并非同一事务。请求认证或参数校验未完成时不记录线程摘要；若进程在两者之间崩溃也可能有缺口，不能宣称完整生产审计。失败且没有响应时保留已有线程号、查询状态，不猜测业务是否生效。

1. 创建待批准申请，停止服务，再启动并GET同线程：仍应待审批。
2. 经理批准两次：回执相同，只有一笔效果。
3. 未批准时调用retry：409，余额不变。
4. 用租户2或同租户另一员工访问：403。
5. 保存关联号、退出码与脱敏状态；不要把“服务启动”写成“业务验收通过”。

限流与指标只在进程内，重启清空；指标按服务端已解析的租户隔离，认证失败和身份尚未解析的限流请求不纳入任何租户的统计。多worker会分裂计数，所以此教材固定一个worker；生产监控仍需另设平台级安全聚合。所有图操作串行，适合小规模实验。凭证撤销/轮换：生成新随机token、更新私有摘要绑定、重启服务、验证旧token变401；没有自助登录、有效期、OIDC或身份生命周期管理。

## 6. 备份、恢复、PostgreSQL 与升级

按[运维交接手册](../runbooks/m12-service-handoff.md)完成：三库一致备份 → 新目录恢复 → 暂停流程继续审批 → 重放只有一笔效果。PG路径先完成M11真实数据库验收，再切换M12服务器配置。备份必须包括业务、线程注册、checkpoint；只复制某一个sqlite文件不是可恢复交付。

## 7. 练习与参考答案

1. 请求正文加入 `tenant_id=2` 或 `role=manager` 会怎样？**答：**422；身份仅来自服务器绑定。
2. 超时后换一个幂等键重发更安全吗？**答：**不安全，新键表示新业务请求；先用原线程查看，按原决定恢复。
3. live=200、ready=503说明什么？**答：**进程运行，后端不可用或校验失败；不能对外宣告服务就绪。
4. 为什么备份时持操作锁，恢复后仍检查幂等？**答：**锁保证三库同一应用边界；备份中仍可能保留“业务已提交、图未完成”的可恢复窗口。
5. 将服务绑定0.0.0.0就成为企业服务了吗？**答：**没有。先补TLS、身份系统、入口限流、密钥轮换、数据库验收和运营责任，再评估外部发布。

官方参考：[FastAPI认证](https://fastapi.tiangolo.com/tutorial/security/first-steps/)、[FastAPI测试](https://fastapi.tiangolo.com/tutorial/testing/)、[SQLite备份API](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.backup)、[PostgreSQL16备份](https://www.postgresql.org/docs/16/backup-dump.html)。本课没有自行实现OAuth授权服务器。
