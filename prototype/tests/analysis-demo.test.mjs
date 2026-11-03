import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../analysis-demo.js',import.meta.url),'utf8');
const {createAnalysisDemo}=await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

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
  assert.match(h.demo.detail(h.records[0].id,'draft'),/사진은 이번에 제외/);
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
  for(const tab of ['original','scope','impact','draft']) assert.ok(reloaded.demo.detail(id,tab).length);
  assert.equal(reloaded.demo.current().running,false);
});

test('selected local model is recorded independently for each run',()=>{
  const h=setup(); h.demo.change({id:'analysis-model',value:'local'});
  h.click('sample'); h.click('start');
  assert.equal(h.demo.current().provider,'local');
  assert.match(h.demo.page(),/로컬 LLM 선택/);
  assert.match(h.demo.page(),/실제 AI 호출 없음/);
});
