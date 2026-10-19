"""Local spelling/phonetic suggestions, never identity verification or web lookup."""
import difflib
import re
import unicodedata
from collections import Counter,defaultdict


def clean(value):
    return re.sub(r'[^a-z ]', ' ', unicodedata.normalize('NFKD',value).encode('ascii','ignore').decode().lower())


def phonetic(value):
    value = re.sub(r'[^a-z]', '', clean(value))
    if not value:
        return ''
    groups = {**dict.fromkeys('bfpv','1'), **dict.fromkeys('cgjkqsxz','2'),
              **dict.fromkeys('dt','3'), **dict.fromkeys('l','4'),
              **dict.fromkeys('mn','5'), **dict.fromkeys('r','6')}
    out, previous = value[0], groups.get(value[0], '')
    for c in value[1:]:
        code = groups.get(c, '')
        if code and code != previous:
            out += code
        previous=code
    return (out+'000')[:4]


def suggestions(description, people):
    tokens=[t for t in clean(description).split() if len(t)>2 and t not in {'upi','imps','neft','transfer','payment','bank','credit','debit','from','ref','chq','cheque','pos','inr'}]
    hits=[]
    for person in people:
        best=0; method='spelling'
        for alias in [person['name'], *person.get('aliases', [])]:
            target=clean(alias).split()
            if not target: continue
            for start in range(len(tokens)):
                candidate=' '.join(tokens[start:start+len(target)])
                score=difflib.SequenceMatcher(None, candidate, ' '.join(target)).ratio()
                sounds=len(target)==len(tokens[start:start+len(target)]) and all(phonetic(a)==phonetic(b) for a,b in zip(tokens[start:start+len(target)],target))
                if sounds and score<.85:
                    score=max(score,.80)
                if score>best:
                    best=score; method='exact text' if score==1 else 'sound + spelling' if sounds else 'similar spelling'
        if best>=.70:
            hits.append({'name':person['name'],'score':round(best,2),'basis':method,'note':'Possible text match, not verified identity'})
    return sorted(hits,key=lambda x:x['score'],reverse=True)[:3]


_NON_NAMES={'TRFR','TRANSFER','TO','FROM','NEFT','RTGS','UPI','IMPS','DR','CR','DEBIT','CREDIT',
            'RECEIVED','PAYMENT','PAYMENTS','TRANSACTION','SUCCESS','SUCCESSFUL','RETURN','REVERSAL',
            'SELF','REF','REFERENCE','UTR','INR','INDIA','ACCOUNT','AC','NA','NIL','NOT AVAILABLE',
            'BANK','ICICI','ICIC','HDFC','HDFCBANK','SBI','SBIN','AXIS','AXISBANK','UTIB','YESBANK',
            'IDFC','KOTAK','KKBK','PUNB','BARB','CNRB','BKID','SBM','FDRL','IOBA','IDIB'}


def _name_part(part):
    """Keep explicit narration names; reject references, VPAs, bank codes and prose."""
    part=re.sub(r'\s+',' ',part).strip(' .:-')
    part=re.sub(r'^(?:M/S\.?\s*|MR\.?\s+|MRS\.?\s+|MS\.?\s+)','',part,flags=re.I)
    if not 3<=len(part)<=100 or '@' in part:return None
    if re.search(r'[^A-Za-z0-9 &.\-\'’]',part):return None
    if any(token.upper() in _NON_NAMES for token in part.split()):return None
    if re.search(r'\d{2,}',part) or len(re.findall(r'\d',part))>2:return None
    # Only digit shapes commonly mistaken for letters can remain in a name guess.
    if re.search(r'[2346789]',part) or not re.search(r'[A-Za-z]{3}',part):return None
    if re.fullmatch(r'[A-Z]{4}[01][A-Z0-9]{6}',part.upper()):return None
    return part


