"""Synthetic checks for AI row placement, source provenance and bounded estimates."""
import copy,unittest
from local_ai import combine_ai,infer_missing_amounts,infer_two_sided_balances


def row(identity,balance=None,debit=None,credit=None,description='UPI/RAJESH KUMAR/123456',date='2025-01-01',page=1):
    return {'id':identity,'date':date,'page':page,'ord':0,'original_description':description,
            'normalized_description':description,'cheque_number':'','debit':debit,'credit':credit,'balance':balance,
            'confidence':.95,'_engine':'Tesseract','_numeric_candidates':{},'_issues':[]}


def item(source,**changes):
    result={key:source.get(key) for key in ('date','debit','credit','balance')}
    result['description']=source['original_description'];result.update(changes)
    return result


class AIEvidenceTests(unittest.TestCase):
    def test_repeated_dates_and_narration_align_by_numeric_sequence(self):
        rows=[row('first',900,100),row('second',800,100),row('third',750,50)]
        proposals=[item(rows[1]),item(rows[2])]
        result,_=combine_ai(rows,{'transactions':proposals},1)
        self.assertEqual([r['id'] for r in result],['first','second','third'])
        self.assertNotIn('_ai_proposal',result[0])
        self.assertEqual(result[1]['_ai_proposal']['balance'],800)
        self.assertEqual(result[2]['_ai_proposal']['balance'],750)

    def test_equal_repeated_rows_reject_ambiguous_attachment_and_duplicate(self):
        rows=[row('first',900,100),row('second',900,100)]
        result,_=combine_ai(rows,{'transactions':[item(rows[0],counterparty='RAJESH KUMAR')]},1)
        self.assertEqual(len(result),2)
        self.assertTrue(all('_ai_proposal' not in r for r in result))
        self.assertTrue(all(r['_ai_proposals'][0]['status']=='rejected_alignment' for r in result))
        self.assertTrue(all('counterparty' not in r for r in result))

    def test_conflicting_date_and_amount_do_not_create_reference_duplicate(self):
        source=row('one',900,100)
        result,_=combine_ai([source],{'transactions':[item(source,date='2025-01-02',debit=123)]},1)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['date'],'2025-01-01')
        self.assertEqual(result[0]['debit'],100)
        self.assertEqual(result[0]['_ai_proposals'][0]['status'],'rejected_alignment')

    def test_balances_misplaced_in_opposite_direction_are_not_promoted(self):
        source=row('one',900,100)
        result,_=combine_ai([source],{'transactions':[item(source,credit=900)]},1)
        self.assertIsNone(result[0]['credit'])
        self.assertEqual(result[0]['_ai_rejected_fields'][0]['field'],'credit')
        self.assertEqual(result[0]['_numeric_candidates'].get('credit',[]),[])
        self.assertIn('credit',result[0]['_ai_proposals'][0]['proposal'])

    def test_blank_direction_zero_never_becomes_estimated_money(self):
        source=row('one',900,100)
        result,_=combine_ai([source],{'transactions':[item(source,credit=0)]},1)
        self.assertIsNone(result[0]['credit'])
        self.assertNotIn('credit',result[0].get('_inferred_fields',{}))
        self.assertFalse(any('credit:' in text for text in result[0]['_issues']))

    def test_recovery_retains_first_ocr_baseline_and_all_proposals(self):
        source=row('one',900,None)
        first,_=combine_ai([source],{'transactions':[item(source,debit=100)]},1)
        baseline=copy.deepcopy(first[0]['_ocr_before_ai'])
        recovered,_=combine_ai(first,{'transactions':[item(source,debit=101)]},1)
        self.assertEqual(recovered[0]['_ocr_before_ai'],baseline)
        self.assertIsNone(baseline['debit'])
        self.assertEqual(len(recovered[0]['_ai_proposals']),2)
        self.assertEqual(recovered[0]['_inferred_fields']['debit']['original_value'],None)
        self.assertTrue(all(c['source']=='ai' for c in recovered[0]['_numeric_candidates']['debit']))

    def test_new_transaction_has_only_ai_source_provenance(self):
        proposal={'date':'2025-01-01','description':'UPI/NEW PERSON/777777','debit':50,'balance':950}
        result,_=combine_ai([],{'transactions':[proposal]},1)
        self.assertEqual(len(result),1);self.assertTrue(result[0]['_ai_only'])
        self.assertEqual(result[0]['_numeric_candidates']['debit'][0]['source'],'ai_only')
        infer_missing_amounts(result,1000)
        self.assertNotEqual(result[0].get('_field_sources',{}).get('balance'),'inferred')

    def test_missing_balances_do_not_form_estimate_chain(self):
        rows=[row('one',None,100),row('two',None,50),row('three',800,50)]
        infer_missing_amounts(rows,1000)
        self.assertEqual(rows[0]['balance'],900)
        self.assertIsNone(rows[1]['balance'])
        before=copy.deepcopy(rows)
        infer_missing_amounts(rows,1000)
        self.assertEqual(rows,before)

    def test_ai_balance_is_not_an_arithmetic_anchor(self):
        rows=[row('one',900,100),row('two',None,50)]
        rows[0]['_inferred_fields']={'balance':{'method':'local AI visual estimate','value':900}}
        infer_missing_amounts(rows,1000)
        self.assertIsNone(rows[1]['balance'])

    def test_two_sided_requires_independent_neighbors_and_single_direction(self):
        for inferred_neighbor,both_columns in [(True,False),(False,True)]:
            rows=[row('one',800,100,5 if both_columns else None),row('two',1100,None,200)]
            if inferred_neighbor:rows[1]['_inferred_fields']={'balance':{'method':'AI estimate'}}
            infer_two_sided_balances(rows,1000)
            self.assertEqual(rows[0]['balance'],800)

    def test_two_sided_provenance_stays_idempotent(self):
        rows=[row('one',850,100),row('two',1100,None,200)]
        infer_two_sided_balances(rows,1000)
        self.assertEqual(rows[0]['balance'],900)
        self.assertEqual(rows[0]['_inferred_fields']['balance']['original_value'],850)
        first=copy.deepcopy(rows)
        infer_two_sided_balances(rows,1000)
        self.assertEqual(rows,first)

    def test_descending_dates_infer_in_chronological_direction(self):
        rows=[row('two',None,50,date='2025-01-02'),row('one',900,100,date='2025-01-01')]
        rows[0]['_opening_balance']=1000
        infer_missing_amounts(rows,order='auto')
        self.assertEqual(rows[0]['balance'],850)
        self.assertEqual([r['id'] for r in rows],['two','one'])

    def test_never_crosses_page_or_explicit_statement_boundary(self):
        rows=[row('one',900,100),row('two',None,50,page=2)]
        infer_missing_amounts(rows,1000)
        self.assertIsNone(rows[1]['balance'])
        rows[1]['_opening_balance']=2000
        infer_missing_amounts(rows,1000)
        self.assertEqual(rows[1]['balance'],1950)

    def test_names_need_real_narration_anchor_and_never_replace_existing(self):
        for name in ('TRANSFER','UNKNOWN','ICIC0001234','A COMPLETELY INVENTED NAME'):
            source=row('one',900,100)
            result,_=combine_ai([source],{'transactions':[item(source,counterparty=name)]},1)
            self.assertNotIn('counterparty',result[0])
        source=row('one',900,100);source['counterparty']='Existing source name'
        result,_=combine_ai([source],{'transactions':[item(source,counterparty='RAJESH KUMAR')]},1)
        self.assertEqual(result[0]['counterparty'],'Existing source name')
        del source['counterparty']
        result,_=combine_ai([source],{'transactions':[item(source,counterparty='RAJESH KUMAR')]},1)
        self.assertEqual(result[0]['counterparty'],'RAJESH KUMAR')
        self.assertTrue(result[0]['_inferred_fields']['counterparty']['not_verified_identity'])

    def test_name_fragments_are_not_anchors(self):
        source=row('one',900,100,description='UPI/VIKRAM SHARMA/123456')
        result,_=combine_ai([source],{'transactions':[item(source,counterparty='RAM')]},1)
        self.assertNotIn('counterparty',result[0])


if __name__=='__main__':unittest.main()
