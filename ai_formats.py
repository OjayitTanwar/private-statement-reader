"""Parse local model output without executing text or guessing table alignment."""
import json
import math
import re
from datetime import date
from html.parser import HTMLParser

from bank_engine import amount_sign_conflict, cents, normal_date


def _span(value):
    try:
        return max(1, min(100, int(value)))
    except (TypeError, ValueError):
        return 1


def _grid(rows):
    """Expand only explicit HTML spans; absent cells are never synthesized."""
    grid = []
    occupied = {}
    for y, cells in enumerate(rows):
        x = 0
        for cell in cells:
            while (y, x) in occupied:
                x += 1
            for dy in range(cell['rowspan']):
                for dx in range(cell['colspan']):
                    occupied[y + dy, x + dx] = cell['text']
            x += cell['colspan']
        indexes = [column for row, column in occupied if row == y]
        width = max(indexes, default=-1) + 1
        # None denotes an actual hole, unlike a printed empty <td>.
        grid.append([occupied.get((y, column)) for column in range(width)])
    return grid


class Tables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self.table = self.row = self.cell = None
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.depth += 1
            if self.depth == 1:
                self.table = []
        elif self.depth == 1 and tag == 'tr':
            self.row = []
        elif self.depth == 1 and tag in ('td', 'th'):
            attributes = dict(attrs)
            self.cell = {'parts': [], 'colspan': _span(attributes.get('colspan', 1)),
                         'rowspan': _span(attributes.get('rowspan', 1))}
        elif tag == 'br' and self.cell is not None:
            self.cell['parts'].append(' ')

    def handle_data(self, data):
        if self.cell is not None and self.depth == 1:
            self.cell['parts'].append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None and self.depth == 1:
            if self.row is not None:
                self.row.append({'text': ' '.join(''.join(self.cell['parts']).split()),
                                 'colspan': self.cell['colspan'], 'rowspan': self.cell['rowspan']})
            self.cell = None
        elif tag == 'tr' and self.row is not None and self.depth == 1:
            self.table.append(self.row)
            self.row = None
        elif tag == 'table':
            if self.depth == 1 and self.table is not None:
                self.tables.append(_grid(self.table))
                self.table = None
            self.depth = max(0, self.depth - 1)


def _field(label):
    value = re.sub('[^a-z]', '', str(label or '').lower())
    if value in ('drcr', 'crdr', 'debitcredit', 'creditdebit', 'type', 'transactiontype'):
        return 'direction'
    if value.startswith(('closingbalance', 'runningbalance', 'availablebalance', 'ledgerbalance', 'bookbalance', 'balance')):
        return 'balance'
    if value in ('dr', 'withdrawalamount') or value.startswith(('withdraw', 'debit')):
        return 'debit'
    if value == 'cr' or value.startswith(('deposit', 'credit')):
        return 'credit'
    if value.startswith(('date', 'txndate', 'transactiondate', 'generalledgerdate', 'ledgerdate', 'postingdate', 'valuedate', 'gldate')):
        return 'date'
    if value.startswith(('particular', 'narration', 'description', 'details', 'transactionremarks', 'transactiondetails', 'remarks')):
        return 'description'
    if value.startswith(('chq', 'cheque', 'reference', 'instrument')):
        return 'cheque_number'
    if value.startswith(('amount', 'transactionamount', 'drcramount', 'crdramount')):
        return 'amount'
    return None


def _valid_date(value):
    value = str(value or '').strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        try:
            parsed = date.fromisoformat(value)
            return value if 1900 <= parsed.year <= 2100 else None
        except ValueError:
            return None
    return normal_date(value)


