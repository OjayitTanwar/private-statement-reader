"""Worker lifetime, concurrency, timeout isolation and evidence-cache checks."""
import json,os,queue,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import local_ai


class FakeStream:
    def __init__(self,process):self.process=process
    def write(self,line):
        request=json.loads(line)
        with self.process.guard:
            self.process.active+=1
            self.process.maximum=max(self.process.maximum,self.process.active)
        def finish():
            if self.process.gate:self.process.gate.wait(timeout=5)
            with self.process.guard:self.process.active-=1
            self.process.output.put(json.dumps({'id':request['id'],'ok':True,'result':{'transactions':[]}})+'\n')
        threading.Timer(.01,finish).start()
    def flush(self):pass
    def close(self):pass


class FakeProcess:
    def __init__(self,*args,**kwargs):
        self.output=queue.Queue();self.output.put('{"ready":true}\n')
        self.stdin=FakeStream(self);self.stdout=self;self.dead=False
        self.guard=threading.Lock();self.active=0;self.maximum=0
        self.gate=None
    def __iter__(self):return self
    def __next__(self):
        line=self.output.get()
        if line is None:raise StopIteration
        return line
    def poll(self):return 0 if self.dead else None
    def terminate(self):self.dead=True;self.output.put(None)
    kill=terminate
    def wait(self,timeout=None):return 0
    def close(self):pass


class SharedAITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        model=self.root/'model';model.mkdir();(model/'config.json').write_text('{}')
        self.environment=patch.dict(os.environ,{'STATEMENT_AI_PYTHON':'test-python','STATEMENT_AI_MODEL':str(model),'STATEMENT_AI_INSTANCES':'1'})
        self.environment.start();self.processes=[];self.gate=None
        self.pressure=patch('local_ai._memory_pressure',return_value=1);self.pressure_mock=self.pressure.start()
        def create(*args,**kwargs):
            process=FakeProcess();process.gate=self.gate;self.processes.append(process);return process
        self.popen=patch('local_ai.subprocess.Popen',side_effect=create);self.popen.start()
        self.image=self.root/'page.png';Image.new('RGB',(80,80),'white').save(self.image)
    def tearDown(self):
        local_ai.shutdown_shared_ai();self.popen.stop();self.pressure.stop();self.environment.stop();self.temp.cleanup()
    def client(self,name):
        folder=self.root/name;folder.mkdir();return local_ai.LocalAI(folder)

    def test_concurrent_documents_load_once_and_serialize_gpu(self):
        first=self.client('first');second=self.client('second')
        self.assertIs(first._worker,second._worker)
        self.assertEqual(len(self.processes),0,'model loading should be lazy')
        errors=[]
        def read(client):
            try:client.read(self.image,[],1)
            except Exception as error:errors.append(error)
        threads=[threading.Thread(target=read,args=(client,)) for client in (first,second)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=5)
        self.assertEqual(errors,[]);self.assertEqual(len(self.processes),1)
        self.assertEqual(self.processes[0].maximum,1)
        first.close();self.assertFalse(self.processes[0].dead)
        second.read(self.image,[],2);self.assertEqual(len(self.processes),1)

    def test_crop_cache_changes_with_image_and_recovery(self):
        client=self.client('cache');worker=client._worker
        with patch.object(worker,'exchange',return_value={'ok':True,'result':{'transactions':[]}}) as exchange:
            client.read(self.image,[],1);client.read(self.image,[],1)
            self.assertEqual(exchange.call_count,1)
            Image.new('RGB',(80,80),'black').save(self.image)
            client.read(self.image,[],1);self.assertEqual(exchange.call_count,2)
            client.read(self.image,[],1,recovery=True);self.assertEqual(exchange.call_count,3)
            self.assertTrue((client.folder/'ai-reading-1-recovery.json').is_file())

    def test_timeout_retires_worker_and_uses_a_new_response_queue(self):
        client=self.client('timeout');worker=client._worker
        client.read(self.image,[],1);old_process=worker.process;old_queue=worker.messages
        with patch.object(old_queue,'get',side_effect=queue.Empty):
            with self.assertRaises(TimeoutError):client.read(self.image,[],2)
        self.assertTrue(old_process.dead);self.assertIsNone(worker.process)
        client.read(self.image,[],3)
        self.assertEqual(len(self.processes),2);self.assertIsNot(worker.messages,old_queue)
        answer=json.loads((client.folder/'ai-reading-3.json').read_text())
        self.assertIn('queue_seconds',answer['timing'])

    def test_model_revision_invalidates_existing_cache(self):
        first=self.client('revision');worker=first._worker
        with patch.object(worker,'exchange',return_value={'ok':True,'result':{'transactions':[]}}) as exchange:
            first.read(self.image,[],1)
            (self.root/'model'/'config.json').write_text('{"revision":2}')
            newer=local_ai.LocalAI(first.folder);newer.read(self.image,[],1)
            self.assertEqual(exchange.call_count,2)

    def run_reads(self,clients):
        errors=[]
        def read(client):
            try:client.read(self.image,[],1)
            except Exception as error:errors.append(error)
        threads=[threading.Thread(target=read,args=(client,)) for client in clients]
        for thread in threads:thread.start()
        return threads,errors

    def wait_active(self,count):
        deadline=time.monotonic()+3
        while sum(process.active for process in self.processes)!=count and time.monotonic()<deadline:time.sleep(.005)
        self.assertEqual(sum(process.active for process in self.processes),count)

    def test_two_models_read_simultaneously_third_waits(self):
        os.environ['STATEMENT_AI_INSTANCES']='2';self.gate=threading.Event()
        clients=[self.client(name) for name in ('one','two','three')]
        self.assertEqual(len(self.processes),0)
        threads,errors=self.run_reads(clients)
        try:
            self.wait_active(2)
            self.assertEqual(len(self.processes),2)
            self.assertEqual(local_ai.ai_worker_stats(),{'configured':2,'live':2,'busy':2})
        finally:self.gate.set()
        for thread in threads:thread.join(timeout=5)
        self.assertEqual(errors,[]);self.assertEqual(len(self.processes),2)
        self.assertTrue(all(process.maximum==1 for process in self.processes))

    def test_memory_warning_caps_admission_and_retires_idle_second_copy(self):
        os.environ['STATEMENT_AI_INSTANCES']='2';self.gate=threading.Event()
        clients=[self.client(name) for name in ('one','two')]
        threads,errors=self.run_reads(clients)
        try:self.wait_active(2)
        finally:self.gate.set()
        for thread in threads:thread.join(timeout=5)
        self.assertEqual(errors,[]);self.assertFalse(self.processes[1].dead)
        self.pressure_mock.return_value=2
        clients[0].read(self.image,[],2)
        self.assertTrue(self.processes[1].dead)
        self.assertEqual(local_ai.ai_worker_stats()['live'],1)
        self.gate=threading.Event();self.processes[0].gate=self.gate
        for pressure in (2,4):
            self.pressure_mock.return_value=pressure
            threads,errors=self.run_reads([self.client('warning'+str(pressure)),self.client('waiting'+str(pressure))])
            try:
                self.wait_active(1);self.assertEqual(len(self.processes),2)
            finally:self.gate.set()
            for thread in threads:thread.join(timeout=5)
            self.assertEqual(errors,[])
            self.gate=threading.Event();self.processes[0].gate=self.gate


if __name__=='__main__':unittest.main()
