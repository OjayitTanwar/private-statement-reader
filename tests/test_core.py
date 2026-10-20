import unittest
from bank_engine import cents,parse_amount,normal_date,extract_page,reconcile,is_statement,select_observed_candidates
from people import suggestions


def word(text,x,y=100,w=None):
    return {'text':text,'x':x,'y':y,'w':w or len(text)*9,'h':20,'conf':.96}


def fixture():
    words=[word(t,x,50) for t,x in [('Date',40),('Particulars',300),('Withdrawal',1200),('Deposit',1470),('Balance',1750)]]
    words += [word(t,x) for t,x in [('01/04/2025',40),('UPI/AJAI/123456',300),('1,000.00',1200),('9,000.00',1750)]]
    words += [word('GROCERY STORE',300,130)]
    words += [word(t,x,170) for t,x in [('02/04/2025',40),('NEFT/RECEIVED',300),('5,000.00',1470),('14,000.00',1750)]]
    return words


class ExtractionTests(unittest.TestCase):
    def test_indian_amounts_exact_cents(self):
        self.assertEqual(cents('1,23,456.70'),12345670)
        self.assertEqual(cents('(500.00)'),-50000)
        self.assertEqual(cents('500.00 DR'),-50000)
        self.assertEqual(cents(0),0)
        self.assertEqual(cents('6.54,321.09'),65432109)
        self.assertEqual(cents('1,54,321,09'),15432109)
        self.assertIsNone(cents('NaN'))
    def test_indian_dates(self):
        for raw in ('01/04/2025','1-Apr-25','01 Apr 2025','1.4.2025'):
            self.assertEqual(normal_date(raw),'2025-04-01')
        self.assertIsNone(normal_date('31/02/2025'))
    def test_columns_multiline_and_balance(self):
        rows,cols,_,_=extract_page(fixture(),2000,1)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['debit'],1000)
        self.assertIsNone(rows[0]['credit'])
        self.assertEqual(rows[1]['credit'],5000)
        self.assertIn('GROCERY STORE',rows[0]['original_description'])
        reconcile(rows,10000)
        self.assertTrue(all(not r['suspicious'] for r in rows))
    def test_tilted_and_currency_suffixed_header(self):
        words=fixture()
        for w in words:
            w['y']+=w['x']*.017
            if w['text']=='Balance':w['text']='Balance(INR)'
        rows,cols,_,_=extract_page(words,2000,1)
        self.assertIn('debit',cols);self.assertIn('balance',cols)
        self.assertEqual(len(rows),2)
    def test_catches_one_rupee_error_and_never_changes_printed_value(self):
        rows,*_=extract_page(fixture(),2000,1)
        rows[1]['credit']=5001
        reconcile(rows,10000)
        self.assertIn('difference -1.00',rows[1]['reason'])
        self.assertEqual(rows[1]['credit'],5001)
    def test_opening_missing_is_explicit(self):
        rows,*_=extract_page(fixture(),2000,1);reconcile(rows)
        self.assertIn('opening balance unavailable',rows[0]['reason'])
    def test_descending_reconciles_in_chronological_order(self):
        rows,*_=extract_page(fixture(),2000,1);rows.reverse();reconcile(rows,10000)
        self.assertTrue(all(not r['suspicious'] for r in rows))
    def test_no_keyword_guessing(self):
        words=fixture();words=[w for w in words if w['y']!=50]
        rows,*_=extract_page(words,2000,1)
        self.assertIsNone(rows[0]['debit']);self.assertIsNone(rows[0]['credit'])
        reconcile(rows,10000,infer=True)
        self.assertEqual(rows[0]['debit'],1000)
        self.assertEqual(rows[1]['credit'],5000)
        self.assertIn('unrecognized columns',rows[0]['reason'])
    def test_bank_document_gate(self):
        _,cols,_,_=extract_page(fixture(),2000,1)
        self.assertTrue(is_statement('Account Statement Account Number 123456 IFSC',cols))
        self.assertFalse(is_statement('Invoice total taxable amount',cols))
    def test_excluded_rows_do_not_participate(self):
        rows,*_=extract_page(fixture(),2000,1);rows[0]['state']='deleted';reconcile(rows,9000)
        self.assertFalse(rows[1]['suspicious'])
    def test_names_fuzzy_and_phonetic_suggestions_only(self):
        hits=suggestions('UPI/AJAI SHARMA/123456',[{'name':'Ajay Sharma','aliases':[]}])
        self.assertEqual(hits[0]['name'],'Ajay Sharma')
        self.assertIn('not verified',hits[0]['note'])
        self.assertEqual(suggestions('ATM CASH 1234',[{'name':'Suresh Gupta','aliases':[]}]),[])
    def test_balance_can_select_observed_alternative_but_cannot_invent_digits(self):
        rows,*_=extract_page(fixture(),2000,1)
        rows[0]['debit']=1300
        # Emulate an uncorroborated bad reading, without the source candidates
        # now recorded by extract_page itself.
        rows[0]['_numeric_candidates']={}
        select_observed_candidates(rows,10000)
        self.assertEqual(rows[0]['debit'],1300)
        rows[0]['_alternatives']=[{'debit':1000,'credit':None,'balance':9000,'_engine':'Other OCR','confidence':.9}]
        select_observed_candidates(rows,10000)
        self.assertEqual(rows[0]['debit'],1000)
        self.assertIn('Alternative OCR',rows[0]['_issues'][-1])


if __name__=='__main__':unittest.main()
