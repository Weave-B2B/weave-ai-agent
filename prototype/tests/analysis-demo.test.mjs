import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../analysis-demo.js',import.meta.url),'utf8');
const {createAnalysisDemo,analysisSnapshot}=await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

function setup(store=new Map()) {
  const records=[],queue=[],visits=[];
  let demo;
  demo=createAnalysisDemo({records,storage:{getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v)},render:()=>{},navigate:(id,tab)=>visits.push([id,tab]),openAnalysis:id=>demo.enter(id),schedule:fn=>queue.push(fn)});
  const click=(action)=>demo.click({closest:selector=>selector==='[data-demo-action]'?{dataset:{demoAction:action}}:null});
  const choice=value=>demo.change({name:'analysis-pm-choice',value});
  const input=(id,value)=>demo.input({id,value});
  const flush=()=>{while(queue.length)queue.shift()();};
  return {demo,records,queue,visits,store,click,choice,input,flush};
}

test('preview waits for explicit PM input, resumes and registers only when ready',()=>{
  const h=setup();
  h.click('start'); assert.match(h.demo.page(),/내용을 먼저 입력/);
  h.input('analysis-request','고객 입력 <script>alert(1)</script>\n사진 첨부'); h.click('start');
  h.flush();
  assert.equal(h.demo.current().step,3); assert.equal(h.records.length,0);
  assert.match(h.demo.page(),/진행 조건을 확인/);
  assert.doesNotMatch(h.demo.page(),/<script>/);
  h.click('answer'); assert.equal(h.demo.current().step,3);
  h.choice('other'); h.click('answer'); assert.equal(h.demo.current().step,3);
  h.input('analysis-pm-other','사진은 이번에 제외'); h.click('answer'); h.flush();
  assert.equal(h.demo.current().ready,true); assert.equal(h.records.length,1);
  assert.equal(h.demo.current().answer,'사진은 이번에 제외');
  assert.equal(h.records[0].analysisDemo,true); assert.equal(h.records[0].api,undefined);
  assert.match(h.demo.resultPreview(h.records[0].id,'draft'),/사진은 이번에 제외/);
  h.click('final'); h.choice('approve'); h.click('answer');
  assert.equal(h.records[0].process,'검토 완료');
});

test('PM can close, hold and reopen without silently continuing',()=>{
  const h=setup(); h.input('analysis-request','검색 기능 추가'); h.click('start'); h.flush();
  h.click('close-dialog'); h.flush(); assert.equal(h.demo.current().step,3);
  h.click('question'); h.choice('추가 정보 확인 후 진행'); h.click('answer'); h.flush();
  assert.equal(h.demo.current().hold,true); assert.equal(h.demo.current().ready,false);
  h.click('question'); h.choice('기존 기능을 재사용하는 가정으로 초안 작성'); h.click('answer'); h.flush();
  h.click('final'); h.choice('hold'); h.click('answer');
  assert.equal(h.demo.current().approved,false);
  h.click('final'); h.choice('approve'); h.click('answer');
  assert.equal(h.demo.current().hold,false);
});

test('completed requests and each agent result survive reload without a backend',()=>{
  const h=setup(); h.click('sample'); h.click('start'); h.flush();
  const id=h.demo.current().id;
  h.demo.click({closest:s=>s==='[data-demo-tab]'?{dataset:{demoId:id,demoTab:'scope'}}:null});
  assert.deepEqual(h.visits,[[id,'scope']]);
  h.choice('기존 기능을 재사용하는 가정으로 초안 작성'); h.click('answer'); h.flush();
  const reloaded=setup(h.store); reloaded.demo.enter(id);
  assert.equal(reloaded.records.length,1);
  for(const tab of ['original','scope','impact','draft']) assert.ok(reloaded.demo.resultPreview(id,tab).length);
  assert.equal(reloaded.demo.current().running,false);
});

test('selected local model is recorded independently for each run',()=>{
  const h=setup(); h.demo.change({id:'analysis-model',value:'local'});
  h.click('sample'); h.click('start');
  assert.equal(h.demo.current().provider,'local');
  assert.match(h.demo.page(),/로컬 LLM 선택/);
  assert.match(h.demo.page(),/실제 AI 호출 없음/);
});

