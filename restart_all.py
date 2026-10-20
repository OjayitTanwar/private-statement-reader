"""Back up and queue a stopped local statement library for fresh extraction."""
import argparse
import copy
from datetime import datetime,timezone
import json
import os
from pathlib import Path
from contextlib import closing
import re
import shutil
import sqlite3
import uuid

EDITABLE=('date','original_description','normalized_description','counterparty','cheque_number','debit','credit','balance','state')
REQUIRED={
    'bank_documents':{'id','name','status','created','pages','progress','stage','error','config','report'},
    'bank_rows':{'id','document_id','page','ord','payload'},
    'audit':{'id','document_id','row_id','time','before_json','after_json'},
}


def _write(path,value):
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'w') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.flush();os.fsync(stream.fileno())


def _overlays(rows,audits,prior):
    snapshots={row['id']:row for row in rows};current_ids=set(snapshots);prior_ids=set();fields={}
    for item in prior.get('manual_overrides',[]):
        original=item.get('original_row',{})
        if original.get('id'):
            prior_ids.add(original['id'])
            snapshots.setdefault(original['id'],original)
            fields.setdefault(original['id'],set()).update(item.get('fields',[]))
    for audit in audits:
        try:
            before=json.loads(audit.get('before_json') or '{}');after=json.loads(audit.get('after_json') or '{}')
        except (ValueError,TypeError):continue
        if not isinstance(before,dict) or not isinstance(after,dict):continue
        if len(after.get('_source_repairs',[]))>len(before.get('_source_repairs',[])):continue
        if after.get('after_source_repairs'):continue
        if any('Automatically excluded: source explicitly states no transactions' in str(issue) for issue in after.get('_issues',[])):continue
        changed={key for key in EDITABLE if before.get(key)!=after.get(key)}
        if changed and audit.get('row_id'):
            identity=audit['row_id'];fields.setdefault(identity,set()).update(changed)
            if identity not in current_ids and identity not in prior_ids:snapshots[identity]=after
    for identity,row in snapshots.items():
        if row.get('state')=='approved' or row.get('_engine')=='Manual transcription':
            fields.setdefault(identity,set()).update(key for key in EDITABLE if key in row)
    return [{'original_row':copy.deepcopy(snapshots[identity]),'fields':[key for key in EDITABLE if key in changed]}
            for identity,changed in fields.items() if changed and identity in snapshots]


