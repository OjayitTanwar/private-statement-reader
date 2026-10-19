import copy,unittest
from people import automatic_counterparties,suggestions


def row(text,identity='row',confidence=.95):
    return {'id':identity,'original_description':text,'normalized_description':text,'confidence':confidence}


class AutomaticNamesTests(unittest.TestCase):
    def test_explicit_transfer_and_company_and_bank_formats(self):
        cases=[('TRFR TO AJAY SHARMA/123456','AJAY SHARMA'),
               ('TRFR TO M/S KUMAR TRADERS/123456','KUMAR TRADERS'),
               ('TRANSFER FROM KUMAR & SONS PRIVATE LIMITED REF 123456','KUMAR & SONS PRIVATE LIMITED'),
               ('NEFT/ICIC0001234/N123456/PRIYA MEHTA','PRIYA MEHTA'),
               ('RTGS/N123456/ABC TRADING LIMITED','ABC TRADING LIMITED'),
               ('UPI/DR/1234567890/RAJESH KUMAR/ICIC/rajesh@okbank','RAJESH KUMAR'),
               ('UPI/AJAI/123456','AJAI')]
        for source,expected in cases:
            with self.subTest(source=source):
                original=row(source);rows=[copy.deepcopy(original)]
                self.assertIs(automatic_counterparties(rows),rows)
                self.assertEqual(rows[0]['counterparty'],expected)
                self.assertEqual(rows[0]['original_description'],original['original_description'])
                self.assertEqual(rows[0]['normalized_description'],original['normalized_description'])
                self.assertTrue(rows[0]['_inferred_fields']['counterparty']['not_verified_identity'])
                self.assertIn(rows[0]['_inferred_fields']['counterparty']['source_text'],source)

    def test_no_name_invented_from_general_words_or_routing_data(self):
        for source in ['CASH WITHDRAWAL ATM','INTEREST PAID','NEFT/123456/ICIC0001234',
                       'UPI/CR/123456/HDFC/a.person@okbank','TRANSFER TO SELF/123456','NEFT/RECEIVED']:
            with self.subTest(source=source):
                rows=[row(source)];automatic_counterparties(rows)
                self.assertNotIn('counterparty',rows[0])

    def test_repeated_clear_source_can_repair_only_ocr_digit_shape(self):
        references=[row('TRFR TO AJAY SHARMA/111111','one'),row('TRFR TO AJAY SHARMA/222222','two')]
        rows=[row('TRFR TO AJAY 5HARMA/333333','three')]
        automatic_counterparties(rows,references)
        self.assertEqual(rows[0]['counterparty'],'AJAY SHARMA')
        self.assertEqual(rows[0]['_counterparty_candidates'][0]['support'],2)
        self.assertEqual(rows[0]['_inferred_fields']['counterparty']['source_text'],'AJAY 5HARMA')

    def test_similar_real_names_are_never_automatically_merged(self):
        references=[row('TRFR TO AJAY SHARMA/111111','one'),row('TRFR TO AJAY SHARMA/222222','two')]
        rows=[row('TRFR TO AJAI SHARMA/333333','three')]
        automatic_counterparties(rows,references)
        self.assertEqual(rows[0]['counterparty'],'AJAI SHARMA')

    def test_single_or_duplicate_reference_is_insufficient_for_correction(self):
        reference=row('TRFR TO AJAY SHARMA/111111','one')
        rows=[row('TRFR TO AJAY 5HARMA/333333','three')]
        automatic_counterparties(rows,[reference,reference])
        self.assertEqual(rows[0]['counterparty'],'AJAY 5HARMA')

    def test_preserves_existing_counterparty_and_existing_suggestions(self):
        current=row('TRFR TO AJAY SHARMA/111111');current['counterparty']='AI source reading'
        automatic_counterparties([current]);self.assertEqual(current['counterparty'],'AI source reading')
        self.assertTrue(suggestions('UPI/AJAI SHARMA/123456',[{'name':'Ajay Sharma','aliases':[]}]))


if __name__=='__main__':unittest.main()
