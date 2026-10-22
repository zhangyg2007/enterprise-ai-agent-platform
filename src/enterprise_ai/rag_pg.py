"""Ubuntu pgvector adapter. Offline contract tests do not verify a live database."""
import argparse
import json
import math
import re
import sys
from pathlib import Path

from .db import connect
from .settings import load_settings
from .document_pipeline import _json
from .rag_index import assert_fresh, load_index
from .rag_models import ENCODER_REVISION, vectors, encoder_options, make_encoder, LocalReranker
from .rag_search import allowed, options, rank_result, evidence_answer, search

FILTER = 'collection_id=%s AND tenant_id=%s AND roles && %s::text[] AND effective_from<=%s::date AND (effective_to IS NULL OR effective_to>%s::date)'


def _collection(name):
    if not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',name):
        raise ValueError('invalid collection id')


def _real(index):
    model=index['model']
    if (model.get('kind')!='local-transformers' or model.get('model')!='BAAI/bge-m3'
            or model.get('revision')!=ENCODER_REVISION or model.get('pooling')!='cls' or not model.get('assets')):
        raise ValueError('pgvector requires a real pinned BGE-M3 package; fixture is offline only')


def _vector(row):
    vectors([row],1)
    return '['+','.join(format(v,'.9g') for v in row)+']'


def _content_matches(conn,collection,index):
    rows=conn.execute('SELECT chunk_id,payload,embedding::text FROM rag_lab.chunks WHERE collection_id=%s ORDER BY chunk_id',(collection,)).fetchall()
    expected={c['chunk_id']:(c,v) for c,v in zip(index['chunks'],index['vectors'])}
    if len(rows)!=len(expected): raise ValueError('persisted chunk count differs')
    for key,payload,vector in rows:
        if key not in expected or payload!=expected[key][0]: raise ValueError('persisted chunk differs')
        actual=json.loads(vector)
        if len(actual)!=1024 or any(not math.isclose(a,b,rel_tol=1e-5,abs_tol=1e-6) for a,b in zip(actual,expected[key][1])):
            raise ValueError('persisted embedding differs')


def load(settings,collection,index,state):
    _collection(collection); assert_fresh(index,state); _real(index)
    with connect(settings) as conn,conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(collection,))
        previous=conn.execute('SELECT tenant_id,generation,digest,model FROM rag_lab.collections WHERE collection_id=%s FOR UPDATE',(collection,)).fetchone()
        if previous:
            tenant,generation,digest,model=previous
            if tenant!=index['tenant_id'] or generation>index['generation']:
                raise ValueError('collection tenant mismatch/generation rollback')
            if generation==index['generation']:
                if digest!=index['digest'] or model!=index['model']: raise ValueError('same generation conflict')
                _content_matches(conn,collection,index); assert_fresh(index,state)
                return {'status':'unchanged','generation':generation,'chunks':len(index['chunks'])}
        conn.execute('INSERT INTO rag_lab.collections VALUES (%s,%s,%s,%s,%s::jsonb) ON CONFLICT(collection_id) DO UPDATE SET generation=excluded.generation,digest=excluded.digest,model=excluded.model',
                     (collection,index['tenant_id'],index['generation'],index['digest'],_json(index['model'])))
        # Only the named derived index is replaced; source documents/history stay in M08.
        conn.execute('DELETE FROM rag_lab.chunks WHERE collection_id=%s',(collection,))
        for chunk,vector in zip(index['chunks'],index['vectors']):
            conn.execute('INSERT INTO rag_lab.chunks VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::vector)',
                         (collection,index['tenant_id'],chunk['chunk_id'],chunk['roles'],chunk['effective_from'],chunk['effective_to'],_json(chunk),_vector(vector)))
        assert_fresh(index,state)
    return {'status':'loaded','generation':index['generation'],'chunks':len(index['chunks'])}


