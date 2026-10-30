"""Run three synthetic proposals with M09 citations and M11 approvals."""
import argparse
import json
from pathlib import Path
import sys

from .business_agent import OllamaBusinessPlanner, prepare, decide
from .document_samples import generate_corpus
from .document_pipeline import ingest
from .rag_index import build_index
from .rag_models import FixtureEncoder
from .workflow_graph import WorkflowLab
from .guarded_workflow import create_guarded_lab
from .workflow_store import FixtureActor, _arguments


def run_demo(output, *, planner=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    source = output/'sources'
    catalog = generate_corpus(source, formats='md')
    # This new synthetic policy is explicitly published to employees, not HR-elevated.
    leave = next(d for d in catalog['documents'] if d['tenant_id']==1 and d['document_id']=='hcm-0007')
    leave['roles'] = ['employee', 'hr']
    (source/'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
    state = output/'documents.sqlite'
    ingest(source/'catalog.json', source, state, output/'ingest.json')
    encoder = FixtureEncoder()
    index = build_index(state, 1, 1, encoder, output/'index.json')
    lab = create_guarded_lab(output/'lab')
    manager = FixtureActor(1, 4, 4, 'manager')
    cases = [
        (FixtureActor(1,1,1,'sales'), 'quote', '客户1购买商品1两件，折扣5%，生成报价草案',
         {'customer_id':1,'product_id':1,'quantity':2,'discount_percent':'5.00'}),
        (FixtureActor(1,2,1,'employee'), 'leave', '申请本人3天休假', {'days':3}),
        (FixtureActor(1,3,3,'procurement'), 'replenish', '为商品1申请补货', {'product_id':1}),
    ]
    completed = []
    for actor, case, request, arguments in cases:
        selected = planner or (lambda text, expected=case, args=arguments: {'case': expected, 'arguments': args})
        # A wrong model plan is rejected rather than counted as the intended scenario.
        def checked(text):
            value = selected(text)
            if (not isinstance(value, dict) or set(value) != {'case', 'arguments'}
                    or value.get('case') != case
                    or _arguments(case, value['arguments']) != _arguments(case, arguments)):
                raise ValueError('model did not propose the exact expected scenario and arguments')
            return value
        thread = 'demo-'+case
        context = prepare(lab, actor, thread, thread+'-key', request, checked, index, state, encoder,
                          as_of='2026-10-22')
        with (output/(thread+'.json')).open('x', encoding='utf-8') as stream:
            json.dump(context, stream, ensure_ascii=False, indent=2)
        # New instances prove state is read from disk. This actor is a simulated
        # manager in a local lab, not a claim of real user authentication.
        restored = WorkflowLab(output/'lab')
        for _ in range(2):
            result = decide(restored, manager, context, True, index, state, encoder)
        completed.append({'thread_id': thread, 'case': case, 'status': result['status'],
                          'citation_count': len(context['citations'])})
    report = {'kind': 'ollama-model-synthetic-business' if planner else 'synthetic-scripted-not-real-model',
              'cases': completed, 'effect_count': lab.store.effect_count(manager),
              'limitations': ['synthetic data and identities', 'simulated manager approval',
                              'policy and ledger not transactionally locked together',
                              'PostgreSQL guard and integrated HTTP route not yet implemented',
                              'policy index and document state require separate backup']}
    with (output/'summary.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', help='optional exact Ollama tag; still uses synthetic business data')
    args = parser.parse_args(argv)
    try:
        planner = None
        if args.model:
            from .ollama import OllamaClient
            client = OllamaClient(timeout_seconds=30)
            client.inventory(args.model)
            planner = OllamaBusinessPlanner(client, args.model)
        result = run_demo(args.output, planner=planner)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception:
        print('business demo failed; retain partial output and use a new directory', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
