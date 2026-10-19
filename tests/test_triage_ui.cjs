'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const elements=new Map();
function element(selector){
    if(!elements.has(selector))elements.set(selector,{
        innerHTML:'',textContent:'',value:'',dataset:{},style:{},open:false,
        querySelectorAll:()=>[],contains:()=>false,before(){},prepend(){},scrollIntoView(){}
    });
    return elements.get(selector);
}
const context={
    document:{querySelector:element,querySelectorAll:()=>[],createElement:()=>({})},
    window:{},Intl,console
};
vm.createContext(context);
for(const filename of ['evidence.js','ui.js']){
    const source=fs.readFileSync(path.join(__dirname,'../static',filename),'utf8');
    vm.runInContext(source.replace(/start\(\);setInterval\(refresh,2500\);/,''),context);
}
const run=code=>vm.runInContext(code,context);
run(`
const base={date:'2025-04-01',original_description:'TRANSFER',normalized_description:'TRANSFER',debit:100,credit:0,balance:900,page:1,confidence:.99,suspicious:false,state:'review'};
rows=[
 {...base,id:'financial',state:'approved',reason:'balance mismatch: difference +1.00'},
 {...base,id:'estimate',_inferred_fields:{debit:{method:'balance delta',original_value:90}}},
 {...base,id:'name',counterparty:'Example Name',normalized_description:'TRANSFER Example Name',_ai_proposal:{description:'TRANSFER Example Name'},_inferred_fields:{counterparty:{method:'narration reading'}}},
 {...base,id:'weak',confidence:.6,reason:'weak OCR; compare numbers with source',suspicious:true},
 {...base,id:'ordinary'},
 {...base,id:'excluded',state:'deleted',reason:'balance mismatch'}
];
selected='sample';docs=[{id:'sample',name:'Synthetic statement',status:'ready',pages:1,config:{},report:{automatic:{inferred_rows:999,balance_mismatches:999}}}];
`);
assert.equal(run('JSON.stringify(triageCounts())'),JSON.stringify({financial_error:1,estimated_value:1,information:2}));
run('renderUncertain()');
let html=element('#uncertainReadings').innerHTML;
assert.match(html,/Financial checks/);
assert.match(html,/evidence-financial/);
assert.match(html,/evidence-estimate/);
assert.doesNotMatch(html,/evidence-name|evidence-weak|evidence-ordinary|evidence-excluded/);
assert.match(html,/<details><summary>Information notes · 2 information-only rows/);
assert(html.indexOf('<figure')<html.indexOf('<dl'));
assert.match(html,/Manual confirmation does not remove financial flags/);

element('#showInformation').onclick();
html=element('#uncertainReadings').innerHTML;
assert.equal(run('filter'),'information');
assert.match(html,/evidence-name|evidence-weak/);
assert.doesNotMatch(html,/evidence-financial|evidence-estimate/);
assert.match(html,/Name guess; identity is not verified/);
assert.doesNotMatch(html,/class="evidence-messages"/);
run('renderRows()');
assert.match(element('#rowList').innerHTML,/Information only/);
assert.doesNotMatch(element('#rowList').innerHTML,/tag good|tag warn/);

run("filter='financial_error';renderUncertain()");
html=element('#uncertainReadings').innerHTML;
assert.match(html,/evidence-financial/);
assert.doesNotMatch(html,/evidence-estimate/);
run("filter='flagged';renderRows()");
assert.match(element('#rowList').innerHTML,/data-row="financial"/);
assert.match(element('#rowList').innerHTML,/data-row="estimate"/);
assert.doesNotMatch(element('#rowList').innerHTML,/data-row="name"|data-row="weak"/);

run("selectedRow='name';renderInspector()");
html=element('#inspector').innerHTML;
assert.match(html,/Information only/);
assert.match(html,/Name guess; identity is not verified/);
assert.match(html,/Source and processing notes/);
assert.doesNotMatch(html,/class="notice"|tag good|tag warn/);
run("selectedRow='financial';renderInspector()");
assert.match(element('#inspector').innerHTML,/class="notice"/);

run("filter='information';docs[0].status='processing';docs[0].stage='Reading';docs[0].progress=1;renderProcessing()");
assert.equal(element('#filter').value,'information');
assert.match(element('#content').innerHTML,/Financial errors|financial errors/);
assert.match(element('#uncertainReadings').innerHTML,/evidence-name/);
assert.doesNotMatch(element('#uncertainReadings').innerHTML,/data-inspect-evidence=/);
run("filter='all';rows=rows.filter(row=>['name','weak'].includes(row.id));renderUncertain()");
assert.match(element('#uncertainReadings').innerHTML,/No financial checks flagged in available rows/);
assert.doesNotMatch(element('#uncertainReadings').innerHTML,/<article /);

run("docs[0].status='ready';renderReview()");
assert.doesNotMatch(element('#content').innerHTML,/999/);
assert.match(element('#content').innerHTML,/0 financial errors · 0 estimated values · 2 information-only rows/);
console.log('PASS: financial triage cards, current-row counts, independent information notes, source order, filters and inspector states');
