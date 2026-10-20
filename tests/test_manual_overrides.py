import copy
import unittest

from manual_overrides import apply_manual_overrides


def row(identity='fresh',**updates):
    item={'id':identity,'date':'2025-04-01','page':1,'_top':100,'original_description':'SOURCE NARRATION',
          'normalized_description':'SOURCE NARRATION','debit':100,'credit':None,'balance':900,'state':'review'}
    item.update(updates);return item


class ManualOverlayTests(unittest.TestCase):
    def test_identity_match_restores_explicit_fields_and_preserves_original_evidence(self):
        fresh=row(_inferred_fields={'debit':{'method':'estimate'},'balance':{'method':'estimate'}})
        original=row(debit=123,normalized_description='Reviewer text',original_description='Do not copy this',state='approved')
        state={'manual_overrides':[{'original_row':original,'fields':['debit','normalized_description','original_description','state','id']}]}
        before=copy.deepcopy([fresh]);saved=copy.deepcopy(state)
        result,report=apply_manual_overrides([fresh],state)
        self.assertEqual([fresh],before);self.assertEqual(state,saved)
        self.assertEqual(result[0]['debit'],123)
        self.assertEqual(result[0]['normalized_description'],'Reviewer text')
        self.assertEqual(result[0]['original_description'],'SOURCE NARRATION')
        self.assertEqual(result[0]['state'],'approved')
        self.assertEqual(result[0]['_field_sources']['debit'],'manual')
        self.assertNotIn('debit',result[0]['_inferred_fields'])
        self.assertIn('balance',result[0]['_inferred_fields'])
        self.assertEqual(result[0]['_manual_override_applied'][0]['before']['debit'],100)
        self.assertEqual(report['applied_rows'],1)

    def test_unique_geometry_matches_but_tied_geometry_remains_unmatched(self):
        state={'manual_overrides':[{'original_row':row('old',debit=123),'fields':['debit']}]}
        result,report=apply_manual_overrides([row('a',_top=105),row('b',_top=150)],state)
        self.assertEqual(result[0]['debit'],123);self.assertEqual(result[1]['debit'],100)
        original=[row('a',_top=95),row('b',_top=105)]
        result,report=apply_manual_overrides(original,state)
        self.assertEqual(result,original);self.assertEqual(report['unmatched_overrides'],1)

    def test_exact_narration_date_fallback_is_unique_and_page_specific(self):
        state={'manual_overrides':[{'original_row':row('old',_top=None,debit=123),'fields':['debit']}]}
        result,report=apply_manual_overrides([row('a',_top=None),row('b',page=2,_top=None)],state)
        self.assertEqual(result[0]['debit'],123);self.assertEqual(result[1]['debit'],100)
        original=[row('a',_top=None),row('b',_top=None)]
        result,report=apply_manual_overrides(original,state)
        self.assertEqual(result,original);self.assertEqual(report['unmatched_overrides'],1)

    def test_one_to_one_matching_does_not_apply_two_overrides_to_one_row(self):
        state={'manual_overrides':[{'original_row':row('old1',debit=123),'fields':['debit']},
                                   {'original_row':row('old2',debit=456),'fields':['debit']}]}
        result,report=apply_manual_overrides([row()],state)
        self.assertEqual(len(result),1);self.assertEqual(result[0]['debit'],123)
        self.assertEqual(report['applied_rows'],1);self.assertEqual(report['unmatched_overrides'],1)

    def test_unmatched_manual_transcription_is_preserved_and_marked_manual(self):
        manual=row('manual',page=2,_engine='Manual transcription',original_description='Manual source text',debit=321)
        state={'manual_overrides':[{'original_row':manual,'fields':['debit','original_description','state']}]}
        result,report=apply_manual_overrides([row()],state)
        self.assertEqual(len(result),2);self.assertEqual(result[1]['id'],'manual')
        self.assertEqual(result[1]['original_description'],'Manual source text')
        self.assertEqual(result[1]['_field_sources']['debit'],'manual')
        self.assertEqual(report['appended_manual_rows'],1)

    def test_grid_and_page_positions_cannot_be_mixed(self):
        state={'manual_overrides':[{'original_row':row('old',debit=123,_cell_evidence={'date':[]}),
                                   'fields':['debit']}]}
        original=[row(original_description='Different source narration')]
        result,report=apply_manual_overrides(original,state)
        self.assertEqual(result,original)
        self.assertEqual(report['unmatched_overrides'],1)
        self.assertIn('Source coordinate frames differ',report['unmatched_details'][0]['reasons'])

    def test_geometry_requires_same_source_date_or_exact_narration_for_edited_date(self):
        state={'manual_overrides':[{'original_row':row('old',debit=123),'fields':['debit']}]}
        original=[row(date='2025-04-02')]
        result,report=apply_manual_overrides(original,state)
        self.assertEqual(result,original)
        self.assertEqual(report['unmatched_overrides'],1)
        state['manual_overrides'][0]['fields']=['debit','date']
        result,report=apply_manual_overrides(original,state)
        self.assertEqual(result[0]['date'],'2025-04-01')
        self.assertEqual(result[0]['debit'],123)
        original=[row(date='2025-04-02',original_description='Different narration')]
        result,report=apply_manual_overrides(original,state)
        self.assertEqual(result,original)
        self.assertIn('Edited date requires exact original narration',report['unmatched_details'][0]['reasons'])

    def test_near_tied_positions_are_unmatched_even_if_not_exactly_tied(self):
        state={'manual_overrides':[{'original_row':row('old',debit=123),'fields':['debit']}]}
        original=[row('a',_top=105),row('b',_top=106)]
        result,report=apply_manual_overrides(original,state)
        self.assertEqual(result,original)
        self.assertEqual(report['unmatched_overrides'],1)


if __name__=='__main__':unittest.main()
