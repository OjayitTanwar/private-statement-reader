'use strict';
/* Display evidence honestly: arithmetic agreement never certifies source text. */
(function(root){
    const labels={date:'Date',normalized_description:'Description',counterparty:'Person / company',cheque_number:'Cheque / reference',debit:'Debit',credit:'Credit',balance:'Balance'};
    const text=value=>String(value??'').replace(/\s+/g,' ').trim();
    const number=value=>{
        if(value===null||value===undefined||typeof value==='boolean'||text(value)==='')return null;
        const raw=text(value).replaceAll(',','');
        if(!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(raw))return null;
        const parsed=Number(raw);return Number.isFinite(parsed)?parsed:null;
    };
    const distinct=values=>new Set((values||[]).filter(x=>x&&x.value!==null&&x.value!==undefined).map(x=>String(number(x.value)??x.value))).size;
    const validDate=value=>{
        const raw=typeof value==='string'?value:'';
        if(!/^\d{4}-\d{2}-\d{2}$/.test(raw))return false;
        const date=new Date(raw+'T00:00:00Z');
        return Number.isFinite(date.getTime())&&date.toISOString().slice(0,10)===raw&&Number(raw.slice(0,4))>=1900&&Number(raw.slice(0,4))<=2100;
    };
    function classify(row){
        const financial=[],estimated=[],information=[];
        const add=(target,value)=>{if(!target.includes(value))target.push(value)};
        const values=Object.fromEntries(['debit','credit','balance'].map(key=>[key,number(row[key])]));
        const inactiveZero=key=>['debit','credit'].includes(key)&&values[key]===0&&Boolean(values[key==='debit'?'credit':'debit']);
        if(!validDate(row.date))add(financial,'Transaction date missing or invalid');
        if(values.balance===null)add(financial,'Running balance missing or malformed');
        for(const key of ['debit','credit']){
            if(row[key]!=null&&String(row[key]).trim()&&values[key]===null)add(financial,`${key==='debit'?'Debit':'Credit'} malformed`);
            if(values[key]!==null&&values[key]<0)add(financial,`${key==='debit'?'Debit':'Credit'} is negative`);
        }
        if(values.debit&&values.credit)add(financial,'Both debit and credit populated');
        if(!values.debit&&!values.credit)add(financial,'Transaction amount or direction unresolved');
        for(const key of ['debit','credit','balance']){
            const candidates=(row._numeric_candidates?.[key]||[]).filter(item=>item&&typeof item==='object').map(item=>item.value);
            candidates.push(...(row._alternatives||[]).filter(item=>item&&typeof item==='object').map(item=>item[key]));
            const observed=new Set([row[key],...candidates].map(number).filter(value=>value!==null));
            if(observed.size>1)add(financial,`Conflicting ${key} readings`);
        }
        const dates=(row._date_candidates||[]).map(item=>item&&typeof item==='object'?item.value:item);
        dates.push(...(row._alternatives||[]).filter(item=>item&&typeof item==='object').map(item=>item.date));
        if(new Set([row.date,...dates].filter(value=>value!=null&&String(value).trim())).size>1)add(financial,'Conflicting date readings');
        const inferred=row._inferred_fields||{};
        for(const key of Object.keys(inferred)){
            if(['debit','credit','balance','date','opening_balance'].includes(key)){
                if(inactiveZero(key))add(information,`Blank ${key} column read as zero`);
                else add(estimated,key);
            }else add(information,`${key.replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase())} includes a guess`);
        }
        const fragments=[...new Set([...(row._issues||[]),row.reason||''].flatMap(value=>String(value).split(';')).map(value=>value.trim()).filter(Boolean))];
        for(const fragment of fragments){
            const value=fragment.toLowerCase();
            const estimate=value.match(/\b(debit|credit|balance|date|opening balance)\b.*\b(inferred|estimated|estimate|proposed|filled by local ai)\b/);
            if(estimate){
                const key=value.includes('opening balance')?'opening_balance':estimate[1];
                if(inactiveZero(key))add(information,fragment);
                else add(estimated,key);
                continue;
            }
            if(value.includes('opening balance read by local ai')){add(estimated,'opening_balance');continue;}
            const strong=['balance mismatch','unreadable','unrecognized columns','ai-only','not independently confirmed','manual transcription','direction unresolved','first row not reconciled','opening balance unavailable','both debit and credit','multiple values mapped','date invalid','date missing'].some(term=>value.includes(term));
            const description=['description','narration','counterparty','name proposed','name is a','identity'].some(term=>value.includes(term));
            const info=['description/name proposed by local ai','counterparty name','name is a local ai guess','not verified identity','identity is not verified','description missing','missing description','description unreadable','multiple values mapped to description','weak ocr','compare numbers with source','original ocr retained','original reading retained','ocr passes disagree or another pass missed this row','local ai did not independently recover this ocr row','alternative ocr numbers selected using balance evidence','observed alternate selected by whole-statement balance chain'].some(term=>value.includes(term))||['verify source','original ocr retained','original reading retained'].includes(value);
            if(strong&&!(description&&(value.includes('unreadable')||value.includes('multiple values mapped to description'))))add(financial,fragment);
            else if(info)add(information,fragment);
            else add(financial,fragment);
        }
        if(row._engine==='Manual transcription')add(financial,'Manual transcription requires source confirmation');
        if(row._engine==='Local GLM-OCR 8-bit AI')add(financial,'AI-only transaction candidate');
        const confidence=number(row.confidence);
        if(confidence!==null&&confidence<.80)add(information,'Low aggregate OCR confidence');
        if(row._ai_proposal&&Object.keys(row._ai_proposal).length&&row.normalized_description!==row.original_description)add(information,'Description includes an AI reading');
        if(!String(row.normalized_description||row.original_description||'').trim())add(information,'Description missing');
        if(row.suspicious&&!(financial.length||estimated.length||information.length))add(financial,'Legacy suspicious flag has no explanation');
        const category=financial.length?'financial_error':estimated.length?'estimated_value':information.length?'information':'none';
        return {category,review_required:['financial_error','estimated_value'].includes(category),financial_reasons:financial,estimated_fields:estimated,information,priority:{financial_error:0,estimated_value:1,information:2,none:3}[category]};
    }
    function describe(row){
        const triage=classify(row);
        const inferred=row._inferred_fields||{},earlier=row._ocr_before_ai||{};
        const reasons=[...(row._issues||[]),...text(row.reason).split(';')].map(text).filter(Boolean);
        const messages=[...new Set(reasons)];
        const reason=messages.join('; ').toLowerCase();
        const mismatch=reason.includes('balance mismatch');
        const noOpening=reason.includes('opening balance unavailable')||reason.includes('first row not reconciled');
        const both=Boolean(number(row.debit)&&number(row.credit));
        const missingAmount=!(number(row.debit)||number(row.credit));
        const numericEstimates=['debit','credit','balance'].some(k=>inferred[k]);
        const fields=Object.keys(labels).map(key=>{
            const value=row[key]??null;
            let status='read',detail='Read from the source; not independently verified.';
            if(row.state==='approved')detail='Manual confirmation recorded. Automatic checks remain visible.';
            if(inferred[key]||triage.estimated_fields.includes(key)){
                status='estimated';detail=text(inferred[key]?.method)||'Estimated reading';
                detail+=key==='counterparty'?'. Name guess; identity is not verified.':'. Inferred value; not confirmed as printed.';
            }else if(key==='date'&&!validDate(value)){
                status='missing';detail='The transaction date could not be resolved.';
            }else if(key==='balance'&&number(value)===null){
                status='missing';detail='The running balance could not be read.';
            }else if(['debit','credit'].includes(key)&&missingAmount){
                status='missing';detail='The transaction amount or its direction is unresolved.';
            }else if(['debit','credit'].includes(key)&&both){
                status='conflict';detail='Both debit and credit contain amounts.';
            }else if(key==='date'&&distinct(row._date_candidates)>1){
                status='conflict';detail='OCR passes read different dates.';
            }else if(distinct(row._numeric_candidates?.[key])>1){
                status='conflict';detail='OCR passes read different values. The selected reading may use balance evidence.';
            }else if(key==='normalized_description'){
                if(!text(value)||/^unreadable description$/i.test(text(value))){status='missing';detail='The narration could not be read.';}
                else if(text(value)!==text(row.original_description)&&row._ai_proposal){status='ai';detail='AI supplied this reading. Earlier OCR text is retained.';}
            }
            if(key==='balance'&&mismatch){
                detail+=' The running-balance equation does not match.';
                if(status==='read')status='conflict';
            }
            if(key==='counterparty'&&!text(value))detail='No counterparty name identified; a name is optional.';
            if(key==='cheque_number'&&!text(value))detail='No cheque or reference number identified; this field may be blank.';
            const oldKey=key==='normalized_description'?'original_description':key;
            const earlierValue=inferred[key]?.original_value??earlier[oldKey]??(key==='normalized_description'?row.original_description:null);
            return {key,label:labels[key],value,status,detail,earlierValue};
        });
        const fieldIssues=fields.some(f=>!['read','unconfirmed'].includes(f.status));
        const uncertain=Boolean(row.suspicious||fieldIssues||Object.keys(inferred).length);
        let headline='No unresolved checks flagged',priority=9;
        if(mismatch){headline='Running balance does not match';priority=0;}
        else if(fields.some(f=>f.status==='missing')){headline='One or more values could not be resolved';priority=1;}
        else if(numericEstimates){headline='Amounts include estimated values';priority=2;}
        else if(fields.some(f=>f.status==='conflict')){headline='OCR readings disagree';priority=3;}
        else if(inferred.counterparty){headline='Person or company name is a guess';priority=4;}
        else if(fields.some(f=>f.status==='ai')){headline='Description includes an AI reading';priority=5;}
        else if(uncertain){headline='Source reading is uncertain';priority=6;}
        let balanceStatus='Arithmetic matches the running balance; this does not verify the source text.';
        if(mismatch)balanceStatus='Balance mismatch';
        else if(number(row.balance)===null||missingAmount||both)balanceStatus='Balance could not be fully checked';
        else if(noOpening)balanceStatus='Opening balance unavailable; this row is not reconciled';
        else if(numericEstimates)balanceStatus='Arithmetic uses estimated values';
        if(noOpening&&!messages.some(m=>m.toLowerCase().includes('opening balance')))messages.push('Opening balance unavailable.');
        const crop=text(row.crop),page=row.page??'?';
        const sourceLabel=crop.startsWith('ai-table-')?`Source table · page ${page}`:crop?`Source crop · page ${page}`:`Source page · page ${page}`;
        const statusLabel={financial_error:'Financial check',estimated_value:'Estimated value',information:'Information only',none:'No financial flags'}[triage.category];
        if(triage.category==='information')headline='Information notes — no financial flag';
        else if(triage.category==='estimated_value')headline='Financial values include estimates';
        else if(triage.category==='financial_error'&&!mismatch&&!fields.some(f=>['missing','conflict'].includes(f.status)))headline='Financial check needs attention';
        else if(triage.category==='none')headline='No financial flags';
        return {uncertain:uncertain||triage.category!=='none',priority:triage.priority,headline,fields,messages,balanceStatus,sourceLabel,
            category:triage.category,reviewRequired:triage.review_required,statusLabel,financialReasons:triage.financial_reasons,
            estimatedFields:triage.estimated_fields,information:triage.information};
    }
    root.StatementEvidence={describe,classify};
    if(typeof module!=='undefined'&&module.exports)module.exports=root.StatementEvidence;
})(globalThis);
