"""Authorized retrieval baselines, reciprocal rank fusion and cited extracts."""
import argparse
import math
import sys
from collections import Counter
from pathlib import Path

from .document_pipeline import _date, _roles, _json, _digest
from .rag_index import assert_fresh, load_index
from .rag_models import tokens, vectors, encoder_options, make_encoder, LocalReranker


def options(query,roles,as_of,mode,k,candidates):
    if not isinstance(query,str) or not query.strip() or len(query)>500 or '\x00' in query:
        raise ValueError('query must contain 1..500 characters')
    _roles(roles); _date(as_of)
    if mode not in ('dense','keyword','hybrid'): raise ValueError('unknown retrieval mode')
    if type(k) is not int or type(candidates) is not int or not 1<=k<=20 or not k<=candidates<=100:
        raise ValueError('k:1..20; candidates:k..100')


def allowed(chunk,roles,as_of):
    return (bool(set(roles).intersection(chunk['roles'])) and chunk['effective_from']<=as_of
            and (chunk['effective_to'] is None or as_of<chunk['effective_to']))


def keyword_ranking(query,chunks):
    query_tokens=set(tokens(query)); counts=[Counter(tokens(c['text'])) for c in chunks]
    frequencies=Counter(token for count in counts for token in count)
    scores=[]
    for chunk,count in zip(chunks,counts):
        value=sum((1+math.log(count[t]))*math.log(1+len(chunks)/(1+frequencies[t]))
                  for t in query_tokens if count[t])
        if value>0: scores.append((chunk['chunk_id'],value))
    return sorted(scores,key=lambda pair:(-pair[1],pair[0]))


def fuse(*rankings):
    scores=Counter()
    for ranking in rankings:
        seen=set()
        for rank,key in enumerate(ranking,1):
            if key not in seen: scores[key]+=1/(60+rank); seen.add(key)
    return sorted(scores.items(),key=lambda pair:(-pair[1],pair[0]))


def rank_result(index,query,chunks,dense,*,mode,k,candidates,reranker=None):
    """All chunks/dense candidates must already be authorized by the caller."""
    keyword=keyword_ranking(query,chunks)[:candidates]
    dense=dense[:candidates]
    if mode=='keyword': ranked=keyword
    elif mode=='dense': ranked=dense
    else: ranked=fuse([key for key,_ in dense],[key for key,_ in keyword])[:candidates]
    by_id={c['chunk_id']:c for c in chunks}
    if reranker is not None and ranked:
        scores=reranker.score(query,[by_id[key]['text'] for key,_ in ranked])
        if (not isinstance(scores,list) or len(scores)!=len(ranked)
                or any(type(v) not in (int,float) or not math.isfinite(v) for v in scores)):
            raise ValueError('invalid reranker scores')
        if hasattr(reranker,'verify'): reranker.verify()
        ranked=sorted([(pair[0],score) for pair,score in zip(ranked,scores)],key=lambda pair:(-pair[1],pair[0]))
    hits=[{'chunk':by_id[key],'score':score,'lexical_overlap':len(set(tokens(query)).intersection(tokens(by_id[key]['text'])))}
          for key,score in ranked[:k]]
    return {'schema_version':'m09-results-v1','query':query,'mode':mode,'k':k,'candidates':candidates,
            'index_digest':index['digest'],'source_digest':index['source_digest'],'model':index['model'],
            'reranker':reranker.identity if reranker else None,'hits':hits}


def search(index,state,query,*,roles,as_of,mode='hybrid',k=5,candidates=20,encoder,reranker=None):
    options(query,roles,as_of,mode,k,candidates); assert_fresh(index,state)
    if encoder.identity!=index['model']: raise ValueError('embedding model identity mismatch')
    encoder.verify()
    pairs=[(c,v) for c,v in zip(index['chunks'],index['vectors']) if allowed(c,roles,as_of)]
    dense=[]
    if mode!='keyword' and pairs:
        q=vectors(encoder.encode([query]),1)[0]
        dense=sorted([(c['chunk_id'],sum(a*b for a,b in zip(q,v))) for c,v in pairs],key=lambda pair:(-pair[1],pair[0]))
    result=rank_result(index,query,[c for c,_ in pairs],dense,mode=mode,k=k,candidates=candidates,reranker=reranker)
    encoder.verify(); assert_fresh(index,state)
    return {**result,'tenant_id':index['tenant_id'],'roles':sorted(roles),'as_of':as_of,'backend':'python-exact'}


def evidence_answer(result):
    citations=[]
    for hit in result['hits']:
        chunk=hit['chunk']
        if chunk['chunk_id']!=_digest({key:value for key,value in chunk.items() if key!='chunk_id'}):
            raise ValueError('citation chunk changed')
        if not hit['lexical_overlap']: continue
        citations.append({**{key:chunk[key] for key in ('chunk_id','tenant_id','document_id','version','source_path','page','section','block')},'quote':chunk['text']})
    if not citations:
        return {'status':'insufficient','answer':'没有找到可展示的相关证据；请补充问题或核对授权范围。','citations':[]}
    lines=['以下为检索到的原文摘录，相关性与适用性仍需核对：']
    for n,c in enumerate(citations,1):
        lines.append(f"[{n}] {c['quote']}\n来源：{c['document_id']} v{c['version']}，页={c['page']}，章节={c['section']}，块={c['block']}")
    return {'status':'evidence','answer':'\n\n'.join(lines),'citations':citations}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index',type=Path,required=True); parser.add_argument('--state',type=Path,required=True)
    parser.add_argument('--query',required=True); parser.add_argument('--roles',nargs='+',required=True)
    parser.add_argument('--as-of',required=True); parser.add_argument('--mode',choices=('dense','keyword','hybrid'),default='hybrid')
    parser.add_argument('--k',type=int,default=5); parser.add_argument('--candidates',type=int,default=20)
    parser.add_argument('--reranker',type=Path); encoder_options(parser); args=parser.parse_args(argv)
    try:
        result=search(load_index(args.index),args.state,args.query,roles=args.roles,as_of=args.as_of,
                      mode=args.mode,k=args.k,candidates=args.candidates,encoder=make_encoder(args),
                      reranker=LocalReranker(args.reranker,device=args.device) if args.reranker else None)
        print(_json({'retrieval':result,'answer':evidence_answer(result)})); return 0
    except (ValueError,OSError,ImportError) as exc:
        print(str(exc),file=sys.stderr); return 1


if __name__=='__main__': raise SystemExit(main())
