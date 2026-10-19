"""Local AI proposals with source provenance and independent arithmetic checks."""
import copy,json,os,queue,subprocess,threading,uuid,re,difflib,hashlib,time,atexit
from pathlib import Path
from PIL import Image,ImageOps,ImageEnhance
from bank_engine import cents,normal_date,normalize,header_groups,parse_amount


_workers={}
_workers_lock=threading.Lock()


class _SharedWorker:
    """One model per runtime, with only one GPU request in flight at a time."""
    def __init__(self,key):
        self.key=key;self.lock=threading.Lock();self.process=None;self.log=None;self.messages=None

    @staticmethod
    def _read(process,messages):
        try:
            for line in process.stdout:
                try:messages.put(json.loads(line))
                except json.JSONDecodeError:pass
        finally:messages.put({'worker_exited':True})

    def _stop(self):
        process=self.process;self.process=None
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            for stream in (process.stdin,process.stdout):
                try:stream.close()
                except (AttributeError,OSError):pass
        if self.log:self.log.close();self.log=None
        self.messages=None

    def _start(self,folder):
        python,model,packages=self.key
        env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1','DO_NOT_TRACK':'1'}
        env.update({'OMP_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'2','VECLIB_MAXIMUM_THREADS':'2','TOKENIZERS_PARALLELISM':'false'})
        env.pop('PYTHONPATH',None)
        if packages:env['PYTHONPATH']=packages
        self.log=(folder/'local-ai.log').open('a')
        self.process=subprocess.Popen([python,str(Path(__file__).with_name('ai_runner.py')),model],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,text=True,bufsize=1,env=env)
        self.messages=queue.Queue()
        threading.Thread(target=self._read,args=(self.process,self.messages),daemon=True).start()
        ready=self.messages.get(timeout=180)
        if not ready.get('ready'):raise ValueError('AI worker failed to load')

    def exchange(self,path,folder,recovery=False):
        queued=time.monotonic()
        with self.lock:
            started=time.monotonic()
            try:
                if self.process is None or self.process.poll() is not None:
                    self._stop();self._start(folder)
                request_id=uuid.uuid4().hex
                self.process.stdin.write(json.dumps({'id':request_id,'image':str(path),'recovery':recovery})+'\n')
                self.process.stdin.flush()
                answer=self.messages.get(timeout=240)
                if answer.get('id')!=request_id:raise ValueError('AI worker response was interrupted or out of sequence')
                if not answer.get('ok'):raise ValueError(answer.get('error','AI model could not read the table'))
                answer['timing']={**answer.get('timing',{}),'queue_seconds':round(started-queued,3),'worker_seconds':round(time.monotonic()-started,3)}
                return answer
            except queue.Empty as error:
                self._stop()
                raise TimeoutError('Local AI did not respond within the allowed time; its worker was restarted for the next page') from error
            except Exception:
                # Retire the entire response queue, so a late result can never land on another page.
                self._stop();raise


def _memory_pressure():
    """macOS levels are normal=1, warning=2 and critical=4."""
    try:
        result=subprocess.run(['/usr/sbin/sysctl','-n','kern.memorystatus_vm_pressure_level'],capture_output=True,text=True,timeout=2)
        return int(result.stdout.strip()) if result.returncode==0 else 1
    except (OSError,ValueError,subprocess.TimeoutExpired):return 1


class _WorkerPool:
    """Two warm model copies when memory permits, each with one request at a time."""
    def __init__(self,key,count):
        self.workers=[_SharedWorker(key) for _ in range(count)]
        self.busy=[False]*count;self.condition=threading.Condition();self.closed=False
        threading.Thread(target=self._watch_pressure,daemon=True).start()

    def _watch_pressure(self):
        with self.condition:
            while not self.closed:
                self.condition.wait(timeout=2)
                if not self.closed:
                    self._retire_idle_extra(_memory_pressure())
                    self.condition.notify_all()

    def _retire_idle_extra(self,pressure):
        if pressure<2:return
        for index,worker in enumerate(self.workers[1:],start=1):
            if not self.busy[index]:
                with worker.lock:worker._stop()

    def exchange(self,path,folder,recovery=False):
        queued=time.monotonic()
        with self.condition:
            while True:
                if self.closed:raise RuntimeError('Local AI pool is shutting down')
                pressure=_memory_pressure();self._retire_idle_extra(pressure)
                limit=1 if pressure>=2 else len(self.workers)
                index=next((i for i in range(limit) if not self.busy[i]),None)
                if index is not None:
                    self.busy[index]=True;break
                self.condition.wait(timeout=.5)
        try:
            started=time.monotonic()
            answer=self.workers[index].exchange(path,folder,recovery)
            timing=answer.setdefault('timing',{})
            timing['queue_seconds']=round(started-queued+timing.get('queue_seconds',0),3)
            answer['timing']['model_instance']=index+1
            return answer
        finally:
            with self.condition:
                self.busy[index]=False
                self._retire_idle_extra(_memory_pressure())
                self.condition.notify_all()

    def shutdown(self):
        with self.condition:
            self.closed=True;self.condition.notify_all()
            while any(self.busy):self.condition.wait(timeout=.5)
        for worker in self.workers:
            with worker.lock:worker._stop()


def ai_worker_stats():
    with _workers_lock:entries=list(_workers.values())
    stats={'configured':0,'live':0,'busy':0}
    for entry in entries:
        workers=entry.workers if isinstance(entry,_WorkerPool) else [entry]
        stats['configured']+=len(workers)
        for worker in workers:
            process=worker.process
            stats['live']+=int(process is not None and process.poll() is None)
        stats['busy']+=sum(entry.busy) if isinstance(entry,_WorkerPool) else int(entry.lock.locked())
    return stats


def shutdown_shared_ai():
    with _workers_lock:
        workers=list(_workers.values());_workers.clear()
    for worker in workers:
        if isinstance(worker,_WorkerPool):worker.shutdown()
        else:
            with worker.lock:worker._stop()


atexit.register(shutdown_shared_ai)


class LocalAI:
    def __init__(self,folder):
        self.folder=Path(folder);self._worker=None
        python=os.environ.get('STATEMENT_AI_PYTHON');model=os.environ.get('STATEMENT_AI_MODEL')
        if not python or not model or not Path(model,'config.json').is_file():return
        key=(python,str(Path(model).resolve()),os.environ.get('STATEMENT_AI_PACKAGES',''))
        try:instances=max(1,min(2,int(os.environ.get('STATEMENT_AI_INSTANCES','1'))))
        except ValueError:instances=1
        with _workers_lock:
            registry_key=(*key,instances)
            if registry_key not in _workers:_workers[registry_key]=_SharedWorker(key) if instances==1 else _WorkerPool(key,instances)
            self._worker=_workers[registry_key]
        revision=hashlib.sha256()
        for source in ('ai_runner.py','ai_formats.py','local_ai.py'):
            revision.update(Path(__file__).with_name(source).read_bytes())
        for source in sorted(Path(model).glob('*')):
            if source.is_file():
                stat=source.stat();revision.update(f'{source.name}:{stat.st_size}:{stat.st_mtime_ns}'.encode())
                if source.suffix=='.json':revision.update(source.read_bytes())
        self.revision=revision.hexdigest()

    @property
    def available(self):return self._worker is not None
    def read(self,image,words,page,recovery=False):
        if not self.available:return None
        with Image.open(image) as im:
            groups=header_groups(words)
            if groups and len({k for w,k in groups[0]})>=3:
                headers=[w for w,k in groups[0]]
                left=max(0,min(w['x'] for w in headers)-100)
                right=min(im.width,max(w['x']+w['w'] for w in headers)+100)
                top=max(0,min(w['y'] for w in headers)-35)
                # Keep the complete table and a little footer context; the model excludes totals.
                bottom=im.height
                crop=im.crop((left,top,right,bottom))
            else:crop=im.copy()
            if recovery:
                crop=ImageEnhance.Sharpness(ImageOps.autocontrast(crop.convert('RGB'),cutoff=.2)).enhance(1.5)
            crop.thumbnail((2200,2500))
            suffix='-recovery' if recovery else ''
            path=self.folder/f'ai-table-{page}{suffix}.png';crop.save(path)
        cache=self.folder/f'ai-reading-{page}{suffix}.json'
        fingerprint=hashlib.sha256(self.revision.encode()+path.read_bytes()+str(recovery).encode()).hexdigest()
        try:
            answer=json.loads(cache.read_text())
            if answer.get('cache_key')==fingerprint and answer.get('ok'):
                return answer['result']
        except (OSError,ValueError,KeyError):pass
        answer=self._worker.exchange(path,self.folder,recovery)
        answer['cache_key']=fingerprint
        temporary=cache.with_suffix('.json.tmp');temporary.write_text(json.dumps(answer,indent=2));temporary.replace(cache)
        return answer['result']
    def close(self):
        # Per-document handles do not own the shared model lifetime.
        pass


def combine_ai(rows,proposal,page):
    if not isinstance(proposal,dict) or not isinstance(proposal.get('transactions'),list):return rows,None
    combined=[];used=set();transactions=proposal['transactions'];opening=parse_amount(proposal.get('opening_balance'))
    original_order={r['id']:i for i,r in enumerate(rows)};last_order=-1
    if len(transactions)>1000:return rows,None
    for item in transactions:
        if not isinstance(item,dict):continue
        rawdate=str(item.get('date') or '')
        date=normal_date(rawdate)
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}',rawdate):date=rawdate
        desc=str(item.get('description') or '')[:5000]
        if re.search(r'(?i)^\s*(?:b\s*/\s*f|opening\s+balance|brought\s+forward)\s*$',desc):
            opening=opening if opening is not None else parse_amount(item.get('balance'))
            continue
        if re.search(r'(?i)^\s*(?:page\s*total|grand\s*total|closing\s*balance)\b',desc):continue
        if not date and not desc:continue
        if not date and all(parse_amount(item.get(k)) is None for k in ('debit','credit','balance')):continue
        def alignment(r):
            similarity=difflib.SequenceMatcher(None,r['original_description'].lower(),desc.lower()).ratio()
            same_balance=cents(r.get('balance')) is not None and cents(r.get('balance'))==cents(item.get('balance'))
            return .65*similarity+.25*(r.get('date')==date)+.1*same_balance
        options=[r for r in rows if r['id'] not in used and alignment(r)>=.58]
        matched=max(options,key=alignment,default=None)
        if matched:
            r=copy.deepcopy(matched);used.add(r['id'])
            last_order=original_order[r['id']];r['_merge_order']=last_order
        else:
            r={'id':str(uuid.uuid4()),'date':date,'original_description':desc,'normalized_description':desc,'cheque_number':'',
               'debit':None,'credit':None,'balance':None,'page':page,'confidence':.55,'suspicious':True,'reason':'',
               'state':'review','crop':f'ai-table-{page}.png','ord':len(combined),'_issues':['AI-only transaction candidate; not independently confirmed'],
               '_engine':'Local GLM-OCR 8-bit AI','_numeric_candidates':{}}
            r['_merge_order']=last_order+.5 if rows else len(combined)
        r['_ai_proposal']=item;r['_ocr_before_ai']={k:r.get(k) for k in ('date','original_description','debit','credit','balance')}
        r.setdefault('_numeric_candidates',{})
        for key in ('debit','credit','balance'):
            value=parse_amount(item.get(key))
            if key!='balance' and value is not None:value=abs(value)
            if value is None:continue
            current=r.get(key)
            choices=r['_numeric_candidates'].setdefault(key,[])
            if current is not None and not any(cents(c['value'])==cents(current) for c in choices):
                choices.append({'value':current,'confidence':r['confidence'],'engine':r['_engine']})
            if not any(cents(c['value'])==cents(value) for c in choices):choices.append({'value':value,'confidence':.65,'engine':'Local GLM-OCR AI guess'})
            if current is None:r[key]=value;r['_issues'].append(key+': filled by local AI estimate')
        if not r.get('date') and date:r['date']=date;r['_issues'].append('Date proposed by local AI')
        if normalize(desc)!=normalize(r.get('normalized_description','')):
            r['normalized_description']=normalize(desc);r['_issues'].append('Description/name proposed by local AI; original OCR retained')
        if not r.get('cheque_number'):r['cheque_number']=str(item.get('cheque_number') or '')[:100]
        person=str(item.get('counterparty') or '').strip()[:150]
        if person:
            r['counterparty']=person
            r.setdefault('_inferred_fields',{})['counterparty']={'method':'local AI reading/name guess','value':person,'not_verified_identity':True}
            r['_issues'].append('Counterparty name is a local AI guess, not verified identity')
        r['ord']=len(combined);combined.append(r)
    # Never lose rows just because the generative model omitted one.
    for r in rows:
        if r['id'] not in used:
            r=copy.deepcopy(r);r['_issues'].append('Local AI did not independently recover this OCR row');r['_merge_order']=original_order[r['id']];combined.append(r)
    combined.sort(key=lambda r:r['_merge_order'])
    for i,r in enumerate(combined):r['ord']=i;r.pop('_merge_order',None)
    return combined,opening


def infer_missing_amounts(rows,opening=None):
    previous=cents(opening)
    for r in rows:
        if r.get('_opening_balance') is not None:previous=cents(r['_opening_balance'])
        bal=cents(r.get('balance'));debit=cents(r.get('debit'));credit=cents(r.get('credit'))
        if previous is not None and bal is not None and not (debit or credit) and bal!=previous:
            change=bal-previous;key='credit' if change>0 else 'debit'
            r.setdefault('_inferred_fields',{})[key]={'method':'balance delta','value':abs(change)/100,'not_printed_evidence':True}
            r[key]=abs(change)/100;r['_issues'].append(key+': inferred from balance delta, not directly read')
        if previous is not None and bal is None and (debit or credit):
            estimate=previous-(debit or 0)+(credit or 0)
            r.setdefault('_inferred_fields',{})['balance']={'method':'previous balance and transaction amount','value':estimate/100,'not_printed_evidence':True}
            r['balance']=estimate/100;bal=estimate
            r['_issues'].append('Balance estimated from previous balance and transaction amount')
        previous=bal
    return rows


def infer_two_sided_balances(rows,opening=None):
    """Recover an isolated bad balance only when both adjacent equations imply the same value."""
    previous=cents(opening)
    for index,r in enumerate(rows):
        if r.get('_opening_balance') is not None:previous=cents(r['_opening_balance'])
        bal=cents(r.get('balance'));debit=cents(r.get('debit'));credit=cents(r.get('credit'))
        if previous is not None and (debit is not None or credit is not None) and index+1<len(rows):
            nxt=rows[index+1]
            nb=cents(nxt.get('balance'));nd=cents(nxt.get('debit'));nc=cents(nxt.get('credit'))
            if nxt['page']==r['page'] and nb is not None and (nd is not None or nc is not None):
                left=previous-(debit or 0)+(credit or 0)
                right=nb+(nd or 0)-(nc or 0)
                if left==right and left!=bal:
                    r.setdefault('_inferred_fields',{})['balance']={'method':'two adjacent balance equations','value':left/100,'original_value':r.get('balance'),'not_printed_evidence':True}
                    r['balance']=left/100;r['_issues'].append('Balance estimated from two adjacent equations; original reading retained')
        previous=cents(r.get('balance'))
    return rows
