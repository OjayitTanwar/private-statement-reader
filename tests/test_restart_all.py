import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from restart_all import restart_library


class ClosingConnection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()


def connect(path):return sqlite3.connect(path,factory=ClosingConnection)


class RestartLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.data=Path(self.temp.name)
        (self.data/'documents').mkdir();self.db=self.data/'statements.sqlite3'
        with connect(self.db) as db:
            db.executescript('''CREATE TABLE bank_documents(id TEXT PRIMARY KEY,name TEXT,status TEXT,created TEXT,pages INTEGER,progress INTEGER,stage TEXT,error TEXT,config TEXT,report TEXT);
            CREATE TABLE bank_rows(id TEXT PRIMARY KEY,document_id TEXT,page INTEGER,ord INTEGER,payload TEXT);
            CREATE TABLE audit(id INTEGER PRIMARY KEY,document_id TEXT,row_id TEXT,time TEXT,before_json TEXT,after_json TEXT);''')
    def tearDown(self):self.temp.cleanup()
    def document(self,identity,status='ready',config=None):
        config=config or {'automatic_attempts':2,'opening':1000,'opening_source':'Automatically detected','source_repair_revision':1}
        with connect(self.db) as db:
            db.execute('INSERT INTO bank_documents VALUES(?,?,?,?,?,?,?,?,?,?)',(identity,identity+'.pdf',status,'2025-01-01',2,1,'Old stage','Old error',json.dumps(config),'{}'))
        folder=self.data/'documents'/identity;folder.mkdir()
        (folder/'source.pdf').write_bytes(b'%PDF-1.7\nSynthetic source only')
        (folder/'ocr-1-tesseract.json').write_text('[]');(folder/'checkpoint.json').write_text('{}')
        return folder
    def row(self,identity='r',**changes):
        result={'id':identity,'page':1,'ord':0,'date':'2025-04-01','debit':100,'credit':None,'balance':900,
                'original_description':'SYNTHETIC TRANSFER','normalized_description':'SYNTHETIC TRANSFER','state':'review','_issues':[]}
        result.update(changes);return result
    def save(self,document,row):
        with connect(self.db) as db:db.execute('INSERT INTO bank_rows VALUES(?,?,?,?,?)',(row['id'],document,row['page'],row['ord'],json.dumps(row)))
    def audit(self,document,before,after):
        with connect(self.db) as db:db.execute('INSERT INTO audit(document_id,row_id,time,before_json,after_json) VALUES(?,?,?,?,?)',(document,after['id'],'2025-04-01',json.dumps(before),json.dumps(after)))

    def test_restart_archives_every_generated_file_queues_all_states_and_preserves_source(self):
        folders={state:self.document(state,state) for state in ('ready','processing','error','unsupported')}
        duplicate=self.document('alias','duplicate')
        for state in folders:self.save(state,self.row(state+'-row'))
        exports=self.data/'exports';exports.mkdir();(exports/'All statements.csv').write_text('Old outputs')
        hashes={key:hashlib.sha256((folder/'source.pdf').read_bytes()).hexdigest() for key,folder in folders.items()}
        preview=restart_library(self.data)
        self.assertEqual(preview['documents'],4);self.assertEqual(preview['pages'],8)
        self.assertFalse((self.data/'backups').exists())
        report=restart_library(self.data,apply=True);backup=Path(report['backup'])
        self.assertEqual(report['queued'],4);self.assertFalse(exports.exists())
        self.assertEqual((backup/'exports'/'All statements.csv').read_text(),'Old outputs')
        with connect(backup/'statements.sqlite3') as db:self.assertEqual(db.execute('SELECT count(*) FROM bank_rows').fetchone()[0],4)
        with connect(self.db) as db:
            rows=db.execute("SELECT status,progress,config FROM bank_documents WHERE status<>'duplicate'").fetchall()
            self.assertEqual(db.execute('SELECT count(*) FROM bank_rows').fetchone()[0],0)
        for status,progress,raw in rows:
            self.assertEqual((status,progress),('queued',0));config=json.loads(raw)
            self.assertEqual(config['automatic_attempts'],0);self.assertIsNone(config['opening'])
            self.assertNotIn('source_repair_revision',config)
        for key,folder in folders.items():
            self.assertEqual(hashlib.sha256((folder/'source.pdf').read_bytes()).hexdigest(),hashes[key])
            self.assertFalse((folder/'checkpoint.json').exists())
            self.assertTrue((folder/'runs'/report['run_id']/'checkpoint.json').exists())
            self.assertTrue((folder/'reextract-state.json').exists())
            self.assertTrue((backup/(key+'.json')).exists())
        self.assertTrue((duplicate/'checkpoint.json').exists())
        self.assertEqual(backup.stat().st_mode&0o777,0o700)
        self.assertEqual((backup/'ready.json').stat().st_mode&0o777,0o600)

    def test_manual_edits_approvals_and_manual_added_rows_survive_repeat_restart(self):
        folder=self.document('manual',config={'opening':1000,'opening_source':'Reviewer entered','columns':{'balance':.9}})
        before=self.row();edited=self.row(debit=123,normalized_description='Corrected synthetic narration')
        self.save('manual',edited);self.audit('manual',before,edited)
        approved=self.row('approved',state='approved');self.save('manual',approved)
        added=self.row('added',_engine='Manual transcription');self.save('manual',added)
        auto=self.row('auto',_source_repairs=[{'method':'source consensus'}]);self.save('manual',auto)
        self.audit('manual',self.row('auto'),auto)
        empty=self.row('empty',state='deleted',_issues=['Automatically excluded: source explicitly states no transactions'])
        self.save('manual',empty);self.audit('manual',self.row('empty'),empty)
        first=restart_library(self.data,apply=True)
        state=json.loads((folder/'reextract-state.json').read_text())
        overlays={item['original_row']['id']:item for item in state['manual_overrides']}
        self.assertEqual(set(overlays),{'r','approved','added'})
        self.assertEqual(set(overlays['r']['fields']),{'debit','normalized_description'})
        self.assertEqual(overlays['r']['original_row']['debit'],123)
        second=restart_library(self.data,apply=True)
        again=json.loads((folder/'reextract-state.json').read_text())
        self.assertEqual(again['manual_overrides'],state['manual_overrides'])
        self.assertEqual(again['previous_rows'],state['previous_rows'])
        self.assertNotEqual(first['run_id'],second['run_id'])
        with connect(self.db) as db:
            config=json.loads(db.execute('SELECT config FROM bank_documents').fetchone()[0])
            self.assertEqual(config['opening'],1000);self.assertEqual(config['columns'],{'balance':.9})
            self.assertEqual(db.execute('SELECT count(*) FROM audit').fetchone()[0],3)

    def test_missing_source_refuses_before_backup_or_mutation(self):
        folder=self.document('missing');self.save('missing',self.row())
        (folder/'source.pdf').unlink()
        with self.assertRaises(ValueError):restart_library(self.data,apply=True)
        self.assertFalse((self.data/'backups').exists());self.assertTrue((folder/'checkpoint.json').exists())
        with connect(self.db) as db:self.assertEqual(db.execute('SELECT count(*) FROM bank_rows').fetchone()[0],1)

    def test_archive_failure_restores_files_and_does_not_clear_rows(self):
        folder=self.document('failure');self.save('failure',self.row())
        import restart_all
        original=restart_all.shutil.move;calls=[]
        def fail_once(source,destination):
            calls.append(source)
            if len(calls)==2:raise OSError('Synthetic archive failure')
            return original(source,destination)
        with patch.object(restart_all.shutil,'move',side_effect=fail_once):
            with self.assertRaises(RuntimeError):restart_library(self.data,apply=True)
        self.assertTrue((folder/'checkpoint.json').exists());self.assertTrue((folder/'ocr-1-tesseract.json').exists())
        self.assertFalse((folder/'reextract-state.json').exists())
        with connect(self.db) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM bank_rows').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT status FROM bank_documents').fetchone()[0],'ready')

    def test_manual_crop_reference_points_to_recoverable_archived_run(self):
        folder=self.document('crop',config={'source_repair_pages':[1],'opening':None})
        (folder/'crop-1-0.png').write_bytes(b'Synthetic crop placeholder')
        self.save('crop',self.row('approved',state='approved',crop='crop-1-0.png'))
        report=restart_library(self.data,apply=True)
        state=json.loads((folder/'reextract-state.json').read_text())
        archived=state['manual_overrides'][0]['original_row']['crop']
        self.assertEqual(archived,str(Path('runs')/report['run_id']/'crop-1-0.png'))
        self.assertEqual((folder/archived).read_bytes(),b'Synthetic crop placeholder')
        self.assertEqual(state['previous_rows'][0]['crop'],archived)
        with connect(self.db) as db:config=json.loads(db.execute('SELECT config FROM bank_documents').fetchone()[0])
        self.assertNotIn('source_repair_pages',config)


if __name__=='__main__':unittest.main()
