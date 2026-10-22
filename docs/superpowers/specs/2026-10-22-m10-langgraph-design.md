# M10 LangGraph 入门设计（接续已确认的M10范围）

目标：把M02只读查询、M09可引用检索与模型规划接到真实LangGraph StateGraph。先用显式fixture逐节点理解状态、路由、有限重试和updates流，再以Ubuntu现有Ollama/数据库/知识库运行真实冒烟。本章不做业务写入、持久化、人审或生产认证，留给M11/M12。

约束：沿用Python>=3.11,<3.14；教程应用候选3.12。开发验证将langgraph1.0.2加载到被忽略的reports/local/m10-runtime，不修改全局环境、不下载模型或启动服务。实际LangGraph构图/执行/stream必须运行验证，不能把自制调度器冒充框架。锁定核心依赖并保存开发环境版本；Ubuntu另验收。

结构：agent_runtime.py（Principal/策略/节点/StateGraph），agent_tools.py（显式fixture数据和真实只读DB/RAG适配），agent_planner.py（规则fixture与Ollama JSON计划），agent_demo.py（CLI/demo/失败实验）。配三组测试、requirements/config、教程10、服务器runbook与validation，Filelist/plan/README/ROADMAP同步。

图：START->plan->tool或finish；tool的暂态失败在预算内重试自身；成功或永久失败->finish->END。单次模型只规划一次，不让模型自行执行函数。状态含request/status/plan/calls/result/answer/events，events用追加reducer；不保存settings密码或模型原始异常。updates是节点进展，不冒充token流。

Principal由可信CLI传入并绑定于节点闭包，tenant为1/2，reader为a/b相配，roles为已知角色集合；当前不等于服务端认证。模型不可设置tenant/reader/roles/employee/SQL。工具白名单customer(customer_id)、leave()、inventory(product_id)、knowledge(query)，另clarify/refuse无执行；参数严格字段、正整数与长度。角色customer sales，leave employee或hr（只查当前DB身份本人），inventory procurement，knowledge由M09角色过滤。任意未知字段/写动作拒绝。

真实DB：只用M02 reader连接；核对非super/bypass和auth.current_tenant/current_employee与绑定上下文一致；固定参数化CUSTOMER/LEAVE/INVENTORY，事务只读、statement_timeout，最多50行。leave按数据库session_user映射本人，不接受employee参数。金额转字符串保精度；实时结果不当作模型记忆。知识库调用M09 search/evidence_answer，索引tenant必须匹配Principal并核对freshness，引用原样保留。

预算：1次规划调用；每个请求最多1..3次只读工具尝试，默认2；软时限1..300秒默认60，在调用前后检查，不能强制终止已阻塞I/O。HTTP socket timeout与SQL statement_timeout独立。只重试显式暂态Timeout/Connection类错误；权限、参数、stale和其他异常不重试。失败输出安全代码，不打印连接详情。限制问题500字符、工具输出64KiB、固定HTTP token上限和Graph recursion_limit。

回答：确定性展示已获得工具结果/引用，不再让模型编写未经核对的业务结论；有模型决策的工作流入门，不宣称多步自主Agent或完成四组生成式RAG评估。空结果或无证据明确不足。M11再增加业务任务与审批。

CLI：--fixture创建全新实验目录，生成M08 MD并用FixtureEncoder构建真实M09管线；DB结果显式synthetic-fixture。--live使用已存在.env、Ollama确切模型标签和可选RAG包；只运行用户选择的只读请求。--fixture --scenario提供normal/retry/permanent/invalid-plan/budget的可追溯演示。输出state.json/events.json，不覆盖路径，报告LangGraph和fixture/真实后端身份，不保存原始秘密。

验收：实际StateGraph节点顺序与updates、条件澄清、工具ACL/注入参数拒绝（backend未被调用）、本人leave不受模型控制、有限重试/永久失败、软时限、结果大小和无凭证异常、RAG旧包拒绝与引用。Ollama用已有HTTP测试服务验证真实请求契约，真实模型/数据库/GPU仍按Ubuntu runbook验收。完整suite包含既有217项；新增可选真实Graph集成测试应明确运行而非默默skip。
