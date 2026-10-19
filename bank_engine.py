"""Bank table extraction from positioned OCR. No model, API, or fabricated numbers."""
from __future__ import annotations

import csv
import io
import os
import re
import statistics
import subprocess
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation

DATE_RE = re.compile(r"(?<!\d)(\d{1,2}[-/. ](?:\d{1,2}|[A-Za-z]{3,9})[-/. ]\d{2,4})(?!\d)")
AMOUNT_RE = re.compile(r"^\(?[-+]?\s*(?:₹|Rs\.?|INR)?\s*\d[\d,.]*[.,]\d{2}\)?(?:\s*(?:CR|DR))?$", re.I)
HEADERS = {
    'date': {'date', 'dt', 'txn.date', 'transactiondate'},
    'description': {'description', 'particulars', 'narration', 'details', 'remarks'},
    'cheque_number': {'cheque', 'chq', 'ref', 'reference', 'instrument'},
    'debit': {'debit', 'withdrawal', 'withdrawals', 'paid', 'withdraw'},
    'credit': {'credit', 'deposit', 'deposits', 'received'},
    'balance': {'balance', 'bal', 'closingbalance'},
    'amount': {'amount'},
}


def cents(value):
    if value is None or value == '':
        return None
    if isinstance(value, (int, float, Decimal)):
        return int((Decimal(str(value)) * 100).quantize(Decimal('1')))
    s = re.sub(r'(?i)₹|INR|RS\.?|CR|DR|,|\s', '', str(value))
    raw=re.sub(r'(?i)₹|INR|RS\.?|CR|DR|\s|[()\-+]','',str(value))
    pieces=re.split(r'[.,]',raw)
    if len(pieces)>1 and len(pieces[-1])==2 and all(p.isdigit() for p in pieces) and all(len(p) in (2,3) for p in pieces[1:-1]):
        s=('-' if str(value).strip().startswith('-') else '')+''.join(pieces[:-1])+'.'+pieces[-1]
    negative = str(value).strip().startswith('(') or str(value).strip().upper().endswith('DR')
    try:
        n = Decimal(s.strip('()'))
        if not n.is_finite():
            return None
        return int(((-abs(n) if negative else n) * 100).quantize(Decimal('1')))
    except InvalidOperation:
        return None


def parse_amount(value):
    n = cents(value)
    return n / 100 if n is not None else None


def normal_date(raw):
    value = re.sub(r'\s+', '-', raw.strip()).replace('/', '-').replace('.', '-')
    for fmt in ('%d-%m-%Y', '%d-%m-%y', '%d-%b-%Y', '%d-%b-%y', '%d-%B-%Y', '%d-%B-%y'):
        try:
            parsed=datetime.strptime(value, fmt).date()
            return parsed.isoformat() if 1900<=parsed.year<=2100 else None
        except ValueError:
            pass
    return None


def normalize(value):
    # Preserve meaningful UPI/NEFT/IMPS references; normalize whitespace only.
    return re.sub(r'\s+', ' ', value).strip()


def header_key(text):
    value=re.sub(r'\([^)]*\)','',text.lower())
    value=re.sub(r'[^a-z.]','',value).strip('.')
    # Printed variants: Chq.No., Debit(INR), Deposits(Cr.), Balance(Rs.).
    value={'chq.no':'chq','chequeno':'cheque','ref.no':'ref','value.date':'date','transaction.date':'date'}.get(value,value)
    for field,names in HEADERS.items():
        if value in names: return field
    return None


def header_groups(words):
    headers=[(w,header_key(w['text'])) for w in words if header_key(w['text'])]
    candidates=[]
    for anchor,key in headers:
        near=[(w,k) for w,k in headers if abs((w['y']+w['h']/2)-(anchor['y']+anchor['h']/2))<max(anchor['h']*2.5,65)]
        fields={k for w,k in near}
        if 'balance' in fields:
            candidates.append(near)
    return sorted(candidates,key=lambda g:len({k for w,k in g}),reverse=True)


def read_tsv(tsv):
    words = []
    for r in csv.DictReader(io.StringIO(tsv), delimiter='\t'):
        if r.get('level') != '5' or not r.get('text', '').strip():
            continue
        words.append({'text': r['text'].strip(), 'x': int(r['left']), 'y': int(r['top']),
                      'w': int(r['width']), 'h': int(r['height']), 'conf': max(0, float(r['conf'])) / 100})
    return words


