"""Local AI proposals with source provenance and independent arithmetic checks."""
import copy,json,os,queue,subprocess,threading,uuid,re,difflib,hashlib,time,atexit
from datetime import date as calendar_date
from pathlib import Path
from PIL import Image,ImageOps,ImageEnhance
from bank_engine import cents,normal_date,normalize,header_groups,parse_amount


_workers={}
_workers_lock=threading.Lock()
_resource_ai_limit=None


def configure_ai_limits(provider):
    """Provider returns 0 to wait/reclaim, 1 or 2; never kills an active read."""
    global _resource_ai_limit
    _resource_ai_limit=provider
    with _workers_lock:entries=list(_workers.values())
    for entry in entries:
        if isinstance(entry,_WorkerPool):
            with entry.condition:entry.condition.notify_all()


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
        env.update({'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','VECLIB_MAXIMUM_THREADS':'1','TOKENIZERS_PARALLELISM':'false'})
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
        limit=min(1 if pressure>=2 else len(self.workers),_resource_ai_limit() if _resource_ai_limit else len(self.workers))
        for index,worker in enumerate(self.workers):
            if index<limit:continue
            if not self.busy[index]:
                with worker.lock:worker._stop()

    def exchange(self,path,folder,recovery=False):
        queued=time.monotonic()
        with self.condition:
            while True:
                if self.closed:raise RuntimeError('Local AI pool is shutting down')
                pressure=_memory_pressure();self._retire_idle_extra(pressure)
                limit=min(1 if pressure>=2 else len(self.workers),_resource_ai_limit() if _resource_ai_limit else len(self.workers))
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
        try:instances=2 if _resource_ai_limit else max(1,min(2,int(os.environ.get('STATEMENT_AI_INSTANCES','1'))))
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
                # Header labels can end far before right-aligned balance digits.
                # Never trim financial columns using the width of their labels.
                left=0
                right=im.width
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


def _ai_date(value):
    raw=str(value or '')
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',raw):
        try:
            parsed=calendar_date.fromisoformat(raw)
            return raw if 1900<=parsed.year<=2100 else None
        except ValueError:return None
    return normal_date(raw)


def _issue(row,message):
    if message not in row.setdefault('_issues',[]):row['_issues'].append(message)


def _ai_only(row):
    return row.get('_ai_only') or row.get('_engine')=='Local GLM-OCR 8-bit AI'


def _observed(row,key):
    if _ai_only(row) or key in row.get('_inferred_fields',{}) or row.get('_field_sources',{}).get(key) in ('ai','ai_only','inferred'):return None
    return cents(row.get(key))


def _pair_score(row,item):
    desc=normalize(str(item.get('description') or '')).lower()
    original=normalize(str(row.get('original_description') or '')).lower()
    similarity=difflib.SequenceMatcher(None,original,desc).ratio() if desc and original else 0
    date=_ai_date(item.get('date'));same_date=bool(date and date==row.get('date'))
    equal=0;score=.8*similarity+.5*same_date
    for key in ('debit','credit','balance'):
        before=cents(row.get(key));after=cents(item.get(key))
        if before is not None and after is not None and before==after and (after!=0 or key=='balance'):
            equal+=1;score+=.8 if key=='balance' else .65
    # Date disagreements require strong shared visual evidence elsewhere.
    if date and row.get('date') and date!=row['date']:
        if equal<2 or similarity<.8:return 0
        score-=.5
    if similarity<.35 and equal<2:return 0
    if not same_date and equal==0:return 0
    return score if score>=1 else 0


def _monotonic_alignment(rows,items):
    """Maximum-evidence ordered alignment; only uniquely supported pairs survive."""
    n=len(rows);m=len(items)
    scores=[[_pair_score(row,item) for item in items] for row in rows]
    forward=[[0.]*(m+1) for _ in range(n+1)]
    backward=[[0.]*(m+1) for _ in range(n+1)]
    for i in range(n):
        for j in range(m):forward[i+1][j+1]=max(forward[i][j+1],forward[i+1][j],forward[i][j]+scores[i][j])
    for i in range(n-1,-1,-1):
        for j in range(m-1,-1,-1):backward[i][j]=max(backward[i+1][j],backward[i][j+1],backward[i+1][j+1]+scores[i][j])
    optimum=forward[n][m];options={}
    for j in range(m):
        options[j]=[i for i in range(n) if scores[i][j]>0 and forward[i][j]+scores[i][j]+backward[i+1][j+1]>=optimum-.12]
    matches={j:indices[0] for j,indices in options.items() if len(indices)==1}
    # A near-tie may leave two proposal rows competing for one OCR row.
    counts={i:list(matches.values()).count(i) for i in matches.values()}
    matches={j:i for j,i in matches.items() if counts[i]==1}
    conflicts=set()
    for j,i in matches.items():
        for other_j,other_i in matches.items():
            if j<other_j and i>=other_i:conflicts.update((j,other_j))
    return {j:i for j,i in matches.items() if j not in conflicts},scores


