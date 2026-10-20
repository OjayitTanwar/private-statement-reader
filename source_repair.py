"""Repair existing transaction numbers from agreement between saved OCR passes."""
import copy
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image
from bank_engine import cents, extract_page, reconcile
from review_checks import valid_date

FIELDS=('debit','credit','balance')
PASSES=('tesseract','apple','tesseract-enhanced')


def _tuple(row):
    return (cents(row.get('debit')) or 0,cents(row.get('credit')) or 0,cents(row.get('balance')))


def _valid(row):
    d,c,b=_tuple(row)
    return bool(valid_date(row.get('date')) and b is not None and bool(d)!=bool(c)
                and d>=0 and c>=0 and not any('multiple values mapped to '+field in issue
                    for issue in row.get('_issues',[]) for field in FIELDS))


def _grade(rows,opening,order):
    checked=copy.deepcopy(rows)
    for row in checked:
        row['_issues']=[issue for issue in row.get('_issues',[]) if not any(term in issue.lower() for term in
                       ('balance mismatch','both debit and credit populated'))]
    reconcile(checked,opening=opening,order=order,infer=False)
    active=[row for row in checked if row.get('state')!='deleted']
    return (sum('balance mismatch' in row.get('reason','') for row in active),
            sum(bool(cents(row.get('debit'))) and bool(cents(row.get('credit'))) for row in active))


