import copy
import csv
import io
import unittest

from review_checks import classify_review
from automation import prepared_rows, export_bytes


def row(**updates):
    result={'date':'2025-04-01','debit':100,'credit':None,'balance':900,
            'original_description':'TRANSFER','normalized_description':'TRANSFER',
            'confidence':.95,'suspicious':False,'reason':'','_issues':[]}
    result.update(updates)
    return result


class ReviewClassificationTests(unittest.TestCase):
    def test_normal_row_has_no_financial_review(self):
        result=classify_review(row())
        self.assertEqual(result['category'],'none')
        self.assertFalse(result['review_required'])

    def test_description_names_confidence_and_generic_disagreement_are_information(self):
        samples=[row(confidence=.55,suspicious=True,reason='weak OCR; compare numbers with source'),
                 row(suspicious=True,_issues=['OCR passes disagree or another pass missed this row']),
                 row(suspicious=True,_issues=['Description/name proposed by local AI; original OCR retained']),
                 row(suspicious=True,_issues=['Counterparty name is a local AI guess, not verified identity']),
                 row(_inferred_fields={'counterparty':{'method':'name guess'}}),
                 row(_issues=['Alternative OCR numbers selected using balance evidence; verify source']),
                 row(_issues=['debit: observed alternate selected by whole-statement balance chain']),
                 row(_issues=['multiple values mapped to description']),
                 row(_issues=['Local AI did not independently recover this OCR row']),
                 row(normalized_description='',original_description='')]
        for sample in samples:
            with self.subTest(sample=sample):
                result=classify_review(sample)
                self.assertEqual(result['category'],'information')
                self.assertFalse(result['review_required'])

    def test_financial_structure_errors_cannot_be_hidden_by_informational_issues(self):
        cases=[{'date':None},{'date':'2025-02-30'},{'date':'01/04/2025'},
               {'balance':None},{'balance':'NaN'},{'balance':'unreadable'},
               {'debit':None},{'debit':0,'credit':0},{'debit':-100},
               {'credit':'bad digits'},{'debit':100,'credit':10},
               {'reason':'balance mismatch: difference +1.00'},
               {'reason':'opening balance unavailable; first row not reconciled'},
               {'_issues':['AI-only transaction candidate; not independently confirmed']},
               {'_issues':['unrecognized columns; calibrate table']},
               {'_issues':['multiple values mapped to debit']},
               {'_issues':['Previously unknown substantive problem']},
               {'_engine':'Manual transcription'},{'suspicious':True}]
        for case in cases:
            with self.subTest(case=case):
                sample=row(**case)
                sample['_issues']=[*sample['_issues'],'Description/name proposed by local AI; original OCR retained'] if case!={'suspicious':True} else []
                result=classify_review(sample)
                self.assertEqual(result['category'],'financial_error')
                self.assertTrue(result['review_required'])

    def test_numeric_and_date_conflicts_ignore_missing_alternatives_only(self):
        for field in ('debit','credit','balance'):
            sample=row(_numeric_candidates={field:[{'value':1},{'value':2}]})
            self.assertEqual(classify_review(sample)['category'],'financial_error')
        self.assertEqual(classify_review(row(_date_candidates=[{'value':'2025-04-01'},{'value':'2025-04-02'}]))['category'],'financial_error')
        self.assertEqual(classify_review(row(_alternatives=[{'debit':99,'balance':900}]))['category'],'financial_error')
        self.assertEqual(classify_review(row(_alternatives=[{'debit':None,'credit':None,'balance':None}]))['category'],'none')
        self.assertEqual(classify_review(row(_numeric_candidates={'balance':[{'value':'900.00'},{'value':900}]}))['category'],'none')

    def test_inferred_money_dates_and_ai_opening_balances_require_review(self):
        samples=[row(_inferred_fields={field:{'method':'estimate'}}) for field in ('debit','credit','balance','date')]
        samples += [row(_issues=['Date proposed by local AI']),
                    row(_issues=['debit: filled by local AI estimate']),
                    row(_issues=['Balance estimated from two adjacent equations; original reading retained']),
                    row(_issues=['Opening balance read by local AI; verify source'])]
        for sample in samples:
            with self.subTest(sample=sample):
                result=classify_review(sample)
                self.assertEqual(result['category'],'estimated_value')
                self.assertTrue(result['estimated_fields'])
                self.assertTrue(result['review_required'])
        result=classify_review(row(_inferred_fields={'balance':{}},reason='balance mismatch'))
        self.assertEqual(result['category'],'financial_error')
        self.assertEqual(result['estimated_fields'],['balance'])

    def test_exports_preserve_legacy_reason_and_values_without_mutating_rows(self):
        original=row(suspicious=True,reason='weak OCR; compare numbers with source',confidence=.7)
        before=copy.deepcopy(original)
        exported=prepared_rows([original])[0]
        self.assertEqual(original,before)
        self.assertTrue(exported['legacy_suspicious'])
        self.assertFalse(exported['suspicious'])
        self.assertFalse(exported['review_required'])
        self.assertEqual(exported['review_category'],'information')
        self.assertEqual(exported['reason'],original['reason'])
        parsed=list(csv.DictReader(io.StringIO(export_bytes('csv',[exported],{}).decode('utf-8-sig'))))[0]
        self.assertEqual(parsed['review_category'],'information')
        self.assertIn('weak OCR',parsed['information_notes'])

    def test_ai_zero_in_unused_amount_column_is_information_not_money_estimate(self):
        for active,inactive in (('debit','credit'),('credit','debit')):
            sample=row(**{active:100,inactive:0},_issues=[f'{inactive}: filled by local AI estimate'],
                       _inferred_fields={inactive:{'method':'AI estimate'}})
            result=classify_review(sample)
            self.assertEqual(result['category'],'information')
            self.assertEqual(result['estimated_fields'],[])
        result=classify_review(row(debit=0,credit=0,_issues=['credit: filled by local AI estimate']))
        self.assertEqual(result['category'],'financial_error')
        result=classify_review(row(debit=100,credit=0,_issues=['credit: filled by local AI estimate','AI-only transaction candidate']))
        self.assertEqual(result['category'],'financial_error')
        result=classify_review(row(debit=100,credit=0,balance=0,_inferred_fields={'balance':{'method':'AI estimate'}}))
        self.assertEqual(result['category'],'estimated_value')

    def test_report_counts_separate_financial_review_from_names_and_deleted_rows(self):
        from app import automatic_report
        rows=[row(),row(_inferred_fields={'counterparty':{'method':'name guess'}}),
              row(_inferred_fields={'balance':{'method':'estimate'}}),
              row(reason='balance mismatch'),row(state='deleted',reason='balance mismatch')]
        report=automatic_report(rows)
        self.assertEqual(report['rows'],4)
        self.assertEqual(report['flagged_rows'],2)
        self.assertEqual(report['financial_error_rows'],1)
        self.assertEqual(report['estimated_value_rows'],1)
        self.assertEqual(report['information_only_rows'],1)
        self.assertEqual(report['inferred_rows'],1)


if __name__=='__main__':unittest.main()