def _record_proposal(row,item,status,reason=None):
    entry={'proposal':copy.deepcopy(item),'status':status}
    if reason:entry['reason']=reason
    history=row.setdefault('_ai_proposals',[])
    if entry not in history:history.append(entry)


def _credible_name(person,narration):
    person=normalize(person)
    if not re.fullmatch(r"[A-Za-z][A-Za-z .&'’-]{2,99}",person):return False
    tokens=re.findall(r'[A-Z]+',person.upper())
    forbidden={'TRANSFER','TRFR','TO','FROM','NEFT','RTGS','UPI','IMPS','PAYMENT','UNKNOWN','UNREADABLE','BANK','ACCOUNT','REF','UTR','NA','NIL'}
    if not tokens or any(token in forbidden for token in tokens):return False
    source=' '.join(re.findall(r'[A-Z]+',narration.upper()))
    if ' '+' '.join(tokens)+' ' in ' '+source+' ':return True
    # A small visual spelling repair still needs an exact neighboring name token.
    # Sound alone is never enough to introduce a person absent from the narration.
    from people import phonetic
    source_tokens=source.split()
    if len(tokens)<2:return False
    for start in range(len(source_tokens)-len(tokens)+1):
        segment=source_tokens[start:start+len(tokens)]
        if not any(a==b and len(a)>=4 for a,b in zip(tokens,segment)):continue
        if all(a==b or len(a)>=4 and len(b)>=4 and phonetic(a)==phonetic(b) and
               difflib.SequenceMatcher(None,a,b).ratio()>=.75 for a,b in zip(tokens,segment)):
            return True
    return False


