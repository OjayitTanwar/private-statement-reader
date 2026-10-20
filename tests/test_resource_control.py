import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import app
import local_ai
from resource_control import ResourceBudget,descendant_rss,gpu_memory,GIB


class ResourceTests(unittest.TestCase):
    def setUp(self):self.budget=ResourceBudget({'memory_gb':16,'cpu_cores':10})
    def custom(self,**values):return {'preset':'custom','documents':4,'cpu_threads':8,'memory_gb':12,'ai_instances':2,**values}

    def test_descendant_memory_excludes_other_apps_and_follows_all_generations(self):
        text='10 1 100\n12 11 30\n11 10 20\n99 1 9000\nmalformed\n'
        self.assertEqual(descendant_rss(text,10),150*1024)

    def test_presets_respect_hardware_and_leave_system_headroom(self):
        for name in ('light','balanced','maximum'):
            settings=self.budget.validate({'preset':name})
            self.assertLessEqual(settings['memory_gb'],14)
            self.assertLessEqual(settings['documents'],8)
            self.assertLessEqual(settings['cpu_threads'],10)
        self.assertEqual(self.budget.preset('light')['documents'],1)

    def test_custom_limits_reject_invalid_or_unsafe_values(self):
        for override in ({'documents':0},{'documents':True},{'memory_gb':15},{'memory_gb':float('nan')},
                         {'cpu_threads':11},{'ai_instances':3},{'memory_gb':8},{'unknown':1}):
            with self.subTest(override=override),self.assertRaises(ValueError):self.budget.configure(self.custom(**override))
        with self.assertRaises(ValueError):self.budget.configure([])
        with self.assertRaises(ValueError):self.budget.configure({'preset':'bogus'})

    def test_cpu_and_memory_reduce_requested_concurrency(self):
        self.budget.configure(self.custom(cpu_threads=2,ai_instances=1))
        self.assertEqual(self.budget.limits()['document_workers'],1)
        self.budget.configure(self.custom(memory_gb=4,ai_instances=1,cpu_threads=10))
        self.assertEqual(self.budget.limits()['document_workers'],1)

    def test_memory_gate_reclaims_ai_before_admitting_more_work(self):
        self.budget.configure(self.custom())
        self.budget.used_bytes=11*GIB
        self.assertTrue(self.budget.limits()['memory_blocked'])
        self.assertEqual(self.budget.ai_limit(),0)
        self.budget.used_bytes=7*GIB
        self.assertEqual(self.budget.ai_limit(),2)

    def test_live_reduction_does_not_kill_page_but_waits_for_safe_release(self):
        self.budget.configure(self.custom(ai_instances=1))
        self.budget.enter_page('first');self.budget.enter_page('second')
        self.budget.configure({'preset':'light'})
        entered=threading.Event()
        waiter=threading.Thread(target=lambda:(self.budget.enter_page('third'),entered.set()))
        waiter.start();self.assertFalse(entered.wait(.04))
        self.budget.leave_page('first');self.assertFalse(entered.wait(.04))
        self.budget.leave_page('second');self.assertTrue(entered.wait(1));waiter.join()
        self.budget.leave_page('third')

    def test_memory_gate_unblocks_when_usage_falls(self):
        self.budget.used_bytes=12*GIB;entered=threading.Event();notified=[]
        waiter=threading.Thread(target=lambda:(self.budget.enter_page('first',lambda:notified.append(True)),entered.set()))
        waiter.start();self.assertFalse(entered.wait(.04))
        with self.budget.condition:self.budget.used_bytes=GIB;self.budget.condition.notify_all()
        self.assertTrue(entered.wait(1));waiter.join();self.assertEqual(notified,[True]);self.budget.leave_page('first')

    def test_failed_sampling_discards_stale_blocking_reading(self):
        self.budget.used_bytes=15*GIB
        with patch('resource_control.subprocess.run',side_effect=OSError):self.budget.sample()
        self.assertIsNone(self.budget.used_bytes);self.assertTrue(self.budget.snapshot()['memory_sample_error'])
        self.assertFalse(self.budget.limits()['memory_blocked'])

    def test_gpu_reports_only_accept_fresh_descendant_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for pid,stamp,value in ((11,99,2*GIB),(12,80,7*GIB),(99,99,8*GIB)):
                (root/f'ai-memory-{pid}.json').write_text(json.dumps({'pid':pid,'time':stamp,'metal_bytes':value}))
            self.assertEqual(gpu_memory(root,{10,11,12},now=100),(2*GIB,1))
            (root/'ai-memory-11.json').write_text('{bad json')
            self.assertEqual(gpu_memory(root,{10,11,12},now=100),(0,0))

    def test_process_sample_adds_local_ai_allocations_conservatively(self):
        completed=type('Result',(),{'stdout':f'{app.os.getpid()} 1 1024\n'})()
        with patch('resource_control.subprocess.run',return_value=completed),patch('resource_control.gpu_memory',return_value=(2*GIB,1)):
            self.budget.sample()
        snapshot=self.budget.snapshot()
        self.assertEqual(self.budget.used_bytes,2*GIB+1024**2)
        self.assertEqual(snapshot['gpu_reporters'],1)
        self.assertIn('shared pages may count twice',snapshot['memory_scope'])

    def test_setting_changes_are_persisted_and_return_effective_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.multiple(app,DB=root/'test.sqlite3',RESOURCE_BUDGET=self.budget,CUSTOM_RESOURCES=False),patch.object(app,'capacity_limit',return_value=2):
                with app.connection() as db:db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
                response=app.resource_settings(self.custom())
                self.assertEqual(response['settings']['memory_gb'],12)
                with app.connection() as db:saved=json.loads(db.execute("SELECT value FROM settings WHERE key='resources'").fetchone()[0])
                self.assertEqual(saved,response['settings'])
                with self.assertRaises(ValueError):app.resource_settings(self.custom(documents=100))
                self.assertEqual(app.resource_settings()['settings'],saved)

    def test_idle_ai_instances_obey_live_budget_without_killing_busy_worker(self):
        key=('unused','model','')
        with patch.object(local_ai._WorkerPool,'_watch_pressure'):
            pool=local_ai._WorkerPool(key,2)
        with patch.object(local_ai,'_resource_ai_limit',return_value=1),patch.object(pool.workers[0],'_stop') as first,patch.object(pool.workers[1],'_stop') as second:
            pool._retire_idle_extra(1);first.assert_not_called();second.assert_called_once()
            pool.busy[1]=True;second.reset_mock();pool._retire_idle_extra(1);second.assert_not_called()
        with patch.object(local_ai,'_resource_ai_limit',return_value=0),patch.object(pool.workers[0],'_stop') as first:
            pool._retire_idle_extra(1);first.assert_called_once()
        pool.busy[1]=False;pool.shutdown()


if __name__=='__main__':unittest.main()
