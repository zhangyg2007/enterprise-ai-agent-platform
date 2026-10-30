"""M10 supplemental create_agent lab: real framework, scripted offline model."""
import asyncio
import json

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from .workflow_store import FixtureActor, create_store


class ScriptedToolModel(FakeMessagesListChatModel):
    """Replays a known tool request; this is not an intelligent model."""

    def bind_tools(self, tools, **kwargs):
        return self


def build_readonly_agent(store, actor):
    """Bind identity outside model/tool arguments; expose one read-only operation."""
    if not isinstance(actor, FixtureActor) or actor.role != 'employee':
        raise PermissionError('trusted employee identity required')

    @tool
    def my_leave_balance() -> str:
        """Read the current trusted employee's synthetic leave balance."""
        value = store.read_resource(actor, 'leave', actor.employee_id)['value']
        return json.dumps({'remaining_days': value['remaining_days'], 'kind': 'synthetic'}, sort_keys=True)

    model = ScriptedToolModel(responses=[
        AIMessage(content='', tool_calls=[{'name': 'my_leave_balance', 'args': {}, 'id': 'fixture-call-1'}]),
        AIMessage(content='已读取合成余额，请以工具结果为准。'),
    ])
    return create_agent(model=model, tools=[my_leave_balance],
                        system_prompt='只读取可信员工本人余额；不要尝试写入或变更身份。')


async def _async_demo(agent):
    return await agent.ainvoke({'messages': [('user', '查询我的余额')]},
                               config={'recursion_limit': 8})


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lab', required=True, help='new synthetic SQLite file')
    args = parser.parse_args()
    store = create_store(args.lab)
    actor = FixtureActor(1, 2, 1, 'employee')
    sync = build_readonly_agent(store, actor).invoke(
        {'messages': [('user', '查询我的余额')]}, config={'recursion_limit': 8})
    asynchronous = asyncio.run(_async_demo(build_readonly_agent(store, actor)))
    def messages(result):
        return [json.loads(m.content) for m in result['messages'] if isinstance(m, ToolMessage)]
    print(json.dumps({'kind': 'scripted-fixture-not-real-model', 'invoke': messages(sync),
                      'ainvoke': messages(asynchronous)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
