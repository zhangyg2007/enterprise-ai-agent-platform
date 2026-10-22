"""M10 executable lab. Fixture and real-model modes are always distinguished."""
import argparse
import importlib.metadata
import json
import sys
from pathlib import Path

from .agent import Principal, Runner, FixturePlanner, OllamaPlanner, build_graph
from .agent_tools import FixtureTools, LiveTools
from .document_samples import generate_corpus
from .document_pipeline import ingest
from .rag_index import build_index, load_index, write_new
from .rag_models import FixtureEncoder, LocalEncoder
from .ollama import OllamaClient
from .settings import load_settings


def execute(output,request,principal,planner,tools,*,kind,attempts=2,seconds=60,clock=None,stream=False,model=None):
    runner=Runner(principal,planner,tools,attempts=attempts,seconds=seconds,**({'clock':clock} if clock else {}))
    graph=build_graph(runner); updates=[]; final=None
    for channel,value in graph.stream({'request':request},stream_mode=['updates','values'],config={'recursion_limit':10}):
        if channel=='values': final=value
        else:
            updates.append(value)
            if stream:
                for node,update in value.items(): print(json.dumps({'node':node,'status':update.get('status')},ensure_ascii=False),flush=True)
    report={'schema_version':'m10-run-v1','kind':kind,'langgraph':importlib.metadata.version('langgraph'),
            'principal':{'tenant_id':principal.tenant_id,'reader':principal.reader,'roles':principal.roles,'as_of':principal.as_of},
            'budget':{'attempts':attempts,'soft_seconds':seconds},'model':model,'state':final}
    write_new(Path(output)/'events.json',updates); write_new(Path(output)/'state.json',final)
    write_new(Path(output)/'summary.json',report)
    return report


def run_fixture(output,request='报价审批制度',*,scenario='normal',principal=None,stream=False):
    if scenario not in ('normal','retry','permanent','invalid-plan','budget'): raise ValueError('invalid scenario')
    principal=principal or Principal(1,'a',('sales',))
    output=Path(output); output.mkdir(parents=True,exist_ok=False)
    sources=output/'sources'; generate_corpus(sources,formats='md')
    state=output/'documents.sqlite'; ingest(sources/'catalog.json',sources,state,output/'ingest.json')
    encoder=FixtureEncoder(); index=build_index(state,principal.tenant_id,1,encoder,output/'index.json')
    base=FixtureTools(index,state,encoder); tick=[0]; calls=[0]
    class Scenario:
        def call(self,p,plan):
            calls[0]+=1
            if scenario=='retry' and calls[0]==1: raise TimeoutError('synthetic timeout')
            if scenario=='permanent': raise ValueError('synthetic permanent failure')
            result=base.call(p,plan)
            if scenario=='budget': tick[0]=61
            return result
    planner=FixturePlanner() if scenario!='invalid-plan' else lambda q:{'tool':'sql','arguments':{'sql':'arbitrary'}}
    return execute(output,request,principal,planner,Scenario(),kind='synthetic-fixture-not-live-agent',
                   clock=lambda:tick[0],stream=stream)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    choice=parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--fixture',action='store_true'); choice.add_argument('--live',action='store_true')
    parser.add_argument('--output',type=Path,required=True); parser.add_argument('--request',default='报价审批制度')
    parser.add_argument('--scenario',choices=('normal','retry','permanent','invalid-plan','budget'),default='normal')
    parser.add_argument('--reader',choices=('a','b'),default='a'); parser.add_argument('--roles',nargs='+',default=['sales'])
    parser.add_argument('--as-of',default='2026-10-22'); parser.add_argument('--stream',action='store_true')
    parser.add_argument('--model'); parser.add_argument('--base-url',default='http://127.0.0.1:11434')
    parser.add_argument('--index',type=Path); parser.add_argument('--state',type=Path); parser.add_argument('--embedding',type=Path)
    parser.add_argument('--attempts',type=int,default=2); parser.add_argument('--seconds',type=float,default=60)
    args=parser.parse_args(argv)
    try:
        principal=Principal(1 if args.reader=='a' else 2,args.reader,tuple(args.roles),args.as_of)
        if args.fixture:
            if args.attempts!=2 or args.seconds!=60 or args.model or any((args.index,args.state,args.embedding)):
                raise ValueError('fixture uses fixed scenario budgets and generated evidence')
            report=run_fixture(args.output,args.request,scenario=args.scenario,principal=principal,stream=args.stream)
        else:
            if not args.model or args.scenario!='normal': raise ValueError('live mode needs exact model, no fixture scenario')
            if any((args.index,args.state,args.embedding)) and not all((args.index,args.state,args.embedding)):
                raise ValueError('supply index/state/embedding together')
            args.output.mkdir(parents=True,exist_ok=False)
            client=OllamaClient(args.base_url,timeout_seconds=min(30,args.seconds))
            identity=client.inventory(args.model)
            model={k:identity[k] for k in ('model','digest','server_version','template_sha256')}
            tools=LiveTools(load_settings(),load_index(args.index) if args.index else None,args.state,
                            LocalEncoder(args.embedding) if args.embedding else None)
            report=execute(args.output,args.request,principal,OllamaPlanner(client,args.model),tools,
                           kind='live-model-readonly-workflow',attempts=args.attempts,seconds=args.seconds,stream=args.stream,model=model)
        print(json.dumps({'status':report['state']['status'],'kind':report['kind'],'langgraph':report['langgraph']},ensure_ascii=False))
        return 0 if report['state']['status'] in ('ok','clarify','refuse','insufficient') else 1
    except Exception as exc:
        print('M10 operation failed: '+type(exc).__name__,file=sys.stderr); return 1


if __name__=='__main__': raise SystemExit(main())
