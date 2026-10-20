"""Final synthetic regressions for preserved edits and independent source anchors."""
import copy,json,queue,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import app
from bank_engine import reconcile
from local_ai import _observed,_observed_transaction,infer_missing_amounts,infer_two_sided_balances
from source_repair import repair_cached_rows


def row(identity='one',**changes):
    value={'id':identity,'date':'2025-04-01','page':1,'ord':0,'_top':100,'_bottom':120,
           'original_description':'SYNTHETIC TRANSFER','normalized_description':'SYNTHETIC TRANSFER',
           'debit':100,'credit':None,'balance':900,'confidence':.95,'state':'review',
           '_engine':'Tesseract','_issues':[],'_numeric_candidates':{},'reason':'','suspicious':False}
    value.update(changes);return value


class FinalSourceSafetyTests(unittest.TestCase):
    def test_manual_edits_survive_both_estimate_paths(self):
        for marker in ({'state':'approved'},{'state':'corrected'},{'_manual_fields':['balance']},
                       {'_edited_fields':['debit']},{'_manual':True},{'_engine':'Manual transcription'}):
            with self.subTest(marker=marker):
                missing=row(debit=None,**marker);before=copy.deepcopy(missing)
                infer_missing_amounts([missing],1000)
                self.assertEqual(missing,before)
                corrected=row(balance=850,**marker);before=copy.deepcopy(corrected)
                neighbor=row('two',debit=None,credit=200,balance=1100)
                infer_two_sided_balances([corrected,neighbor],1000)
                self.assertEqual(corrected,before)

    def test_balance_derived_direction_has_provenance_and_is_not_observed(self):
        source=row(debit=None,_amount=100,_direction='unknown')
        reconcile([source],1000,infer=True)
        self.assertEqual(source['debit'],100)
        self.assertEqual(source['_direction'],'balance-supported')
        self.assertEqual(source['_field_sources']['debit'],'inferred')
        self.assertEqual(source['_inferred_fields']['debit']['method'],'balance-derived direction')
        self.assertIsNone(source['_inferred_fields']['debit']['original_value'])
        self.assertIsNone(_observed(source,'debit'))
        self.assertIsNone(_observed_transaction(source))
        # Compatibility guard also protects a saved row missing newer metadata.
        source.pop('_inferred_fields');source.pop('_field_sources')
        self.assertIsNone(_observed_transaction(source))

    def test_derived_direction_cannot_support_two_sided_balance_correction(self):
        first=row(balance=850)
        derived=row('two',debit=None,credit=None,balance=1100,_amount=200,_direction='unknown')
        reconcile([derived],900,infer=True)
        self.assertEqual(derived['credit'],200)
        infer_two_sided_balances([first,derived],1000)
        self.assertEqual(first['balance'],850)
        self.assertNotIn('balance',first.get('_inferred_fields',{}))

    def test_reconcile_does_not_assign_direction_to_manual_rows(self):
        for marker in ({'state':'approved'},{'state':'corrected'},{'_manual_fields':['credit']}):
            with self.subTest(marker=marker):
                source=row(debit=None,_amount=100,**marker)
                reconcile([source],1000,infer=True)
                self.assertIsNone(source['debit']);self.assertIsNone(source['credit'])
                self.assertNotIn('_inferred_fields',source)

    def test_grid_geometry_is_not_matched_against_raw_page_coordinates(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);Image.new('RGB',(1000,250),'white').save(folder/'page-1.jpg')
            def word(text,x,y,w=60):return {'text':text,'x':x,'y':y,'w':w,'h':20,'conf':.96}
            words=[word('Date',30,30),word('Particulars',200,30),word('Withdrawal',620,30),
                   word('Deposit',750,30),word('Balance',880,30),word('01/04/2025',30,100,100),
                   word('SYNTHETIC TRANSFER',200,100,180),word('100.00',590,100),word('900.00',850,100)]
            for engine in ('tesseract','apple'):(folder/f'ocr-1-{engine}.json').write_text(json.dumps(words))
            raw=row(balance=800)
            repaired,report=repair_cached_rows([raw],folder,opening=1000)
            self.assertEqual(report['applied_rows'],1)
            self.assertEqual(repaired[0]['balance'],900)
            grid=row(balance=800,_cell_evidence={'balance':[{'text':'800.00','engine':'grid cell'}]})
            original=copy.deepcopy(grid)
            repaired,report=repair_cached_rows([grid],folder,opening=1000)
            self.assertEqual(report['applied_rows'],0)
            self.assertEqual(repaired,[original])


class FinalMigrationSafetyTests(unittest.TestCase):
    def test_later_resumed_pages_are_not_skipped_by_repair_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with patch.multiple(app,DATA=root,DOCS=root/'documents',DB=root/'statements.sqlite3',JOBS=queue.Queue(),SOURCE_REPAIR_BACKUP=None):
                app.init_db();folder=app.DOCS/'synthetic';folder.mkdir()
                (folder/'source.pdf').write_bytes(b'%PDF- synthetic test source')
                with app.connection() as connection:
                    connection.execute('INSERT INTO bank_documents(id,name,status,created,config,report) VALUES(?,?,?,?,?,?)',
                                       ('synthetic','Synthetic.pdf','processing','2025-04-01','{}','{"pages":[]}'))
                first=[row()];later=first+[row('two',page=2,ord=1)]
                calls=[]
                def repair(rows,*args,**kwargs):
                    calls.append([item['page'] for item in rows])
                    return copy.deepcopy(rows),{'applied_rows':0,'candidate_changes':0,'rollback':False}
                with patch('source_repair.repair_cached_rows',side_effect=repair):
                    app.apply_cached_source_repairs('synthetic',first)
                    app.apply_cached_source_repairs('synthetic',first)
                    app.apply_cached_source_repairs('synthetic',later)
                self.assertEqual(calls,[[1],[1,2]])
                self.assertEqual(app.document('synthetic')['config']['source_repair_pages'],2)


if __name__=='__main__':unittest.main()
