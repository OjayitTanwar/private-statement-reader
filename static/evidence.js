'use strict';
/* Display evidence honestly: arithmetic agreement never certifies source text. */
(function(root){
    const labels={date:'Date',normalized_description:'Description',counterparty:'Person / company',cheque_number:'Cheque / reference',debit:'Debit',credit:'Credit',balance:'Balance'};
    const text=value=>String(value??'').replace(/\s+/g,' ').trim();
    const number=value=>{
        if(value===null||value===undefined||text(value)==='')return null;
        const parsed=Number(text(value).replaceAll(',',''));return Number.isFinite(parsed)?parsed:null;
    };
    const distinct=values=>new Set((values||[]).filter(x=>x&&x.value!==null&&x.value!==undefined).map(x=>String(number(x.value)??x.value))).size;
    const validDate=value=>{
        const raw=text(value);
        if(!/^\d{4}-\d{2}-\d{2}$/.test(raw))return false;
        const date=new Date(raw+'T00:00:00Z');
        return Number.isFinite(date.getTime())&&date.toISOString().slice(0,10)===raw&&Number(raw.slice(0,4))>=1900&&Number(raw.slice(0,4))<=2100;
    };
    function describe(row){
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
            if(inferred[key]){
                status='estimated';detail=text(inferred[key].method)||'Estimated reading';
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
        return {uncertain,priority,headline,fields,messages,balanceStatus,sourceLabel};
    }
    root.StatementEvidence={describe};
    if(typeof module!=='undefined'&&module.exports)module.exports=root.StatementEvidence;
})(globalThis);
