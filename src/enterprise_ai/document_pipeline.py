"""Versioned, tenant-scoped document chunks for the M08 teaching lab."""
import argparse
import datetime
import hashlib
import importlib.metadata
import json
import os
import re
import sqlite3
import stat
import sys
from contextlib import closing
from pathlib import Path

from .document_parse import MAX_BYTES, parse_document
from .ollama import strict_json_loads

ROLES = {'employee','sales','hr','procurement'}
APP_ID = 0x4D3038
BASE_FIELDS = {'tenant_id','document_id','version','op'}
UPSERT_FIELDS = BASE_FIELDS | {'source_path','title','domain','roles','effective_from','effective_to','sha256'}


def _json(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _integer(value):
    return type(value) is int and 0 < value < 2**63


def _date(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
        raise ValueError('date must be YYYY-MM-DD')
    datetime.date.fromisoformat(value)
    return value


def _roles(value):
    if (not isinstance(value,list) or not value or any(not isinstance(x,str) for x in value)
            or len(value)!=len(set(value)) or not set(value)<=ROLES):
        raise ValueError('roles must be a nonempty unique supported list')
    return sorted(value)


def _relative(value):
    if (not isinstance(value,str) or not value or len(value)>240 or '\\' in value or ':' in value
            or any(ord(c)<32 for c in value) or any(p in ('','.','..') for p in value.split('/'))
            or any(p.endswith((' ','.')) for p in value.split('/'))):
        raise ValueError('unsafe relative source_path')
    if Path(value).suffix.lower() not in ('.md','.pdf','.docx'):
        raise ValueError('unsupported source format')
    return value


def _no_links(path):
    path = Path(path).absolute()
    for part in [path,*path.parents]:
        if part.is_symlink() or (hasattr(part,'is_junction') and part.is_junction()):
            raise ValueError('symlinks/junctions are unsupported')
    return path


def _read(path):
    path = _no_links(path)
    with path.open('rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('input must be a regular file')
        raw = stream.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:
        raise ValueError('input exceeds 10MiB')
    return raw


def _entry(entry):
    if not isinstance(entry,dict) or entry.get('op') not in ('upsert','delete'):
        raise ValueError('invalid operation')
    if set(entry) != (UPSERT_FIELDS if entry['op']=='upsert' else BASE_FIELDS):
        raise ValueError('unexpected/missing document fields')
    if not _integer(entry['tenant_id']) or not _integer(entry['version']):
        raise ValueError('tenant_id/version must be positive integers')
    if not isinstance(entry['document_id'],str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}',entry['document_id']):
        raise ValueError('invalid document_id')
    result = dict(entry)
    if entry['op']=='upsert':
        _relative(entry['source_path'])
        if (entry['domain'] not in ('crm','hcm','scm') or not isinstance(entry['title'],str)
                or not entry['title'].strip() or len(entry['title'])>200 or any(ord(c)<32 for c in entry['title'])):
            raise ValueError('invalid title/domain')
        result['roles'] = _roles(entry['roles'])
        _date(entry['effective_from'])
        if entry['effective_to'] is not None and _date(entry['effective_to'])<=entry['effective_from']:
            raise ValueError('effective_to must be after effective_from')
        if not isinstance(entry['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',entry['sha256']):
            raise ValueError('invalid sha256')
    return result


def _settings(max_chars,overlap):
    if (type(max_chars) is not int or not 80<=max_chars<=2000 or type(overlap) is not int
            or not 0<=overlap<max_chars/2):
        raise ValueError('max_chars:80..2000; overlap:0..<half max_chars')


def _pipeline(suffix,max_chars,overlap):
    package = {'.pdf':'pypdf','.docx':'python-docx'}.get(suffix.lower())
    try:
        version = importlib.metadata.version(package) if package else 'stdlib'
    except importlib.metadata.PackageNotFoundError as exc:
        raise ValueError('install requirements/m08-documents.txt') from exc
    return f'm08-parser-v1:{suffix.lower()}:{version}:chars-v1:{max_chars}:{overlap}'


def chunk_segments(metadata,segments,*,max_chars=400,overlap=40):
    _settings(max_chars,overlap)
    meta = _entry(metadata)
    if meta['op']!='upsert':
        raise ValueError('only upserts have chunks')
    pipeline_id = _pipeline(Path(meta['source_path']).suffix,max_chars,overlap)
    result = []
    for segment in segments:
        text = segment['text']
        start = 0
        while start<len(text):
            end = min(start+max_chars,len(text))
            if end<len(text):
                # Prefer a nearby boundary, but ensure progress even for long unbroken strings.
                candidates = [text.rfind(mark,start+max_chars//2,end) for mark in ('\n','。','！','？',';','；','. ')]
                boundary = max(candidates)
                if boundary>=0:
                    end = boundary+1
            part = text[start:end]
            chunk = {k:meta[k] for k in ('tenant_id','document_id','version','title','domain','roles','effective_from','effective_to','source_path')}
            chunk.update(schema_version='m08-chunk-v1',source_sha256=meta['sha256'],
                         page=segment['page'],section=segment['section'],block=segment['block'],
                         start=start,end=end,text=part,content_sha256=hashlib.sha256(part.encode()).hexdigest(),
                         pipeline_id=pipeline_id,embedding_model_revision=None)
            chunk['chunk_id'] = _digest(chunk)
            result.append(chunk)
            if end==len(text):
                break
            start = end-overlap
    return result


def _check_database(db, *, initialize=False):
    app_id = db.execute('PRAGMA application_id').fetchone()[0]
    version = db.execute('PRAGMA user_version').fetchone()[0]
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if app_id==APP_ID and version==1 and tables=={'versions','chunks','heads'}:
        return
    if not initialize or app_id or version or tables:
        raise ValueError('foreign or incompatible document state database')
    db.execute(f'PRAGMA application_id={APP_ID}')
    db.execute('PRAGMA user_version=1')
    db.execute('CREATE TABLE versions (tenant_id INTEGER, document_id TEXT, version INTEGER, op TEXT, entry_digest TEXT, metadata TEXT, pipeline_id TEXT, PRIMARY KEY(tenant_id,document_id,version))')
    db.execute('CREATE TABLE chunks (tenant_id INTEGER, document_id TEXT, version INTEGER, ordinal INTEGER, payload TEXT, PRIMARY KEY(tenant_id,document_id,version,ordinal))')
    db.execute('CREATE TABLE heads (tenant_id INTEGER, document_id TEXT, version INTEGER, PRIMARY KEY(tenant_id,document_id))')


def _write(stream,report):
    stream.write((_json(report)+'\n').encode())
    stream.flush()
    os.fsync(stream.fileno())


def ingest(catalog_path,source_root,state_path,report_path,*,max_chars=400,overlap=40):
    """A failed report means no batch changes; report I/O errors propagate explicitly."""
    report_path = _no_links(report_path)
    with report_path.open('xb') as report_stream:
        report = {'schema_version':'m08-ingest-v1','status':'failed','created':0,'updated':0,'deleted':0,'unchanged':0,'errors':[]}
        try:
            _settings(max_chars,overlap)
            catalog = strict_json_loads(_read(catalog_path).decode('utf-8'))
            if (not isinstance(catalog,dict) or set(catalog)!={'schema_version','documents'}
                    or catalog['schema_version']!='m08-catalog-v1' or not isinstance(catalog['documents'],list)
                    or not 1<=len(catalog['documents'])<=1000):
                raise ValueError('invalid catalog; expected 1..1000 explicit entries')
            entries = [_entry(item) for item in catalog['documents']]
            keys = [(d['tenant_id'],d['document_id']) for d in entries]
            if len(set(keys))!=len(keys):
                raise ValueError('duplicate tenant/document key')
            root = _no_links(source_root).resolve(strict=True)
            prepared = []
            for entry in entries:
                chunks, pipeline_id = [], 'm08-delete-v1'
                if entry['op']=='upsert':
                    path = _no_links(root/entry['source_path'])
                    path.resolve(strict=True).relative_to(root)
                    raw = _read(path)
                    if hashlib.sha256(raw).hexdigest()!=entry['sha256']:
                        raise ValueError('source sha256 mismatch: '+entry['document_id'])
                    segments = parse_document(raw,path.suffix)
                    chunks = chunk_segments(entry,segments,max_chars=max_chars,overlap=overlap)
                    pipeline_id = _pipeline(path.suffix,max_chars,overlap)
                fingerprint = _digest({'entry':entry,'pipeline_id':pipeline_id})
                prepared.append((entry,chunks,pipeline_id,fingerprint))
            state = _no_links(state_path)
            counts = dict(created=0,updated=0,deleted=0,unchanged=0)
            with closing(sqlite3.connect(state,timeout=10)) as db, db:
                db.execute('BEGIN IMMEDIATE')
                _check_database(db,initialize=True)
                for entry,chunks,pipeline_id,fingerprint in prepared:
                    key = (entry['tenant_id'],entry['document_id'])
                    old = db.execute('SELECT h.version,v.entry_digest FROM heads h JOIN versions v USING(tenant_id,document_id,version) WHERE h.tenant_id=? AND h.document_id=?',key).fetchone()
                    version = entry['version']
                    if old and (version<old[0] or (version==old[0] and fingerprint!=old[1])):
                        raise ValueError('version rollback/conflict: '+entry['document_id'])
                    if old and version==old[0]:
                        counts['unchanged']+=1
                        continue
                    if not old and entry['op']=='delete':
                        raise ValueError('cannot delete unknown document')
                    db.execute('INSERT INTO versions VALUES (?,?,?,?,?,?,?)',(*key,version,entry['op'],fingerprint,_json(entry),pipeline_id))
                    db.executemany('INSERT INTO chunks VALUES (?,?,?,?,?)',[(*key,version,n,_json(c)) for n,c in enumerate(chunks)])
                    db.execute('INSERT INTO heads VALUES (?,?,?) ON CONFLICT(tenant_id,document_id) DO UPDATE SET version=excluded.version',(*key,version))
                    counts['deleted' if entry['op']=='delete' else ('updated' if old else 'created')]+=1
                db.commit()
            report.update(status='ok',**counts)
        except (ValueError,OSError,sqlite3.Error,UnicodeError) as exc:
            report['errors'] = [str(exc)]
        # Outside the exception handler: cannot claim a report write succeeded when it failed.
        _write(report_stream,report)
        return report


def export_chunks(state_path,output_path,*,tenant_id,roles,as_of):
    if not _integer(tenant_id):
        raise ValueError('invalid tenant_id')
    roles = _roles(roles); _date(as_of)
    state = _no_links(state_path).resolve(strict=True)
    output = _no_links(output_path)
    # Read-only URI prevents silently creating an empty state when a filename is wrong.
    with closing(sqlite3.connect(state.as_uri()+'?mode=ro',uri=True)) as db, db:
        db.execute('BEGIN')
        _check_database(db)
        metadata = db.execute('SELECT v.document_id,v.version,v.metadata FROM heads h JOIN versions v USING(tenant_id,document_id,version) WHERE h.tenant_id=? AND v.op=? ORDER BY v.document_id',(tenant_id,'upsert')).fetchall()
        payloads = []
        for doc_id,version,encoded in metadata:
            meta = json.loads(encoded)
            if (not set(meta['roles']).intersection(roles) or as_of<meta['effective_from']
                    or (meta['effective_to'] is not None and as_of>=meta['effective_to'])):
                continue
            payloads.extend(r[0] for r in db.execute('SELECT payload FROM chunks WHERE tenant_id=? AND document_id=? AND version=? ORDER BY ordinal',(tenant_id,doc_id,version)))
        raw = (''.join(line+'\n' for line in payloads)).encode()
        with output.open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    return {'schema_version':'m08-export-v1','tenant_id':tenant_id,'roles':roles,'as_of':as_of,
            'chunks':len(payloads),'sha256':hashlib.sha256(raw).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    load = sub.add_parser('ingest')
    for arg in ('catalog','sources','state','report'):
        load.add_argument('--'+arg,type=Path,required=True)
    load.add_argument('--max-chars',type=int,default=400)
    load.add_argument('--overlap',type=int,default=40)
    export = sub.add_parser('export')
    export.add_argument('--state',type=Path,required=True)
    export.add_argument('--output',type=Path,required=True)
    export.add_argument('--tenant',type=int,required=True)
    export.add_argument('--roles',nargs='+',required=True)
    export.add_argument('--as-of',required=True)
    args = parser.parse_args(argv)
    try:
        if args.command=='ingest':
            report = ingest(args.catalog,args.sources,args.state,args.report,max_chars=args.max_chars,overlap=args.overlap)
        else:
            report = export_chunks(args.state,args.output,tenant_id=args.tenant,roles=args.roles,as_of=args.as_of)
        print(_json(report))
        return 1 if report.get('status')=='failed' else 0
    except (ValueError,OSError,sqlite3.Error) as exc:
        print(str(exc),file=sys.stderr); return 1


if __name__ == '__main__':
    raise SystemExit(main())