def group_lines(words):
    """Regroup by vertical geometry; OCR block IDs can split a table into columns."""
    lines = []
    groups=header_groups(words);slope=0
    if groups and len({k for w,k in groups[0]})>=3:
        header=[w for w,k in groups[0]]
        slopes=[((b['y']+b['h']/2)-(a['y']+a['h']/2))/(b['x']-a['x']) for i,a in enumerate(header) for b in header[i+1:] if abs(b['x']-a['x'])>200]
        if slopes:slope=max(-.05,min(.05,statistics.median(slopes)))
    def baseline(w):return w['y']+w['h']/2-slope*w['x']
    for word in sorted(words, key=lambda w: (baseline(w), w['x'])):
        mid = baseline(word)
        found = next((l for l in reversed(lines[-4:]) if abs(l['mid'] - mid) < max(7, min(l['height'], word['h']) * .60)), None)
        if found is None:
            found = {'mid': mid, 'height': word['h'], 'words': []}
            lines.append(found)
        found['words'].append(word)
        found['mid'] = statistics.median(baseline(w) for w in found['words'])
    for l in lines:
        l['words'].sort(key=lambda w: w['x'])
        l['text'] = ' '.join(w['text'] for w in l['words'])
        l['top'] = min(w['y'] for w in l['words'])
        l['bottom'] = max(w['y'] + w['h'] for w in l['words'])
    return sorted(lines, key=lambda l: l['mid'])


def detect_columns(lines, width):
    groups=header_groups([w for l in lines for w in l['words']])
    if not groups:return {}
    cols={}
    for w,field in groups[0]:cols.setdefault(field,(w['x']+w['w']/2)/width)
    return cols


def is_statement(text, columns):
    text = text.lower()
    title = bool(re.search(r'(?:account|bank|transaction)\s+statement|statement\s+of\s+account|pass\s*book', text))
    structure = 'balance' in columns and any(k in columns for k in ('debit', 'credit', 'amount'))
    account = bool(re.search(r'account\s*(?:no|number|type)|ifsc|customer\s*id|a/c', text))
    return bool(structure and (title or account))