def _headers(table):
    best, header_index, expected = {}, -1, 0
    paths = []
    for index, cells in enumerate(table):
        if any(_valid_date(cell) for cell in cells):
            break
        if sum(_field(cell) is not None for cell in cells) < 2:
            continue
        while len(paths) < len(cells):
            paths.append([])
        fields = {}
        for column, cell in enumerate(cells):
            if cell and cell not in paths[column]:
                paths[column].append(cell)
            field = _field(cell)
            parent_balance = any(_field(value) == 'balance' for value in paths[column][:-1])
            if parent_balance and field == 'direction':
                field = 'balance_direction'
            elif parent_balance and field == 'amount':
                field = 'balance'
            if field is None:
                field = next((_field(value) for value in reversed(paths[column]) if _field(value)), None)
            if field:
                fields.setdefault(field, column)
        if 'direction' in fields and 'amount' not in fields and fields.get('balance', 999) < fields['direction']:
            fields['balance_direction'] = fields.pop('direction')
        if 'date' in fields and 'balance' in fields and any(key in fields for key in ('debit', 'credit', 'amount')):
            if len(fields) > len(best):
                best, header_index, expected = fields, index, len(cells)
    return best, header_index, expected


def _direction(value):
    normalized = re.sub('[^a-z]', '', str(value or '').lower())
    if normalized in ('dr', 'debit', 'withdrawal', 'withdrawals'):
        return 'DR'
    if normalized in ('cr', 'credit', 'deposit', 'deposits'):
        return 'CR'
    return None


def _markers(value):
    raw = str(value or '').strip()
    return {match.group(1).upper() for pattern in (r'^\s*(CR|DR)\.?(?=\s|[₹\d()+-])', r'(?<=[\d)\s])(CR|DR)\.?\s*$')
            if (match := re.search(pattern, raw, re.I))}


def _positive(value):
    """Retain displayed digits/separators while direction comes from its column."""
    raw = str(value or '').strip()
    raw = re.sub(r'^\s*(?:CR|DR)\.?(?=\s|[₹\d()+-])\s*|\s*(?<=[\d)\s])(?:CR|DR)\.?\s*$', '', raw, flags=re.I)
    raw = raw.strip().strip('()').strip()
    currency = re.match(r'^(₹|Rs\.?|INR)\s*', raw, re.I)
    if currency:
        return currency.group(0) + raw[currency.end():].strip().strip('()').lstrip('+-').strip()
    return raw.lstrip('+-').strip()


def _convert(values):
    values = dict(values)
    for key, marker in (('debit', 'DR'), ('credit', 'CR')):
        raw = values.get(key, '')
        if raw and raw not in ('-', '—', '–'):
            if cents(raw) is None:
                raise ValueError(f'{key} is not a readable amount')
            if amount_sign_conflict(raw):
                raise ValueError(f'{key} amount has conflicting sign and Cr marker')
            if _markers(raw) - {marker}:
                raise ValueError(f'{key} column conflicts with its Cr/Dr marker')
            values[key] = _positive(raw)
        elif key in values:
            values[key] = ''
    amount = values.get('amount', '')
    if amount:
        if cents(amount) is None:
            raise ValueError('Amount is not readable')
        if amount_sign_conflict(amount):
            raise ValueError('Amount has conflicting sign and Cr marker')
        markers = _markers(amount)
        direction = _direction(values.get('direction'))
        if direction:
            markers.add(direction)
        if len(markers) > 1:
            raise ValueError('Amount direction markers disagree')
        if markers:
            key = 'debit' if markers.pop() == 'DR' else 'credit'
            values[key] = _positive(amount)
    balance = values.get('balance', '')
    marker = _direction(values.get('balance_direction'))
    if marker:
        if _markers(balance) - {marker}:
            raise ValueError('Balance direction markers disagree')
        if not _markers(balance):
            balance = str(balance).strip() + ' ' + marker
            values['balance'] = balance
    if balance and cents(balance) is None:
        raise ValueError('Balance is not readable')
    if balance and amount_sign_conflict(balance):
        raise ValueError('Balance has conflicting sign and Cr marker')
    return values


