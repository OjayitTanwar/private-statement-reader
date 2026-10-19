"""Classify review work without changing extracted evidence or original flags."""
from datetime import date
from decimal import Decimal, InvalidOperation
import re

NUMERIC=('debit','credit','balance')
ESTIMATED_FIELDS=(*NUMERIC,'date','opening_balance')


def number(value):
    if value is None or isinstance(value,bool) or str(value).strip()=='':return None
    try:
        result=Decimal(str(value).replace(',','').strip())
        return result if result.is_finite() else None
    except (InvalidOperation,ValueError):return None


def valid_date(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):return False
    try:return 1900<=date.fromisoformat(value).year<=2100
    except ValueError:return False


def classify_review(row):
    financial=[];estimated=[];information=[]
    def add(target,value):
        if value not in target:target.append(value)
    if not valid_date(row.get('date')):add(financial,'Transaction date missing or invalid')
    values={key:number(row.get(key)) for key in NUMERIC}
    def inactive_zero(key):
        return key in ('debit','credit') and values[key]==0 and bool(values['credit' if key=='debit' else 'debit'])
    if values['balance'] is None:add(financial,'Running balance missing or malformed')
    for key in ('debit','credit'):
        if row.get(key) is not None and str(row[key]).strip() and values[key] is None:add(financial,f'{key.title()} malformed')
        if values[key] is not None and values[key]<0:add(financial,f'{key.title()} is negative')
    if values['debit'] and values['credit']:add(financial,'Both debit and credit populated')
    if not values['debit'] and not values['credit']:add(financial,'Transaction amount or direction unresolved')
    for key in NUMERIC:
        candidates=[item.get('value') for item in (row.get('_numeric_candidates',{}).get(key) or []) if isinstance(item,dict)]
        candidates.extend(item.get(key) for item in (row.get('_alternatives') or []) if isinstance(item,dict))
        observed={value for candidate in [row.get(key),*candidates] if (value:=number(candidate)) is not None}
        if len(observed)>1:add(financial,f'Conflicting {key} readings')
    dates=[item.get('value') if isinstance(item,dict) else item for item in (row.get('_date_candidates') or [])]
    dates.extend(item.get('date') for item in (row.get('_alternatives') or []) if isinstance(item,dict))
    if len({value for value in [row.get('date'),*dates] if value is not None and str(value).strip()})>1:
        add(financial,'Conflicting date readings')
    inferred=row.get('_inferred_fields') or {}
    for key in inferred:
        if inactive_zero(key):add(information,f'Blank {key} column read as zero')
        elif key in ESTIMATED_FIELDS:add(estimated,key)
        else:add(information,f'{key.replace("_"," ").title()} includes a guess')
    raw=[*(row.get('_issues') or []),row.get('reason') or '']
    fragments=list(dict.fromkeys(part.strip() for text in raw for part in str(text).split(';') if part.strip()))
    for fragment in fragments:
        text=fragment.lower()
        estimate=re.search(r'\b(debit|credit|balance|date|opening balance)\b.*\b(inferred|estimated|estimate|proposed|filled by local ai)\b',text)
        if estimate:
            field='opening_balance' if 'opening balance' in text else estimate.group(1)
            if inactive_zero(field):add(information,fragment)
            else:add(estimated,field)
            continue
        if 'opening balance read by local ai' in text:
            add(estimated,'opening_balance');continue
        strong=any(term in text for term in ('balance mismatch','unreadable','unrecognized columns','ai-only',
                    'not independently confirmed','manual transcription','direction unresolved','first row not reconciled',
                    'opening balance unavailable','both debit and credit','multiple values mapped','date invalid','date missing'))
        # A missing description is informational; missing monetary/date fields are not.
        description=any(term in text for term in ('description','narration','counterparty','name proposed','name is a','identity'))
        info=any(term in text for term in ('description/name proposed by local ai','counterparty name','name is a local ai guess',
                    'not verified identity','identity is not verified','description missing','missing description','description unreadable',
                    'multiple values mapped to description','weak ocr','compare numbers with source','original ocr retained',
                    'original reading retained','ocr passes disagree or another pass missed this row',
                    'local ai did not independently recover this ocr row','alternative ocr numbers selected using balance evidence',
                    'observed alternate selected by whole-statement balance chain',
                    )) or text in ('verify source','original ocr retained','original reading retained')
        if strong and not (description and ('unreadable' in text or 'multiple values mapped to description' in text)):add(financial,fragment)
        elif info:add(information,fragment)
        else:add(financial,fragment)
    if row.get('_engine')=='Manual transcription':add(financial,'Manual transcription requires source confirmation')
    if row.get('_engine')=='Local GLM-OCR 8-bit AI':add(financial,'AI-only transaction candidate')
    confidence=number(row.get('confidence'))
    if confidence is not None and confidence<Decimal('.80'):add(information,'Low aggregate OCR confidence')
    if row.get('_ai_proposal') and row.get('normalized_description')!=row.get('original_description'):
        add(information,'Description includes an AI reading')
    if not str(row.get('normalized_description') or row.get('original_description') or '').strip():
        add(information,'Description missing')
    if row.get('suspicious') and not (financial or estimated or information):
        add(financial,'Legacy suspicious flag has no explanation')
    category='financial_error' if financial else 'estimated_value' if estimated else 'information' if information else 'none'
    return {'category':category,'review_required':category in ('financial_error','estimated_value'),
            'financial_reasons':financial,'estimated_fields':estimated,'information':information,
            'priority':{'financial_error':0,'estimated_value':1,'information':2,'none':3}[category]}
