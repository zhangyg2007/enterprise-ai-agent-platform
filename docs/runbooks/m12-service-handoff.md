# M12 服务交付与恢复演练

执行位置：Ubuntu 仓库根目录，已激活第12课独立环境。先读[第12课](../tutorials/12-api-operations.md)。下面是待学习者执行的服务器步骤，不是已运行记录。合成lab与PG数据库分开验收；管理员只执行迁移/备份等管理操作，API不能使用管理员登录。

## A. SQLite一致备份与新目录恢复

不要使用正在执行第12课HTTP脚本的lab做下面的初始状态实验，另建一个独立lab：

```bash
mkdir -p data/generated backups reports/local
python -m enterprise_ai.workflow_cli init --lab data/generated/m12-recovery
python -m enterprise_ai.workflow_cli start --lab data/generated/m12-recovery \
  --actor employee --thread restore-leave-01 --key restore-key-01 \
  --case leave --arguments '{"days":3}'
python -m enterprise_ai.workflow_backup backup \
  --source data/generated/m12-recovery --target backups/m12-01
python -m enterprise_ai.workflow_backup restore \
  --source backups/m12-01 --target data/generated/m12-restored-01
python -m enterprise_ai.workflow_cli inspect --lab data/generated/m12-restored-01 \
  --actor employee --thread restore-leave-01
python -m enterprise_ai.workflow_cli approve --lab data/generated/m12-restored-01 \
  --actor manager --thread restore-leave-01
python -m enterprise_ai.workflow_cli approve --lab data/generated/m12-restored-01 \
  --actor manager --thread restore-leave-01
```

预期：恢复后的inspect为pending；两次approve均executed，effect_count仍1，休假余额由15变12。原始lab仍pending且没有生效。这是独立恢复验证，不将恢复目录替换正在运行的服务目录。要切换API，停服务、修改私有配置的绝对lab路径、重启，执行就绪和只读状态检查，再恢复流量。

备份实现持M11操作锁，用SQLite backup API读取三库，验证integrity_check、application_id、lab_id和格式版本，最后写manifest完成标记。恢复先核对固定文件清单与SHA256，只接受新目录，不接受链接或混库。SHA256用于发现损坏，不是签名；仅恢复来自受控私有备份存储的可信数据。手工SQL写库绕过应用锁不受此保证保护。备份失败保留源与不完整目标供排查，选择新目标重试；不要把缺manifest的目录当成可恢复备份。

记录备份时间、大小、代码SHA、lab_id、恢复耗时、恢复点与批准回执。RPO是最后可恢复备份到故障的间隔；RTO是停止服务至恢复业务检查通过的实测时间。RPO≤24小时、RTO≤30分钟只是可选教学目标，必须填实测结果，不能从测试耗时推断生产RTO。

## B. PostgreSQL后端接入

先完成[M11 PostgreSQL交接](m11-postgres-handoff.md)，确认独立库、受限业务登录、m11-cp检查点账号、schema和seed都已实际验收。每个API凭证绑定一个固定libpq service，最终actor来自数据库session_user映射，不从请求体读取。CLI和API共用M11同一契约；所有runtime路径不自动setup。

```bash
python -m enterprise_ai.service_config init-postgres --directory .secrets/m12-pg
export M12_CONFIG="$PWD/.secrets/m12-pg/service.json"
export PGSERVICEFILE="$PWD/.secrets/m11/services.conf"
export PGPASSFILE="$PWD/.secrets/m11/pgpass"
python -m uvicorn enterprise_ai.service_api:app_from_env --factory \
  --host 127.0.0.1 --port 8012 --workers 1 --no-access-log \
  --log-config configs/service/m12-logging.json
```

如果上一课私有目录不是`.secrets/m11`，替换成真实位置。生成的绑定是`m11-a/b-sales/employee/procurement/manager`和`m11-cp`；数据库账号必须与M11映射一致。就绪接口读取当前业务身份和检查点表，任何登录、lab身份或表检查失败均拒绝。真实三案例、跨租户、撤权、并发、业务提交后失败恢复仍须按M11验收脚本与HTTP客户端分别验证。本章`m12_verify_http.py`故意拒绝postgres模式，避免把合成固定余额脚本用于未知业务数据。

## C. PostgreSQL备份恢复演练

本节使用PostgreSQL16客户端；先核对`pg_dump --version`和服务端版本。停止API和其他写入者，确保业务账本、注册表与checkpoint处于明确应用边界。由DBA使用**备份专用服务配置**执行完整数据库dump，不能用受RLS限制的员工账号备份而遗漏其他租户。管理员配置不得写入API的service.json。

以下名称是独立演练库示例，执行前由DBA确认源和目标；目标必须不存在，禁止对正式库运行恢复或清理：

```bash
umask 077
mkdir -p backups
test ! -e backups/m11-full-01.dump || exit 1
pg_dump --dbname='service=m11-backup' --format=custom \
  --file=backups/m11-full-01.dump
sha256sum backups/m11-full-01.dump > backups/m11-full-01.sha256
pg_restore --list backups/m11-full-01.dump > backups/m11-full-01.contents
```

`m11-backup`需由DBA在独立私有libpq配置中创建（指向M11源库、能读取全部表），并非上一课自动生成。核对contents包含workflow_lab业务数据/metadata/threads、workflow_auth映射以及workflow_cp全套表。dump只包含一个数据库：角色和登录密码不在其中，另行私密保存角色定义/授权方案；M02源库、M08源文档、M09索引元数据和模型revision也各有备份条目，不能用此dump代替。

