"""M10: strict planning, trusted identity, bounded read-only StateGraph workflow."""
import json
import math
import operator
import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Annotated, TypedDict

from .ollama import ModelError, strict_json_loads


@dataclass(frozen=True)
class Principal:
    tenant_id: int
    reader: str
    roles: tuple
    as_of: str = '2026-10-22'
    employee_id: int = 1

    def __post_init__(self):
        if (type(self.tenant_id) is not int or (self.reader,self.tenant_id) not in (('a',1),('b',2))
                or type(self.employee_id) is not int or self.employee_id<1
                or not isinstance(self.roles,tuple) or not self.roles
                or any(r not in ('sales','employee','hr','procurement') for r in self.roles)):
            raise ValueError('invalid trusted principal')
        date.fromisoformat(self.as_of)


def decision(value, principal):
    if not isinstance(value,dict) or set(value)!={'tool','arguments'}:
        raise ValueError('invalid decision fields')
    tool=value['tool']; args=value['arguments']
    fields={'customer':{'customer_id'},'inventory':{'product_id'},'leave':set(),
            'knowledge':{'query'},'clarify':set(),'refuse':set()}
    if not isinstance(tool,str) or tool not in fields or not isinstance(args,dict) or set(args)!=fields[tool]:
        raise ValueError('unknown tool or arguments')
    for key in ('customer_id','product_id'):
        if key in args and (type(args[key]) is not int or not 1<=args[key]<=1000000):
            raise ValueError('invalid id')
    if tool=='knowledge': _request(args['query'])
    access={'customer':{'sales'},'leave':{'employee','hr'},'inventory':{'procurement'}}
    if tool in access and not access[tool].intersection(principal.roles):
        raise ValueError('tool role denied')
    return json.loads(json.dumps(value,ensure_ascii=False))


def _request(text):
    if not isinstance(text,str) or not text.strip() or len(text)>500 or any(ord(c)<32 for c in text):
        raise ValueError('request must be 1..500 printable characters')


class State(TypedDict, total=False):
    request: str
    status: str
    plan: dict
    calls: int
    result: dict
    answer: str
    events: Annotated[list[dict], operator.add]


class Input(TypedDict):
    request: str


class Runner:
    def __init__(self, principal, planner, tools, *, attempts=2, seconds=60, clock=time.monotonic):
        if type(attempts) is not int or not 1<=attempts<=3:
            raise ValueError('attempts must be 1..3')
        if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 1<=seconds<=300:
            raise ValueError('seconds must be 1..300')
        self.principal=principal; self.planner=planner; self.tools=tools
        self.attempts=attempts; self.seconds=seconds; self.clock=clock; self.deadline=None

    def expired(self): return self.clock()>=self.deadline

    def plan(self,state):
        self.deadline=self.clock()+self.seconds
        try:
            _request(state.get('request'))
            plan=decision(self.planner(state['request']),self.principal)
            status=plan['tool'] if plan['tool'] in ('clarify','refuse') else 'ready'
            if self.expired(): status='budget'
            return {'plan':plan,'status':status,'calls':0,'events':[{'node':'plan','status':status}]}
        except (ValueError,TypeError,KeyError): status='rejected'
        except Exception: status='planning_failed'
        return {'status':status,'calls':0,'events':[{'node':'plan','status':status}]}

    def tool(self,state):
        if self.expired(): return {'status':'budget','events':[{'node':'tool','status':'budget'}]}
        calls=state['calls']+1
        if calls>self.attempts: return {'status':'unavailable'}
        try:
            plan=decision(state['plan'],self.principal)
            result=self.tools.call(self.principal,plan)
            if self.expired(): return {'calls':calls,'status':'budget','events':[{'node':'tool','status':'budget'}]}
            if not isinstance(result,dict) or len(json.dumps(result,ensure_ascii=False,allow_nan=False).encode())>65536:
                raise ValueError('invalid or oversized tool result')
            return {'calls':calls,'result':result,'status':'ok','events':[{'node':'tool','status':'ok','attempt':calls}]}
        except (TimeoutError,ConnectionError): status='retry' if calls<self.attempts else 'unavailable'
        except Exception: status='failed'
        return {'calls':calls,'status':status,'events':[{'node':'tool','status':status,'attempt':calls}]}

    def finish(self,state):
        status=state['status']; answer='请求未执行成功：'+status
        if status=='ok':
            result=state['result']
            if result.get('answer',{}).get('status')=='insufficient' or result.get('rows')==[]:
                status='insufficient'; answer='当前授权范围内没有足够结果。'
            else: answer=json.dumps(result,ensure_ascii=False,allow_nan=False)
        elif status=='clarify': answer='请补充业务类型与必要编号，例如客户1或商品1；休假仅查询本人。'
        elif status=='refuse': answer='本课只支持白名单只读查询，不执行写入或任意SQL。'
        return {'status':status,'answer':answer,'events':[{'node':'finish','status':status}]}


def build_graph(runner):
    from langgraph.graph import StateGraph, START, END
    graph=StateGraph(State,input_schema=Input)
    for name in ('plan','tool','finish'): graph.add_node(name,getattr(runner,name))
    graph.add_edge(START,'plan')
    graph.add_conditional_edges('plan',lambda s:'tool' if s['status']=='ready' else 'finish', ['tool','finish'])
    graph.add_conditional_edges('tool',lambda s:'tool' if s['status']=='retry' else 'finish', ['tool','finish'])
    graph.add_edge('finish',END)
    return graph.compile()


class FixturePlanner:
    def __call__(self,text):
        if any(w in text.lower() for w in ('delete','drop','更新','删除','批准')):
            return {'tool':'refuse','arguments':{}}
        for word,tool,key in [('客户','customer','customer_id'),('商品','inventory','product_id')]:
            if word in text:
                match=re.search(word+r'\s*(\d+)',text)
                return {'tool':tool,'arguments':{key:int(match[1])}} if match else {'tool':'clarify','arguments':{}}
        if '休假' in text: return {'tool':'leave','arguments':{}}
        return {'tool':'knowledge','arguments':{'query':text}}


class OllamaPlanner:
    def __init__(self,client,model): self.client=client; self.model=model

    def __call__(self,text):
        schema={'type':'object','properties':{'tool':{'type':'string','enum':['customer','leave','inventory','knowledge','clarify','refuse']},
                'arguments':{'type':'object'}},'required':['tool','arguments'],'additionalProperties':False}
        system=('只输出JSON计划，不执行工具。tool/arguments契约：customer:{customer_id:正整数}；'
                'inventory:{product_id:正整数}；leave:{}只查本人；knowledge:{query:原问题}；'
                'clarify:{}缺编号；refuse:{}写入/任意SQL/越权请求。禁止tenant/roles/employee/SQL字段。')
        raw=self.client.chat(self.model,[{'role':'system','content':system},{'role':'user','content':text}],
                             format_schema=schema,options={'temperature':0,'num_predict':192},think=False)
        return strict_json_loads(raw['message']['content'])
