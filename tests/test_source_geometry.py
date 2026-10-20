import copy
import unittest
from bank_engine import cents,extract_page,match_ocr_rows


def word(text,x,w,y=100):
    return dict(text=text,x=x,w=w,y=y,h=20,conf=.97)


class SourceGeometryTests(unittest.TestCase):
    def test_wide_balance_is_not_shifted_into_credit(self):
        words=[word('01/04/2025',30,90),word('PAYMENT',190,100),
               word('100.00',620,60),word('1,234,567.89',700,240)]
        before=copy.deepcopy(words)
        rows,*_=extract_page(words,1000,1,{'date':.05,'description':.2,'debit':.65,'credit':.78,'balance':.91})
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['debit'],100)
        self.assertIsNone(rows[0]['credit'])
        self.assertEqual(rows[0]['balance'],1234567.89)
        self.assertEqual(rows[0]['_numeric_candidates']['balance'][0]['source'],'ocr')
        self.assertEqual(words,before)

    def test_prefix_and_punctuated_dr_cr_and_nonfinite(self):
        for text in ('Dr 1,234.56','1,234.56 Dr.','(1,234.56)'):
            self.assertEqual(cents(text),-123456)
        for text in ('Cr 1,234.56','1,234.56 Cr.'):
            self.assertEqual(cents(text),123456)
        self.assertEqual(cents('-1,234.56 CR'),-123456,'explicit minus must not be erased')
        for value in (True,float('nan'),float('inf')):self.assertIsNone(cents(value))

    def test_centered_headers_and_right_aligned_debits_keep_direction(self):
        words=[word('01/04/2025',30,90),word('PAYMENT',190,100),
               word('100.00',695,60),word('900.00',918,60)]
        rows,*_=extract_page(words,1000,1,{'date':.05,'description':.2,'debit':.692,'credit':.797,'balance':.934})
        self.assertEqual(rows[0]['debit'],100)
        self.assertIsNone(rows[0]['credit'])
        self.assertEqual(rows[0]['balance'],900)

    def test_dotted_short_year_date_is_not_a_money_cell(self):
        words=[word('1.04.25',30,70),word('SHOP',200,60),word('100.00',620,60),word('900.00',870,60)]
        rows,*_=extract_page(words,1000,1,{'date':.05,'description':.2,'debit':.65,'credit':.78,'balance':.91})
        self.assertEqual(rows[0]['original_description'],'SHOP')

    def test_same_date_neighbors_have_unique_nearest_matches(self):
        rows=[dict(id='a',date='2025-04-01',_top=100,_bottom=120),
              dict(id='b',date='2025-04-01',_top=125,_bottom=145)]
        others=[dict(date='2025-04-01',_top=101,_bottom=121),dict(date='2025-04-01',_top=126,_bottom=146)]
        matches=match_ocr_rows(rows,others)
        self.assertIs(matches['a'],others[0]);self.assertIs(matches['b'],others[1])
        self.assertEqual(match_ocr_rows(rows,[dict(date='2025-04-01',_top=112.5,_bottom=132.5)]),{})
