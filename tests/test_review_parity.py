"""Keep display triage and exported triage identical for synthetic evidence."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from review_checks import classify_review


@unittest.skipUnless(shutil.which('node'),'Node.js is required for display/export parity')
class ReviewParityTests(unittest.TestCase):
    def test_synthetic_evidence_has_identical_display_and_export_categories(self):
        base=dict(date='2025-04-01',debit=100,credit=0,balance=900,
                  original_description='TRANSFER',normalized_description='TRANSFER',
                  confidence=.95,suspicious=False)
        variants=[{},dict(confidence=.5),dict(suspicious=True),
                  dict(reason='weak OCR; compare numbers with source'),
                  dict(reason='OCR passes disagree or another pass missed this row'),
                  dict(reason='Description/name proposed by local AI; original OCR retained'),
                  dict(reason='Counterparty name is a local AI guess, not verified identity'),
                  dict(reason='Opening balance read by local AI; verify source'),
                  dict(reason='Local AI did not independently recover this OCR row'),
                  dict(reason='Alternative OCR numbers selected using balance evidence; verify source'),
                  dict(reason='unrecognized columns; calibrate table'),
                  dict(reason='AI-only transaction candidate; not independently confirmed'),
                  dict(reason='balance mismatch: expected 800.00, difference +100.00'),
                  dict(reason='opening balance unavailable; first row not reconciled'),
                  dict(reason='multiple values mapped to description'),
                  dict(reason='multiple values mapped to debit'),
                  dict(reason='Unknown numeric extraction warning'),
                  dict(normalized_description='',original_description=''),
                  dict(normalized_description='TRANSFER A',_ai_proposal={}),
                  dict(normalized_description='TRANSFER A',_ai_proposal={'description':'TRANSFER A'}),
                  dict(_inferred_fields={'counterparty':{'method':'guess'}})]
        for field in ('debit','credit','balance','date','opening_balance'):
            variants.extend([dict(_inferred_fields={field:{'method':'estimate'}}),
                             dict(reason=f'{field.replace("_"," ")}: filled by local AI estimate')])
        for field in ('debit','credit','balance'):
            variants.extend([dict(_numeric_candidates={field:[{'value':100},{'value':101}]}),
                             dict(_alternatives=[{field:None}]),
                             dict(_alternatives=[{field:123}]),
                             dict(reason=f'{field}: observed alternate selected by whole-statement balance chain')])
        for field in ('debit','credit','balance'):
            for value in (None,'',False,'bad','NaN','0x10',-1,0,'1,000.00'):
                variants.append({field:value})
        for value in (None,'2025-02-30','2024-02-29',' 2025-04-01','01/04/2025'):
            variants.append(dict(date=value))
        variants.extend([dict(_date_candidates=[{'value':'2025-04-02'}]),
                         dict(_engine='Manual transcription'),dict(_engine='Local GLM-OCR 8-bit AI'),
                         dict(credit=10),dict(debit=0,credit=0),
                         dict(credit=0,_issues=['credit: filled by local AI estimate'],_inferred_fields={'credit':{}})])
        rows=[{**base,**variant} for variant in variants]
        script="const {classify}=require('./static/evidence.js');let s='';process.stdin.on('data',b=>s+=b);process.stdin.on('end',()=>process.stdout.write(JSON.stringify(JSON.parse(s).map(classify))));"
        actual=json.loads(subprocess.run(['node','-e',script],input=json.dumps(rows),
                          text=True,capture_output=True,check=True,
                          cwd=Path(__file__).resolve().parents[1]).stdout)
        for row,result in zip(rows,actual):
            with self.subTest(row=row):self.assertEqual(result,classify_review(row))
        self.assertEqual(len(actual),len(rows))
