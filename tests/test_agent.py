import unittest
from enterprise_ai.agent import Principal, Runner, build_graph, FixturePlanner


class Tools:
    def __init__(self, failures=0, error=TimeoutError, value=None):
        self.calls = 0; self.failures = failures; self.error = error
        self.value = {'kind': 'synthetic-fixture', 'rows': [{'id': 1}]} if value is None else value
    def call(self, principal, decision):
        self.calls += 1
        if self.calls <= self.failures: raise self.error('private connection credentials')
        return self.value


class AgentTests(unittest.TestCase):
    def graph(self, plan, tools=None, **kwargs):
        return build_graph(Runner(Principal(1, 'a', ('sales',)), lambda q: plan, tools or Tools(), **kwargs))

    def test_genuine_graph_invocation_and_updates(self):
        g=self.graph({'tool':'customer','arguments':{'customer_id':1}})
        result=g.invoke({'request':'客户1'})
        self.assertEqual(result['status'],'ok'); self.assertEqual(result['calls'],1)
        g=self.graph({'tool':'customer','arguments':{'customer_id':1}})
        events=list(g.stream({'request':'客户1'}, stream_mode='updates'))
        self.assertEqual([next(iter(e)) for e in events], ['plan','tool','finish'])

    def test_model_authority_extra_args_and_wrong_role_never_reach_backend(self):
        plans=[{'tool':'sql','arguments':{'sql':'DROP TABLE x'}},
               {'tool':'customer','arguments':{'customer_id':1,'tenant_id':2}},
               {'tool':'leave','arguments':{'employee_id':2}},
               {'tool':'inventory','arguments':{'product_id':1}},
               {'tool':'customer','arguments':{'customer_id':True}},
               {'tool':'customer','arguments':{'customer_id':1},'roles':['hr']}]
        for plan in plans:
            backend=Tools(); r=self.graph(plan,backend).invoke({'request':'请求'})
            self.assertEqual(r['status'],'rejected'); self.assertEqual(backend.calls,0)

    def test_retries_only_transient_and_keeps_bound(self):
        plan={'tool':'customer','arguments':{'customer_id':1}}
        backend=Tools(failures=1); r=self.graph(plan,backend).invoke({'request':'客户1'})
        self.assertEqual((r['status'],r['calls']),('ok',2))
        backend=Tools(failures=5); r=self.graph(plan,backend).invoke({'request':'客户1'})
        self.assertEqual((r['status'],r['calls']),('unavailable',2))
        backend=Tools(failures=5,error=ValueError); r=self.graph(plan,backend).invoke({'request':'客户1'})
        self.assertEqual((r['status'],r['calls']),('failed',1))
        self.assertNotIn('credentials',str(r))

    def test_deadline_after_tool_discards_result_and_oversize_is_permanent(self):
        plan={'tool':'customer','arguments':{'customer_id':1}}
        clock=[0]
        backend=Tools()
        call=backend.call
        def late(*args): clock[0]=10; return call(*args)
        backend.call=late
        r=self.graph(plan,backend,seconds=1,clock=lambda:clock[0]).invoke({'request':'客户1'})
        self.assertEqual(r['status'],'budget'); self.assertNotIn('result',r)
        r=self.graph(plan,Tools(value={'rows':['x'*70000]})).invoke({'request':'客户1'})
        self.assertEqual(r['status'],'failed'); self.assertNotIn('result',r)

    def test_empty_result_clarification_and_invalid_principal(self):
        r=self.graph({'tool':'customer','arguments':{'customer_id':1}},Tools(value={'rows':[]})).invoke({'request':'客户1'})
        self.assertEqual(r['status'],'insufficient')
        r=self.graph({'tool':'clarify','arguments':{}}).invoke({'request':'客户'})
        self.assertEqual(r['status'],'clarify'); self.assertEqual(r['calls'],0)
        with self.assertRaises(ValueError): Principal(2,'a',('sales',))
        r=self.graph({'tool':'clarify','arguments':{}}).invoke({'request':'x'*501})
        self.assertEqual(r['status'],'rejected')

    def test_fixture_planner_covers_missing_id_and_knowledge(self):
        p=FixturePlanner()
        self.assertEqual(p('客户')['tool'],'clarify')
        self.assertEqual(p('客户12订单')['arguments'],{'customer_id':12})
        self.assertEqual(p('报价审批制度')['tool'],'knowledge')


if __name__=='__main__': unittest.main()
