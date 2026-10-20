"""Synthetic table-output regressions; no private statement files are used."""
import unittest

from ai_formats import parse_json, parse_table
from bank_engine import cents


def table(headers, rows):
    return '<table><tr>' + ''.join(f'<th>{text}</th>' for text in headers) + '</tr>' + ''.join(
        '<tr>' + ''.join(f'<td>{text}</td>' for text in row) + '</tr>' for row in rows) + '</table>'


HEADERS = ['Date', 'Particulars', 'Debit', 'Credit', 'Balance']
GOOD = ['01/04/2025', 'Payment', '100.00', '', '900.00']


class AITableTests(unittest.TestCase):
    def test_missing_middle_blank_never_shifts_balance_into_credit(self):
        malformed = ['01/04/2025', 'Payment', '100.00', '900.00']
        with self.assertRaises(ValueError) as raised:
            parse_table(table(HEADERS, [malformed]))
        evidence = raised.exception.table_evidence
        self.assertEqual(evidence['transactions'], [])
        self.assertEqual(evidence['_rejected_table_rows'][0]['cells'], malformed)
        self.assertIn('ragged row', evidence['_table_warnings'][0])

    def test_safe_rows_survive_but_ragged_extra_or_missing_cells_do_not(self):
        result = parse_table(table(HEADERS, [GOOD, GOOD[:-1], GOOD + ['extra']]))
        self.assertEqual(len(result['transactions']), 1)
        self.assertEqual(result['transactions'][0]['debit'], '100.00')
        self.assertEqual(result['transactions'][0]['credit'], '')
        self.assertEqual(len(result['_rejected_table_rows']), 2)

    def test_indian_header_aliases_and_iso_dates(self):
        result = parse_table(table(['General Ledger Date', 'Transaction Remarks', 'Dr', 'Cr', 'Closing Balance'],
                                   [['2025-04-01', 'Payment', '100.00', '', '900.00']]))
        row = result['transactions'][0]
        self.assertEqual(row['description'], 'Payment')
        self.assertEqual(row['balance'], '900.00')
        self.assertEqual(row['debit'], '100.00')

    def test_amount_column_with_explicit_direction(self):
        for marker, side in [('Dr', 'debit'), ('Cr.', 'credit')]:
            result = parse_table(table(['Date', 'Description', 'Amount', 'Dr/Cr', 'Running Balance'],
                                       [['01-04-2025', 'Transfer', '1,000.00', marker, '9,000.00']]))
            self.assertEqual(result['transactions'][0][side], '1,000.00')

    def test_amount_direction_prefix_and_suffix_are_not_lost(self):
        for amount, side in [('Dr 100.00', 'debit'), ('100.00DR', 'debit'), ('Cr 100.00', 'credit'), ('100.00 Cr.', 'credit'), ('Dr. 100.00', 'debit')]:
            with self.subTest(amount=amount):
                result = parse_table(table(['Date', 'Description', 'Amount', 'Balance'],
                                           [['01-04-2025', 'Transfer', amount, '900.00']]))
                self.assertEqual(result['transactions'][0][side], '100.00')

    def test_unsigned_single_amount_remains_unassigned(self):
        result = parse_table(table(['Date', 'Description', 'Amount', 'Balance'],
                                   [['01-04-2025', 'Transfer', '100.00', '900.00']]))
        row = result['transactions'][0]
        self.assertEqual(row['amount'], '100.00')
        self.assertNotIn('debit', row)
        self.assertNotIn('credit', row)

    def test_explicit_debit_and_credit_values_are_positive_with_currency(self):
        result = parse_table(table(HEADERS, [['01-04-2025', 'Payment', 'Rs. -100.00', '', '900.00']]))
        self.assertEqual(cents(result['transactions'][0]['debit']), 10000)

    def test_multilevel_headers_expand_both_rowspan_and_colspan(self):
        raw = '''<table><tr><th rowspan="2">Date</th><th rowspan="2">Particulars</th>
        <th colspan="2">Amount</th><th colspan="2">Running Balance</th></tr>
        <tr><th>Debit</th><th>Credit</th><th>Amount</th><th>Dr/Cr</th></tr>
        <tr><td>01-04-2025</td><td>Transfer</td><td>100.00</td><td></td><td>900.00</td><td>Dr</td></tr></table>'''
        row = parse_table(raw)['transactions'][0]
        self.assertEqual(row['debit'], '100.00')
        self.assertEqual(cents(row['balance']), -90000)
        self.assertEqual(row['_table_source']['header_columns']['balance'], 4)

    def test_separate_balance_direction_stays_with_balance(self):
        result = parse_table(table(HEADERS + ['Dr/Cr'], [GOOD + ['Dr']]))
        row = result['transactions'][0]
        self.assertEqual(row['balance'], '900.00 DR')
        self.assertEqual(row['debit'], '100.00')
        self.assertEqual(row['credit'], '')

    def test_explicit_adjacent_balance_marker_without_a_header_is_retained(self):
        row = parse_table(table(HEADERS + [''], [GOOD + ['Dr.']]))['transactions'][0]
        self.assertEqual(cents(row['balance']), -90000)

    def test_conflicting_direction_is_rejected_not_silently_flipped(self):
        with self.assertRaises(ValueError):
            parse_table(table(['Date', 'Description', 'Amount', 'Dr/Cr', 'Balance'],
                              [['01-04-2025', 'Transfer', '100.00 CR', 'Dr', '900.00']]))
        with self.assertRaises(ValueError):
            parse_table(table(HEADERS, [['01-04-2025', 'Transfer', '100.00 CR', '', '900.00']]))
        with self.assertRaises(ValueError):
            parse_table(table(HEADERS, [['01-04-2025', 'Transfer', '100.00', '', '-900.00 CR']]))

    def test_opening_balance_repeated_headers_and_totals(self):
        result = parse_table(table(HEADERS, [['', 'B/F', '', '', '1,000.00'], HEADERS, GOOD,
                                            ['', 'Page Total', '100.00', '', '900.00']]))
        self.assertEqual(result['opening_balance'], '1,000.00')
        self.assertEqual(len(result['transactions']), 1)

    def test_dated_closing_balance_narration_is_still_a_transaction(self):
        result = parse_table(table(HEADERS, [['01-04-2025', 'Closing balance correction', '100.00', '', '900.00']]))
        self.assertEqual(len(result['transactions']), 1)

    def test_invalid_dates_are_rejected_and_narration_wraps_preserved(self):
        result = parse_table(table(HEADERS, [GOOD, ['', 'continued narration', '', '', ''],
                                            ['31-02-2025', 'Invalid', '100.00', '', '800.00']]))
        self.assertEqual(result['transactions'][0]['description'], 'Payment continued narration')
        self.assertEqual(len(result['transactions']), 1)
        self.assertEqual(result['_rejected_table_rows'][0]['reason'], 'invalid date')

    def test_markdown_table_separator_is_not_a_transaction(self):
        raw = '| Date | Description | Debit | Credit | Balance |\n|---|---|---|---|---|\n|01-04-2025|Payment|100.00||900.00|'
        self.assertEqual(len(parse_table(raw)['transactions']), 1)

    def test_json_preserves_unknown_provenance_and_flags(self):
        value = parse_json('```json\n{"transactions":[{"date":"01-04-2025","debit":100,"uncertain":true}],"flags":["visual guess"]}\n```')
        self.assertTrue(value['transactions'][0]['uncertain'])
        self.assertEqual(value['flags'], ['visual guess'])

    def test_json_rejects_unsafe_structures_and_nonfinite_numbers(self):
        invalid = ['{"transactions":[1]}', '{"transactions":{}}',
                   '{"transactions":[{"debit":true}]}', '{"transactions":[{"balance":{}}]}',
                   '{"transactions":[{"date":123}]}', '{"transactions":[],"opening_balance":{}}',
                   '{"transactions":[],"transactions":[]}', '{"transactions":[{"balance":NaN}]}',
                   '{"transactions":[{"balance":1e999}]}']
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_json(raw)


if __name__ == '__main__':
    unittest.main()
