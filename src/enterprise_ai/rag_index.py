"""Complete current-head snapshots and immutable, freshness-checked index packages."""
import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from .document_pipeline import _check_database, _digest, _entry, _integer, _json, _no_links
from .ollama import strict_json_loads
from .rag_models import DIMENSION, encoder_options, make_encoder, vectors


def _validate_source(source):
    if not _integer(source['tenant_id']) or not isinstance(source['heads'],list) or not isinstance(source['chunks'],list):
        raise ValueError('invalid source snapshot')
    if len(source['chunks'])>10000: raise ValueError('teaching limit: 10000 chunks')
    heads={}
    for head in source['heads']:
        meta=_entry(head['metadata'])
        if meta['tenant_id']!=source['tenant_id'] or meta['document_id'] in heads:
            raise ValueError('source tenant/duplicate head mismatch')
        if head['entry_digest']!=_digest({'entry':meta,'pipeline_id':head['pipeline_id']}):
            raise ValueError('source version metadata mismatch')
        heads[meta['document_id']]=head
    seen=set(); populated=set()
    for chunk in source['chunks']:
        head=heads.get(chunk.get('document_id'))
        if not head or head['metadata']['op']!='upsert': raise ValueError('chunk without upsert head')
        meta=head['metadata']
        for key in ('tenant_id','document_id','version','title','domain','roles','effective_from','effective_to','source_path'):
            if chunk.get(key)!=meta[key]: raise ValueError('chunk metadata differs from head')
        if (chunk.get('schema_version')!='m08-chunk-v1' or chunk.get('source_sha256')!=meta['sha256']
                or chunk.get('pipeline_id')!=head['pipeline_id'] or chunk.get('embedding_model_revision') is not None
                or not isinstance(chunk.get('text'),str) or not chunk['text']):
            raise ValueError('invalid source chunk contract')
        if chunk.get('content_sha256')!=hashlib.sha256(chunk['text'].encode()).hexdigest():
            raise ValueError('chunk text hash mismatch')
        if chunk.get('chunk_id')!=_digest({k:v for k,v in chunk.items() if k!='chunk_id'}) or chunk['chunk_id'] in seen:
            raise ValueError('chunk id mismatch/duplicate')
        seen.add(chunk['chunk_id']); populated.add(chunk['document_id'])
    if populated!={key for key,h in heads.items() if h['metadata']['op']=='upsert'}:
        raise ValueError('incomplete upsert chunks')


def snapshot(state,tenant):
    if not _integer(tenant): raise ValueError('invalid tenant')
    path=_no_links(state).resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db,db:
        db.execute('BEGIN'); _check_database(db)
        rows=db.execute('SELECT v.metadata,v.pipeline_id,v.entry_digest FROM heads h JOIN versions v USING(tenant_id,document_id,version) WHERE h.tenant_id=? ORDER BY h.document_id',(tenant,)).fetchall()
        count=db.execute('SELECT COUNT(*) FROM heads WHERE tenant_id=?',(tenant,)).fetchone()[0]
        if count!=len(rows): raise ValueError('dangling head')
        heads=[{'metadata':strict_json_loads(r[0]),'pipeline_id':r[1],'entry_digest':r[2]} for r in rows]
        rows=db.execute('SELECT c.payload FROM chunks c JOIN heads h USING(tenant_id,document_id,version) WHERE h.tenant_id=? ORDER BY c.document_id,c.ordinal LIMIT 10001',(tenant,)).fetchall()
    source={'tenant_id':tenant,'heads':heads,'chunks':[strict_json_loads(r[0]) for r in rows]}
    _validate_source(source)
    return {**source,'source_digest':_digest(source)}


def validate_index(index):
    try:
        if set(index)!={'schema_version','tenant_id','generation','source_digest','heads','chunks','vectors','model','digest'}:
            raise ValueError('invalid index fields')
        if index['schema_version']!='m09-index-v1' or not _integer(index['generation']):
            raise ValueError('invalid index schema/generation')
        source={k:index[k] for k in ('tenant_id','heads','chunks')}
        _validate_source(source)
        if index['source_digest']!=_digest(source): raise ValueError('source snapshot digest mismatch')
        model=index['model']
        if (not isinstance(model,dict) or model.get('dimension')!=DIMENSION or model.get('normalized') is not True
                or model.get('kind') not in ('synthetic-fixture','local-transformers') or not model.get('revision')):
            raise ValueError('invalid embedding identity')
        vectors(index['vectors'],len(index['chunks']))
        if index['digest']!=_digest({k:v for k,v in index.items() if k!='digest'}):
            raise ValueError('index digest mismatch')
    except (KeyError,TypeError,OverflowError) as exc:
        raise ValueError('invalid index structure') from exc
    return index


def assert_fresh(index,state):
    validate_index(index)
    current=snapshot(state,index['tenant_id'])
    if current['source_digest']!=index['source_digest']:
        raise ValueError('stale index: rebuild from current M08 heads before serving')


def write_new(path,value):
    path=_no_links(path)
    with path.open('x',encoding='utf-8',newline='\n') as stream:
        stream.write(_json(value)+'\n')


def build_index(state,tenant,generation,encoder,output):
    if not _integer(generation): raise ValueError('generation must be a positive integer')
    output=_no_links(output)
    if output.exists(): raise FileExistsError(output)
    before=snapshot(state,tenant)
    encoder.verify()
    rows=vectors(encoder.encode([c['text'] for c in before['chunks']]),len(before['chunks']))
    encoder.verify()
    if snapshot(state,tenant)['source_digest']!=before['source_digest']:
        raise ValueError('source changed during embedding')
    index={**before,'schema_version':'m09-index-v1','generation':generation,'model':encoder.identity,'vectors':rows}
    index['digest']=_digest(index)
    validate_index(index); write_new(output,index)
    return index


def load_index(path):
    with _no_links(path).open('rb') as stream: raw=stream.read(256*1024*1024+1)
    if len(raw)>256*1024*1024: raise ValueError('index exceeds 256MiB teaching limit')
    return validate_index(strict_json_loads(raw.decode('utf-8')))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,required=True); parser.add_argument('--tenant',type=int,required=True)
    parser.add_argument('--generation',type=int,required=True); parser.add_argument('--output',type=Path,required=True)
    encoder_options(parser); args=parser.parse_args(argv)
    try:
        index=build_index(args.state,args.tenant,args.generation,make_encoder(args),args.output)
        print(_json({k:index[k] for k in ('schema_version','tenant_id','generation','digest')})); return 0
    except (ValueError,OSError,ImportError,sqlite3.Error) as exc:
        print(str(exc),file=sys.stderr); return 1


if __name__=='__main__': raise SystemExit(main())
