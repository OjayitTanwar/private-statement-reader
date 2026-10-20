import copy
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from source_repair import repair_cached_rows


def word(text,x,y,width=60):return {'text':text,'x':x,'y':y,'w':width,'h':20,'conf':.96}


def evidence(debit='100.00',credit=None,balance='900.00',top=100):
    words=[word('Date',30,30),word('Particulars',200,30),word('Withdrawal',620,30),
           word('Deposit',750,30),word('Balance',880,30),word('01/04/2025',30,top,100),
           word('TRANSFER',200,top,100)]
    for text,right in ((debit,650),(credit,780),(balance,910)):
        if text:words.append(word(text,right-60,top))
    return words


def old_row(**changes):
    row={'id':'original-row','date':'2025-04-01','page':1,'ord':0,'_top':100,'_bottom':120,
         'debit':100,'credit':900,'balance':None,'confidence':.9,'state':'review',
         'original_description':'Original narrative','normalized_description':'Changed narrative',
         'counterparty':'PERSON','suspicious':True,'reason':'credit: filled by local AI estimate',
         '_issues':['credit: filled by local AI estimate'],'_inferred_fields':{'credit':{'method':'local AI estimate'}},
         '_numeric_candidates':{'credit':[{'value':900,'source':'ai'}]},'_alternatives':[]}
    row.update(changes);return row


class CachedSourceRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        Image.new('RGB',(1000,250),'white').save(self.folder/'page-1.jpg')
    def tearDown(self):self.temp.cleanup()
    def save(self,engine,words):
        (self.folder/f'ocr-1-{engine}.json').write_text(json.dumps(words))

    def test_two_source_passes_repair_columns_without_changing_identity_or_inputs(self):
        self.save('tesseract',evidence());self.save('apple',evidence())
        source=[old_row()];before=copy.deepcopy(source)
        result,report=repair_cached_rows(source,self.folder,opening=1000)
        self.assertEqual(source,before)
        self.assertEqual((result[0]['debit'],result[0]['credit'],result[0]['balance']),(100,None,900))
        self.assertEqual(report['applied_rows'],1);self.assertFalse(report['rollback'])
        for key in ('id','date','original_description','normalized_description','counterparty'):
            self.assertEqual(result[0][key],before[0][key])
        self.assertNotIn('credit',result[0]['_inferred_fields'])
        self.assertEqual(result[0]['_issues'],[])
        audit=result[0]['_source_repairs'][0]
        self.assertEqual(audit['before']['credit'],900)
        self.assertIn('credit',audit['original_inferred_fields'])
        self.assertEqual(len(audit['support']),2)
        self.assertTrue(all(c['source']=='ocr' for candidates in result[0]['_numeric_candidates'].values() for c in candidates))
        self.assertEqual(result[0]['_field_sources'],{'debit':'ocr','credit':'ocr','balance':'ocr'})
        self.assertEqual(len(result[0]['_alternatives']),2)
        self.assertTrue(all(item['source']=='ocr' for item in result[0]['_alternatives']))

    def test_single_pass_and_disagreeing_passes_cannot_repair(self):
        self.save('tesseract',evidence())
        original=[old_row()]
        result,report=repair_cached_rows(original,self.folder,opening=1000)
        self.assertEqual(result,original);self.assertEqual(report['applied_rows'],0)
        self.save('apple',evidence(balance='800.00'))
        result,report=repair_cached_rows(original,self.folder,opening=1000)
        self.assertEqual(result,original);self.assertEqual(report['applied_rows'],0)

    def test_protected_manual_and_no_geometry_rows_stay_unchanged(self):
        self.save('tesseract',evidence());self.save('apple',evidence())
        for change,protected in (({'state':'approved'},()),({'state':'corrected'},()),
                                 ({'_manual_fields':['balance']},()),({},('original-row',)),
                                 ({'_top':None},())):
            with self.subTest(change=change):
                original=[old_row(**change)]
                result,report=repair_cached_rows(original,self.folder,opening=1000,protected_ids=protected)
                self.assertEqual(result,original)
                self.assertEqual(report['applied_rows'],0)

    def test_wrong_date_or_distant_geometry_never_matches(self):
        self.save('tesseract',evidence());self.save('apple',evidence())
        for change in ({'date':'2025-04-02'},{'_top':150,'_bottom':170}):
            original=[old_row(**change)]
            result,report=repair_cached_rows(original,self.folder,opening=1000)
            self.assertEqual(result,original);self.assertEqual(report['applied_rows'],0)

    def test_repair_that_increases_balance_mismatches_rolls_back_all_rows(self):
        self.save('tesseract',evidence(balance='800.00'));self.save('apple',evidence(balance='800.00'))
        original=[old_row(debit=100,credit=None,balance=900,_issues=[],_inferred_fields={})]
        result,report=repair_cached_rows(original,self.folder,opening=1000)
        self.assertEqual(result,original)
        self.assertTrue(report['rollback']);self.assertEqual(report['candidate_changes'],1)
        self.assertEqual(report['applied_rows'],0)

    def test_detected_columns_fall_back_to_supplied_columns_without_headers(self):
        words=[item for item in evidence() if item['y']!=30]
        self.save('tesseract',words);self.save('apple',words)
        result,report=repair_cached_rows([old_row()],self.folder,
            columns_by_page={1:{'date':.03,'description':.2,'debit':.65,'credit':.78,'balance':.91}},opening=1000)
        self.assertEqual(report['applied_rows'],1)
        self.assertEqual(result[0]['balance'],900)

    def test_equally_near_rows_with_same_date_are_not_forced_into_a_match(self):
        words=[item for item in evidence() if item['y']==30]
        for top,balance in ((95,'900.00'),(105,'800.00')):
            body=[item for item in evidence(balance=balance,top=top) if item['y']!=30]
            for item in body:item['h']=10
            words.extend(body)
        self.save('tesseract',words);self.save('apple',words)
        original=[old_row()]
        result,report=repair_cached_rows(original,self.folder,opening=1000)
        self.assertEqual(result,original)
        self.assertEqual(report['applied_rows'],0)

    def test_two_tesseract_variants_are_not_independent_engine_families(self):
        self.save('tesseract',evidence());self.save('tesseract-enhanced',evidence())
        original=[old_row()]
        result,report=repair_cached_rows(original,self.folder,opening=1000)
        self.assertEqual(result,original)
        self.assertEqual(report['candidate_changes'],0)

    def test_bad_candidate_does_not_block_safe_repair_on_same_page(self):
        words=evidence()
        second=[item for item in evidence(balance='700.00',top=160) if item['y']!=30]
        next(item for item in second if item['text']=='01/04/2025')['text']='02/04/2025'
        words.extend(second)
        self.save('tesseract',words);self.save('apple',words)
        original=[old_row(),old_row(id='second',date='2025-04-02',_top=160,_bottom=180,
                                   debit=100,credit=None,balance=800,_issues=[],reason='',_inferred_fields={})]
        result,report=repair_cached_rows(original,self.folder,opening=1000)
        self.assertEqual((result[0]['credit'],result[0]['balance']),(None,900))
        self.assertEqual(result[1],original[1])
        self.assertEqual(report['candidate_changes'],2)
        self.assertEqual(report['applied_rows'],1)
        self.assertEqual(report['rejected_changes'],1)
        self.assertTrue(report['partial_repair']);self.assertFalse(report['rollback'])
        self.assertEqual(len(result),len(original))


if __name__=='__main__':unittest.main()
