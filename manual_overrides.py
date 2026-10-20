"""Restore explicit reviewer choices after extraction without fuzzy matching."""
import copy
from review_checks import valid_date

EDITABLE=('date','normalized_description','counterparty','cheque_number','debit','credit','balance','state')


def apply_manual_overrides(rows,state):
    result=copy.deepcopy(rows);used=set()
    report={'applied_rows':0,'appended_manual_rows':0,'unmatched_overrides':0,'unmatched_details':[]}
    for override in state.get('manual_overrides',[]):
        original=override.get('original_row') or {}
        manual=original.get('_engine')=='Manual transcription'
        allowed=(*EDITABLE,'original_description') if manual else EDITABLE
        fields=[key for key in allowed if key in override.get('fields',[]) and key in original]
        if not fields:continue
        candidates=[index for index,row in enumerate(result) if index not in used and original.get('id') and row.get('id')==original['id']]
        match=candidates[0] if len(candidates)==1 else None;method='original row identity'
        ambiguous=len(candidates)>1
        rejected=[]
        if match is None and not ambiguous and original.get('_top') is not None:
            near=[]
            for index,row in enumerate(result):
                if index in used or row.get('page')!=original.get('page') or row.get('_top') is None:continue
                if bool(row.get('_cell_evidence'))!=bool(original.get('_cell_evidence')):
                    rejected.append('Source coordinate frames differ');continue
                if 'date' in fields:
                    if not original.get('original_description') or row.get('original_description')!=original['original_description']:
                        rejected.append('Edited date requires exact original narration');continue
                elif valid_date(original.get('date')) and valid_date(row.get('date')) and row['date']!=original['date']:
                    rejected.append('Source dates differ');continue
                try:distance=abs(float(row['_top'])-float(original['_top']))
                except (ValueError,TypeError):continue
                if distance<=12:near.append((distance,index))
            near.sort()
            if near:
                ambiguous=len(near)>1 and abs(near[0][0]-near[1][0])<=2
                if ambiguous:rejected.append('Source positions are ambiguous within two pixels')
                if not ambiguous:match=near[0][1];method='unique source position'
        if match is None and not ambiguous and original.get('original_description') and original.get('date'):
            exact=[index for index,row in enumerate(result) if index not in used and row.get('page')==original.get('page')
                   and row.get('original_description')==original['original_description'] and row.get('date')==original['date']]
            if len(exact)==1:match=exact[0];method='exact source narration and date'
        if match is None:
            if not manual:
                report['unmatched_overrides']+=1
                report['unmatched_details'].append({'original_id':original.get('id'),
                    'reasons':list(dict.fromkeys(rejected)) or ['No unique source row matched']})
                continue
            # Explicitly added rows are user evidence, even without an OCR match.
            result.append(copy.deepcopy(original));match=len(result)-1;method='preserved manual transcription'
            report['appended_manual_rows']+=1
        row=result[match];used.add(match)
        audit={'fields':fields,'before':{key:copy.deepcopy(row.get(key)) for key in fields},
               'original_id':original.get('id'),'method':method}
        row.setdefault('_manual_override_applied',[]).append(audit)
        for key in fields:
            row[key]=copy.deepcopy(original[key]);row.setdefault('_field_sources',{})[key]='manual'
            row.get('_inferred_fields',{}).pop(key,None)
        row['_manual_fields']=list(dict.fromkeys([*(row.get('_manual_fields') or []),*fields]))
        report['applied_rows']+=1
    return result,report
