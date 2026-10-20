"""Automatic ruled-bank-table cell detection and balance-constrained OCR decoding."""
from __future__ import annotations
import copy,json,math,os,re,statistics,subprocess,uuid,sys
from pathlib import Path
import numpy as np
from PIL import Image,ImageOps,ImageFilter
from bank_engine import header_groups,header_key,normal_date,DATE_RE,parse_amount,cents,reconcile,normalize
try:
    import cv2
except ImportError:
    extra=Path(__file__).resolve().parent/'work/ai-env/lib/python3.13/site-packages'
    if extra.is_dir():sys.path.append(str(extra))
    try:import cv2
    except ImportError:cv2=None


def ruled_geometry(rotated,transformed,ht):
    if cv2 is None:return None
    arr=np.asarray(rotated);height,width=arr.shape
    binary=cv2.adaptiveThreshold(arr,255,cv2.ADAPTIVE_THRESH_MEAN_C,cv2.THRESH_BINARY_INV,31,15)
    horizontal=cv2.morphologyEx(binary,cv2.MORPH_OPEN,np.ones((1,max(40,width//35)),np.uint8))
    vertical=cv2.morphologyEx(cv2.dilate(binary,np.ones((1,3),np.uint8)),cv2.MORPH_OPEN,np.ones((max(35,height//120),1),np.uint8))
    combined=cv2.bitwise_or(horizontal,vertical)
    contours,_=cv2.findContours(combined,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    options=[]
    for contour in contours:
        x,y,w,h=cv2.boundingRect(contour)
        if w<width*.35 or h<ht*2:continue
        hits=sum(x-10<=hx<=x+w+10 and y-10<=hy<=y+h+10 for hx,hy in transformed.values())
        if hits>=3:options.append((hits,w*h,(x,y,w,h)))
    if not options:return None
    _,_,(x,y,w,h)=max(options,key=lambda o:(o[0],o[1]))
    hs=bands(np.flatnonzero((horizontal[y:y+h,x:x+w]>0).mean(axis=1)>.4),gap=5)
    hm=statistics.median(hy for hx,hy in transformed.values())
    lines=[y+(hi if y+lo-15<=hm<=y+hi+15 else (lo+hi)//2) for lo,hi in hs]
    lines=[v for v in lines if v>hm+ht*.15]
    if len(lines)<2:return None
    vs=bands(np.flatnonzero((vertical[lines[0]:y+h,x:x+w]>0).mean(axis=0)>.35),gap=5)
    xs=[x+(lo+hi)//2 for lo,hi in vs]
    if len(xs)<4:return None
    fields={}
    for key,(hx,hy) in transformed.items():
        if key=='amount' and ('debit' in transformed or 'credit' in transformed):continue
        pair=next(((a,b) for a,b in zip(xs,xs[1:]) if a<hx<b),None)
        if pair:fields[key]=pair
    if 'balance' not in fields:return None
    if 'date' not in fields:fields['date']=(xs[0],xs[1])
    if 'description' not in fields and len(xs)>2:fields['description']=(xs[1],xs[2])
    rows=[(a,b) for a,b in zip(lines,lines[1:]) if b-a>ht*.4]
    return fields,rows


def recover_reversed_headers(image_path,words,folder,page,vision,faint=False):
    """Read dark bank-table headings separately from white page backgrounds."""
    from bank_engine import ocr
    with Image.open(image_path) as im:
        gray=ImageOps.grayscale(im)
        if faint:gray=ImageOps.autocontrast(gray,cutoff=.5)
        arr=np.asarray(gray);threshold=180 if faint else 125
        smooth=np.convolve((arr<threshold).mean(axis=1),np.ones(9)/9,mode='same')
        recovered=[]
        for index,(top,bottom) in enumerate(bands(np.flatnonzero(smooth>.24),gap=10)):
            if top<im.height*.2 or bottom-top<10 or bottom-top>im.height*.08:continue
            stripe=arr[max(0,top-4):min(im.height,bottom+5)]
            xs=np.flatnonzero((stripe<threshold).mean(axis=0)>.30)
            if len(xs)<im.width*.35:continue
            left,right=max(0,int(xs[0])-3),min(im.width,int(xs[-1])+4)
            crop=ImageOps.autocontrast(ImageOps.invert(gray.crop((left,top-4,right,bottom+5))))
            crop=ImageOps.expand(crop.resize((crop.width*2,crop.height*2),Image.Resampling.LANCZOS),border=30,fill=255)
            path=folder/f'header-{page}-{index}.png';crop.save(path);engines=[]
            if vision and Path(vision).is_file():
                try:engines.append(json.loads(subprocess.run([str(vision),str(path)],check=True,capture_output=True,text=True,timeout=90).stdout))
                except subprocess.SubprocessError:pass
            engines.append(ocr(path,6))
            best=max(engines,key=lambda ws:len({header_key(w['text']) for w in ws if header_key(w['text'])}),default=[])
            if sum(header_key(w['text']) is not None for w in best)>=3:
                for w in best:
                    q=dict(w);q['x']=left+(w['x']-30)/2;q['y']=top-4+(w['y']-30)/2;q['w']/=2;q['h']/=2;recovered.append(q)
            path.unlink(missing_ok=True)
        if recovered:
            levels=[w['y'] for w in recovered]
            words=[w for w in words if not (header_key(w['text']) and any(abs(w['y']-y)<50 for y in levels))]+recovered
        return words


def bands(indices,gap=3):
    groups=[]
    for index in indices:
        if not groups or index-groups[-1][-1]>gap:groups.append([int(index)])
        else:groups[-1].append(int(index))
    return [(g[0],g[-1]) for g in groups]


def transform(x,y,width,height,angle):
    a=math.radians(angle);dx=x-width/2;dy=y-height/2
    return width/2+dx*math.cos(a)+dy*math.sin(a),height/2-dx*math.sin(a)+dy*math.cos(a)


def detect_grid(image,words):
    groups=header_groups(words)
    if not groups or len({k for w,k in groups[0]})<4:return None
    headers=groups[0];hw=[w for w,k in headers]
    slopes=[((b['y']+b['h']/2)-(a['y']+a['h']/2))/(b['x']-a['x']) for i,a in enumerate(hw) for b in hw[i+1:] if abs(b['x']-a['x'])>200]
    angle=math.degrees(statistics.median(slopes)) if slopes else 0
    angle=max(-5,min(5,angle));width,height=image.size
    gray=ImageOps.grayscale(image)
    # Optimize the actual grid-line alignment, not the noisy OCR word baselines.
    hx=min(w['x'] for w in hw);rx=max(w['x']+w['w'] for w in hw)
    hy=max(w['y']+w['h'] for w in hw)
    probe=gray.crop((max(0,int(hx-100)),int(hy+30),min(width,int(rx+100)),min(height,int(hy+height*.35))))
    probe.thumbnail((1300,1500))
    def alignment(a):
        values=(np.asarray(probe.rotate(a,resample=Image.Resampling.BILINEAR,fillcolor=255))<145).mean(axis=1)
        count=min(45,len(values))
        return float(np.partition(values,-count)[-count:].sum())
    coarse=[angle+i*.15 for i in range(-10,11)]
    angle=max(coarse,key=alignment)
    angle=max([angle+i*.03 for i in range(-4,5)],key=alignment)
    rotated=gray.rotate(angle,resample=Image.Resampling.BICUBIC,fillcolor=255)
    transformed={k:transform(w['x']+w['w']/2,w['y']+w['h']/2,width,height,angle) for w,k in headers}
    ht=statistics.median(w['h'] for w in hw)
    precise=ruled_geometry(rotated,transformed,ht)
    if precise:
        fields,rows=precise
        return {'image':rotated,'angle':angle,'fields':fields,'rows':rows,'width':width,'height':height}
    left=max(0,int(min(x for x,y in transformed.values())-ht*3))
    right=min(width,int(max(x for x,y in transformed.values())+ht*3))
    top=max(0,int(min(y for x,y in transformed.values())-ht*1.8))
    bottom=min(height,int(top+height*.65))
    a=np.asarray(rotated)
    # Rule density and continuity, rather than text, locate rows and columns.
    mask=a[top:bottom,left:right]<110
    row_density=mask.mean(axis=1)
    horizontal=bands(np.flatnonzero(row_density>.56),gap=4)
    if len(horizontal)<4:return None
    # A dark reversed header is a band, not a transaction row.
    head_mid=statistics.median(y for x,y in transformed.values())
    ylines=[top+(hi if top+lo-10<=head_mid<=top+hi+10 else (lo+hi)//2) for lo,hi in horizontal]
    body=[y for y in ylines if y>head_mid+ht*.2]
    if len(body)<3:return None
    spacings=[b-a for a,b in zip(body,body[1:]) if ht*.6<b-a<ht*4]
    spacing=statistics.median(spacings) if spacings else ht*1.8
    end=1
    while end<len(body) and body[end]-body[end-1]<spacing*2.4:end+=1
    body=body[:end]
    if len(body)<3:return None
    upper,lower=body[0],body[-1]
    vertical_mask=a[upper:min(lower,int(upper+spacing*5)),left:right]<145
    vd=vertical_mask.mean(axis=0)
    vertical=bands(np.flatnonzero(vd>.42),gap=4)
    xlines=[left+(lo+hi)//2 for lo,hi in vertical]
    if len(xlines)<4:return None
    fields={}
    for field,(x,y) in transformed.items():
        if field=='amount' and ('debit' in transformed or 'credit' in transformed):continue
        pair=next(((l,r) for l,r in zip(xlines,xlines[1:]) if l<x<r),None)
        if pair:fields[field]=pair
    if 'date' not in fields and len(xlines)>=6:fields['date']=(xlines[0],xlines[1])
    if 'description' not in fields and len(xlines)>=6:fields['description']=(xlines[1],xlines[2])
    if 'balance' not in fields or not any(k in fields for k in ('debit','credit','amount')):return None
    valid=[]
    for lo,hi in zip(body,body[1:]):
        if hi-lo<ht*.5:continue
        supports=[float((a[lo+3:hi-3,max(0,x-3):x+4]<145).mean())>.12 for x in xlines]
        if supports and sum(supports)/len(supports)>=.5:valid.append((lo,hi))
    if not valid:return None
    return {'image':rotated,'angle':angle,'fields':fields,'rows':valid,'width':width,'height':height}


def cell_contact_sheet(grid,folder,page):
    """White padding removes rules; a contact sheet reads all cells in one Vision request."""
    tiles=[];manifest=[];fields=grid['fields'];im=grid['image']
    for ri,(top,bottom) in enumerate(grid['rows']):
        if bottom-top<12:continue
        for field,(left,right) in fields.items():
            if right-left<20:continue
            cell=im.crop((left+5,top+5,right-5,bottom-5))
            cell=ImageOps.autocontrast(cell,cutoff=1)
            scale=min(3,max(1.5,70/max(1,cell.height)))
            cell=cell.resize((max(1,int(cell.width*scale)),max(1,int(cell.height*scale))),Image.Resampling.LANCZOS)
            tiles.append(cell)
            manifest.append({'row':ri,'field':field,'box':[left,top,right,bottom],'sheet':None})
    if not tiles:return None
    # Single vertical strip avoids OCR assigning words to adjacent financial columns.
    sheet_width=max(t.width for t in tiles)+80
    sheet_height=sum(max(100,t.height+50) for t in tiles)
    # Limit large tables to multiple sheets to keep page-image memory bounded.
    chunks=[];start=0;current=[];height=0
    for i,t in enumerate(tiles):
        th=max(100,t.height+50)
        if height+th>2000 and current:chunks.append(current);current=[];height=0
        current.append(i);height+=th
    if current:chunks.append(current)
    sheets=[]
    for ci,indices in enumerate(chunks):
        h=sum(max(100,tiles[i].height+50) for i in indices)
        sheet=Image.new('L',(sheet_width,h),255);y=20
        for i in indices:
            tile=tiles[i];sheet.paste(tile,(40,y));manifest[i]['sheet']={'chunk':ci,'x':40,'y':y,'w':tile.width,'h':tile.height};y+=max(100,tile.height+50)
        path=folder/f'cells-{page}-{ci}.png';sheet.save(path);sheets.append(path)
    return sheets,manifest


def extract_grid(image_path,words,folder,page,vision):
    with Image.open(image_path) as original:
        grid=detect_grid(original,words)
        if not grid:return [],None,{'grid':False}
        made=cell_contact_sheet(grid,folder,page)
        if not made:return [],None,{'grid':False}
        sheets,manifest=made
        readings={}
        model=os.environ.get('STATEMENT_TESSDATA')
        for ci,path in enumerate(sheets):
            engines=[]
            if vision and Path(vision).is_file():
                try:
                    p=subprocess.run([str(vision),str(path)],capture_output=True,text=True,check=True,timeout=180)
                    engines.append(('Vision cells',json.loads(p.stdout)))
                except subprocess.SubprocessError:pass
            from bank_engine import ocr
            engines.append(('Tesseract cells',ocr(path,6)))
            for name,recognized in engines:
                for mi,item in enumerate(manifest):
                    tile=item['sheet']
                    if tile['chunk']!=ci:continue
                    contained=[w for w in recognized if tile['y']-5 <=w['y']+w['h']/2<=tile['y']+tile['h']+5]
                    # Sort by geometry, retain line breaks as whitespace for names.
                    text=' '.join(w['text'] for w in sorted(contained,key=lambda w:(round(w['y']/25),w['x'])))
                    confidence=statistics.mean(w['conf'] for w in contained) if contained else 0
                    readings.setdefault(mi,[]).append({'text':text,'confidence':confidence,'engine':name})
                    # Vision exposes its alternate neural readings; retain them for sequence decoding.
                    if item['field'] in ('date','debit','credit','balance','amount'):
                        alternatives={s for w in contained for s in w.get('alternatives',[])}
                        for alternative in alternatives:
                            if parse_amount(alternative) is not None or normal_date(alternative):
                                readings[mi].append({'text':alternative,'confidence':min(.8,confidence),'engine':name+' alternate'})
            path.unlink(missing_ok=True)
        rows=[];opening=None
        for ri,bounds in enumerate(grid['rows']):
            cells={item['field']:readings.get(mi,[]) for mi,item in enumerate(manifest) if item['row']==ri}
            descs=cells.get('description',[])
            desc=max(descs,key=lambda c:c['confidence'],default={'text':'','confidence':0,'engine':'none'})
            desc_text=desc['text']
            date_options=[]
            for candidate in cells.get('date',[]):
                value=normal_date(candidate['text'])
                if value:date_options.append({**candidate,'value':value})
            numeric={}
            for k in ('debit','credit','balance','amount'):
                options=[]
                for candidate in cells.get(k,[]):
                    value=parse_amount(candidate['text'])
                    if value is not None and not any(o['value']==value for o in options):options.append({**candidate,'value':value})
                numeric[k]=options
            if re.search(r'(?i)\bb\s*/\s*f\b|opening|brought\s+forward',desc_text):
                if numeric['balance']:opening=max(numeric['balance'],key=lambda c:c['confidence'])['value']
                continue
            if re.search(r'(?i)page\s*total|closing\s*balance|grand\s*total|total',desc_text) and not date_options:continue
            if not date_options:
                # Do not silently drop a grid row containing transaction amounts.
                if not any(numeric[k] for k in ('debit','credit','amount')):continue
            selected_date=max(date_options,key=lambda c:c['confidence'],default={'value':None})['value']
            r={'id':str(uuid.uuid4()),'date':selected_date,'original_description':desc_text,'normalized_description':normalize(desc_text),
               'cheque_number':'','debit':None,'credit':None,'balance':None,'page':page,'confidence':desc['confidence'],
               'suspicious':True,'reason':'','state':'review','crop':'','ord':len(rows),'_top':bounds[0],'_bottom':bounds[1],
               '_issues':[],'_engine':'Automatic table-cell OCR','_direction':'grid columns','_alternatives':[],'_numeric_candidates':numeric,'_date_candidates':date_options,'_cell_evidence':cells}
            refs=cells.get('cheque_number',[])
            if refs:r['cheque_number']=max(refs,key=lambda c:c['confidence'])['text']
            for k in ('debit','credit','balance'):
                if numeric[k]:r[k]=max(numeric[k],key=lambda c:c['confidence'])['value']
            if selected_date is None:r['_issues'].append('Date unresolved across cell OCR passes')
            if len({v['value'] for v in date_options})>1:r['_issues'].append('OCR date candidates disagree')
            for k in ('debit','credit','balance'):
                if len(numeric[k])>1:r['_issues'].append(f'{k} cell OCR candidates disagree')
            top,bottom=bounds;left=min(v[0] for v in grid['fields'].values());right=max(v[1] for v in grid['fields'].values())
            crop=f'grid-crop-{page}-{ri}.png';grid['image'].crop((left,top,right,bottom)).save(folder/crop);r['crop']=crop
            rows.append(r)
        return rows,opening,{'grid':True,'grid_rows':len(grid['rows']),'extracted':len(rows),'angle':round(grid['angle'],2),'fields':list(grid['fields'])}


def decode_balance_chain(rows,opening=None,beam_width=48,order='auto'):
    """Select a unique exact path of observed readings; ambiguity leaves values intact."""
    keys=('debit','credit','balance')
    def is_ai(item):
        return item.get('source') in ('ai','ai_only','inferred') or bool(re.search(r'\b(ai|glm|mlx)\b',str(item.get('engine',item.get('_engine',''))),re.I))
    def signature(item):return tuple(cents(item.get(key)) or 0 if key!='balance' else cents(item.get(key)) for key in keys)
    def observed(row):
        if is_ai(row) or any(k in row.get('_inferred_fields',{}) for k in keys):return False
        before=row.get('_ocr_before_ai',{})
        return not any(k in before and row.get('_field_sources',{}).get(k)!='ocr' and (cents(before[k]) or 0)!=(cents(row.get(k)) or 0) for k in keys)
    def frozen(row):
        return row.get('state') in ('approved','corrected') or row.get('_engine')=='Manual transcription' or any(row.get(k) for k in ('_manual','_edited','_corrected','_manual_fields','_edited_fields'))
    def variants(row):
        base={k:row.get(k) for k in keys};result={}
        def add(values,evidence):
            if not all(k in values for k in keys):return
            if any(values.get(k) is not None and str(values[k]).strip() and cents(values[k]) is None for k in keys):return
            d,c,b=signature(values)
            if b is None or d<0 or c<0 or bool(d)==bool(c):return
            result.setdefault((d,c,b),(values,evidence))
        if observed(row):add(base,{'engine':row.get('_numeric_engine',row.get('_engine','Observed OCR')),'baseline':True})
        if frozen(row) or is_ai(row) or any(k in row.get('_inferred_fields',{}) for k in keys):return list(result.values())
        before=row.get('_ocr_before_ai',{})
        if all(k in before for k in keys) and not any(k in row.get('_inferred_fields',{}) for k in keys):
            add({k:before[k] for k in keys},{'engine':'Original OCR before AI'})
        for alternative in row.get('_alternatives',[]):
            if not is_ai(alternative):add(alternative,alternative)
        # One cell may change against the observed baseline. Separate engines are
        # never combined into an invented whole-row reading.
        if observed(row):
            for key in keys:
                for candidate in row.get('_numeric_candidates',{}).get(key,[]):
                    if is_ai(candidate):continue
                    value=candidate.get('value')
                    if cents(value) is None:continue
                    add({**base,key:value},{**candidate,'field':key})
        return list(result.values())
    dates=[r['date'] for r in rows if r.get('date') and r.get('state')!='deleted']
    descending=order=='descending' or order=='auto' and len(dates)>1 and dates[0]>dates[-1]
    ordered=[r for r in (reversed(rows) if descending else rows) if r.get('state')!='deleted']
    segments=[];segment=[];anchor=cents(opening)
    for row in ordered:
        if row.get('_opening_balance') is not None:
            if segment:segments.append((anchor,segment));segment=[]
            ai_opening=any('opening balance read by local ai' in str(issue).lower() for issue in row.get('_issues',[]))
            ai_opening=ai_opening or 'opening_balance' in row.get('_inferred_fields',{}) or row.get('_opening_balance_source')=='ai'
            anchor=None if ai_opening else cents(row['_opening_balance'])
        segment.append(row)
    if segment:segments.append((anchor,segment))
    def apply_path(targets,paths):
        if len(paths)!=1:return
        for row,(values,evidence) in zip(targets,paths[0][1]):
            if signature(row)==signature(values):continue
            before={key:row.get(key) for key in keys}
            row.setdefault('_candidate_selections',[]).append({'before':before,'selected':{key:values.get(key) for key in keys},'evidence':copy.deepcopy(evidence),'method':'unique exact observed balance path'})
            for key in keys:
                if cents(row.get(key))!=cents(values.get(key)):
                    issue=f'{key}: observed alternate selected by whole-statement balance chain'
                    if issue not in row.setdefault('_issues',[]):row['_issues'].append(issue)
                row[key]=values.get(key)
            row['_numeric_engine']=str(evidence.get('engine',evidence.get('_engine','Observed OCR')))
            notice='Alternative OCR numbers selected using balance evidence; verify source'
            if notice not in row['_issues']:row['_issues'].append(notice)
    for anchor,segment in segments:
        start=0
        if anchor is None:
            # An unchanged printed balance can anchor following transactions.
            while start<len(segment) and (not observed(segment[start]) or cents(segment[start].get('balance')) is None):start+=1
            if start==len(segment):continue
            anchor=cents(segment[start].get('balance'));start+=1
        paths=[(anchor,[])];island=[]
        for row in segment[start:]:
            next_paths={}
            for previous,path in paths:
                for values,evidence in variants(row):
                    debit,credit,balance=signature(values)
                    if previous-debit+credit!=balance:continue
                    choices=path+[(values,evidence)]
                    identity=tuple(signature(item[0]) for item in choices)
                    next_paths.setdefault(identity,(balance,choices))
            new_paths=list(next_paths.values())
            if not new_paths or len(new_paths)>beam_width:
                if not new_paths:apply_path(island,paths)
                # A bad row breaks verification continuity. Its unchanged printed
                # balance may independently anchor the next local source island.
                anchor=cents(row.get('balance')) if observed(row) else None
                paths=[(anchor,[])] if anchor is not None else []
                island=[]
                continue
            paths=new_paths;island.append(row)
        apply_path(island,paths)
    return rows