test('snapshot keeps real Agent 1 item types, spans and questions',()=>{
  const items=[
    {item_id:'C-1',case_id:'C',display_no:'R1',item_type:'deadline',intent:'constraint',text:'기존 오픈 일정 유지',spans:[{start:0,end:3,text:'기존 '}]},
    {item_id:'C-2',case_id:'C',display_no:'R2',item_type:'question',intent:'estimate_request',text:'견적 요청',spans:[]}
  ];
  const questions=[{question_id:'Q1',text:'확인'}];
  const snap=analysisSnapshot({id:'C',text:'원문',created:'2026-11-05',provider:'gpt',step:2,items:items.map(i=>i.text),realA1:{status:'READY',items,questions}});
  assert.deepEqual(snap.a1.items.map(i=>i.item_type),['deadline','question']);
  assert.equal(snap.a1.items[0].spans.length,1);
  assert.deepEqual(snap.a1.questions,questions);
  const realA2={status:'READY_FOR_REVIEW',assessments:[{item_id:'C-1',label:'in_scope',rationale:'계약 근거',evidence_level:'explicit',evidence:[{doc_id:'D1'}]}],questions:[]};
  const withA2=analysisSnapshot({id:'C',text:'원문',created:'x',step:3,items:['a'],realA1:{status:'READY',items,questions},realA2});
  assert.equal(withA2.a2.assessments[0].label,'in_scope');
  assert.equal(withA2.input.a2_result.assessments[0].evidence.length,1);
  const fallback=analysisSnapshot({id:'C',text:'원문',created:'x',step:2,items:['a']});
  assert.equal(fallback.a1.items[0].item_type,'function');
});

test('real flow runs Agent 2 right after Agent 1, then asks PM to confirm judgments',async()=>{
  const items=[
    {item_id:'X-1',case_id:'X',display_no:'R1',item_type:'function',intent:'change_request',text:'리뷰 기능',spans:[]},
    {item_id:'X-2',case_id:'X',display_no:'R2',item_type:'deadline',intent:'constraint',text:'기존 오픈 일정 유지',spans:[]}
  ];
  const a2={status:'READY_FOR_REVIEW',assessments:[{item_id:'X-1',label:'out_of_scope',rationale:'제외 기능',evidence:[{doc_id:'FSP-A-001-v1.0',section_id:'F-11'}]}],questions:[],meta:{result_id:'a2-1'}};
  const calls=[];
  globalThis.fetch=async(url,opts)=>{const body=JSON.parse(opts.body);calls.push(body.kind);
    if (body.kind==='items') return {ok:true,json:async()=>({state:{state_revision:3,stage:'pm_review'},a2})};
    return {ok:true,json:async()=>({state:{state_revision:4},a3:{status:'NOT_REQUIRED',estimates:[]}})};};
  const store=new Map(),records=[];
  let demo;
  const analysisApi={start:async()=>({analysis_id:'A',case_id:'X',structured_request:{structured:{items,questions:[]}},
    schedule_reference:{item_ids:['X-2'],as_of:'2026-10-05',contract_end:'2026-11-24',contract_end_ref:'CO-A-001-v1.0:CO-02',applied_change_orders:['CO-A-001-v1.0'],milestones:[],note:'기준 안내'},
    workflow:{state_revision:2,stage:'pm_items'}})};
  demo=createAnalysisDemo({records,storage:{getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v)},render:()=>{},navigate:()=>{},openAnalysis:id=>demo.enter(id),schedule:()=>{},analysisApi});
  const click=action=>demo.click({closest:s=>s==='[data-demo-action]'?{dataset:{demoAction:action}}:null});
  demo.input({id:'analysis-request',value:'요청'});
  click('start'); await new Promise(r=>setTimeout(r,10));
  assert.deepEqual(calls,['items']);
  const page=demo.page();
  assert.match(page,/범위 판정 결과를 확인해 주세요/);
  assert.match(page,/2026-11-24/);
  assert.match(page,/FSP-A-001-v1.0 F-11/);
  demo.change({name:'analysis-pm-choice',value:'범위 판정 결과 확인 완료, 영향 산정 진행'});
  click('answer'); await new Promise(r=>setTimeout(r,10));
  assert.deepEqual(calls,['items','pm_review']);
});
