# M11第一步：业务事务基础实验（T1）

这是第11课的基础实验，不代表整章交付。先验证报价、本人休假和采购补货的写入规则，再接入持久化LangGraph审批（T2）和正式PostgreSQL路径（T3）。

使用独立SQLite库、合成数据与可信的本地Actor；Actor不是生产认证。所有三种动作均需独立manager批准。不要连接M02业务数据库。本实验只需项目支持的Python3.11–3.13及标准库，无须GPU、模型或数据库服务。默认解释器不满足版本要求时，先使用独立的应用环境。

## 1. 先跑边界验证

在包含本实验文件的项目根目录、已激活的应用环境中运行：

```bash
export PYTHONPATH=src
python -m unittest tests.test_workflow_store -v
```

预期10项成功。覆盖三种结果的手算、无批准/被拒绝不写入、跨租户/他人访问、自审、幂等键冲突、实际并发、重新打开数据库重试、资源版本冲突和故障回滚。

## 2. 保存一个可重新打开的实验库

下面创建新目录；重复运行会拒绝覆盖。保留输出后，重做请另选新目录名，不要删除已有证据。默认reports/local被Git忽略。

```bash
python - <<'PY'
from pathlib import Path
from enterprise_ai.workflow_store import FixtureActor, FixtureStore, create_store

folder = Path('reports/local/m11-business-lab-01')
folder.mkdir(parents=True, exist_ok=False)
path = folder / 'business.sqlite'
store = create_store(path)
sales = FixtureActor(1, 1, 1, 'sales')
employee = FixtureActor(1, 2, 1, 'employee')
procurement = FixtureActor(1, 3, 3, 'procurement')
manager = FixtureActor(1, 4, 4, 'manager')
cases = [
    (sales, 'quote-1', 'quote', {'customer_id': 1, 'product_id': 1,
                              'quantity': 3, 'discount_percent': '15.00'}),
    (employee, 'leave-1', 'leave', {'days': 3}),
    (procurement, 'buy-1', 'replenish', {'product_id': 1}),
]
for owner, request_id, case, args in cases:
    draft = store.draft(owner, request_id, 'key-' + request_id, case, args)
    print('draft:', draft)
    store.decide(manager, request_id, True)
    print('receipt:', store.execute(manager, request_id))
assert store.read_resource(employee, 'leave', 1)['value']['remaining_days'] == 12
assert store.read_resource(procurement, 'stock', 1)['value']['on_order'] == 15
assert store.read_resource(procurement, 'stock', 1)['value']['on_hand'] == 20
assert store.effect_count(manager) == 3
assert FixtureStore(path).execute(manager, 'leave-1')['replayed'] is True
assert FixtureStore(path).effect_count(manager) == 3
print('PASS: quote=255.00; leave=12; on_order=15; on_hand=20; effects=3')
PY
```

报价金额为100×3×(1−15%)=255.00；报价不扣库存。休假余额15−3=12。补货缺口30−(20−5+4)=11；下采购单后在途量4+11=15，在库量仍为20。重新打开文件重试休假，返回原回执，业务效果仍只有3笔。

## 3. 理解事务边界

草案冻结资源版本。批准后，执行在同一个事务中检查版本、更新资源、保存回执和审计；事务失败全部回滚。同一余额版本的两笔休假只有第一笔成功，第二笔返回conflict，需重新起草。报价保存回执但不改变价格版本，因此不同报价可使用同一价格版本。

同租户、同owner、同幂等键与同规范化输入返回原请求；修改输入或换owner复用键会拒绝。执行后的重试先检查身份，再返回原回执，不再次扣减。数据库文件由本地实验者掌管，本实验不防御管理员直接篡改SQLite文件。

记录Python版本、代码commit、10项测试输出与实验输出。当前尚未实现图中断/跨进程恢复、PostgreSQL业务适配、Web身份认证；它们分别属于T2、T3和M12。进度见[执行记录](../progress/2026-10-22-m11-execution.md)，文件导航见[Filelist](../../Filelist.txt)。
