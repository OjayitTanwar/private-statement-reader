#!/usr/bin/env python3
"""Private bank statement reviewer, localhost service for the native macOS app."""
from __future__ import annotations
import csv, io, json, math, os, queue, re, secrets, shutil, signal, sqlite3, subprocess, sys, threading, uuid,hashlib
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from bank_engine import extract_page, group_lines, is_statement, normalize, ocr, pass_score, reconcile, parse_amount, cents, select_observed_candidates
from people import suggestions,automatic_counterparties
from table_grid import extract_grid,recover_reversed_headers,decode_balance_chain
from local_ai import LocalAI,combine_ai,infer_missing_amounts,infer_two_sided_balances,ai_worker_stats
from automation import atomic_json,empty_summary,prepared_rows,export_bytes,write_document_outputs,atomic_bytes,FIELDS

ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('STATEMENT_DATA',str(ROOT/'data'))).resolve()
DOCS=DATA/'documents'; DB=DATA/'statements.sqlite3'
TOKEN=secrets.token_urlsafe(32)
JOBS=queue.Queue()
NUMERIC=('debit','credit','balance')
EXPORT_LOCK=threading.RLock()
ACTIVITY_LOCK=threading.RLock()
CAPACITY_CHANGED=threading.Condition(ACTIVITY_LOCK)
ACTIVE_JOBS=set()
SLEEP_GUARD=None


