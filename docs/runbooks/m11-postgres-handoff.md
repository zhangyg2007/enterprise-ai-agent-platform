# M11 PostgreSQL实操与验收

所有命令在Ubuntu、仓库根目录执行。先读[第11课](../tutorials/11-enterprise-workflows.md)，按[T2第1节](m11-t2-persistent-approval.md#1-获取本批开发文件)取得M11分支。此手册是服务器执行清单，本地SQL解析/连接替身不表示PostgreSQL验收通过。

## 1. 前置条件与独立环境

M01 Compose数据库已经健康，管理员能访问集群；仅监听回环地址。端口以自己的`.env`为准，下文5432需要与它一致。先核对现有数据库、角色和磁盘；本手册创建独立新数据库，不改M02数据。若同名角色/数据库已存在，停止并核对来源，禁止删除重建。

```bash
git status --short --branch
git rev-parse HEAD
sudo docker compose ps
test ! -e .venv-workflows-pg
python3.12 -m venv .venv-workflows-pg
source .venv-workflows-pg/bin/activate
python -m pip install -e . -r requirements/m11-postgres.txt
python -m pip check
mkdir -p reports/local .secrets
python -m pip freeze > reports/local/m11-pg-environment.txt
python -m unittest tests.test_workflow_store tests.test_workflow_graph tests.test_workflow_cli tests.test_workflow_pg tests.test_workflow_snapshot tests.test_workflow_pg_cli -v
```

已有环境时先核对并激活，不重复覆盖；`test`失败就停止本段。需要宿主机psql客户端，可按服务器的软件管理流程安装`postgresql-client`；先检查`psql --version`，不用在宿主机再启动PostgreSQL服务。

## 2. 管理员一次性建角色、数据库与凭证

下面是学习者明确执行的初始化步骤，不会由应用启动自动触发。029在集群创建独立角色；SQL不含密码。030仅允许m11_migrator在`enterprise_workflows`或其后缀新库执行，并拒绝混入crm/hcm/scm。

```bash
sudo docker compose exec -T db sh -c 'exec psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1' \
  < infra/postgres/migrations/029-m11-roles.sql
sudo docker compose exec -T db sh -c 'exec createdb -U "$POSTGRES_USER" -O m11_migrator enterprise_workflows'
python -m enterprise_ai.workflow_pg_cli create-services \
  --directory .secrets/m11-01 --port 5432
sudo docker compose exec -T db sh -c 'exec psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1' \
  < .secrets/m11-01/passwords.sql
export PGSERVICEFILE="$PWD/.secrets/m11-01/services.conf"
export PGPASSFILE="$PWD/.secrets/m11-01/pgpass"
psql 'service=m11-migrate' -X -Atc 'SELECT current_database(),session_user,current_user'
psql 'service=m11-migrate' -X -v ON_ERROR_STOP=1 \
  -f infra/postgres/migrations/030-m11-workflows.sql
python -m enterprise_ai.workflow_pg_cli setup --service m11-migrate
```

每条成功后再继续；shell未启用遇错退出时不要整段盲贴。预期身份为enterprise_workflows/m11_migrator/m11_migrator。setup使用真实PostgresSaver3.2.0的setup/autocommit/dict_row；运行期不调用setup，不授予runtime或cp schema CREATE。

`create-services`只接受新目录，生成10个独立随机密码及libpq service/pass文件，Unix目录0700、文件0600，不打印密码。passwords.sql用于一次性设置本批新角色密码，之后仍作为私密凭证文件保管，不能上传或贴进辅导会话；不要反复执行生成命令或轮换已有账号密码。新终端只需重新export两个路径。m11_migrator凭证只用于初始化/种子，m11_cp凭证只在可信后端使用，均不交给终端用户。

若使用后缀数据库，createdb名称和create-services的`--database enterprise_workflows_test`必须一致；角色为集群级，只创建一次。迁移自己会按当前库撤销PUBLIC连接权限，并给runtime/cp组授予连接，不触及其他数据库。

## 3. 固定输入与自动验收

先做可手算的合成快照。两个tenant分别导入，摘要锁定输入；重复相同输入返回unchanged，改输入拒绝，绝不重置已有余额。新文件名不存在才能创建。

```bash
python -m enterprise_ai.workflow_pg_cli demo-snapshot --tenant 1 --output reports/local/m11-pg-demo-a.json
python -m enterprise_ai.workflow_pg_cli demo-snapshot --tenant 2 --output reports/local/m11-pg-demo-b.json
python -m enterprise_ai.workflow_pg_cli seed --service m11-migrate \
  --source synthetic-demo --input reports/local/m11-pg-demo-a.json
python -m enterprise_ai.workflow_pg_cli seed --service m11-migrate \
  --source synthetic-demo --input reports/local/m11-pg-demo-b.json
```

接下来选自动或手动路径。自动验收要求tenant1无任何effect且余额15，不应在手动实验之后执行；保留已有实验，另建后缀数据库才能再次跑整套。

```bash
python scripts/m11_verify_pg.py --run pg01 \
  --diagnostic-log reports/local/m11-pg-diagnostic-01.txt > reports/local/m11-pg-acceptance.json
```

成功时退出码0、status=passed，checks包含三案例、审批重开、拒绝、权限、故障回执、数据库锁、并发版本冲突、事务回滚、session映射、RLS和ACL。前半三案例及故障恢复后effect4/余额10；随后两个10天并发请求仅一个成功，最终effect5/余额0。回滚探针留下approved请求但没有effect，便于核查，不能把它当作正式业务请求继续操作。任何非零退出都属于失败；不要把输出空文件当通过。服务器检查仍由学习者实际执行后填表。

诊断文件仅在失败且显式指定选项时创建，Unix权限0600、不覆盖已有文件，包含traceback与可能敏感的数据库值，不能上传或粘贴到公开会话。控制台只显示保存状态。再次尝试改用新的日志编号；若没有指定该选项，不会生成日志。业务CLI也支持把`--diagnostic-log reports/local/新编号.txt`放在子命令之前，便于在同一进程捕获错误；解析参数错误仍由argparse直接报告。

## 4. 可替代自动验收的逐进程实验

在相同初始输入、尚无effect的新库使用本节。每条命令是新进程，service决定数据库身份，不能添加tenant/role来切换身份。

```bash
python -m enterprise_ai.workflow_pg_cli start --service m11-a-employee --thread leave01 --key leave01 --case leave --arguments '{"days":3}'
python -m enterprise_ai.workflow_pg_cli inspect --service m11-a-manager --thread leave01
python -m enterprise_ai.workflow_pg_cli approve --service m11-a-manager --thread leave01
python -m enterprise_ai.workflow_pg_cli retry --service m11-a-manager --thread leave01
python -m enterprise_ai.workflow_pg_cli start --service m11-a-sales --thread quote01 --key quote01 --case quote --arguments '{"customer_id":1,"product_id":1,"quantity":3,"discount_percent":"15"}'
python -m enterprise_ai.workflow_pg_cli approve --service m11-a-manager --thread quote01
python -m enterprise_ai.workflow_pg_cli start --service m11-a-procurement --thread buy01 --key buy01 --case replenish --arguments '{"product_id":1}'
python -m enterprise_ai.workflow_pg_cli approve --service m11-a-manager --thread buy01
python -m enterprise_ai.workflow_pg_cli start --service m11-a-employee --thread reject01 --key reject01 --case leave --arguments '{"days":1}'
python -m enterprise_ai.workflow_pg_cli reject --service m11-a-manager --thread reject01
```

预期初始inspect pending/0效果/余额15；休假后余额12/1效果；报价255.00、补货quantity11/on_order15/on_hand20，合计3效果；拒绝后仍3效果、余额12。正常retry不重跑execute时回执replayed可以仍false。

```bash
python -m enterprise_ai.workflow_pg_cli start --service m11-a-employee --thread fault01 --key fault01 --case leave --arguments '{"days":2}'
if python -m enterprise_ai.workflow_pg_cli approve --service m11-a-manager --thread fault01 --fail-after-effect; then
  printf 'Unexpected success; preserve output and stop.\n'
  false
else
  fault_code=$?
  test "$fault_code" -eq 5
fi
python -m enterprise_ai.workflow_pg_cli inspect --service m11-a-manager --thread fault01
python -m enterprise_ai.workflow_pg_cli retry --service m11-a-manager --thread fault01
```

预期故障退出码5；业务已executed、图next仍execute。retry后replayed=true，4效果/余额10。不能新键重做这一笔。退出码0/1/2/3/4/5含义与T2一致，其中4为数据库图锁busy。

## 5. 只读导出M02输入

本步骤单独保存输入，不覆盖上面已使用的demo账本。已有M02 reader只读账号能看到自己的余额、可见客户/产品和库存/在途；source身份由M02自己的session_user映射确定。保持M02已配置账号，不授予写权限。

为避免复制密码到命令行，可在私有service/pass文件中新增m02-a。下面读取既有`.env`，只把凭证写入被忽略文件，不打印密码；先确认两个文件中还没有m02-a，再执行一次：

```bash
python - <<'PY'
from pathlib import Path
from enterprise_ai.settings import load_settings
s=load_settings()
service=Path('.secrets/m11-01/services.conf')
passwd=Path('.secrets/m11-01/pgpass')
if '[m02-a]' in service.read_text():
    raise SystemExit('m02-a already configured; preserve existing files')
with service.open('a') as f:
    f.write(f'\n[m02-a]\nhost=127.0.0.1\nport={s["POSTGRES_PORT"]}\ndbname={s["POSTGRES_DB"]}\nuser=lab_a_reader\n')
with passwd.open('a') as f:
    f.write(f'127.0.0.1:{s["POSTGRES_PORT"]}:{s["POSTGRES_DB"]}:lab_a_reader:{s["LAB_A_PASSWORD"]}\n')
PY
python -m enterprise_ai.workflow_pg_cli snapshot-export --service m02-a --year 2026 \
  --output reports/local/m11-m02-a-snapshot.json
```

导出只执行SELECT和read-only/repeatable-read事务，不改M02。余额有小数天、空资源、重复编号或数据越界时明确失败，不截断、不跳过。若要实际导入此快照，请新建独立后缀工作流库、迁移、setup，再以新service执行seed，source选择m02-reader-snapshot；已demo-seeded的tenant会拒绝不同摘要。真实快照金额/余额不应套用demo预期，应先手算并记录来源摘要。快照导入也只改变派生库，未回写HR/ERP。

## 6. 验收记录

| 项目 | 记录 |
| -- | -- |
| 代码commit、分支、Python路径/版本 | 待填写 |
| PostgreSQL/依赖版本、pip check、固定pin | 待填写 |
| 独立库名/三个schema、角色与授权核对 | 待填写，不记录密码 |
| demo或M02输入、摘要、导出年份 | 待填写 |
| 自动或手动路径、每条退出码与脱敏JSON | 待填写 |
| 正常/拒绝/跨租户/自审/错误字段/重复键 | 待填写 |
| 故障重开、不重复effect、资源冲突与回滚 | 待填写 |
| RLS/ACL、session变量不能改身份、无运行DDL | 待填写 |
| M02前后只读核对、私密文件权限 | 待填写 |
| 未通过项、原始私有日志位置和下一步 | 待填写 |

无实际服务器输出时所有项目保持待填写。[本地验证](../validation/2026-10-24-m11-t3.md)只覆盖它明确列出的测试。备份/恢复、高可用、生产认证与多worker调度未在本地证明；保留数据库和实验文件，不用清库、删卷、改UUID或放宽授权来绕过失败。
