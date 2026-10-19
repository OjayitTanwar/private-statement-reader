import csv
import io
import json
import queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class DurableWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.patch=patch.multiple(app,DATA=self.root,DOCS=self.root/'documents',
                                  DB=self.root/'statements.sqlite3',JOBS=queue.Queue(),
                                  ACTIVE_JOBS=set(),SLEEP_GUARD=None)
        self.patch.start()
        self.no_external=patch.object(app.shutil,'which',return_value=None)
        self.no_external.start();app.init_db()

    def tearDown(self):
        self.no_external.stop();self.patch.stop();self.temp.cleanup()

    def insert_document(self,identifier,state='ready',created='2025-01-01',**changes):
        record=dict(name=identifier+'.pdf',status=state,created=created,
                    config={},report={'warnings':[]})
        record.update(changes)
        with app.connection() as db:
            db.execute('INSERT INTO bank_documents(id,name,status,created,config,report) VALUES(?,?,?,?,?,?)',
                       (identifier,record['name'],record['status'],record['created'],
                        json.dumps(record['config']),json.dumps(record['report'])))
        folder=app.DOCS/identifier;folder.mkdir()
        (folder/'source.pdf').write_bytes(b'Untouched source')
        return folder

    def row(self,identifier,state='pending'):
        return dict(id=identifier,page=1,ord=0,date='2025-01-01',original_description='TRANSFER',
                    normalized_description='TRANSFER',debit=100.01,credit=0,balance=899.99,
                    state=state,suspicious=False,reason='',confidence=.9)

    def test_restart_schedules_incomplete_documents_in_creation_order_and_keeps_evidence(self):
        self.insert_document('later','queued','2025-03-01')
        earlier=self.insert_document('earlier','processing','2025-01-01')
        self.insert_document('middle','interrupted','2025-02-01')
        self.insert_document('done','ready')
        self.insert_document('failed','error')
        app.save_rows('earlier',[self.row('saved-row')])
        (earlier/'checkpoint.json').write_text('{"next_page":2}')
        app.init_db()
        scheduled=[]
        while not app.JOBS.empty():scheduled.append(app.JOBS.get_nowait())
        self.assertEqual(scheduled,['earlier','middle','later'])
        self.assertEqual(app.document('earlier')['status'],'queued')
        self.assertEqual(app.document('done')['status'],'ready')
        self.assertEqual(app.document('failed')['status'],'error')
        self.assertEqual(app.rows_for('earlier')[0]['id'],'saved-row')
        self.assertEqual((earlier/'source.pdf').read_bytes(),b'Untouched source')
        self.assertEqual(json.loads((earlier/'checkpoint.json').read_text()),{'next_page':2})

    def test_finish_outputs_produces_active_rows_and_combined_completed_statements(self):
        first=self.insert_document('first')
        self.insert_document('second',created='2025-02-01')
        self.insert_document('unfinished','processing')
        app.save_rows('first',[self.row('active'),self.row('excluded','deleted')])
        app.save_rows('second',[self.row('second-row')])
        app.save_rows('unfinished',[self.row('must-not-export')])
        app.finish_outputs('first')
        app.finish_outputs('second')
        doc=app.document('first')
        folder=Path(doc['report']['output_folder'])
        exported=list(csv.DictReader(io.StringIO((folder/'transactions.csv').read_text(encoding='utf-8-sig'))))
        self.assertEqual(len(exported),1)
        self.assertEqual(exported[0]['balance'],'899.99')
        self.assertEqual(doc['report']['automatic']['rows'],1)
        combined=list(csv.DictReader(io.StringIO((self.root/'exports'/'All statements.csv').read_text(encoding='utf-8-sig'))))
        self.assertEqual([row['document_id'] for row in combined],['first','second'])
        self.assertTrue((self.root/'exports'/'All statements.xlsx').is_file())
        self.assertEqual((first/'source.pdf').read_bytes(),b'Untouched source')

    def test_checkpoint_resume_accepts_same_source_and_rejects_changed_source(self):
        folder=self.insert_document('checkpoint','queued')
        state={'source_signature':app.source_signature(folder),'next_page':4,
               'rows':[self.row('saved')],'report':{'pages':[]},'config':{},
               'columns':{'balance':.9},'recognized':True}
        (folder/'checkpoint.json').write_text(json.dumps(state))
        self.assertEqual(app.resume_state(app.document('checkpoint'),folder),state)
        (folder/'source.pdf').write_bytes(b'Different PDF source bytes')
        self.assertIsNone(app.resume_state(app.document('checkpoint'),folder))
        (folder/'checkpoint.json').write_text('Partial corrupt checkpoint')
        self.assertIsNone(app.resume_state(app.document('checkpoint'),folder))

    def test_old_version_resume_reuses_only_complete_consecutive_pages(self):
        folder=self.insert_document('legacy','queued',report={'pages':[
            {'page':1,'columns':{'balance':.8}},{'page':2,'columns':{'balance':.9}}]})
        saved=self.row('page1');later=self.row('page3');later['page']=3
        app.save_rows('legacy',[saved,later]);app.status('legacy',progress=2)
        state=app.resume_state(app.document('legacy'),folder)
        self.assertEqual(state['next_page'],3)
        self.assertEqual([row['id'] for row in state['rows']],['page1'])
        self.assertEqual(state['columns'],{'balance':.9})
        app.status('legacy',progress=3)
        self.assertIsNone(app.resume_state(app.document('legacy'),folder))

    def test_failed_document_retries_twice_then_batch_continues(self):
        self.insert_document('fails','queued')
        self.insert_document('succeeds','queued')
        calls=[]
        class FiniteQueue(queue.Queue):
            def get(self,*args,**kwargs):
                try:return self.get_nowait()
                except queue.Empty:raise SystemExit
            def get_nowait(self):return queue.Queue.get(self,block=False)
        jobs=FiniteQueue();jobs.put('fails');jobs.put('succeeds')
        def fake_process(identifier):
            calls.append(identifier)
            if identifier=='fails':
                app.status(identifier,status='error',error='Temporary OCR failure')
            else:app.status(identifier,status='ready')
        with patch.object(app,'JOBS',jobs),patch.object(app,'process',side_effect=fake_process):
            with self.assertRaises(SystemExit):app.worker()
        self.assertEqual(calls,['fails','succeeds','fails'])
        self.assertEqual(jobs.unfinished_tasks,0)
        self.assertEqual(app.document('fails')['config']['automatic_attempts'],2)
        self.assertEqual(app.document('fails')['status'],'error')
        self.assertEqual(app.document('succeeds')['status'],'ready')
        self.assertTrue((self.root/'exports'/'Incomplete--fails'/'extraction-report.json').exists())
        self.assertEqual((app.DOCS/'fails'/'source.pdf').read_bytes(),b'Untouched source')
        self.assertFalse(app.ACTIVE_JOBS)

    def test_two_documents_actually_process_concurrently(self):
        self.insert_document('parallel1','queued');self.insert_document('parallel2','queued')
        barrier=threading.Barrier(2,timeout=5);observed=[];errors=[]
        def fake_process(identifier):
            barrier.wait()
            observed.append(len(app.ACTIVE_JOBS))
            barrier.wait()
            app.status(identifier,status='ready')
        def invoke(identifier):
            try:app.run_job(identifier)
            except BaseException as error:errors.append(error)
        with patch.object(app,'process',side_effect=fake_process):
            threads=[threading.Thread(target=invoke,args=(identifier,)) for identifier in ('parallel1','parallel2')]
            for thread in threads:thread.start()
            for thread in threads:thread.join(timeout=8)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertFalse(errors)
        self.assertEqual(observed,[2,2])
        self.assertFalse(app.ACTIVE_JOBS)

    def test_resumed_checkpoint_replaces_stale_database_rows_in_all_exports(self):
        folder=self.insert_document('stale','queued')
        valid=self.row('valid-checkpoint-row');stale=self.row('partial-uncommitted-row')
        app.save_rows('stale',[valid,stale])
        report={'pages':[{'page':1,'rows':1}],'warnings':[],'engines':[],
                'searchable':True,'statement_detected':True}
        state={'source_signature':app.source_signature(folder),'next_page':2,
               'rows':[valid],'report':report,'config':{},'columns':{},
               'bank_evidence':'Bank Statement','recognized':True}
        (folder/'checkpoint.json').write_text(json.dumps(state))
        (folder/'searchable.pdf').write_bytes(b'Searchable PDF placeholder')
        with patch.object(app,'dependencies',return_value={'ready':True}),\
             patch.object(app,'run',return_value=SimpleNamespace(stdout='Pages: 1\n')):
            app.process('stale')
        self.assertEqual(app.document('stale')['status'],'ready')
        self.assertEqual([row['id'] for row in app.rows_for('stale')],['valid-checkpoint-row'])
        combined=list(csv.DictReader(io.StringIO((self.root/'exports'/'All statements.csv').read_text(encoding='utf-8-sig'))))
        self.assertEqual(len(combined),1)

    def test_unexpected_processing_exception_enters_the_same_bounded_retry_path(self):
        self.insert_document('unexpected','queued')
        with patch.object(app,'process',side_effect=RuntimeError('Unexpected processing exception')):
            app.run_job('unexpected')
        self.assertEqual(app.document('unexpected')['status'],'queued')
        self.assertEqual(app.document('unexpected')['config']['automatic_attempts'],1)
        self.assertEqual(app.JOBS.get_nowait(),'unexpected')
        self.assertFalse(app.ACTIVE_JOBS)

    def test_startup_skips_pending_identical_pdf_but_preserves_sources_and_history(self):
        original=self.insert_document('original','ready',created='2025-03-01')
        duplicate=self.insert_document('duplicate','queued',created='2025-01-01')
        different=self.insert_document('different','queued',created='2025-02-01')
        finished_copy=self.insert_document('finished-copy','ready',created='2025-04-01')
        pdf=b'%PDF-1.7\nIdentical complete statement evidence'
        for folder in (original,duplicate,finished_copy):(folder/'source.pdf').write_bytes(pdf)
        (different/'source.pdf').write_bytes(b'%PDF-1.7\nDifferent statement evidence')
        app.save_rows('original',[self.row('original-row')])
        app.save_rows('finished-copy',[self.row('finished-copy-row')])
        with app.connection() as db:
            db.execute('INSERT INTO audit(document_id,row_id,time,before_json,after_json) VALUES(?,?,?,?,?)',
                       ('original','original-row','2025-03-01','{}','{"review":"kept"}'))
        app.init_db()
        self.assertEqual(app.JOBS.get_nowait(),'different')
        self.assertTrue(app.JOBS.empty())
        self.assertEqual(app.document('duplicate')['status'],'duplicate')
        self.assertEqual(app.document('duplicate')['config']['duplicate_of'],'original')
        self.assertEqual(app.document('original')['status'],'ready')
        self.assertEqual(app.document('finished-copy')['status'],'ready')
        self.assertEqual(len(app.document('original')['config']['source_sha256']),64)
        self.assertEqual((duplicate/'source.pdf').read_bytes(),pdf)
        self.assertEqual((original/'source.pdf').read_bytes(),pdf)
        self.assertEqual([row['id'] for row in app.rows_for('original')],['original-row'])
        self.assertEqual([row['id'] for row in app.rows_for('finished-copy')],['finished-copy-row'])
        with app.connection() as db:
            audit=db.execute('SELECT after_json FROM audit WHERE document_id=?',('original',)).fetchall()
        self.assertEqual([json.loads(row[0]) for row in audit],[{'review':'kept'}])

    def test_sixteen_gb_ten_core_mac_schedules_four_documents(self):
        with patch.object(app.os,'cpu_count',return_value=10),\
             patch.object(app.subprocess,'check_output',return_value=str(16*1024**3)):
            plan=app.resource_plan()
        self.assertEqual(plan['document_workers'],4)
        self.assertEqual(plan['ocr_jobs'],2)
        self.assertEqual(plan['memory_gb'],16)

    def test_memory_pressure_limits_only_new_document_slots(self):
        with patch.object(app,'RESOURCES',{'document_workers':4}):
            for pressure,expected in ((1,4),(2,2),(4,1)):
                with self.subTest(pressure=pressure),\
                     patch.object(app.subprocess,'check_output',return_value=str(pressure)):
                    self.assertEqual(app.capacity_limit(),expected)
        with patch.object(app,'RESOURCES',{'document_workers':2}),\
             patch.object(app.subprocess,'check_output',return_value='1'):
            self.assertEqual(app.capacity_limit(),2)

    def test_four_documents_run_together_and_fifth_waits_for_a_slot(self):
        identifiers=[f'parallel-{index}' for index in range(5)]
        for identifier in identifiers:self.insert_document(identifier,'queued')
        all_four=threading.Barrier(5,timeout=5)
        release=threading.Event();fifth_checked=threading.Event();fifth_started=threading.Event()
        observed=[];errors=[]
        def capacity():
            if threading.current_thread().name=='fifth-document':fifth_checked.set()
            return 4
        def fake_process(identifier):
            if identifier==identifiers[-1]:fifth_started.set()
            else:
                observed.append(len(app.ACTIVE_JOBS))
                all_four.wait()
                if not release.wait(timeout=5):raise RuntimeError('Test release timed out')
            app.status(identifier,status='ready')
        def invoke(identifier):
            try:app.run_job(identifier)
            except BaseException as error:errors.append(error)
        with patch.object(app,'process',side_effect=fake_process),\
             patch.object(app,'capacity_limit',side_effect=capacity):
            threads=[threading.Thread(target=invoke,args=(identifier,)) for identifier in identifiers[:4]]
            fifth=threading.Thread(target=invoke,args=(identifiers[-1],),name='fifth-document')
            try:
                for thread in threads:thread.start()
                all_four.wait()
                self.assertEqual(len(app.ACTIVE_JOBS),4)
                fifth.start()
                self.assertTrue(fifth_checked.wait(timeout=3))
                self.assertFalse(fifth_started.is_set())
            finally:
                release.set()
                for thread in [*threads,fifth]:
                    if thread.ident is not None:thread.join(timeout=8)
        self.assertFalse(errors)
        self.assertTrue(fifth_started.is_set())
        self.assertFalse(any(thread.is_alive() for thread in [*threads,fifth]))
        self.assertFalse(app.ACTIVE_JOBS)


if __name__=='__main__':unittest.main()