def check_reader(conn,tenant,roles):
    flags=conn.execute('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
    if flags!=(False,False): raise ValueError('query requires a non-superuser, non-bypass reader')
    context=conn.execute('SELECT auth.current_tenant(),rag_lab.current_roles()').fetchone()
    if not context or context[0]!=tenant or not set(roles)<=set(context[1] or []):
        raise ValueError('requested tenant/roles exceed authenticated database identity')


def dense_sql():
    return 'SELECT chunk_id,embedding <=> %s::vector AS distance FROM rag_lab.chunks WHERE '+FILTER+' ORDER BY embedding <=> %s::vector LIMIT %s'


def retrieve(settings,reader,collection,index,state,query,*,roles,as_of,method='exact',mode='hybrid',k=5,candidates=20,encoder,reranker=None):
    options(query,roles,as_of,mode,k,candidates); _collection(collection); assert_fresh(index,state); _real(index)
    if reader not in ('a','b') or method not in ('exact','hnsw'): raise ValueError('invalid reader/method')
    if encoder.identity!=index['model']: raise ValueError('query embedding space mismatch')
    encoder.verify()
    q=_vector(vectors(encoder.encode([query]),1)[0]) if mode!='keyword' else None
    scope=(collection,index['tenant_id'],roles,as_of,as_of)
    with connect(settings,reader) as conn,conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        check_reader(conn,index['tenant_id'],roles)
        marker=conn.execute('SELECT tenant_id,generation,digest,model FROM rag_lab.collections WHERE collection_id=%s',(collection,)).fetchone()
        if marker!=(index['tenant_id'],index['generation'],index['digest'],index['model']):
            raise ValueError('database collection differs from current index package')
        # Bound teaching corpus. Filter in SQL before loading candidate text or reranking.
        rows=conn.execute('SELECT payload FROM rag_lab.chunks WHERE '+FILTER+' ORDER BY chunk_id LIMIT 10001',scope).fetchall()
        if len(rows)>10000: raise ValueError('keyword candidate limit exceeded')
        expected={c['chunk_id']:c for c in index['chunks']}
        chunks=[r[0] for r in rows]
        for chunk in chunks:
            if chunk!=expected.get(chunk.get('chunk_id')) or not allowed(chunk,roles,as_of):
                raise ValueError('unexpected/unauthorized persisted candidate')
        dense=[]; plan=None
        if q is not None:
            if method=='exact':
                conn.execute('SET LOCAL enable_indexscan=off'); conn.execute('SET LOCAL enable_bitmapscan=off')
            else:
                conn.execute("SET LOCAL hnsw.iterative_scan='strict_order'"); conn.execute('SET LOCAL hnsw.ef_search=100')
            params=(q,*scope,q,candidates)
            plan=conn.execute('EXPLAIN (FORMAT JSON) '+dense_sql(),params).fetchone()[0]
            actual=conn.execute(dense_sql(),params).fetchall()
            keys={c['chunk_id'] for c in chunks}
            for key,distance in actual:
                if key not in keys or not math.isfinite(distance): raise ValueError('invalid dense candidate')
                dense.append((key,1-distance))
        result=rank_result(index,query,chunks,dense,mode=mode,k=k,candidates=candidates,reranker=reranker)
    encoder.verify(); assert_fresh(index,state)
    return {**result,'tenant_id':index['tenant_id'],'roles':sorted(roles),'as_of':as_of,'backend':'pgvector',
            'diagnostics':{'requested_method':method,'plan':plan,'dense_candidates':len(dense),
                           'authorized_chunks':len(chunks),'shortfall':q is not None and len(dense)<min(candidates,len(chunks))}}


def verify(settings,reader,collection,index,state,query,*,roles,as_of,encoder):
    """Live-server experiment. ANN overlap is measured, never assumed to be 100%."""
    kwargs=dict(roles=roles,as_of=as_of,encoder=encoder,mode='dense')
    baseline=search(index,state,query,**kwargs)
    exact=retrieve(settings,reader,collection,index,state,query,method='exact',**kwargs)
    ann=retrieve(settings,reader,collection,index,state,query,method='hnsw',**kwargs)
    a={h['chunk']['chunk_id'] for h in exact['hits']}; b={h['chunk']['chunk_id'] for h in ann['hits']}
    # Ties can differ because PostgreSQL stores float32. Retain raw rankings for inspection.
    return {'kind':'live-pgvector-experiment','index_digest':index['digest'],'python':baseline,'exact':exact,
            'hnsw':ann,'hnsw_overlap_at_k':len(a&b)/len(a) if a else None,
            'manual_checks':['compare Python/exact score tolerances and tied ranks','inspect actual EXPLAIN plan','test second reader and changed M08 heads']}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('load','search','verify'))
    parser.add_argument('--index',type=Path,required=True); parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--collection',required=True); parser.add_argument('--reader',choices=('a','b'),default='a')
    parser.add_argument('--query',default='报价审批'); parser.add_argument('--roles',nargs='+',default=['sales'])
    parser.add_argument('--as-of',default='2026-10-21'); parser.add_argument('--method',choices=('exact','hnsw'),default='exact')
    parser.add_argument('--mode',choices=('dense','keyword','hybrid'),default='hybrid')
    parser.add_argument('--model',type=Path); parser.add_argument('--device',choices=('cpu','cuda'),default='cpu')
    parser.add_argument('--reranker',type=Path)
    args=parser.parse_args(argv)
    try:
        index=load_index(args.index); settings=load_settings()
        if args.command=='load': report=load(settings,args.collection,index,args.state)
        else:
            if args.model is None: raise ValueError('--model is required for retrieval')
            args.fixture=False; encoder=make_encoder(args)
            kwargs=dict(roles=args.roles,as_of=args.as_of,encoder=encoder)
            if args.command=='verify': report=verify(settings,args.reader,args.collection,index,args.state,args.query,**kwargs)
            else:
                result=retrieve(settings,args.reader,args.collection,index,args.state,args.query,method=args.method,mode=args.mode,
                                reranker=LocalReranker(args.reranker,device=args.device) if args.reranker else None,**kwargs)
                report={'retrieval':result,'answer':evidence_answer(result)}
        print(_json(report)); return 0
    except Exception as exc:
        # Do not echo connection details, credentials, or arbitrary database diagnostics.
        print('M09 database operation failed: '+type(exc).__name__,file=sys.stderr); return 1


if __name__=='__main__': raise SystemExit(main())
