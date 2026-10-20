import copy
import json
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch

import app


class RepairLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.paths=patch.multiple(app,DATA=self.root,DOCS=self.root/'documents',DB=self.root/'statements.sqlite3',JOBS=queue.Queue(),SOURCE_REPAIR_BACKUP=None)
        self.paths.start();app.init_db();self.folder=app.DOCS/'example';self.folder.mkdir()
        (self.folder/'source.pdf').write_bytes(b'Synthetic source, never replaced')
        with app.connection() as c:c.execute("INSERT INTO bank_documents(id,name,status,config,report) VALUES('example','Synthetic.pdf','ready','{}','{}')")
        self.row=dict(id='row1',page=1,ord=0,state='review',date='2025-04-01',debit=100,credit=900,balance=None,
                      original_description='SYNTHETIC',normalized_description='SYNTHETIC',confidence=.9,_issues=[])
        app.save_rows('example',[self.row])
    def tearDown(self):self.paths.stop();self.tmp.cleanup()

    def test_backup_and_checkpoint_precede_repeated_version_skip(self):
        checkpoint={'source_signature':app.source_signature(self.folder),'next_page':2,'rows':[self.row]}
        (self.folder/'checkpoint.json').write_text(json.dumps(checkpoint))
        repaired=copy.deepcopy(self.row);repaired.update(credit=None,balance=900,_source_repairs=[{'before':{'credit':900}}])
        with patch('source_repair.repair_cached_rows',return_value=([repaired],{'applied_rows':1})) as repair:
            updated=app.apply_cached_source_repairs('example')
            self.assertEqual(updated[0]['balance'],900)
            self.assertTrue(Path(app.SOURCE_REPAIR_BACKUP).is_file())
            self.assertEqual(json.loads((self.folder/f'before-{app.SOURCE_REPAIR_REVISION}.json').read_text())['rows'][0]['credit'],900)
            self.assertEqual(json.loads((self.folder/'checkpoint.json').read_text())['rows'][0]['balance'],900)
            app.apply_cached_source_repairs('example');self.assertEqual(repair.call_count,1)
        self.assertEqual((self.folder/'source.pdf').read_bytes(),b'Synthetic source, never replaced')
        self.assertEqual(app.rows_for('example')[0]['id'],'row1')

    def test_manual_history_is_protected_and_no_change_does_not_write_backup(self):
        with app.connection() as c:c.execute("INSERT INTO audit(document_id,row_id) VALUES('example','row1')")
        with patch('source_repair.repair_cached_rows',return_value=([self.row],{'applied_rows':0})) as repair:
            app.apply_cached_source_repairs('example')
            self.assertIn('row1',repair.call_args.kwargs['protected_ids'])
        self.assertIsNone(app.SOURCE_REPAIR_BACKUP)
        self.assertEqual(app.rows_for('example')[0],self.row)