def restart_library(data,apply=False):
    data=Path(data).resolve();database=data/'statements.sqlite3';docs=data/'documents'
    if not database.is_file() or not docs.is_dir():raise ValueError('Not a statement library: database or document folder missing')
    connection=sqlite3.connect(database);connection.row_factory=sqlite3.Row
    try:
        for table,columns in REQUIRED.items():
            actual={row['name'] for row in connection.execute(f'PRAGMA table_info({table})')}
            if actual!=columns:raise ValueError(f'Unexpected {table} schema; restart refused')
        documents=[dict(row) for row in connection.execute("SELECT * FROM bank_documents WHERE status<>'duplicate' ORDER BY created,id")]
        if not documents:raise ValueError('No original documents to restart')
        bundles=[]
        for document in documents:
            identity=document['id']
            if not re.fullmatch(r'[A-Za-z0-9_-]+',identity):raise ValueError('Unexpected document identifier')
            folder=docs/identity;source=folder/'source.pdf'
            if folder.is_symlink() or not source.is_file() or source.is_symlink():raise ValueError('Original PDF missing or not a regular library source')
            with source.open('rb') as stream:
                if stream.read(5)!=b'%PDF-':raise ValueError('Original source is not a PDF')
            rows=[json.loads(row['payload']) for row in connection.execute('SELECT payload FROM bank_rows WHERE document_id=? ORDER BY page,ord,id',(identity,))]
            audits=[dict(row) for row in connection.execute('SELECT * FROM audit WHERE document_id=? ORDER BY id',(identity,))]
            state=folder/'reextract-state.json';prior=json.loads(state.read_text()) if state.exists() else {}
            json.loads(document['config']);json.loads(document['report'])
            bundles.append((document,folder,rows,audits,prior))
        statuses={}
        for document in documents:statuses[document['status']]=statuses.get(document['status'],0)+1
        report={'documents':len(documents),'pages':sum(document.get('pages') or 0 for document in documents),
                'statuses':statuses,'apply':bool(apply)}
        if not apply:return report
        run_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
        backup=data/'backups'/('rerun-'+run_id);backup.mkdir(parents=True,mode=0o700)
        backup.chmod(0o700);backup.parent.chmod(0o700)
        backup_database=backup/'statements.sqlite3'
        with closing(sqlite3.connect(backup_database)) as target:connection.backup(target)
        backup_database.chmod(0o600)
        for document,folder,rows,audits,prior in bundles:
            _write(backup/(document['id']+'.json'),{'document':document,'rows':rows,'audit':audits,'previous_reextract_state':prior})
        moves=[];states=[]
        try:
            for document,folder,rows,audits,prior in bundles:
                run=folder/'runs'/run_id;run.mkdir(parents=True,mode=0o700);run.chmod(0o700)
                for child in list(folder.iterdir()):
                    if child.name in ('source.pdf','runs'):continue
                    destination=run/child.name;shutil.move(str(child),str(destination));moves.append((child,destination))
                info=(folder/'source.pdf').stat()
                state=folder/'reextract-state.json'
                states.append(state)
                previous=copy.deepcopy(rows or prior.get('previous_rows',[]))
                overlays=_overlays(rows,audits,prior)
                for row in [*previous,*(item['original_row'] for item in overlays)]:
                    crop=row.get('crop')
                    if not isinstance(crop,str) or not crop:continue
                    relative=Path(crop)
                    if not relative.is_absolute() and '..' not in relative.parts and relative.parts[0]!='runs' and (run/relative).is_file():
                        row['crop']=str(Path('runs')/run_id/relative)
                _write(state,{'source_signature':[info.st_size,info.st_mtime_ns],
                              'previous_rows':previous,
                              'manual_overrides':overlays,'run_id':run_id})
            exports=data/'exports'
            if exports.exists():
                destination=backup/'exports';shutil.move(str(exports),str(destination));moves.append((exports,destination))
            with connection:
                for document,folder,rows,audits,prior in bundles:
                    config=json.loads(document['config']);config['automatic_attempts']=0
                    for key in ('source_repair_revision','source_repair_version','source_repair_pages','processing_revision'):config.pop(key,None)
                    if config.get('opening_source')!='Reviewer entered':
                        config['opening']=None;config.pop('opening_source',None)
                    connection.execute('DELETE FROM bank_rows WHERE document_id=?',(document['id'],))
                    connection.execute("UPDATE bank_documents SET status='queued',pages=0,progress=0,stage='Queued for fresh extraction',error='',config=?,report='{}' WHERE id=?",
                                       (json.dumps(config),document['id']))
        except Exception as error:
            recovery_errors=[]
            for state in states:
                try:state.unlink(missing_ok=True)
                except OSError:recovery_errors.append('New restart state could not be removed')
            for original,archived in reversed(moves):
                try:
                    if archived.exists():shutil.move(str(archived),str(original))
                except OSError:recovery_errors.append('An archived file could not be restored')
            detail='; '.join(recovery_errors) or 'Archived files restored; database transaction rolled back'
            raise RuntimeError(f'Restart failed. Recoverable backup: {backup}. {detail}') from error
        report.update(run_id=run_id,backup=str(backup),queued=len(documents))
        return report
    finally:connection.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Back up and restart a stopped local statement library')
    parser.add_argument('--data',required=True,type=Path);parser.add_argument('--apply',action='store_true')
    args=parser.parse_args();print(json.dumps(restart_library(args.data,apply=args.apply),indent=2))