def repair_cached_rows(rows,folder,columns_by_page=None,opening=None,order='auto',protected_ids=()):
    """Return repaired copies and counts, without changing source rows or files."""
    originals=copy.deepcopy(rows);result=copy.deepcopy(rows);folder=Path(folder)
    protected=set(protected_ids);columns_by_page=columns_by_page or {}
    report={'candidate_changes':0,'applied_rows':0,'rollback':False,'sourcepasses':0,'skippedprotected':0,
            'rejected_changes':0,'partial_repair':False}
    pages=defaultdict(list)
    for row in result:pages[row.get('page')].append(row)
    parsed={}
    for page in pages:
        if not isinstance(page,int) or page<1:continue
        image=folder/f'page-{page}.jpg'
        try:
            with Image.open(image) as im:width=im.width
        except (OSError,ValueError):continue
        sources=[]
        for engine in PASSES:
            try:
                words=json.loads((folder/f'ocr-{page}-{engine}.json').read_text())
                if not isinstance(words,list) or not words:continue
                records,columns,_,_=extract_page(words,width,page)
                if 'balance' not in columns or not any(key in columns for key in ('debit','credit','amount')):
                    fallback=columns_by_page.get(page,columns_by_page.get(str(page)))
                    if fallback:records,_,_,_=extract_page(words,width,page,columns=fallback)
                sources.append((engine,[row for row in records if _valid(row)]))
                report['sourcepasses']+=1
            except (OSError,ValueError,TypeError,KeyError,AttributeError):continue
        parsed[page]=sources
    changed=[]
    for index,row in enumerate(result):
        if row.get('state')=='deleted':continue
        if row.get('_cell_evidence') or row.get('id') in protected or row.get('state') in ('approved','corrected') or row.get('_engine')=='Manual transcription' or any(row.get(key) for key in ('_manual_fields','_edited_fields','_manual','_corrected','_edited')):
            report['skippedprotected']+=1;continue
        if row.get('_top') is None or not valid_date(row.get('date')):continue
        matches=[]
        for engine,records in parsed.get(row.get('page'),[]):
            near=[]
            for candidate in records:
                if candidate['date']!=row['date']:continue
                sourceheight=max(1,candidate.get('_bottom',candidate['_top']+20)-candidate['_top'])
                rowheight=max(1,row.get('_bottom',row['_top']+20)-row['_top'])
                distance=abs(candidate['_top']-row['_top'])
                if distance<=min(12,min(sourceheight,rowheight)*.65):near.append((distance,candidate))
            near.sort(key=lambda item:item[0])
            if not near or len(near)>1 and abs(near[0][0]-near[1][0])<.001:continue
            matches.append((engine,near[0][1]))
        counts=Counter(_tuple(candidate) for _,candidate in matches)
        if not counts:continue
        high=max(counts.values());leaders=[values for values,count in counts.items() if count==high]
        if high<2 or len(leaders)!=1 or leaders[0]==_tuple(row):continue
        consensus=leaders[0];support=[(engine,item) for engine,item in matches if _tuple(item)==consensus]
        families={'apple' if engine=='apple' else 'tesseract' for engine,_ in support}
        if len(families)<2:continue
        changedfields=[key for key in FIELDS if (cents(row.get(key)) or 0 if key!='balance' else cents(row.get(key)))!=consensus[FIELDS.index(key)]]
        before={key:row.get(key) for key in FIELDS}
        audit={'before':before,'after':{key:support[0][1].get(key) for key in FIELDS},
               'changed_fields':changedfields,'confirmed_fields':list(FIELDS),
               'original_issues':copy.deepcopy(row.get('_issues',[])),'original_reason':row.get('reason',''),
               'original_inferred_fields':copy.deepcopy(row.get('_inferred_fields',{})),
               'original_numeric_candidates':copy.deepcopy(row.get('_numeric_candidates',{})),
               'original_alternatives':copy.deepcopy(row.get('_alternatives',[])),
               'original_ocr_before_ai':copy.deepcopy(row.get('_ocr_before_ai',{})),
               'original_provenance':copy.deepcopy({key:value for key,value in row.items() if key.startswith('_') and key!='_source_repairs'}),
               'support':[{'engine':engine,'top':item['_top'],'bottom':item['_bottom'],
                           'fields':copy.deepcopy(item.get('_field_evidence',{}))} for engine,item in support],
               'method':'agreement between independent saved OCR passes'}
        row.setdefault('_source_repairs',[]).append(audit)
        for key in FIELDS:
            row[key]=support[0][1].get(key)
            row.setdefault('_field_sources',{})[key]='ocr'
        for key in FIELDS:row.get('_inferred_fields',{}).pop(key,None)
        row['_issues']=[issue for issue in row.get('_issues',[]) if not any(
            re.match(r'(?i)^'+key+r'\s*:.*(?:filled by local ai|inferred|estimate)',issue)
            or key=='balance' and re.match(r'(?i)^balance estimated',issue) for key in FIELDS)]
        row['_numeric_candidates']={key:[] for key in FIELDS}
        row['_alternatives']=[{**{key:candidate.get(key) for key in FIELDS},'source':'ocr','_engine':engine,
                              'confidence':candidate.get('confidence',0)} for engine,candidate in matches]
        for engine,candidate in matches:
            for key in FIELDS:
                if candidate.get(key) is None:continue
                evidence=candidate.get('_field_evidence',{}).get(key,[])
                row['_numeric_candidates'][key].append({'value':candidate[key],'source':'ocr','engine':engine,
                    'confidence':candidate.get('confidence',0),'evidence':copy.deepcopy(evidence)})
        row['_numeric_engine']='Saved OCR pass consensus';changed.append(index)
    report['candidate_changes']=len(changed)
    if changed:
        before=_grade(originals,opening,order);after=_grade(result,opening,order)
        if after[0]>before[0] or after[1]>before[1]:
            # One problematic source reading must not block unrelated repairs.
            # Try each page once, then each rejected page's candidates once.
            accepted=copy.deepcopy(originals);accepted_grade=before;accepted_indices=[]
            by_page=defaultdict(list)
            for index in changed:by_page[result[index].get('page')].append(index)
            for indices in by_page.values():
                trial=copy.deepcopy(accepted)
                for index in indices:trial[index]=copy.deepcopy(result[index])
                grade=_grade(trial,opening,order)
                if grade[0]<=accepted_grade[0] and grade[1]<=accepted_grade[1]:
                    accepted=trial;accepted_grade=grade;accepted_indices.extend(indices);continue
                for index in indices:
                    trial=copy.deepcopy(accepted);trial[index]=copy.deepcopy(result[index])
                    grade=_grade(trial,opening,order)
                    if grade[0]<=accepted_grade[0] and grade[1]<=accepted_grade[1]:
                        accepted=trial;accepted_grade=grade;accepted_indices.append(index)
            report['applied_rows']=len(accepted_indices)
            report['rejected_changes']=len(changed)-len(accepted_indices)
            report['rollback']=not bool(accepted_indices)
            report['partial_repair']=0<len(accepted_indices)<len(changed)
            return accepted,report
    report['applied_rows']=len(changed)
    return result,report