def extract_page(words, width, page, columns=None):
    lines = group_lines(words)
    detected = detect_columns(lines, width)
    cols = columns or detected
    rows, warnings, opening = [], [], None
    money_cols = {k: v for k, v in cols.items() if k in ('debit', 'credit', 'balance', 'amount')}
    for li, l in enumerate(lines):
        text = l['text']
        dates = [(w, normal_date(m.group(1))) for w in l['words'] if (m := DATE_RE.search(w['text']))]
        # Also support OCR splitting "01 Apr 2025" into three words.
        if not dates:
            match = DATE_RE.match(text)
            if match and normal_date(match.group(1)):
                dates = [(l['words'][0], normal_date(match.group(1)))]
        money = [w for w in l['words'] if AMOUNT_RE.match(w['text'])]
        # Some OCR engines split a printed CR/DR suffix into a separate word.
        for index,w in enumerate(l['words'][:-1]):
            suffix=l['words'][index+1]
            if w in money and not w['text'].upper().endswith(('CR','DR')) and suffix['text'].upper() in ('CR','DR') and suffix['x']-(w['x']+w['w'])<max(30,w['h']*2):
                w['text']+=' '+suffix['text'].upper()
        if re.search(r'opening\s+balance|(?:^|\s)b\s*/\s*f(?:\s|$)|balance\s+b/?f|brought\s+forward', text, re.I) and money:
            opening = parse_amount(money[-1]['text'])
            continue
        if not dates:
            # Wrapped narration: nearby, in description area, no amount or header/footer.
            if rows and not money and 0 < l['top'] - rows[-1]['_bottom'] < l['height'] * 2.4:
                desc_x = cols.get('description', .20)
                first_money = min(money_cols.values(), default=.65)
                if all(desc_x - .07 <= w['x'] / width < first_money - .04 for w in l['words']) and not re.search(r'total|page|statement|balance|date', text, re.I):
                    rows[-1]['original_description'] += '\n' + text
                    rows[-1]['normalized_description'] = normalize(rows[-1]['original_description'])
                    rows[-1]['_bottom'] = l['bottom']
                    rows[-1]['_ocr'] += [w['conf'] for w in l['words']]
            continue
        if dates[0][0]['x'] / width > .36:
            continue
        if not money:
            warnings.append(f'Page {page}: a dated line has no readable amount; inspect the full page for omissions.')
            continue
        row = {'id': str(uuid.uuid4()), 'date': dates[0][1], 'original_description': '',
               'normalized_description': '', 'cheque_number': '', 'debit': None, 'credit': None,
               'balance': None, 'page': page, 'confidence': 0, 'suspicious': True,
               'reason': '', 'state': 'review', 'crop': '', 'ord': len(rows),
               '_top': l['top'], '_bottom': l['bottom'], '_line': li,
               '_ocr': [w['conf'] for w in l['words']], '_issues': [], '_direction': 'unknown'}
        if row['date'] is None:row['_issues'].append('Date unreadable or outside supported years')
        assigned = set()
        if money_cols:
            for w in money:
                center = (w['x'] + w['w'] / 2) / width
                # Right-aligned numeric cells are accepted within half the nearest column spacing.
                field = min(money_cols, key=lambda k: abs(center - money_cols[k]))
                if abs(center - money_cols[field]) > .13:
                    continue
                value = parse_amount(w['text'])
                if value is None:continue
                if field == 'amount':
                    row['_amount'] = abs(value)
                    if w['text'].upper().endswith('CR'):
                        row['credit'] = abs(value); row['_direction'] = 'suffix'
                    elif w['text'].upper().endswith('DR'):
                        row['debit'] = abs(value); row['_direction'] = 'suffix'
                else:
                    if field in assigned:
                        row['_issues'].append('multiple values mapped to ' + field)
                    row[field] = abs(value) if field != 'balance' else value
                    row['_direction'] = 'columns'
                assigned.add(field)
        else:
            row['_issues'].append('unrecognized columns; calibrate table')
            if len(money) >= 2:
                row['balance'] = parse_amount(money[-1]['text'])
                row['_amount'] = abs(parse_amount(money[-2]['text']))
        first_number = min((w['x'] for w in money), default=width)
        date_matches = list(DATE_RE.finditer(text))
        date_end = date_matches[-1].end() if date_matches and date_matches[-1].start() < 26 else len(date_matches[0].group()) if date_matches else 0
        desc_words = [w for w in l['words'] if w['x'] < first_number]
        desc = ' '.join(w['text'] for w in desc_words)
        desc = DATE_RE.sub('', desc, count=2).strip(' |')
        row['original_description'] = desc
        row['normalized_description'] = normalize(desc)
        ref = re.search(r'(?i)(?:chq|cheque|ref|utr|rrn)[\s:/#.-]*([A-Z0-9-]{6,})', desc)
        if ref:
            row['cheque_number'] = ref.group(1)
        elif 'cheque_number' in cols:
            refs = [w['text'] for w in l['words'] if abs((w['x'] + w['w']/2)/width-cols['cheque_number'])<.045 and re.fullmatch(r'[A-Z0-9-]{6,}', w['text'],re.I)]
            row['cheque_number'] = ' '.join(refs)
        rows.append(row)
    for r in rows:
        mean = statistics.mean(r['_ocr']) if r['_ocr'] else 0
        numeric = [w['conf'] for l in lines if r['_top'] <= l['top'] <= r['_bottom'] for w in l['words'] if AMOUNT_RE.match(w['text'])]
        # Confidence is an OCR/structure quality score, not a calibrated probability.
        r['confidence'] = round(min(mean, min(numeric, default=0)), 3)
        if not cols:
            r['confidence'] = min(r['confidence'], .49)
    return rows, detected, warnings, opening