def resource_plan():
    cores=os.cpu_count() or 4
    try:memory=int(subprocess.check_output(['sysctl','-n','hw.memsize'],text=True,stderr=subprocess.DEVNULL))
    except (OSError,ValueError,subprocess.SubprocessError):memory=16*1024**3
    # The GPU model is shared, so each extra document needs page/OCR memory only.
    workers=max(1,min(4,cores//2,max(1,(memory-4*1024**3)//(3*1024**3))))
    return {'cpu_cores':cores,'memory_gb':round(memory/1024**3),'document_workers':workers,
            'ocr_jobs':max(1,min(2,(cores-1)//workers)),'ai_instances':2 if memory>=16*1024**3 else 1}


RESOURCES=resource_plan()
os.environ.setdefault('STATEMENT_AI_INSTANCES',str(RESOURCES['ai_instances']))


def capacity_limit():
    try:
        pressure=int(subprocess.check_output(['sysctl','-n','kern.memorystatus_vm_pressure_level'],text=True,stderr=subprocess.DEVNULL,timeout=2))
    except (OSError,ValueError,subprocess.SubprocessError):pressure=1
    return min(RESOURCES['document_workers'],1 if pressure>=4 else 2 if pressure>=2 else 4)


def acquire_slot(doc):
    with CAPACITY_CHANGED:
        while len(ACTIVE_JOBS)>=capacity_limit():CAPACITY_CHANGED.wait(timeout=3)
        set_active(doc,True)


def set_active(doc,active):
    global SLEEP_GUARD
    with ACTIVITY_LOCK:
        if active:ACTIVE_JOBS.add(doc)
        else:ACTIVE_JOBS.discard(doc)
        CAPACITY_CHANGED.notify_all()
        if ACTIVE_JOBS and SLEEP_GUARD is None and shutil.which('caffeinate'):
            SLEEP_GUARD=subprocess.Popen(['caffeinate','-i','-w',str(os.getpid())],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        elif not ACTIVE_JOBS and SLEEP_GUARD is not None:
            try:SLEEP_GUARD.terminate();SLEEP_GUARD.wait(timeout=5)
            except (OSError,subprocess.TimeoutExpired):pass
            SLEEP_GUARD=None


class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()


def connection():
    c=sqlite3.connect(DB,timeout=30,factory=ClosingConnection); c.row_factory=sqlite3.Row
    return c


def init_db():
    DATA.mkdir(parents=True,exist_ok=True,mode=0o700); DOCS.mkdir(exist_ok=True,mode=0o700)
    with connection() as c:
        c.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS bank_documents(id TEXT PRIMARY KEY,name TEXT,status TEXT,created TEXT,pages INTEGER DEFAULT 0,progress INTEGER DEFAULT 0,stage TEXT,error TEXT,config TEXT DEFAULT '{}',report TEXT DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS bank_rows(id TEXT PRIMARY KEY,document_id TEXT,page INTEGER,ord INTEGER,payload TEXT);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,document_id TEXT,row_id TEXT,time TEXT,before_json TEXT,after_json TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);''')
        c.execute("UPDATE bank_documents SET status='queued',stage='Resuming automatically' WHERE status IN ('queued','processing','interrupted')")
    deduplicate_pending()
    with connection() as c:pending=[r['id'] for r in c.execute("SELECT id FROM bank_documents WHERE status='queued' ORDER BY created,id")]
    for doc in pending:JOBS.put(doc)


def deduplicate_pending():
    with connection() as c:documents=[dict(r) for r in c.execute("SELECT * FROM bank_documents WHERE status<>'duplicate' ORDER BY CASE WHEN status='ready' THEN 0 ELSE 1 END,progress DESC,created,id")]
    seen={}
    for d in documents:
        source=DOCS/d['id']/'source.pdf'
        try:
            with source.open('rb') as f:
                if f.read(5)!=b'%PDF-':continue
            config=json.loads(d['config']);signature=source_signature(source.parent)
            digest=config.get('source_sha256') if config.get('source_signature')==signature else None
            if not digest:
                with source.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
            config.update(source_sha256=digest,source_signature=signature)
            if digest in seen and d['status'] in ('queued','processing','interrupted'):
                config['duplicate_of']=seen[digest]
                status(d['id'],status='duplicate',stage='Identical PDF already imported',config=config)
            else:
                seen.setdefault(digest,d['id']);status(d['id'],config=config)
        except (OSError,ValueError):continue


def stamp(): return datetime.now(timezone.utc).isoformat()
def document(doc):
    with connection() as c: r=c.execute('SELECT * FROM bank_documents WHERE id=?',(doc,)).fetchone()
    if not r: raise ValueError('Statement not found')
    r=dict(r); r['config']=json.loads(r['config']); r['report']=json.loads(r['report']); return r


def status(doc,**values):
    with connection() as c:
        c.execute('UPDATE bank_documents SET '+','.join(k+'=?' for k in values)+' WHERE id=?',[*[json.dumps(v) if isinstance(v,dict) else v for v in values.values()],doc])


def rows_for(doc):
    with connection() as c: rows=[json.loads(r['payload']) for r in c.execute('SELECT payload FROM bank_rows WHERE document_id=? ORDER BY page,ord,id',(doc,))]
    return rows


def save_rows(doc,rows,c=None):
    if c is None:
        with connection() as c: save_rows(doc,rows,c)
        return
    c.executemany('INSERT OR REPLACE INTO bank_rows VALUES(?,?,?,?,?)',[(r['id'],doc,r['page'],r['ord'],json.dumps(r)) for r in rows])


def replace_rows(doc,rows):
    with connection() as c:
        c.execute('DELETE FROM bank_rows WHERE document_id=?',(doc,))
        save_rows(doc,rows,c)


def recalc(doc):
    d=document(doc); rows=rows_for(doc)
    reconcile(rows,opening=d['config'].get('opening'),order=d['config'].get('order','auto'),infer=False)
    save_rows(doc,rows)
    finish_outputs(doc,rows)


def automatic_report(rows):
    from review_checks import classify_review
    active=[r for r in rows if r.get('state')!='deleted']
    reviews=[classify_review(r) for r in active]
    return {'rows':len(active),'flagged_rows':sum(r['review_required'] for r in reviews),
            'financial_error_rows':sum(r['category']=='financial_error' for r in reviews),
            'estimated_value_rows':sum(r['category']=='estimated_value' for r in reviews),
            'information_only_rows':sum(r['category']=='information' for r in reviews),
            'balance_mismatches':sum('balance mismatch' in r.get('reason','') for r in active),
            'inferred_rows':sum(bool(r['estimated_fields']) for r in reviews),
            'ai_assisted_rows':sum(bool(r.get('_ai_proposal')) for r in active)}


def finish_outputs(doc,rows=None):
    with EXPORT_LOCK:
        d=document(doc);rows=rows if rows is not None else rows_for(doc)
        if d['status'] not in ('ready','unsupported','needs_layout'):return
        with connection() as c:references=[r['id'] for r in c.execute("SELECT id FROM bank_documents WHERE status='ready' AND id<>?",(doc,))]
        automatic_counterparties(rows,[r for other in references for r in rows_for(other)])
        save_rows(doc,rows)
        d['report']['automatic']=automatic_report(rows)
        folder=write_document_outputs(DATA,d,rows)
        d['report']['output_folder']=str(folder)
        status(doc,report=d['report'])
        combined=[];warnings=[]
        with connection() as c:finished=[dict(r) for r in c.execute("SELECT id,name,report FROM bank_documents WHERE status='ready' ORDER BY created,id")]
        for item in finished:
            for r in prepared_rows(rows_for(item['id'])):combined.append({'source_document':item['name'],'document_id':item['id'],**r})
            warnings.extend(f"{item['name']}: {w}" for w in json.loads(item['report']).get('warnings',[]))
        exports=DATA/'exports';exports.mkdir(exist_ok=True)
        for kind in ('csv','xlsx'):
            atomic_bytes(exports/f'All statements.{kind}',export_bytes(kind,combined,{'name':'All completed bank statements','report':{'warnings':warnings}},['source_document','document_id',*FIELDS]))


def restore_completed_outputs():
    with connection() as c:finished=[r['id'] for r in c.execute("SELECT id FROM bank_documents WHERE status IN ('ready','needs_layout','unsupported')")]
    for doc in finished:
        try:
            rows=rows_for(doc);d=document(doc);excluded=False
            for page in range(1,(d.get('pages') or 0)+1):
                cache=DOCS/doc/f'ocr-{page}-tesseract.json'
                if not cache.exists():continue
                text=' '.join(w.get('text','') for w in json.loads(cache.read_text()))
                if not empty_summary(text):continue
                for r in rows:
                    if r['page']==page and r.get('state')!='deleted' and r.get('_engine')!='Manual transcription':
                        before=json.dumps(r);r['state']='deleted';r['_issues'].append('Automatically excluded: source explicitly states no transactions')
                        with connection() as c:c.execute('INSERT INTO audit(document_id,row_id,time,before_json,after_json) VALUES(?,?,?,?,?)',(doc,r['id'],stamp(),before,json.dumps(r)))
                        excluded=True
            if excluded:
                reconcile(rows,opening=d['config'].get('opening'),order=d['config'].get('order','auto'))
                save_rows(doc,rows)
            finish_outputs(doc,rows)
        except Exception as e:
            (DOCS/doc/'automatic-output-error.log').write_text(f'{type(e).__name__}: {e}')


def run(cmd,timeout=300):
    env={**os.environ,'OMP_THREAD_LIMIT':'2'}
    if os.environ.get('STATEMENT_TESSDATA'):
        env['TESSDATA_PREFIX']=os.environ['STATEMENT_TESSDATA']
    return subprocess.run([str(x) for x in cmd],capture_output=True,text=True,check=True,timeout=timeout,env=env)


def dependencies():
    tools={t:shutil.which(t) for t in ('tesseract','ocrmypdf','pdftoppm','pdfinfo')}
    vision=Path(os.environ.get('STATEMENT_VISION',str(ROOT/'work/VisionOCR')))
    return {'tools':{k:bool(v) for k,v in tools.items()},'vision':vision.is_file(),
            'ready':all(tools.values()),'data':str(DATA),'model':'tessdata_best English' if os.environ.get('STATEMENT_TESSDATA') else 'Installed English model',
            'ai':bool(os.environ.get('STATEMENT_AI_MODEL') and Path(os.environ['STATEMENT_AI_MODEL'],'config.json').is_file()),
            'ai_model':'GLM-OCR 8-bit · MLX',
            'ai_workers':ai_worker_stats(),
            'resources':{**RESOURCES,'admission_limit':capacity_limit()},'active_documents':len(ACTIVE_JOBS),'queued_documents':JOBS.qsize()}


def source_signature(folder):
    info=(folder/'source.pdf').stat()
    return [info.st_size,info.st_mtime_ns]


def resume_state(d,folder):
    checkpoint=folder/'checkpoint.json'
    if checkpoint.exists():
        try:
            state=json.loads(checkpoint.read_text())
            if state.get('source_signature')==source_signature(folder):return state
        except (ValueError,OSError):pass
        return None
    # Also resume completed pages from the previous app version, which had no checkpoint file.
    progress=d.get('progress') or 0;pages=d['report'].get('pages',[])
    if progress and [p.get('page') for p in pages if p.get('page',0)<=progress]==list(range(1,progress+1)):
        return {'next_page':progress+1,'rows':[r for r in rows_for(d['id']) if r['page']<=progress],
                'report':{**d['report'],'pages':pages[:progress]},'config':d['config'],
                'columns':pages[progress-1].get('columns',{}),'bank_evidence':'','recognized':d['report'].get('statement_detected',False)}
    return None


def process(doc):
    from PIL import Image,ImageOps,ImageFilter
    d=document(doc); folder=DOCS/doc; source=folder/'source.pdf'; config=d['config']; report={'warnings':[],'pages':[],'searchable':False,'engines':[]}
    resumed=resume_state(d,folder)
    if resumed:report=resumed['report'];config={**resumed['config'],**config}
    status(doc,status='processing',stage='Resuming saved pages' if resumed else 'Inspecting PDF',error='',report=report,progress=resumed['next_page']-1 if resumed else 0)
    ai=None
    try:
        if not dependencies()['ready']: raise ValueError('Missing OCR tools. Run the included setup command, then Retry.')
        info=run(['pdfinfo',source]).stdout
        match=re.search(r'^Pages:\s+(\d+)',info,re.M)
        if not match: raise ValueError('Could not read the PDF. Password-protected PDFs must be unlocked first.')
        total=int(match.group(1))
        if not 1<=total<=2000: raise ValueError('Use PDFs with between 1 and 2,000 pages.')
        status(doc,pages=total,stage='Making searchable PDF (two CPU workers)')
        searchable=folder/'searchable.pdf'
        if searchable.exists():
            try:
                existing=run(['pdfinfo',searchable]).stdout
                if not re.search(r'^Pages:\s+'+str(total)+r'\s*$',existing,re.M):raise ValueError('Incomplete searchable PDF')
            except (ValueError,subprocess.SubprocessError):searchable.unlink()
        if not searchable.exists():
            try:
                run(['ocrmypdf','--skip-text','--rotate-pages','--deskew','--jobs',str(RESOURCES['ocr_jobs']),'--optimize','0',
                     '--output-type','pdf','--tesseract-timeout','300',source,searchable],timeout=max(600,total*360))
                report['searchable']=True
            except (subprocess.CalledProcessError,subprocess.TimeoutExpired) as e:
                report['warnings'].append('Searchable PDF failed. Original PDF retained; transaction OCR will still run. See ocrmypdf.log.')
                (folder/'ocrmypdf.log').write_text(str(getattr(e,'stderr','') or str(e)))
                if searchable.exists(): searchable.unlink()
        else: report['searchable']=True
        render=searchable if report['searchable'] else source
        if os.environ.get('STATEMENT_AI_MODEL') and (not resumed or resumed['next_page']<=total):
            status(doc,stage='Loading local specialist AI (no cloud calls)')
            try:
                ai=LocalAI(folder)
                if ai.available:report['engines'].append('Local GLM-OCR 8-bit AI')
            except Exception as e:report['warnings'].append(f'Local AI failed to load ({type(e).__name__}); OCR retained.')
        allrows=resumed['rows'] if resumed else []
        columns=resumed.get('columns',{}) if resumed else {}
        bank_evidence=resumed.get('bank_evidence','') if resumed else ''
        recognized=resumed.get('recognized',False) if resumed else False
        for page in range(resumed['next_page'] if resumed else 1,total+1):
            status(doc,stage=f'Reading page {page} of {total} with independent OCR passes',progress=page-1,report=report)
            prefix=folder/'working-page'
            run(['pdftoppm','-f',page,'-l',page,'-singlefile','-scale-to','4500','-png',render,prefix])
            image=prefix.with_suffix('.png')
            with Image.open(image) as im:
                width,height=im.size
                im.convert('RGB').save(folder/f'page-{page}.jpg',quality=85)
                prepared=ImageOps.autocontrast(ImageOps.grayscale(im),cutoff=1).filter(ImageFilter.UnsharpMask(radius=1.2,percent=120,threshold=3))
                prepared.save(folder/'enhanced.png')
            candidates=[]
            primary_path=folder/f'ocr-{page}-tesseract.json'
            primary=json.loads(primary_path.read_text()) if primary_path.exists() else []
            if not primary:primary=ocr(image,6)
            candidates.append(('Tesseract table',primary))
            vision=Path(os.environ.get('STATEMENT_VISION',str(ROOT/'work/VisionOCR')))
            if vision.is_file():
                try:
                    cached=folder/f'ocr-{page}-apple.json'
                    candidates.append(('Apple Vision accurate',json.loads(cached.read_text()) if cached.exists() else json.loads(run([vision,image]).stdout)))
                except Exception as e: report['warnings'].append(f'Page {page}: Apple Vision unavailable ({type(e).__name__}); Tesseract retained.')
            # Thorough mode also runs an independent segmentation and contrast pass.
            if config.get('thorough',True):
                cached=folder/f'ocr-{page}-tesseract-enhanced.json'
                enhanced=json.loads(cached.read_text()) if cached.exists() else []
                if not enhanced:enhanced=ocr(folder/'enhanced.png',4)
                candidates.append(('Tesseract enhanced',enhanced))
            parsed=[]
            for name,words in candidates:
                parsed_rows,detected,warnings,opening=extract_page(words,width,page,config.get('columns') or None)
                if len(detected)<3 and len(columns)>=3:
                    parsed_rows,_,warnings,opening=extract_page(words,width,page,config.get('columns') or columns)
                parsed.append((pass_score(parsed_rows),name,words,parsed_rows,detected,warnings,opening))
                (folder/f'ocr-{page}-{name.split()[0].lower()}{"-enhanced" if "enhanced" in name else ""}.json').write_text(json.dumps(words))
            best=max(parsed,key=lambda x:x[0]); _,name,words,page_rows,detected,warnings,opening=best
            page_text='\n'.join(l['text'] for l in group_lines(words))
            explicit_empty_summary=empty_summary(page_text)
            grid_words=words;grid_rows=[];grid_opening=None;grid_report={'grid':False,'empty_summary':explicit_empty_summary}
            if not explicit_empty_summary:
                grid_words=recover_reversed_headers(image,words,folder,page,vision)
                grid_rows,grid_opening,grid_report=extract_grid(image,grid_words,folder,page,vision)
                if not grid_rows:
                    faint_words=recover_reversed_headers(image,words,folder,page,vision,faint=True)
                    faint_rows,faint_opening,faint_report=extract_grid(image,faint_words,folder,page,vision)
                    if faint_rows:grid_rows,grid_opening,grid_report,grid_words=faint_rows,faint_opening,faint_report,faint_words
            (folder/f'grid-{page}.json').write_text(json.dumps(grid_report))
            if grid_rows:
                page_rows=grid_rows;name='Automatic table-cell OCR';warnings=[]
                if grid_opening is not None:opening=grid_opening
                detected=extract_page(grid_words,width,page)[1]
            if len(detected)>=3: columns=detected
            bank_evidence+='\n'+page_text
            recognized=recognized or is_statement(bank_evidence,columns)
            if explicit_empty_summary and re.search(r'\b(?:bank|ifsc|account\s+number)\b',page_text,re.I):recognized=True
            if explicit_empty_summary:
                page_rows=[]
                warnings.append(f'Page {page}: account summary explicitly states there are no transactions.')
            if opening is not None and config.get('opening') is None and not allrows:
                config['opening']=opening; config['opening_source']='Printed opening balance'
            for r in page_rows:
                if r.get('_cell_evidence'):continue
                r['_engine']=name; r['_agreement']=0; r['_alternatives']=[]
                for other in parsed:
                    if other[1]==name: continue
                    match=next((q for q in other[3] if abs(q['_top']-r['_top'])<max(50,height*.008) and q['date']==r['date']),None)
                    if match:
                        r['_alternatives'].append({**{k:match.get(k) for k in NUMERIC},'_engine':other[1],'confidence':match['confidence']})
                    if match and all(cents(match.get(k))==cents(r.get(k)) for k in NUMERIC): r['_agreement']+=1
                    else: r['_issues'].append('OCR passes disagree or another pass missed this row')
                with Image.open(image) as im:
                    crop=f'crop-{page}-{r["ord"]}.png'
                    im.crop((0,max(0,r['_top']-18),width,min(height,r['_bottom']+22))).save(folder/crop)
                    r['crop']=crop
                r.pop('_ocr',None)
            if not page_rows and not explicit_empty_summary: warnings.append(f'Page {page}: no transactions detected. Review the full page; this is not evidence that it contains none.')
            other_count=0 if explicit_empty_summary else max((len(p[3]) for p in parsed),default=0)
            if len(page_rows)<other_count: warnings.append(f'Page {page}: another OCR pass found more dated rows; inspect for omissions.')
            report['warnings'].extend(warnings)
            report['pages'].append({'page':page,'rows':len(page_rows),'engine':name,'columns':columns,'warnings':warnings})
            report['engines']=list(dict.fromkeys(report['engines']+[p[1] for p in parsed]))
            allrows.extend(page_rows)
            if page_rows and opening is not None:page_rows[0]['_opening_balance']=opening
            if ai and ai.available and not explicit_empty_summary:
                status(doc,stage=f'Local AI reading transaction table on page {page} of {total}')
                try:
                    proposal=ai.read(image,grid_words,page)
                    assisted,ai_opening=combine_ai(page_rows,proposal,page)
                    if assisted:
                        if page_rows:del allrows[-len(page_rows):]
                        if opening is not None:assisted[0]['_opening_balance']=opening
                        elif ai_opening is not None:
                            assisted[0]['_opening_balance']=ai_opening
                            assisted[0]['_issues'].append('Opening balance read by local AI; verify source')
                        page_rows=assisted;allrows.extend(assisted)
                        report['pages'][-1]['rows']=len(page_rows)
                        report['pages'][-1]['engine']='Table/OCR + local GLM-OCR AI'
                        if any(r.get('date') and r.get('balance') is not None and (r.get('debit') or r.get('credit')) for r in assisted):
                            recognized=recognized or is_statement(bank_evidence,{'balance':0,'debit':0})
                except Exception as e:report['warnings'].append(f'Page {page}: local AI did not complete ({type(e).__name__}); OCR rows retained.')
            decode_balance_chain(allrows,config.get('opening'))
            infer_two_sided_balances(allrows,config.get('opening'))
            infer_missing_amounts(allrows,config.get('opening'))
            select_observed_candidates(allrows,config.get('opening'),config.get('order','auto'))
            reconcile(allrows,config.get('opening'),config.get('order','auto'),infer=True)
            current=[r for r in allrows if r['page']==page and r.get('state')!='deleted']
            unresolved=any(not r.get('date') or r.get('balance') is None or (r.get('debit') is None and r.get('credit') is None)
                           or 'balance mismatch' in r.get('reason','') for r in current)
            if ai and ai.available and unresolved and not explicit_empty_summary:
                status(doc,stage=f'Automatically recovering uncertain numbers on page {page} of {total}')
                try:
                    proposal=ai.read(image,grid_words,page,recovery=True)
                    recovered,_=combine_ai(current,proposal,page)
                    if recovered:
                        previous=[r for r in allrows if r['page']!=page]
                        allrows=previous+recovered
                        decode_balance_chain(allrows,config.get('opening'))
                        infer_two_sided_balances(allrows,config.get('opening'));infer_missing_amounts(allrows,config.get('opening'))
                        select_observed_candidates(allrows,config.get('opening'),config.get('order','auto'))
                        reconcile(allrows,config.get('opening'),config.get('order','auto'),infer=True)
                        report['pages'][-1]['rows']=len(recovered)
                    report['pages'][-1]['automatic_recovery']=True
                except Exception as e:report['warnings'].append(f'Page {page}: automatic recovery exhausted ({type(e).__name__}); best available readings retained.')
            report['statement_detected']=recognized
            replace_rows(doc,allrows)
            status(doc,progress=page,report=report,config=config)
            atomic_json(folder/'checkpoint.json',{'source_signature':source_signature(folder),'next_page':page+1,
                        'rows':allrows,'report':report,'config':config,'columns':columns,'bank_evidence':bank_evidence,'recognized':recognized})
            image.unlink(missing_ok=True); (folder/'enhanced.png').unlink(missing_ok=True)
        report['statement_detected']=recognized
        replace_rows(doc,allrows)
        report['automatic']=automatic_report(allrows)
        if not recognized: report['warnings'].insert(0,'No bank transaction table was confidently identified. A diagnostic report was saved automatically.')
        status(doc,status='ready' if recognized else 'unsupported',stage='Extraction complete' if recognized else 'No bank transactions identified',report=report,progress=total,config=config)
        finish_outputs(doc,allrows)
    except Exception as e:
        detail=getattr(e,'stderr','') or str(e)
        (folder/'processing-error.log').write_text(detail)
        status(doc,status='error',stage='Processing stopped',error=str(e)[:400],report=report)
    finally:
        if ai:ai.close()


def run_job(doc):
    try:
        acquire_slot(doc)
        try:process(doc)
        except Exception as e:status(doc,status='error',stage='Recovering automatically',error=str(e)[:400])
        d=document(doc)
        if d['status']=='error':
            attempts=d['config'].get('automatic_attempts',0)+1
            config={**d['config'],'automatic_attempts':attempts}
            if attempts<2:
                status(doc,status='queued',stage='Retrying automatically from saved progress',config=config);JOBS.put(doc)
            else:
                d['report']['warnings'].append('Automatic retries exhausted. The source and recovered rows are retained.')
                status(doc,stage='Automatic processing could not complete',config=config,report=d['report'])
                failure=DATA/'exports'/f"Incomplete--{doc[:8]}";failure.mkdir(parents=True,exist_ok=True)
                atomic_json(failure/'extraction-report.json',{'error':d['error'],**d['report']})
    finally:set_active(doc,False)


def worker():
    while True:
        doc=JOBS.get()
        try:run_job(doc)
        except Exception as e:
            try:status(doc,status='error',stage='Automatic processing could not complete',error=str(e)[:400])
            except Exception:pass
        finally:JOBS.task_done()


def people_list():
    with connection() as c: r=c.execute("SELECT value FROM settings WHERE key='people'").fetchone()
    return json.loads(r[0]) if r else []


def spreadsheet_safe(v):
    # Prevent statement text being interpreted as formulas by spreadsheet software.
    return "'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@','\t','\r')) else v


class Handler(BaseHTTPRequestHandler):
    server_version='PrivateStatement/2'
    def log_message(self,*args): pass
    def send(self,code,data=b'',kind='application/json',headers=None):
        self.send_response(code); self.send_header('Content-Type',kind)
        self.send_header('Content-Length',str(len(data))); self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(data)
    def json(self,value,code=200): self.send(code,json.dumps(value,allow_nan=False).encode())
    def authorized(self):
        if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}': return False
        cookie=SimpleCookie(); cookie.load(self.headers.get('Cookie',''))
        supplied=cookie['statement_session'].value if 'statement_session' in cookie else self.headers.get('X-Statement-Token','')
        return secrets.compare_digest(supplied,TOKEN)
    def body(self):
        size=int(self.headers.get('Content-Length','0'))
        if size>2_000_000: raise ValueError('Request too large')
        return json.loads(self.rfile.read(size))
    def do_GET(self):
        try: self.get()
        except (ValueError,KeyError) as e: self.json({'error':str(e)},400)
        except Exception: self.json({'error':'Could not read local data.'},500)
    def get(self):
        u=urlparse(self.path); path=u.path
        if path=='/' and secrets.compare_digest(parse_qs(u.query).get('token',[''])[0],TOKEN):
            return self.send(303,headers={'Location':'/','Set-Cookie':f'statement_session={TOKEN}; HttpOnly; SameSite=Strict; Path=/'})
        if not self.authorized(): return self.json({'error':'Open this window from the Mac app or use the launch URL printed by the server.'},403)
        if path=='/': return self.send(200,(ROOT/'static/index.html').read_bytes(),'text/html; charset=utf-8')
        if path in ('/ui.js','/evidence.js','/style.css'):
            return self.send(200,(ROOT/'static'/path[1:]).read_bytes(),'text/javascript' if path.endswith('.js') else 'text/css')
        if path=='/api/health': return self.json(dependencies())
        if path=='/api/people': return self.json(people_list())
        if path=='/api/documents':
            with connection() as c: docs=[dict(r) for r in c.execute("SELECT * FROM bank_documents WHERE status<>'duplicate' ORDER BY created DESC")]
            for d in docs: d['config']=json.loads(d['config']); d['report']=json.loads(d['report'])
            return self.json(docs)
        if path.startswith('/api/rows/'):
            doc=path.rsplit('/',1)[1]; document(doc); rows=rows_for(doc); people=people_list()
            for r in rows: r['people_suggestions']=suggestions(r['original_description'],people)
            return self.json(rows)
        if path.startswith('/files/'):
            f=(DOCS/path.removeprefix('/files/')).resolve()
            if DOCS not in f.parents or not f.is_file(): return self.json({'error':'File not available yet'},404)
            if f.suffix not in ('.pdf','.png','.jpg','.json','.log'): return self.json({'error':'Unsupported file'},400)
            mime={'.pdf':'application/pdf','.png':'image/png','.jpg':'image/jpeg','.json':'application/json','.log':'text/plain'}[f.suffix]
            return self.send(200,f.read_bytes(),mime)
        if path.startswith('/export/'):
            doc,kind=path.removeprefix('/export/').split('/'); d=document(doc)
            if kind!='audit' and d['status']!='ready':
                raise ValueError('Finish extraction and confirm a recognizable bank statement layout before exporting transactions.')
            rows=prepared_rows(rows_for(doc))
            if parse_qs(u.query).get('approved')==['1']: rows=[r for r in rows if r.get('state')=='approved']
            if kind=='csv':
                return self.send(200,export_bytes(kind,rows,d),'text/csv; charset=utf-8',{'Content-Disposition':'attachment; filename="transactions.csv"'})
            if kind=='xlsx':
                return self.send(200,export_bytes(kind,rows,d),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',{'Content-Disposition':'attachment; filename="transactions.xlsx"'})
            if kind=='audit':
                with connection() as c: history=[dict(r) for r in c.execute('SELECT * FROM audit WHERE document_id=?',(doc,))]
                return self.send(200,json.dumps(history,indent=2).encode(),'application/json',{'Content-Disposition':'attachment; filename="review-audit.json"'})
        return self.json({'error':'Not found'},404)
    def do_POST(self):
        if not self.authorized(): return self.json({'error':'Invalid local session'},403)
        origin=self.headers.get('Origin')
        if origin and origin!=f'http://127.0.0.1:{self.server.server_port}': return self.json({'error':'Invalid request origin'},403)
        try: self.post()
        except (ValueError,KeyError,TypeError) as e: self.json({'error':str(e)},400)
        except Exception: self.json({'error':'Could not save changes. Your source PDF is retained.'},500)
    def post(self):
        path=urlparse(self.path).path
        if path=='/api/upload':
            size=int(self.headers.get('Content-Length','0'))
            if not 5<=size<=1024**3: raise ValueError('Choose a PDF smaller than 1 GB.')
            name=Path(self.headers.get('X-Filename','statement.pdf')).name[:200]
            doc=str(uuid.uuid4()); folder=DOCS/doc; folder.mkdir(mode=0o700)
            remaining=size
            with (folder/'source.pdf').open('wb') as out:
                first=self.rfile.read(min(65536,remaining)); remaining-=len(first)
                if not first.startswith(b'%PDF-'): raise ValueError('Choose a PDF file.')
                out.write(first)
                while remaining:
                    chunk=self.rfile.read(min(1024*1024,remaining))
                    if not chunk: raise ValueError('Upload interrupted; select the PDF again.')
                    out.write(chunk); remaining-=len(chunk)
            with (folder/'source.pdf').open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
            with connection() as c:existing=[dict(r) for r in c.execute("SELECT id,config FROM bank_documents WHERE status<>'duplicate' ORDER BY created,id")]
            reused=next((r['id'] for r in existing if json.loads(r['config']).get('source_sha256')==digest),None)
            if reused:
                (folder/'source.pdf').unlink();folder.rmdir()
                return self.json({'id':reused,'already_imported':True})
            config={'thorough':True,'order':'auto','opening':None,'source_sha256':digest,'source_signature':source_signature(folder)}
            with connection() as c: c.execute('INSERT INTO bank_documents(id,name,status,created,stage,config) VALUES(?,?,?,?,?,?)',(doc,name,'queued',stamp(),'Waiting for OCR',json.dumps(config)))
            JOBS.put(doc); return self.json({'id':doc},201)
        if path=='/api/people':
            people=self.body()
            if not isinstance(people,list) or len(people)>5000: raise ValueError('Provide a list of up to 5,000 people.')
            cleaned=[]
            for p in people:
                if not isinstance(p.get('name'),str) or not p['name'].strip(): raise ValueError('Each person needs a name.')
                if not isinstance(p.get('aliases',[]),list) or any(not isinstance(a,str) for a in p.get('aliases',[])): raise ValueError('Aliases must be text.')
                cleaned.append({'name':p['name'].strip()[:150],'aliases':[a[:150] for a in p.get('aliases',[])][:20]})
            with connection() as c: c.execute("INSERT OR REPLACE INTO settings VALUES('people',?)",(json.dumps(cleaned),))
            return self.json({'ok':True})
        if path.startswith('/api/add/'):
            doc=path.rsplit('/',1)[1]; d=document(doc); payload=self.body()
            if d['status'] in ('queued','processing'): raise ValueError('Wait for OCR to finish before adding rows.')
            page=payload.get('page')
            if not isinstance(page,int) or not 1<=page<=d['pages']: raise ValueError('Choose an available source page.')
            rows=rows_for(doc); anchor=next((r for r in rows if r['id']==payload.get('after') and r['page']==page),None)
            ordinal=anchor['ord']+.5 if anchor else max((r['ord'] for r in rows if r['page']==page),default=-1)+1
            row={'id':str(uuid.uuid4()),'date':anchor['date'] if anchor else datetime.now().date().isoformat(),
                 'original_description':'[Manually added; transcribe from source page]',
                 'normalized_description':'','cheque_number':'','debit':None,'credit':None,'balance':None,
                 'counterparty':'','page':page,'ord':ordinal,'crop':f'page-{page}.jpg','confidence':0,
                 'state':'review','suspicious':True,'reason':'Manual transcription needs review',
                 '_issues':['Manually added from full page; verify transcription'],'_engine':'Manual transcription'}
            rows.append(row); rows.sort(key=lambda r:(r['page'],r['ord']))
            for n,r in enumerate(rows): r['ord']=n
            with connection() as c:
                save_rows(doc,rows,c)
                c.execute('INSERT INTO audit(document_id,row_id,time,before_json,after_json) VALUES(?,?,?,?,?)',(doc,row['id'],stamp(),'null',json.dumps(row)))
            recalc(doc); return self.json({'id':row['id']},201)
        if path.startswith('/api/document/'):
            doc=path.rsplit('/',1)[1]; d=document(doc); payload=self.body()
            if d['status'] in ('queued','processing'): raise ValueError('Wait for processing to finish before changing this statement.')
            config=d['config']
            if 'opening' in payload:
                value=payload['opening']
                if value not in (None,'') and cents(value) is None: raise ValueError('Opening balance must be a valid amount.')
                config['opening']=parse_amount(value); config['opening_source']='Reviewer entered'
            if 'order' in payload:
                if payload['order'] not in ('auto','ascending','descending'): raise ValueError('Invalid transaction order.')
                config['order']=payload['order']
            if 'columns' in payload:
                cols=payload['columns']
                if cols is not None and (not isinstance(cols,dict) or any(k not in ('date','description','cheque_number','debit','credit','balance','amount') or not isinstance(v,(int,float)) or not 0<=v<=1 for k,v in cols.items())): raise ValueError('Column centers must be fractions from 0 to 1.')
                config['columns']=cols
            status(doc,config=config)
            if payload.get('retry'):
                if rows_for(doc):
                    with connection() as c:
                        c.execute('INSERT INTO audit(document_id,row_id,time,before_json,after_json) VALUES(?,?,?,?,?)',(doc,'ALL',stamp(),json.dumps(rows_for(doc)),'Re-extraction requested'))
                        c.execute('DELETE FROM bank_rows WHERE document_id=?',(doc,))
                (DOCS/doc/'checkpoint.json').unlink(missing_ok=True)
                config['automatic_attempts']=0
                status(doc,status='queued',stage='Waiting for re-extraction',progress=0,report={},config=config); JOBS.put(doc)
            else: recalc(doc)
            return self.json({'ok':True})
        if path.startswith('/api/row/'):
            row_id=path.rsplit('/',1)[1]; payload=self.body()
            with connection() as c:
                found=c.execute('SELECT document_id,payload FROM bank_rows WHERE id=?',(row_id,)).fetchone()
                if not found: raise ValueError('Row not found')
                doc=found['document_id']; before=json.loads(found['payload']); after=dict(before)
                if document(doc)['status'] in ('queued','processing'): raise ValueError('Wait for OCR to finish before editing.')
                for k,v in payload.items():
                    if k in NUMERIC:
                        if v not in (None,'') and cents(v) is None: raise ValueError('Enter a valid amount.')
                        val=parse_amount(v)
                        if k!='balance' and val is not None and val<0: raise ValueError('Debit and credit must be non-negative.')
                        after[k]=val
                    elif k in ('date','normalized_description','counterparty','cheque_number'):
                        if not isinstance(v,str) or len(v)>10000: raise ValueError('Invalid text field')
                        if k=='date': datetime.strptime(v,'%Y-%m-%d')
                        after[k]=v
                    elif k=='state':
                        if v not in ('review','approved','deleted'): raise ValueError('Invalid review state')
                        after[k]=v
                    else: raise ValueError('Original OCR evidence is read-only. Edit normalized description instead.')
                c.execute('INSERT INTO audit(document_id,row_id,time,before_json,after_json) VALUES(?,?,?,?,?)',(doc,row_id,stamp(),json.dumps(before),json.dumps(after)))
                save_rows(doc,[after],c)
            recalc(doc); return self.json({'ok':True})
        return self.json({'error':'Not found'},404)


if __name__=='__main__':
    os.umask(0o077)
    if os.environ.get('STATEMENT_NATIVE'):
        try: os.setsid()
        except OSError: pass
    init_db()
    threading.Thread(target=restore_completed_outputs,daemon=True).start()
    for index in range(RESOURCES['document_workers']):threading.Thread(target=worker,daemon=True,name=f'document-{index+1}').start()
    server=ThreadingHTTPServer(('127.0.0.1',int(os.environ.get('STATEMENT_PORT','0'))),Handler)
    url=f'http://127.0.0.1:{server.server_port}/?token={TOKEN}'
    if os.environ.get('STATEMENT_READY'):
        Path(os.environ['STATEMENT_READY']).write_text(json.dumps({'url':url,'pid':os.getpid()}))
    print('Private Statement Reader: '+url,flush=True)
    server.serve_forever()