def combine_ai(rows,proposal,page):
    if not isinstance(proposal,dict) or not isinstance(proposal.get('transactions'),list):return rows,None
    if len(proposal['transactions'])>1000 or len(rows)>1000:return rows,None
    items=[];opening=parse_amount(proposal.get('opening_balance'))
    for item in proposal['transactions']:
        if not isinstance(item,dict):continue
        desc=str(item.get('description') or '')[:5000]
        if re.search(r'(?i)^\s*(?:b\s*/\s*f|opening\s+balance|brought\s+forward)\s*$',desc):
            if opening is None:opening=parse_amount(item.get('balance'))
            continue
        if re.search(r'(?i)^\s*(?:page\s*total|grand\s*total|closing\s*balance)\b',desc):continue
        if not _ai_date(item.get('date')) and all(parse_amount(item.get(key)) is None for key in ('debit','credit','balance')):continue
        items.append(item)
    combined=copy.deepcopy(rows);matches,scores=_monotonic_alignment(rows,items);used=set(matches.values());extra=[]
    for j,item in enumerate(items):
        desc=str(item.get('description') or '')[:5000];date=_ai_date(item.get('date'))
        if j in matches:
            index=matches[j];r=combined[index];position=float(index)
        else:
            possible=[i for i in range(len(rows)) if scores[i][j]>0]
            # A proposed transaction sharing date and narration/reference with an
            # existing row is audit evidence, not grounds to insert a duplicate.
            duplicates=[]
            for i,row in enumerate(rows):
                original=str(row.get('original_description') or '')
                similarity=difflib.SequenceMatcher(None,normalize(original).lower(),normalize(desc).lower()).ratio()
                shared_number=any(cents(row.get(key)) is not None and cents(row.get(key))==cents(item.get(key)) and cents(item.get(key))!=0 for key in ('debit','credit','balance'))
                shared_reference=bool(set(re.findall(r'\d{6,}',original)) & set(re.findall(r'\d{6,}',desc)))
                if date and row.get('date')==date and similarity>=.7 or similarity>=.85 and (shared_number or shared_reference):duplicates.append(i)
            if possible or duplicates:
                for i in set(possible+duplicates):_record_proposal(combined[i],item,'rejected_alignment','Ambiguous or competing AI row; source order and values retained')
                continue
            r={'id':str(uuid.uuid4()),'date':date,'original_description':desc,'normalized_description':desc,'cheque_number':'',
               'debit':None,'credit':None,'balance':None,'page':page,'confidence':.55,'suspicious':True,'reason':'',
               'state':'review','crop':f'ai-table-{page}.png','_issues':['AI-only transaction candidate; not independently confirmed'],
               '_engine':'Local GLM-OCR 8-bit AI','_ai_only':True,'_numeric_candidates':{}}
            before=[i for k,i in matches.items() if k<j];after=[i for k,i in matches.items() if k>j]
            position=(max(before)+.5 if before else min(after)-.5 if after else len(rows)+j+.5)
            extra.append((position,j,r))
        r.setdefault('_ocr_before_ai',{key:r.get(key) for key in ('date','original_description','debit','credit','balance')})
        r['_ai_proposal']=copy.deepcopy(item);_record_proposal(r,item,'aligned' if j in matches else 'ai_only')
        source='ai_only' if _ai_only(r) else 'ai'
        for key in ('debit','credit','balance'):
            value=parse_amount(item.get(key));current=r.get(key)
            if key!='balance' and value is not None:value=abs(value)
            if value is None or key!='balance' and value==0:continue
            choices=r.setdefault('_numeric_candidates',{}).setdefault(key,[])
            if current is not None and _observed(r,key) is not None and not any(cents(c.get('value'))==cents(current) and c.get('source') not in ('ai','ai_only') for c in choices):
                choices.append({'value':current,'confidence':r.get('confidence',0),'engine':r.get('_engine','OCR'),'source':'ocr'})
            opposite='credit' if key=='debit' else 'debit'
            corroborated=any(cents(c.get('value'))==cents(value) and c.get('source') not in ('ai','ai_only') and 'ai' not in str(c.get('engine','')).lower() for c in choices)
            if key in ('debit','credit') and cents(r.get(opposite)) and not corroborated:
                r.setdefault('_ai_rejected_fields',[])
                rejected={'field':key,'value':value,'reason':'Opposite direction already populated; no independent source corroboration','source':source}
                if rejected not in r['_ai_rejected_fields']:r['_ai_rejected_fields'].append(rejected)
                continue
            candidate={'value':value,'confidence':.65,'engine':'Local GLM-OCR AI guess','source':source}
            if not any(cents(c.get('value'))==cents(value) and c.get('source')==source for c in choices):choices.append(candidate)
            if current is None:
                r[key]=value;r.setdefault('_field_sources',{})[key]=source
                r.setdefault('_inferred_fields',{}).setdefault(key,{'method':'local AI visual estimate','value':value,'original_value':None,'not_printed_evidence':True})
                _issue(r,key+': filled by local AI estimate')
        if not r.get('date') and date:
            r['date']=date;r.setdefault('_field_sources',{})['date']=source
            r.setdefault('_inferred_fields',{}).setdefault('date',{'method':'local AI visual estimate','value':date,'original_value':None,'not_printed_evidence':True})
            _issue(r,'Date proposed by local AI')
        original=normalize(r.get('original_description',''))
        similarity=difflib.SequenceMatcher(None,original.lower(),normalize(desc).lower()).ratio()
        if desc and (source=='ai_only' or similarity>=.5) and normalize(desc)!=normalize(r.get('normalized_description','')):
            r['normalized_description']=normalize(desc);_issue(r,'Description/name proposed by local AI; original OCR retained')
        if not r.get('cheque_number'):r['cheque_number']=str(item.get('cheque_number') or '')[:100]
        person=str(item.get('counterparty') or '').strip()[:150]
        if person and not r.get('counterparty'):
            if source!='ai_only' and _credible_name(person,original):
                r['counterparty']=person
                r.setdefault('_inferred_fields',{})['counterparty']={'method':'local AI reading/name guess','value':person,'source_text':original,'not_verified_identity':True}
                _issue(r,'Counterparty name is a local AI guess, not verified identity')
            else:_record_proposal(r,item,'rejected_name','Name is not supported by the original narration')
    for i,r in enumerate(combined):
        if i not in used:_issue(r,'Local AI did not independently recover this OCR row')
    ordered=[(float(i),-1,r) for i,r in enumerate(combined)]+extra
    ordered.sort(key=lambda value:(value[0],value[1]))
    result=[value[2] for value in ordered]
    for i,r in enumerate(result):r['ord']=i
    return result,opening


