"""Local, best-effort resource budgets. Never claim an OS-enforced RAM quota."""
import os
import json
from pathlib import Path
import subprocess
import threading
import time

GIB=1024**3


def process_tree(output,root):
    records={}
    for line in output.splitlines():
        try:
            pid,parent,rss=map(int,line.split());records[pid]=(parent,rss*1024)
        except ValueError:continue
    included={root}
    while True:
        added={pid for pid,(parent,_) in records.items() if parent in included}-included
        if not added:break
        included.update(added)
    return {pid:records[pid][1] for pid in included if pid in records}


def descendant_rss(output,root):return sum(process_tree(output,root).values())


def gpu_memory(folder,pids,now=None):
    if folder is None:return 0,0
    total=0;reported=0;now=time.time() if now is None else now
    for pid in pids:
        try:
            record=json.loads((Path(folder)/f'ai-memory-{pid}.json').read_text())
            if record.get('pid')!=pid or not 0<=now-record['time']<=6:continue
            value=record['metal_bytes']
            if type(value) is not int or not 0<=value<=1024*GIB:continue
            total+=value;reported+=1
        except (OSError,ValueError,TypeError,KeyError):continue
    return total,reported


class ResourceBudget:
    def __init__(self,hardware):
        self.hardware=dict(hardware);self.lock=threading.RLock()
        self.condition=threading.Condition(self.lock);self.pages=set()
        self.used_bytes=None;self.sample_time=None;self.sample_error=False
        self.settings=self.preset('balanced');self.monitor_started=False
        self.metrics_directory=None;self.gpu_bytes=0;self.gpu_reporters=0

    @property
    def max_memory(self):return max(4,self.hardware['memory_gb']-2)
    @property
    def max_documents(self):return min(8,self.hardware['cpu_cores'])

    def preset(self,name):
        if name not in ('light','balanced','maximum'):raise ValueError('Choose Light, Balanced, Maximum or Custom.')
        cores=self.hardware['cpu_cores'];memory=self.hardware['memory_gb']
        candidates={'light':(1,2,6,1),'balanced':(2,max(2,cores//2),min(10,max(4,memory-4)),1),
                    'maximum':(min(8,max(1,cores//2)),cores,max(4,memory-2),2 if memory>=16 else 1)}
        docs,cpu,ram,ai=candidates[name]
        return {'preset':name,'documents':min(docs,self.max_documents),'cpu_threads':min(cpu,cores),
                'memory_gb':min(ram,self.max_memory),'ai_instances':ai if ram>=10 else 1}

    def validate(self,value):
        if not isinstance(value,dict):raise ValueError('Resource settings must be an object.')
        if set(value)-{'preset','documents','cpu_threads','memory_gb','ai_instances'}:raise ValueError('Unknown resource setting.')
        name=value.get('preset','custom')
        if name in ('light','balanced','maximum'):return self.preset(name)
        if name!='custom':raise ValueError('Unknown resource preset.')
        limits={'documents':(1,self.max_documents),'cpu_threads':(1,self.hardware['cpu_cores']),
                'memory_gb':(4,self.max_memory),'ai_instances':(1,2)}
        result={'preset':'custom'}
        for key,(low,high) in limits.items():
            number=value.get(key)
            if type(number) is not int or not low<=number<=high:raise ValueError(f'{key.replace("_"," ")} must be a whole number from {low} to {high}.')
            result[key]=number
        if result['ai_instances']==2 and result['memory_gb']<10:raise ValueError('Two AI instances need a memory budget of at least 10 GB.')
        return result

    def configure(self,value):
        settings=self.validate(value)
        with self.condition:self.settings=settings;self.condition.notify_all()
        return dict(settings)

    def limits(self):
        with self.lock:
            s=dict(self.settings)
            # Reserve room for model(s), page images and OCR subprocesses.
            memory_slots=max(1,int((s['memory_gb']-2-1.5*s['ai_instances'])/1.25))
            ocr_threads=max(1,s['cpu_threads']-min(s['ai_instances'],s['cpu_threads']-1))
            documents=min(s['documents'],ocr_threads,memory_slots)
            return {**s,'document_workers':documents,'ocr_jobs':max(1,min(2,ocr_threads//documents)),
                    'memory_blocked':self.used_bytes is not None and self.used_bytes>=s['memory_gb']*GIB*.9}

    def ai_limit(self):
        with self.lock:
            limit=self.limits()
            # Stop idle model copies before gating page admission to reclaim memory.
            if limit['memory_blocked']:return 0
            return limit['ai_instances']

    def enter_page(self,identity,waiting=None):
        with self.condition:
            self.pages.discard(identity);self.condition.notify_all()
            announced=False
            while True:
                limit=self.limits()
                if len(self.pages)<limit['document_workers'] and not limit['memory_blocked']:
                    self.pages.add(identity);return
                if not announced and waiting:waiting();announced=True
                self.condition.wait(timeout=2)

    def leave_page(self,identity):
        with self.condition:self.pages.discard(identity);self.condition.notify_all()

    def sample(self):
        try:
            result=subprocess.run(['/bin/ps','-axo','pid=,ppid=,rss='],capture_output=True,text=True,timeout=3,check=True)
            tree=process_tree(result.stdout,os.getpid());usage=sum(tree.values())
            if usage<=0:raise ValueError('No process memory sample')
            gpu,reporters=gpu_memory(self.metrics_directory,tree)
            with self.condition:
                # Conservative sum: shared Metal/RSS pages can overlap. Never hide model allocations.
                self.used_bytes=usage+gpu;self.gpu_bytes=gpu;self.gpu_reporters=reporters
                self.sample_time=time.time();self.sample_error=False;self.condition.notify_all()
        except (OSError,ValueError,subprocess.SubprocessError):
            # Do not deadlock forever on a stale high reading when sampling fails.
            with self.condition:self.used_bytes=None;self.sample_error=True;self.condition.notify_all()

    def start_monitor(self):
        with self.lock:
            if self.monitor_started:return
            self.monitor_started=True
        def monitor():
            while True:self.sample();time.sleep(2)
        threading.Thread(target=monitor,daemon=True,name='resource-memory-monitor').start()

    def snapshot(self):
        with self.lock:
            return {'settings':dict(self.settings),'effective':self.limits(),
                    'hardware':{'memory_gb':self.hardware['memory_gb'],'cpu_cores':self.hardware['cpu_cores']},
                    'bounds':{'documents':self.max_documents,'memory_gb':self.max_memory,'cpu_threads':self.hardware['cpu_cores']},
                    'presets':{name:self.preset(name) for name in ('light','balanced','maximum')},
                    'memory_used_gb':round(self.used_bytes/GIB,2) if self.used_bytes is not None else None,
                    'memory_sample_time':self.sample_time,'memory_sample_error':self.sample_error,
                    'gpu_reporters':self.gpu_reporters,'reported_gpu_gb':round(self.gpu_bytes/GIB,2),
                    'running_pages':len(self.pages),'memory_limit_kind':'best_effort',
                    'memory_scope':'Engine and child resident memory plus reported AI GPU allocations; shared pages may count twice. Excludes the app window and unreported system allocations.'}