def reconcile(rows, opening=None, order='auto', infer=False):
    active = [r for r in rows if r.get('state') != 'deleted']
    descending = order == 'descending'
    if order == 'auto':
        dates = [r['date'] for r in active if r.get('date')]
        descending = len(dates)>1 and dates[0]>dates[-1]
    ordered = list(reversed(active)) if descending else active
    previous = cents(opening)
    for r in ordered:
        issues = list(r.get('_issues', []))
        if r.get('_opening_balance') is not None:previous=cents(r['_opening_balance'])
        if r.get('confidence', 0) < .80:
            issues.append('weak OCR; compare numbers with source')
        bal = cents(r.get('balance'))
        debit, credit = cents(r.get('debit')) or 0, cents(r.get('credit')) or 0
        amount = cents(r.get('_amount'))
        if infer and amount is not None and not debit and not credit and bal is not None and previous is not None:
            change = bal-previous
            if abs(change) == amount:
                r['credit' if change > 0 else 'debit'] = amount/100
                debit, credit = (amount,0) if change<0 else (0,amount)
                r['_direction'] = 'balance-supported'
            else:
                issues.append('amount direction unresolved')
        if bal is None:
            issues.append('balance unreadable')
        if debit and credit:
            issues.append('both debit and credit populated')
        if not debit and not credit:
            issues.append('transaction amount or direction unresolved')
        if previous is None:
            issues.append('opening balance unavailable; first row not reconciled')
        elif bal is not None:
            expected = previous-debit+credit
            if expected != bal:
                issues.append(f'balance mismatch: expected {expected/100:.2f}, difference {(bal-expected)/100:+.2f}')
        r['suspicious'] = bool(issues)
        r['reason'] = '; '.join(dict.fromkeys(issues))
        # An unreadable balance breaks continuity; do not bridge it invisibly.
        previous = bal
    return rows


def ocr(image, psm=6):
    model=os.environ.get('STATEMENT_TESSDATA')
    args=['--tessdata-dir',model] if model else []
    result = subprocess.run(['tesseract', str(image), 'stdout', *args, '-l', 'eng', '--oem','1','--psm', str(psm),
                             '-c', 'preserve_interword_spaces=1', '-c','tessedit_create_tsv=1'], capture_output=True, text=True,
                            check=True, timeout=300)
    if not result.stdout.startswith('level\tpage_num\t'):
        raise RuntimeError('Tesseract did not produce positioned OCR. '+result.stderr[-300:])
    return read_tsv(result.stdout)


def pass_score(rows):
    # Prefer recovered rows and readable numeric evidence, never balance-fitting invented digits.
    return sum(1 + r['confidence'] + .4 * (r['balance'] is not None) for r in rows)


def select_observed_candidates(rows, opening=None, order='auto'):
    """Only select numbers actually seen by an OCR pass, and always flag the switch."""
    dates=[r['date'] for r in rows if r.get('date')]
    reverse=order=='descending' or order=='auto' and len(dates)>1 and dates[0]>dates[-1]
    previous=cents(opening)
    for r in reversed(rows) if reverse else rows:
        bal=cents(r.get('balance'))
        debit,credit=cents(r.get('debit')) or 0,cents(r.get('credit')) or 0
        if previous is not None and bal is not None and previous-debit+credit!=bal:
            supported={}
            for candidate in r.get('_alternatives',[]):
                cb=cents(candidate.get('balance')); cd=cents(candidate.get('debit')) or 0; cc=cents(candidate.get('credit')) or 0
                if cb is not None and previous-cd+cc==cb and (cd or cc) and not (cd and cc):
                    supported[(cd,cc,cb)]=candidate
            if len(supported)==1:
                chosen=next(iter(supported.values()))
                for k in ('debit','credit','balance'): r[k]=chosen.get(k)
                r['_issues'].append('Alternative OCR numbers selected using balance evidence; verify source')
                r['_numeric_engine']=chosen['_engine']
                r['confidence']=min(r['confidence'],chosen['confidence'])
        previous=cents(r.get('balance'))
    return rows


def extract_records(text, page, words=None):
    """Compatibility helper for plain-text fixtures; real extraction always uses word geometry."""
    if words:
        return extract_page(words, max(w['x']+w['w'] for w in words), page)[0]
    synthetic=[]
    for y,line in enumerate(text.splitlines()):
        x=0
        for token in line.split():
            synthetic.append({'text':token,'x':x,'y':y*30,'w':len(token)*10,'h':20,'conf':.9}); x+=len(token)*10+20
    return extract_page(synthetic, 1400, page)[0]
