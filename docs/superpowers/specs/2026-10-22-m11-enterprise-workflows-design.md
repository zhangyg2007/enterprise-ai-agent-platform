# M11设计：三个业务案例的审批、幂等与恢复

接续已批准M11目标，开发分批进行，不把第一批基础代码称作整章完成。

目标：CRM报价、本人休假申请、采购补货三个案例，共用“确定性准备草案→人审→执行前重校验→事务写入→持久回执”契约。LangGraph使用interrupt/Command与持久化checkpointer演示暂停/重启；checkpoint不替代业务幂等。M12再提供服务端认证/API。

T1范围：先实现可独立验证的标准库SQLite教学业务账本，真实事务持久化但数据与身份均synthetic-fixture。单独新库不修改M02业务库，不声称PostgreSQL实现完成。Actor是可信CLI输入的冻结身份（tenant/user/employee/role），非模型参数；每个请求必须有同租户owner，manager可查看审批，跨租户/他人不可读。

三个动作：quote输入customer_id/product_id/quantity/discount_percent（十进制字符串），服务端读取价格、Decimal算总额并冻结资源revision；leave只接days正整数，本人employee来自Actor，扣减合成余额；replenish仅接product_id，由库存on_hand-reserved+on_order与reorder_point确定采购缺口，执行只建采购单并增加on_order，不冒充到货增加on_hand。最小实验三种均要求人审，折扣阈值等自动审批分支后续再扩，不自称完整企业政策。

T1数据库：resources（tenant/kind/id/revision/value）、requests（id、tenant、owner、case、固定输入摘要、资源snapshot、status、idempotency_key）、approval（授权manager、决定）、effects（唯一业务回执）、audit。idempotency_key同租户唯一；同owner同输入重复提交返回原请求，不同输入/owner重用拒绝。审批拒绝没有业务effect；不能自审或跨租户审批；审批决定不可反转。execute仅manager，BEGIN IMMEDIATE内校验approval与资源revision/业务边界，写业务资源/回执/状态/审计一个事务。执行后再试先返回原回执，不重复扣减/采购；并发两个相同revision动作只有第一个生效，第二个冲突。冲突需新草案，不覆盖原请求。读写用参数化SQL，所有打开模式防止拼错路径创建空数据库，重建拒绝覆盖。

T2：实际LangGraph prepare/review/execute节点；review前不做业务写入，interrupt重放安全。thread归属先在业务账本校验后才读checkpoint；manager审批身份来自CLI绑定，不接受resume payload自报。approve=false结束且无effect；true执行。SqliteSaver用于离线教学重启实验，显式说明它不是正式PostgreSQL验收。演示执行事务成功但checkpoint前故障，恢复时业务回执防重。CLI按start/inspect/approve/reject/retry分新进程或重开连接运行；保存脱敏证据，不把thread_id当凭证。限制同thread并发，避免checkpoint与业务状态竞态。

T3：提供PostgreSQL checkpointer（固定版本、setup一次）和最小业务存储适配/迁移入口，已有M02数据只读快照作输入，派生动作表与审批/审计在独立schema。真实读者/执行账号分离和session身份映射，不能使用自报session变量授权；对CRM/HCM/SCM所需写入逐项声明作用，不执行真实服务器迁移。必要时拆分server-only任务，不用SQLite通过冒充PG通过。整章到本地验证、独立review、教程与服务器验收手册齐备才称本地交付。

验收重点：三动作结果可手算、身份/角色/字段校验、租户和owner隔离、无批准无effect、拒绝无effect、幂等同键冲突、资源并发revision冲突、断点后重试不重复effect、thread归属先于checkpoint访问、整数/Decimal拒绝bool/NaN/超界。SQLite实际重启通过与PG真实服务器待验收明确分开，Filelist每批同步。

架构调用由builder确定具体文件/接口；不新增真实模型训练、部署或生产认证范围。T1可先交付基础验收记录，剩余T2/T3显式保留待办，不虚构整章完成。

Builder check ruling（T1）：报价执行仅保存报价单/effect及唯一回执，不修改价格或库存revision；相同价格revision可生成多份不同合法报价。“同revision仅一笔成功”只约束休假余额和补货在途等实际变化资源。报价仍重查价格snapshot，且相同幂等键只生成一次。金额按两位小数ROUND_HALF_UP，discount_percent仅0..30的十进制字符串；教学所有三种写入均需manager人审。

T2 builder裁定（2026-10-23）：lab固定business/control/checkpoints三个SQLite文件及operation.lock；控制库绑定thread/tenant/owner/规范化输入，checkpoint实际ID由tenant:thread构成。为降低教学并发复杂度，OS锁串行化整个lab，涵盖同thread及不同thread并在进程退出时释放；不是分布式调度。Actor不从checkpoint读取，start不覆盖已存在的图。审批已提交、图保存失败时，用账本中已有决定恢复；未批准的pending仍不能retry越过人审。独立M11依赖环境使用新的固定框架组合，不覆盖M10复现环境；序列化不启用pickle或任意应用类型。

T2术语澄清：批准前可写草案、registry和checkpoint等元数据；“不做业务写入”指不扣减余额、不产生采购/正式报价effect，并非禁止记录待审批请求。

T2 review修订：已持久化批准/拒绝时，相反CLI动作必须在调用图之前报错，不能依赖LangGraph选择新旧resume值。三个数据库共用lab UUID/格式版本以发现误混用；此标识不是签名，不抵御可信文件管理员篡改。尚未公开发布的旧开发库没有标识时拒绝打开，保留旧证据另建新lab，不自动迁移或清空。