def _explicit_counterparty(description):
    text=re.sub(r'\s+',' ',str(description or '')).strip()
    direct=re.search(r'\b(?:TRFR|TRANSFER)\s+(?:TO|FROM)\b\s*[:/-]?\s*(.+)',text,re.I)
    if direct:
        narration=re.sub(r'^M\s*/\s*S\.?\s*','',direct.group(1),flags=re.I)
        source=re.split(r'[/|;]|\b(?:REF|UTR|A/C|ACCOUNT|CHQ|CHEQUE)\b|\d{3,}',narration,maxsplit=1,flags=re.I)[0].strip()
        name=_name_part(source)
        if name:return name,source,'explicit transfer narration'
    # Restrict these formats to actual slash-separated payment narrations.
    payment=re.search(r'\b(NEFT|RTGS|UPI)\s*/(.+)',text,re.I)
    if not payment:return None
    parts=[part.strip() for part in payment.group(2).split('/') if part.strip()]
    if payment.group(1).upper() in {'NEFT','RTGS'}:
        if parts:
            name=_name_part(parts[-1])
            if name:return name,parts[-1],'terminal NEFT/RTGS narration name'
    else:
        candidates=[(name,part) for part in parts if (name:=_name_part(part))]
        # Name is normally followed by the routing bank and VPA. If routing tokens
        # cannot be distinguished from names, retain the explicit candidate list.
        if candidates:
            name,source=candidates[0]
            return name,source,'UPI narration name segment'
    return None


def _name_key(value):
    return ' '.join(value.upper().split())


def _ocr_name_key(value):
    return _name_key(value).translate(str.maketrans({'0':'O','1':'I','5':'S'}))


def automatic_counterparties(rows,reference_rows=()):
    """Fill source-backed name guesses locally; return the same list of rows.

    Recurring spellings can repair only explicit OCR digit/letter confusions.
    Similar genuine spellings remain separate, with suggestions as provenance.
    """
    support=Counter();display={};seen=set()
    for row in [*reference_rows,*rows]:
        identity=('id',row['id']) if row.get('id') else ('object',id(row))
        if identity in seen:continue
        seen.add(identity)
        extracted=_explicit_counterparty(row.get('original_description') or row.get('normalized_description'))
        if not extracted:continue
        name,_,_=extracted
        if re.search(r'\d',name) or float(row.get('confidence') or 0)<.85:continue
        key=_name_key(name);support[key]+=1;display.setdefault(key,name)
    supported_by_sound=defaultdict(list)
    for target,count in support.items():
        if count>=2:supported_by_sound[tuple(phonetic(token) for token in clean(target).split())].append((target,count))
    for row in rows:
        if row.get('counterparty'):continue
        extracted=_explicit_counterparty(row.get('original_description') or row.get('normalized_description'))
        if not extracted:continue
        name,source,method=extracted;key=_name_key(name);canonical=None;candidates=[]
        left=clean(_ocr_name_key(name)).split();sound_key=tuple(phonetic(token) for token in left)
        for target,count in supported_by_sound.get(sound_key,[]):
            if target==key:continue
            score=difflib.SequenceMatcher(None,_ocr_name_key(name),target).ratio()
            if score<.92:continue
            candidates.append({'name':display[target],'score':round(score,3),'support':count,
                               'basis':'recurring source spelling and matching sound','not_verified_identity':True})
            # Never automatically merge AJAY/AJAI or similarly named people.
            # A literal OCR-confusion digit plus an otherwise exact full spelling
            # gives a narrower, auditable correction than phonetics alone.
            if re.search(r'[015]',name) and _ocr_name_key(name)==target and len(left)>=2 and len(target)>=8:
                canonical=display[target]
        if len(candidates)>1:canonical=None
        row['counterparty']=canonical or name
        row.setdefault('_inferred_fields',{})['counterparty']={
            'method':method+(' with recurring source OCR spelling repair' if canonical else ''),
            'source_text':source,'value':row['counterparty'],'not_verified_identity':True}
        if candidates:row['_counterparty_candidates']=sorted(candidates,key=lambda item:(-item['score'],-item['support']))[:3]
    return rows
