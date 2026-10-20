import copy
import unittest

from bank_engine import pass_score,select_observed_candidates
from table_grid import decode_balance_chain


def row(balance=850,debit=100,credit=None,**changes):
    result={'date':'2025-04-01','balance':balance,'debit':debit,'credit':credit,
            'confidence':.95,'_issues':[],'_engine':'Tesseract','_numeric_candidates':{}}
    result.update(changes);return result


class ObservedCandidateSafetyTests(unittest.TestCase):
    def test_future_printed_balance_disambiguates_observed_alternative(self):
        first=row(_numeric_candidates={'balance':[{'value':850,'engine':'Tesseract'},
                                                  {'value':900,'engine':'Apple Vision','source':'ocr'}]})
        second=row(1100,0,200,date='2025-04-02')
        decode_balance_chain([first,second],1000)
        self.assertEqual(first['balance'],900)
        self.assertEqual(first['_candidate_selections'][0]['before']['balance'],850)
        self.assertEqual(first['_candidate_selections'][0]['evidence']['engine'],'Apple Vision')

    def test_ai_only_exact_fit_is_never_observed_evidence(self):
        for marker in ({'source':'ai'},{'engine':'Local GLM-OCR AI'}):
            sample=row(_numeric_candidates={'balance':[{'value':900,**marker}]})
            before=copy.deepcopy(sample);decode_balance_chain([sample],1000)
            self.assertEqual(sample,before)
        sample=row(_alternatives=[{'debit':100,'credit':None,'balance':900,'source':'ai'}])
        before=copy.deepcopy(sample);select_observed_candidates([sample],1000)
        self.assertEqual(sample,before)

    def test_no_fit_and_two_exact_fits_leave_baseline(self):
        no_fit=row(_numeric_candidates={'balance':[{'value':875,'engine':'OCR'}]})
        before=copy.deepcopy(no_fit);decode_balance_chain([no_fit],1000)
        self.assertEqual(no_fit,before)
        ambiguous=row(_alternatives=[{'debit':100,'credit':None,'balance':900,'_engine':'OCR 1'},
                                    {'debit':200,'credit':None,'balance':800,'_engine':'OCR 2'}])
        before=copy.deepcopy(ambiguous);decode_balance_chain([ambiguous],1000)
        self.assertEqual(ambiguous,before)

    def test_cannot_combine_unrelated_cells_or_clear_populated_column(self):
        sample=row(balance=850,debit=130,credit=30,_numeric_candidates={
            'debit':[{'value':100,'engine':'OCR A'}],
            'credit':[{'value':None,'engine':'OCR B'}],
            'balance':[{'value':900,'engine':'OCR C'}]})
        before=copy.deepcopy(sample);decode_balance_chain([sample],1000)
        self.assertEqual(sample,before)
        sample['_alternatives']=[{'debit':100,'credit':None,'balance':900,'_engine':'Complete OCR row'}]
        decode_balance_chain([sample],1000)
        self.assertEqual((sample['debit'],sample['credit'],sample['balance']),(100,None,900))

    def test_unrelated_bad_row_does_not_block_later_source_island(self):
        rows=[row(balance=900),row(balance=None,date='2025-04-02'),
              row(balance=700,date='2025-04-03'),
              row(balance=550,date='2025-04-04',_numeric_candidates={'balance':[{'value':600,'engine':'OCR'}]})]
        decode_balance_chain(rows,1000)
        self.assertEqual(rows[-1]['balance'],600)
        self.assertIsNone(rows[1]['balance'])

    def test_statement_reset_and_descending_order(self):
        first=row(balance=850,_numeric_candidates={'balance':[{'value':900,'engine':'OCR'}]})
        second=row(balance=400,date='2025-04-02',_opening_balance=500)
        rows=[second,first];decode_balance_chain(rows,1000,order='descending')
        self.assertEqual(first['balance'],900)
        self.assertEqual(second['balance'],400)

    def test_inferred_and_ai_only_balances_do_not_anchor_following_rows(self):
        for change in ({'_inferred_fields':{'balance':{'method':'estimate'}}},
                       {'_engine':'Local GLM-OCR AI'}):
            first=row(balance=1000,**change)
            second=row(_numeric_candidates={'balance':[{'value':900,'engine':'OCR'}]})
            decode_balance_chain([first,second])
            self.assertEqual(second['balance'],850)

    def test_approved_and_corrected_rows_are_never_switched(self):
        for changes in ({'state':'approved'},{'state':'corrected'},{'_manual_fields':['balance']}):
            sample=row(_numeric_candidates={'balance':[{'value':900,'engine':'OCR'}]},**changes)
            before=copy.deepcopy(sample);decode_balance_chain([sample],1000)
            self.assertEqual(sample,before)

    def test_inferred_values_never_become_relabelled_observed_values(self):
        sample=row(_inferred_fields={'balance':{'method':'arithmetic estimate'}},
                   _alternatives=[{'debit':100,'credit':None,'balance':900,'_engine':'OCR'}])
        before=copy.deepcopy(sample);decode_balance_chain([sample],1000)
        self.assertEqual(sample,before)

    def test_ai_opening_balance_cannot_force_a_first_row_correction(self):
        sample=row(_opening_balance=1000,_opening_balance_source='ai',
                   _numeric_candidates={'balance':[{'value':900,'engine':'OCR'}]})
        before=copy.deepcopy(sample);decode_balance_chain([sample])
        self.assertEqual(sample,before)

    def test_pass_score_rejects_large_false_positive_pass(self):
        valid=[row(balance=900),row(balance=800,date='2025-04-02')]
        false=[row(balance=None,debit=None,date=None,confidence=.99) for _ in range(100)]
        self.assertGreater(pass_score(valid),pass_score(false))
        both=[row(balance=900,debit=200,credit=100) for _ in range(20)]
        self.assertGreater(pass_score(valid),pass_score(both))


if __name__=='__main__':unittest.main()