def parse_table(raw):
    parser = Tables()
    parser.feed(raw)
    tables = parser.tables
    if not tables:
        lines = [line for line in raw.splitlines() if line.count('|') >= 3]
        tables = [[line.strip().strip('|').split('|') for line in lines]]
    output = {'opening_balance': None, 'transactions': [], '_table_warnings': [], '_rejected_table_rows': []}
    for table_index, table in enumerate(tables):
        fields, header_index, width = _headers(table)
        if not fields:
            continue
        for row_index, cells in enumerate(table[header_index + 1:], header_index + 2):
            text = ' '.join(str(cell or '') for cell in cells)
            if not text.strip() or all(not str(cell or '').strip(' :-') for cell in cells):
                continue
            if sum(_field(cell) is not None for cell in cells) >= 2:
                continue
            context = f'Table {table_index + 1}, row {row_index}'
            if len(cells) != width or any(cell is None for cell in cells):
                output['_table_warnings'].append(context + ': ragged row rejected; column alignment is unknown')
                output['_rejected_table_rows'].append({'table': table_index + 1, 'row': row_index, 'cells': cells, 'reason': 'ragged row'})
                continue
            values = {key: cells[column].strip() for key, column in fields.items()}
            for key, direction_key in (('balance', 'balance_direction'), ('amount', 'direction')):
                adjacent = fields.get(key, width) + 1
                if direction_key not in values and adjacent < width and adjacent not in fields.values() and _direction(cells[adjacent]):
                    values[direction_key] = cells[adjacent].strip()
            narration = values.get('description', '')
            opening = bool(re.match(r'(?i)^\s*(?:b\s*/\s*f\b|opening\s*balance\b|balance\s+b/?f\b|brought\s*forward\b)', narration))
            dated = _valid_date(values.get('date'))
            if not dated and re.search(r'(?i)page\s*total|grand\s*total|closing\s*balance|^\s*total', text):
                continue
            if not opening and not dated:
                if not values.get('date') and narration and not any(values.get(key) for key in ('debit', 'credit', 'balance', 'amount')):
                    if output['transactions']:
                        output['transactions'][-1]['description'] += ' ' + narration
                    continue
                output['_table_warnings'].append(context + ': invalid or missing transaction date')
                output['_rejected_table_rows'].append({'table': table_index + 1, 'row': row_index, 'cells': cells, 'reason': 'invalid date'})
                continue
            try:
                converted = _convert(values)
            except ValueError as error:
                output['_table_warnings'].append(context + ': ' + str(error))
                output['_rejected_table_rows'].append({'table': table_index + 1, 'row': row_index, 'cells': cells, 'reason': str(error)})
                continue
            if opening:
                output['opening_balance'] = converted.get('balance')
                continue
            converted['_table_source'] = {'cells': cells, 'header_columns': dict(fields)}
            output['transactions'].append(converted)
    if not output['transactions']:
        error = ValueError('No safe bank transaction rows found in AI table output')
        error.table_evidence = output
        raise error
    return output


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('AI JSON contains duplicate keys')
        value[key] = item
    return value


def parse_json(raw):
    start, end = raw.find('{'), raw.rfind('}')
    if start < 0 or end < start:
        raise ValueError('AI output has no JSON object')
    def reject_constant(value):
        raise ValueError('AI JSON contains a non-finite number')
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('AI JSON contains a non-finite number')
        return number
    value = json.loads(raw[start:end + 1], object_pairs_hook=_json_object,
                       parse_constant=reject_constant, parse_float=finite_float)
    if not isinstance(value, dict) or not isinstance(value.get('transactions'), list):
        raise ValueError('AI output has no transaction array')
    if not all(isinstance(item, dict) for item in value['transactions']):
        raise ValueError('AI transactions must be JSON objects')
    if value.get('opening_balance') is not None and (isinstance(value['opening_balance'], bool) or not isinstance(value['opening_balance'], (int, float, str))):
        raise ValueError('AI opening balance must be an amount or null')
    for item in value['transactions']:
        for key in ('date', 'description', 'counterparty', 'cheque_number'):
            if item.get(key) is not None and not isinstance(item[key], str):
                raise ValueError(f'AI transaction {key} must be text or null')
        for key in ('debit', 'credit', 'balance', 'amount'):
            if item.get(key) is not None and (isinstance(item[key], bool) or not isinstance(item[key], (int, float, str))):
                raise ValueError(f'AI transaction {key} must be an amount or null')
    return value
