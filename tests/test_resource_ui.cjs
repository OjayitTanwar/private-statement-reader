'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const elements=new Map();
function element(key){if(!elements.has(key))elements.set(key,{innerHTML:'',textContent:'',value:'',style:{},dataset:{},isConnected:true,lastElementChild:{replaceWith(){}},querySelectorAll:()=>[]});return elements.get(key)}
const preset={preset:'balanced',documents:2,cpu_threads:5,memory_gb:10,ai_instances:1};
let response={settings:preset,effective:{...preset,document_workers:2,ocr_jobs:2,memory_blocked:false},bounds:{documents:8,memory_gb:14,cpu_threads:10},hardware:{memory_gb:16,cpu_cores:10},presets:{light:{...preset,preset:'light',documents:1,memory_gb:6},balanced:preset},memory_used_gb:2.5,running_pages:1,admission_limit:2,ai_workers:{live:1}};
let saved=null;
const context={document:{querySelector:element,createElement:()=>({})},Intl,console,setTimeout:()=>1,clearTimeout(){},fetch:async(url,options)=>{if(options?.body){saved=JSON.parse(options.body);response={...response,settings:saved,effective:{...response.effective,...saved,document_workers:1}}}return {ok:true,json:async()=>response}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync('static/ui.js','utf8').replace(/start\(\);setInterval\(refresh,2500\);/,''),context);
const run=code=>vm.runInContext(code,context);
(async()=>{
    await run('resourceSettings()');
    assert.match(element('#content').innerHTML,/best-effort, not a strict macOS RAM cap/);
    assert.match(element('#content').innerHTML,/Simultaneous documents/);
    assert.equal(element('#resourceMemory').value,10);
    element('#resourceDocuments').value='1';element('#resourceDocuments').onchange();
    assert.equal(element('#resourcePreset').value,'custom');
    await run('pollResources()');
    assert.equal(element('#resourceDocuments').value,'1','live readings must not overwrite unsaved choices');
    await element('#resourceForm').onsubmit({preventDefault(){}});
    assert.equal(saved.documents,1);assert.equal(saved.memory_gb,10);
    assert.equal(element('#saveResources').disabled,false);
    assert.match(element('#resourcesFeedback').textContent,/Saved/);
    assert.match(element('#resourceLive').textContent,/2.50 GB/);
    console.log('PASS: resource settings, custom values, safe save, live usage and unsaved-choice preservation');
})().catch(error=>{console.error(error);process.exitCode=1});
