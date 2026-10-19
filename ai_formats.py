"""Strict parsers for local model JSON/HTML outputs. Model text is never executed."""
import json,re
from html.parser import HTMLParser


class Tables(HTMLParser):
    def __init__(self):
        super().__init__();self.tables=[];self.table=None;self.row=None;self.cell=None;self.span=1
    def handle_starttag(self,tag,attrs):
        if tag=='table':self.table=[]
        elif tag=='tr':self.row=[]
        elif tag in ('td','th'):
            self.cell=[]
            try:self.span=max(1,min(20,int(dict(attrs).get('colspan',1))))
            except ValueError:self.span=1
        elif tag=='br' and self.cell is not None:self.cell.append(' ')
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.cell is not None:
            if self.row is not None:self.row.extend([' '.join(''.join(self.cell).split())]+['']*(self.span-1))
            self.cell=None
        elif tag=='tr' and self.row is not None:
            if self.table is not None:self.table.append(self.row)
            self.row=None
        elif tag=='table' and self.table is not None:
            self.tables.append(self.table);self.table=None


def parse_table(raw):
    parser=Tables();parser.feed(raw)
    tables=parser.tables
    if not tables:
        lines=[line for line in raw.splitlines() if line.count('|')>=3]
        tables=[[line.strip().strip('|').split('|') for line in lines]]
    output={'opening_balance':None,'transactions':[]}
    for table in tables:
        best={};header_index=-1
        for i,row in enumerate(table):
            fields={}
            for index,cell in enumerate(row):
                value=re.sub('[^a-z]','',cell.lower())
                if value.startswith(('withdraw','debit')):fields['debit']=index
                elif value.startswith(('deposit','credit')):fields['credit']=index
                elif value.startswith('balance'):fields['balance']=index
                elif value.startswith(('date','txndate','transactiondate')):fields.setdefault('date',index)
                elif value.startswith(('particular','narration','description','details')):fields['description']=index
                elif value.startswith(('chq','cheque','reference')):fields['cheque_number']=index
            if 'balance' in fields and len(fields)>len(best):best=fields;header_index=i
        if len(best)<3:continue
        for cells in table[header_index+1:]:
            values={k:cells[i].strip() if i<len(cells) else '' for k,i in best.items()}
            text=' '.join(cells)
            if re.search(r'(?i)\bb\s*/\s*f\b|opening\s*balance|brought\s*forward',text):
                output['opening_balance']=values.get('balance');continue
            if re.search(r'(?i)page\s*total|grand\s*total|closing\s*balance|^\s*total',text):continue
            if not values.get('date'):
                if output['transactions'] and values.get('description') and not any(values.get(k) for k in ('debit','credit','balance')):
                    output['transactions'][-1]['description']+=' '+values['description']
                continue
            output['transactions'].append(values)
    if not output['transactions']:raise ValueError('No bank transaction rows found in AI table output')
    return output


def parse_json(raw):
    start=raw.find('{');end=raw.rfind('}')
    value=json.loads(raw[start:end+1])
    if not isinstance(value,dict) or not isinstance(value.get('transactions'),list):raise ValueError('AI output has no transaction array')
    return value