恢复到隔离测试实例（推荐）或同集群新库`enterprise_workflows_restore01`。隔离实例先按M11角色脚本建立同名角色，再由DBA创建空目标库；默认恢复保留owner/ACL，不能随意加`--no-owner --no-acl`后宣称权限不变。新库服务`m11-restore-admin`的连接目标由DBA确认：

```bash
sha256sum -c backups/m11-full-01.sha256
pg_restore --exit-on-error --single-transaction \
  --dbname='service=m11-restore-admin' backups/m11-full-01.dump
```

禁止`--clean`或覆盖现有业务库。为恢复库另建受限业务service与checkpoint service，核查lab_id一致、RLS/ACL、登录身份、请求数量、业务审计与效果数量，再恢复一条备份时pending的线程并重复批准，确认仅一次效果。不要运行M11 seed重灌恢复库；也不要把要求全新demo的验收脚本无差别指向恢复数据。最后记录实际RPO/RTO、恢复库名、脱敏证据和审批人。真实PG恢复本轮未执行。

## D. systemd服务与日志

[服务模板](../../infra/systemd/enterprise-ai-m12.service)给出固定一个worker和回环监听。它不是一条命令自动部署：先按本机目录策略准备下列条件，再由管理员安装到`/etc/systemd/system/enterprise-ai-m12.service`。

| 配置 | 必须满足 |
|---|---|
| enterprise-ai 用户/组 | 独立无sudo服务账号；由管理员创建 |
| /opt/enterprise-ai/releases/<SHA> | 干净版本代码及独立`.venv`，服务账号只读 |
| /opt/enterprise-ai/current | 指向已验证release的入口；切换由管理员完成 |
| /var/lib/enterprise-ai | 服务账号拥有；fixture lab与需要写入的数据放这里 |
| /etc/enterprise-ai/m12.env | 管理员拥有，600；只配置下方环境变量 |
| /etc/enterprise-ai/service.json | 服务账号可读、其他普通用户不可读；owner-only模式 |
| PG配置 | 若使用PG，私有service/pass文件由服务账号可读，pgpass600 |

环境文件示例（按实际路径填写，不要把真实密码写进Git）：

```text
M12_CONFIG=/etc/enterprise-ai/service.json
PGSERVICEFILE=/etc/enterprise-ai/services.conf
PGPASSFILE=/etc/enterprise-ai/pgpass
```

fixture模式可省略PG变量。`service.json`中的lab必须是`/var/lib/enterprise-ai/...`绝对路径；不能仍指向开发者home。`ProtectHome=true`与`ProtectSystem=strict`会主动阻止错误目录布局。日志只用本章JSON配置，关闭原始访问日志。

```bash
sudo systemctl daemon-reload
sudo systemctl start enterprise-ai-m12
sudo systemctl status enterprise-ai-m12 --no-pager
sudo journalctl -u enterprise-ai-m12 --since '10 minutes ago' --no-pager
```

启动服务后先验证live、带凭证ready，再检查已知线程；数据库不可用时服务进程仍可能alive，不要因此恢复外部流量。本轮未在Ubuntu执行systemd，模板部署结果需填表。

## E. 升级、凭证轮换与回滚

1. 记录运行SHA、依赖清单、数据格式版本、私有配置摘要和备份manifest。保留当前release。
2. 新SHA安装到新的release目录和venv；运行单元、HTTP和恢复验收。禁止在运行中的venv上直接升级依赖。
3. 若涉及数据库schema，先提供并演练前向/后向兼容方案；没有兼容证据则暂停切换。本次M12没有改变M11schema。
4. 停止入口请求/服务，做完整备份，切换current和配置，重启，检查live/ready及原线程。单机方案有停机窗口，须事先记录。
5. 若探针失败：停服务，将current和配置恢复到上个验证版本，保留新日志和故障证据，再验证原线程。仅在数据schema兼容时回滚代码；不要自动还原旧数据库丢掉新业务。
6. 凭证轮换后旧token必须401、新token有效。静态token无自动过期；泄漏时立即撤销对应摘要、重启、核查相关业务审计。

## F. 验收表（初始均待执行）

| 项目 | 环境/SHA | 命令/关联号 | 预期 | 实测/证据 | 结果 |
|---|---|---|---|---|---|
| HTTP完整12请求 | 待填 | 待填 | 401/403/200符合契约 | 待填 | 未执行 |
| 服务重启后重复批准 | 待填 | 待填 | 一笔效果 | 待填 | 未执行 |
| fixture备份/恢复 | 待填 | 待填 | pending可继续，原lab不变 | 待填 | 未执行 |
| PG身份/RLS/恢复 | 待填 | 待填 | 隔离与幂等均成立 | 待填 | 未执行 |
| systemd故障/回滚 | 待填 | 待填 | 受控恢复、无数据回退 | 待填 | 未执行 |
| 凭证撤销/轮换 | 待填 | 待填 | 旧401、新可用 | 待填 | 未执行 |
| RPO/RTO | 待填 | 待填 | 按双方确认目标 | 待填 | 未执行 |

报告可进入交付包；凭证、完整数据库、原始业务日志、个人服务器路径不上传公开仓库。