def _segments(rows,opening,order):
    active=[row for row in rows if row.get('state')!='deleted'];segments=[]
    for row in active:
        if not segments or segments[-1][-1].get('page')!=row.get('page') or row.get('_opening_balance') is not None:
            segments.append([])
        segments[-1].append(row)
    dates=[r.get('date') for r in active if r.get('date')]
    descending=order=='descending' or order=='auto' and len(dates)>1 and dates[0]>dates[-1]
    if descending:segments=[list(reversed(segment)) for segment in reversed(segments)]
    for index,segment in enumerate(segments):
        marker=next((r for r in segment if r.get('_opening_balance') is not None),None)
        initial=cents(opening) if index==0 else None
        if marker is not None:
            ai_opening=marker.get('_opening_balance_source') in ('ai','ai_only','inferred') or 'opening_balance' in marker.get('_inferred_fields',{}) or any('opening balance read by local ai' in str(issue).lower() for issue in marker.get('_issues',[]))
            initial=None if ai_opening else cents(marker['_opening_balance'])
        yield segment,initial


def _observed_transaction(row):
    if row.get('_direction')=='balance-supported':return None
    debit=_observed(row,'debit');credit=_observed(row,'credit')
    if debit and credit:return None
    # Nonzero source amount is required; an inferred direction cannot anchor math.
    if not (debit or credit):return None
    if any(key in row.get('_inferred_fields',{}) for key in ('debit','credit')):return None
    return (debit or 0,credit or 0)


def _estimate(row,key,value,method,message):
    if row.get('state') in ('approved','corrected','deleted') or row.get('_engine')=='Manual transcription' or any(row.get(k) for k in ('_manual_fields','_edited_fields','_manual','_edited','_corrected')):return
    if key in row.get('_inferred_fields',{}):return
    row.setdefault('_inferred_fields',{})[key]={'method':method,'value':value/100,'original_value':row.get(key),'not_printed_evidence':True}
    row[key]=value/100;row.setdefault('_field_sources',{})[key]='inferred';_issue(row,message)


def infer_missing_amounts(rows,opening=None,order='auto'):
    for segment,previous in _segments(rows,opening,order):
        for row in segment:
            bal=_observed(row,'balance');debit=cents(row.get('debit'));credit=cents(row.get('credit'))
            if previous is not None and bal is not None and not (debit or credit) and bal!=previous:
                change=bal-previous;key='credit' if change>0 else 'debit'
                _estimate(row,key,abs(change),'balance delta',key+': inferred from balance delta, not directly read')
            transaction=_observed_transaction(row)
            if previous is not None and row.get('balance') is None and transaction:
                _estimate(row,'balance',previous-transaction[0]+transaction[1],'previous balance and transaction amount','Balance estimated from previous balance and transaction amount')
            # The pre-estimate source balance is the only anchor for the next row.
            previous=bal
    return rows


def infer_two_sided_balances(rows,opening=None,order='auto'):
    """An explicit estimate requires two independent neighboring equations."""
    for segment,previous in _segments(rows,opening,order):
        for index,row in enumerate(segment):
            source_balance=_observed(row,'balance');transaction=_observed_transaction(row)
            if previous is not None and transaction and index+1<len(segment):
                nxt=segment[index+1];next_balance=_observed(nxt,'balance');next_transaction=_observed_transaction(nxt)
                if next_balance is not None and next_transaction:
                    left=previous-transaction[0]+transaction[1]
                    right=next_balance+next_transaction[0]-next_transaction[1]
                    if left==right and left!=cents(row.get('balance')):
                        _estimate(row,'balance',left,'two adjacent balance equations','Balance estimated from two adjacent equations; original reading retained')
            previous=_observed(row,'balance')
    return rows
