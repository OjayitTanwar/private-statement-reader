import unittest
from local_ai import combine_ai,infer_two_sided_balances,infer_missing_amounts
from table_grid import decode_balance_chain
from ai_formats import parse_table


def row(balance,debit=None,credit=None):
    return {'id':str(balance),'date':'2025-01-01','page':1,'ord':0,'original_description':'TRANSFER',
            'normalized_description':'TRANSFER','cheque_number':'','balance':balance,'debit':debit,'credit':credit,
            'confidence':.9,'_engine':'Test OCR','_issues':[],'_numeric_candidates':{}}


class InferenceTests(unittest.TestCase):
    def test_ai_empty_placeholder_is_not_a_transaction(self):
        result,_=combine_ai([],{'transactions':[{'description':'Unreadable transaction','date':None,'debit':None,'credit':None,'balance':None}]},1)
        self.assertEqual(result,[])
    def test_structured_ai_fallback_understands_bank_table_not_totals(self):
        raw='<table><tr><th>Date</th><th>Particulars</th><th>Withdrawals</th><th>Deposits</th><th>Balance(INR)</th></tr><tr><td>01/01/2025</td><td>B/F</td><td></td><td></td><td>1,000.00</td></tr><tr><td>02/01/2025</td><td>UPI SHOP</td><td>100.00</td><td>0.00</td><td>900.00</td></tr><tr><td></td><td>Page Total</td><td>100.00</td><td></td><td>900.00</td></tr></table>'
        result=parse_table(raw)
        self.assertEqual(result['opening_balance'],'1,000.00')
        self.assertEqual(len(result['transactions']),1)
        self.assertEqual(result['transactions'][0]['debit'],'100.00')
    def test_two_independent_equations_can_propose_balance_with_provenance(self):
        # Synthetic values: the two equations imply 100020.25, not 200020.25.
        rows=[row(200020.25,200000),row(600020.25,0,500000)]
        infer_two_sided_balances(rows,300020.25)
        self.assertEqual(rows[0]['balance'],100020.25)
        self.assertEqual(rows[0]['_inferred_fields']['balance']['original_value'],200020.25)
        self.assertTrue(rows[0]['_inferred_fields']['balance']['not_printed_evidence'])
    def test_missing_amount_is_explicitly_inferred(self):
        rows=[row(900)]
        infer_missing_amounts(rows,1000)
        self.assertEqual(rows[0]['debit'],100)
        self.assertIn('debit',rows[0]['_inferred_fields'])
    def test_missing_balance_is_estimated_without_claiming_printed_evidence(self):
        r=row(None,100)
        infer_missing_amounts([r],1000)
        self.assertEqual(r['balance'],900)
        self.assertTrue(r['_inferred_fields']['balance']['not_printed_evidence'])
    def test_ai_cannot_erase_an_ocr_row(self):
        rows=[row(900,100),row(800,100)];rows[1]['id']='different'
        result,_=combine_ai(rows,{'transactions':[]},1)
        self.assertEqual(len(result),2)
    def test_ai_description_keeps_original(self):
        r=row(900,100)
        result,_=combine_ai([r],{'transactions':[{'date':'01-01-2025','description':'TRANSFER AJAY','debit':100,'balance':900}]},1)
        self.assertEqual(result[0]['original_description'],'TRANSFER')
        self.assertEqual(result[0]['normalized_description'],'TRANSFER AJAY')
        self.assertEqual(result[0]['_ocr_before_ai']['debit'],100)
    def test_statement_chain_uses_a_later_row_to_disambiguate_an_earlier_balance(self):
        first=row(850,100)
        first['_numeric_candidates']={'balance':[{'value':850,'confidence':1,'engine':'one'},{'value':900,'confidence':.8,'engine':'two'}]}
        second=row(1100,0,200)
        rows=[first,second];decode_balance_chain(rows,1000)
        self.assertEqual(first['balance'],900)


if __name__=='__main__':unittest.main()
