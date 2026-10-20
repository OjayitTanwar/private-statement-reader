"""Synthetic legacy AI name cleanup, with no financial mutation."""
import copy
import unittest

from people import repair_counterparty_guesses


def row(name='UNRELATED PERSON',narration='UPI/123456789/HDFC',identity='row',**changes):
    result={'id':identity,'state':'review','date':'2025-04-01','page':1,'confidence':.95,
            'original_description':narration,'normalized_description':narration,
            'counterparty':name,'cheque_number':'REF12345','debit':100.25,'credit':0,'balance':899.75,
            '_inferred_fields':{'counterparty':{'method':'local AI reading/name guess','value':name,'source_text':narration},
                                'balance':{'method':'test provenance','value':899.75}},
            '_ai_proposal':{'counterparty':name,'debit':100.25},'_issues':['weak OCR']}
    result.update(changes)
    return result


class NameRepairTests(unittest.TestCase):
    def test_ids_generic_and_unanchored_guesses_are_rejected(self):
        for name in ('ICIC0001234','123456789012','TRANSFER','HDFC','UNRELATED PERSON'):
            with self.subTest(name=name):
                original=row(name)
                repaired,report=repair_counterparty_guesses([original])
                self.assertEqual(repaired[0]['counterparty'],'')
                self.assertEqual(report,{'rejected':1,'replaced':0,'unchanged':0})
                self.assertEqual(repaired[0]['_rejected_counterparty_guesses'][0]['original_value'],name)
                self.assertEqual(repaired[0]['_rejected_counterparty_guesses'][0]['provenance'],original['_inferred_fields']['counterparty'])
                self.assertNotIn('counterparty',repaired[0]['_inferred_fields'])

    def test_explicit_original_source_replaces_bad_name(self):
        original=row('TRANSFER','TRFR TO AJAY SHARMA/123456')
        repaired,report=repair_counterparty_guesses([original])
        self.assertEqual(repaired[0]['counterparty'],'AJAY SHARMA')
        self.assertEqual(report['replaced'],1)
        self.assertNotIn('local AI',repaired[0]['_inferred_fields']['counterparty']['method'])
        self.assertEqual(original['counterparty'],'TRANSFER')

    def test_credible_original_and_small_phonetic_neighbor_are_retained(self):
        for name,source in [('AJAY SHARMA','TRFR TO AJAY SHARMA/123456'),
                            ('AJAI SHARMA','TRFR TO AJAY SHARMA/123456')]:
            with self.subTest(name=name):
                original=row(name,source)
                repaired,report=repair_counterparty_guesses([original])
                self.assertEqual(repaired[0],original)
                self.assertEqual(report['unchanged'],1)

    def test_repeated_original_sources_can_repair_narrow_digit_confusion(self):
        items=[row('UNRELATED PERSON','TRFR TO AJAY 5HARMA/123456')]
        for index in (1,2):
            items.append(row('AJAY SHARMA','TRFR TO AJAY SHARMA/123456',str(index),_inferred_fields={}))
        repaired,report=repair_counterparty_guesses(items)
        self.assertEqual(repaired[0]['counterparty'],'AJAY SHARMA')
        self.assertEqual(report['replaced'],1)
        self.assertEqual(repaired[0]['_counterparty_candidates'][0]['support'],2)

    def test_manual_approved_corrected_and_audit_protected_rows_unchanged(self):
        changes=[{'state':'approved'},{'state':'corrected'},{'state':'deleted'},
                 {'_engine':'Manual transcription'},{'_manual_fields':['counterparty']},
                 {'_edited_fields':['balance']},{'_manual':True},{'_corrected':True},{'_edited':True},
                 {'_field_sources':{'counterparty':'manual'}}]
        for change in changes:
            with self.subTest(change=change):
                original=row(**change)
                repaired,report=repair_counterparty_guesses([original])
                self.assertEqual(repaired[0],original)
                self.assertEqual(report['rejected'],0)
        original=row()
        repaired,report=repair_counterparty_guesses([original],protected_ids=('row',))
        self.assertEqual(repaired[0],original)
        self.assertEqual(report['unchanged'],1)

    def test_edited_value_with_stale_ai_metadata_is_protected(self):
        original=row()
        original['counterparty']='Manually corrected name'
        repaired,report=repair_counterparty_guesses([original])
        self.assertEqual(repaired[0],original)
        self.assertEqual(report['rejected'],0)

    def test_non_ai_source_guess_is_never_removed(self):
        original=row()
        original['_inferred_fields']['counterparty']['method']='explicit transfer narration'
        repaired,report=repair_counterparty_guesses([original])
        self.assertEqual(repaired[0],original)
        self.assertEqual(report['unchanged'],1)

    def test_rejected_name_candidates_are_archived_not_reused(self):
        original=row('TRANSFER','TRFR TO AJAY SHARMA/123456')
        original['_counterparty_candidates']=[{'name':'UNRELATED PERSON','score':.99}]
        repaired,_=repair_counterparty_guesses([original])
        self.assertNotIn('_counterparty_candidates',repaired[0])
        self.assertEqual(repaired[0]['_rejected_counterparty_guesses'][0]['original_candidates'],original['_counterparty_candidates'])

    def test_normalized_ai_text_cannot_anchor_a_name_or_replacement(self):
        original=row('AJAY SHARMA','',normalized_description='TRFR TO AJAY SHARMA/123456')
        repaired,report=repair_counterparty_guesses([original])
        self.assertEqual(repaired[0]['counterparty'],'')
        self.assertEqual(repaired[0]['normalized_description'],original['normalized_description'])
        self.assertEqual(report['replaced'],0)

    def test_financial_fields_narrations_and_provenance_do_not_change(self):
        original=row('TRANSFER','TRFR TO AJAY SHARMA/123456')
        snapshot=copy.deepcopy(original)
        repaired,_=repair_counterparty_guesses([original])
        self.assertEqual(original,snapshot)
        for key in ('date','page','debit','credit','balance','cheque_number','original_description',
                    'normalized_description','_ai_proposal','_issues'):
            self.assertEqual(repaired[0][key],snapshot[key])
        self.assertEqual(repaired[0]['_inferred_fields']['balance'],snapshot['_inferred_fields']['balance'])
        second,report=repair_counterparty_guesses(repaired)
        self.assertEqual(second,repaired)
        self.assertEqual(report['rejected'],0)


if __name__=='__main__':
    unittest.main()
