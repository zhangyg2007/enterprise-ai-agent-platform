import asyncio
from pathlib import Path
import tempfile
import unittest

from langchain_core.messages import ToolMessage
from enterprise_ai.workflow_store import FixtureActor, create_store
from enterprise_ai.advanced_agent import build_readonly_agent


class AdvancedAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = create_store(Path(self.tmp.name)/'lab.sqlite')
        self.actor = FixtureActor(1, 2, 1, 'employee')

    def test_create_agent_invoke_uses_real_readonly_tool_and_trusted_actor(self):
        agent = build_readonly_agent(self.store, self.actor)
        result = agent.invoke({'messages': [('user', '查询我的余额')]}, config={'recursion_limit': 8})
        tools = [m for m in result['messages'] if isinstance(m, ToolMessage)]
        self.assertEqual(len(tools), 1)
        self.assertIn('15', tools[0].content)
        self.assertEqual(self.store.effect_count(FixtureActor(1,4,4,'manager')), 0)

    def test_create_agent_ainvoke_and_no_model_supplied_identity(self):
        agent = build_readonly_agent(self.store, self.actor)
        result = asyncio.run(agent.ainvoke({'messages': [('user', '查询他人的余额')]},
                                          config={'recursion_limit': 8}))
        tools = [m for m in result['messages'] if isinstance(m, ToolMessage)]
        self.assertEqual(len(tools), 1)
        self.assertIn('15', tools[0].content)
        schema = agent.get_graph().nodes
        self.assertTrue(schema)
        self.assertEqual(self.store.effect_count(FixtureActor(1,4,4,'manager')), 0)

    def test_only_employee_identity_accepted(self):
        with self.assertRaises(PermissionError):
            build_readonly_agent(self.store, FixtureActor(1, 1, 1, 'sales'))
