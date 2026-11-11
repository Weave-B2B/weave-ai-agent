import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source=await readFile(new URL('../analysis-demo.js',import.meta.url),'utf8');
const {createAnalysisDemo,analysisSnapshot,analysisTitle}=await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('request title uses the feature summary while keeping the complete source',()=>{
  const s={text:'고객 원문 '.repeat(100),realA1:{items:[{item_type:'function',text:'구매자 리뷰 기능 — 별점·텍스트 및 상세 조건'},
    {item_type:'constraint',text:'일정은 미정'},{item_type:'function',text:'알림 추가'}]}};
  const original=s.text;
  assert.equal(analysisTitle(s),'구매자 리뷰 기능 외 1건');
  assert.ok(analysisTitle({text:'긴 원문'.repeat(100)}).length<=49);
  assert.equal(s.text,original);
});

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
  assert.equal(snap.a2,null); // No invented scope result before the real worker runs.
  assert.equal(snap.input.constraints[0].text,'기존 오픈 일정 유지');
  const realA2={status:'READY_FOR_REVIEW',assessments:[{item_id:'C-1',label:'in_scope',rationale:'계약 근거',evidence_level:'explicit',evidence:[{doc_id:'D1'}]}],questions:[]};
  const withA2=analysisSnapshot({id:'C',text:'원문',created:'x',step:3,items:['a'],realA1:{status:'READY',items,questions},realA2});
  assert.equal(withA2.a2.assessments[0].label,'in_scope');
  assert.equal(withA2.input.a2_result.assessments[0].evidence.length,1);
  const fallback=analysisSnapshot({id:'C',text:'원문',created:'x',step:2,items:['a']});
  assert.equal(fallback.a1.items[0].item_type,'function');
  const realA3={status:'IMPACT_DRAFT_READY',estimates:[{item_ids:['C-1'],status:'draft',lines:[{task:'t',role:'FE',md:2,item_ids:['C-1']}],totals:{md:2,cost:1000,price:1500,margin:0.3},rate_card_id:'RC-X',schedule:{business_days:null,reason:'r'}}],questions:[]};
  const withA3=analysisSnapshot({id:'C',text:'원문',created:'x',step:6,items:['a'],realA1:{status:'READY',items,questions},realA3});
  assert.equal(withA3.view.phase,'ORCHESTRATION');
  assert.equal(withA3.view.impact.estimates[0].rate_card_id,'RC-X');
  assert.equal(withA3.view.impact.estimates[0].totals.cost,1000);
});

test('Supervisor automatically retries a temporary failure and stops for a PM answer',async t=>{
  const previous=globalThis.fetch;
  t.after(()=>{globalThis.fetch=previous;});
  const a1={items:[{item_id:'X-1',item_type:'function',intent:'change_request',text:'리뷰 기능'}]};
  const question={question_id:'Q1',topic:'사진도 포함할까요?',target:'pm',blocking:true};
  const calls=[];
  const steps=[
    {event:{action:'assess_scope',status:'error',reason:'범위 확인',error:{message:'일시 오류',retryable:true}},state:{a1,state_revision:0}},
    {event:{action:'assess_scope',status:'ready'},state:{a1,state_revision:1,a2:{assessments:[]}}},
    {event:{action:'ask_pm',status:'waiting_input'},state:{a1,state_revision:2,question}},
    {event:{action:'draft_documents',status:'ready'},state:{a1,state_revision:4,ready_for_review:true,
      agent3:{impact:{status:'NOT_REQUIRED',estimates:[]},snapshot_hash:'H1',documents:{email:{subject:'초안',body:'본문'}}}}},
  ];
  globalThis.fetch=async(url,options)=>{
    const path=url.split('/').at(-1);
    calls.push(path);
    let result;
    if(path==='cases') result={a1,state_revision:0};
    else if(path==='answer') {
      const {operation_id,...payload}=JSON.parse(options.body);
      assert.equal(typeof operation_id,'string');
      assert.match(url,/\/prototype\/runs\/cases\//);
      assert.deepEqual(payload,{question_id:'Q1',text:'포함해 주세요',state_revision:2});
      result={a1,state_revision:3};
    } else result=steps.shift();
    return {ok:true,json:async()=>result};
  };
  let demo;
  demo=createAnalysisDemo({records:[],storage:{getItem:()=>null,setItem:()=>{}},render:()=>{},
    navigate:()=>{},openAnalysis:id=>demo.enter(id),analysisApi:{}});
  const click=action=>demo.click({closest:s=>s==='[data-demo-action]'?{dataset:{demoAction:action}}:null});
  demo.input({id:'analysis-request',value:'요청'});
  click('start');
  await new Promise(setImmediate);
  assert.deepEqual(calls,['cases','step','step','step']);
  assert.match(demo.page(),/자동 재시도합니다/);
  assert.match(demo.page(),/사진도 포함할까요/);
  assert.doesNotMatch(demo.page(),/총괄 실행 계속/);
  demo.input({id:'analysis-pm-other',value:'포함해 주세요'});
  click('answer');
  await new Promise(setImmediate);
  assert.deepEqual(calls,['cases','step','step','step','answer','step']);
  assert.match(demo.page(),/최종 검토하기/);
});

test('common Run exposes server choices and unknown answers stay pending',async t=>{
  const old=globalThis.fetch; t.after(()=>{globalThis.fetch=old;});
  const calls=[];
  const a1={items:[{item_id:'R1',item_type:'function',intent:'change_request',text:'리뷰'}]};
  const q={question_id:'Q1',text:'업로드 방식을 선택하세요',choices:['재사용','신규 구현','모름'],allow_other:true};
  let revision=1;
  globalThis.fetch=async(url,options)=>{
    calls.push({url,body:options?.body?JSON.parse(options.body):null});
    if(url.endsWith('/cases')) return {ok:true,json:async()=>({a1,state_revision:revision,storage:'local-sqlite'})};
    if(url.endsWith('/answer')) { revision++; return {ok:true,json:async()=>({a1,state_revision:revision,question:q,storage:'local-sqlite'})}; }
    return {ok:true,json:async()=>({event:{status:'waiting_input'},state:{a1,state_revision:revision,question:q,storage:'local-sqlite'}})};
  };
  let demo;
  demo=createAnalysisDemo({records:[],storage:{getItem:()=>null,setItem:()=>{}},render:()=>{},navigate:()=>{},openAnalysis:id=>demo.enter(id),analysisApi:{}});
  demo.input({id:'analysis-request',value:'합성 요청'});
  demo.click({closest:s=>s==='[data-demo-action]'?{dataset:{demoAction:'start'}}:null});
  await new Promise(setImmediate);
  assert.ok(calls.every(call=>call.url.startsWith('/prototype/runs/cases')));
  assert.match(demo.page(),/신규 구현/);
  assert.doesNotMatch(demo.page(),/id="analysis-pm-other"/);
  await demo.change({name:'analysis-pm-choice',value:'모름'});
  await new Promise(setImmediate);
  assert.equal(demo.current().supervisorQuestion.question_id,'Q1');
  assert.equal(demo.current().running,false);
  assert.equal(calls.find(c=>c.url.endsWith('/answer')).body.text,'모름');
  assert.ok(calls.find(c=>c.url.endsWith('/answer')).body.operation_id);
});

test('Supervisor groups repeated live tool events under a single Agent 2 result',{timeout:3000},async t=>{
  const previous=globalThis.fetch;
  t.after(()=>{globalThis.fetch=previous;});
  const parent={seq:1,kind:'step',agent:'Agent 2',title:'범위 검토 중'};
  const tool={kind:'tool',parent_seq:1,tool:'search_docs'};
  const started={...tool,seq:2,phase:'start',label:'계약 문서 검색',title:'계약 문서 검색 중'};
  const finished={...tool,seq:3,phase:'done',matches:4};
  const progress=[parent,started,finished,{...started,seq:4},{...finished,seq:5,matches:3}];
  const a1={items:[]};
  const question={question_id:'Q1',topic:'사진도 포함할까요?'};
  let finishStep, signal, showProgress;
  const shown=new Promise(resolve=>{showProgress=resolve;});
  globalThis.fetch=async(url,options)=>{
    if (url.endsWith('/cases')) return {ok:true,json:async()=>({a1,state_revision:0})};
    if (url.endsWith('/step')) {
      if (finishStep) return {ok:true,json:async()=>({event:{action:'ask_pm',status:'waiting_input'},
        state:{a1,state_revision:2,question,progress}})};
      return new Promise(resolve=>{finishStep=resolve;});
    }
    assert.match(url,/\/progress\?after=0$/);
    signal=options.signal;
    return {ok:true,json:async()=>({events:[parent,started]})};
  };
  let demo;
  demo=createAnalysisDemo({records:[],storage:{getItem:()=>null,setItem:()=>{}},render:()=>{},
    updateProgress:showProgress,navigate:()=>{},openAnalysis:id=>demo.enter(id),analysisApi:{}});
  demo.input({id:'analysis-request',value:'합성 요청'});
  demo.click({closest:s=>s==='[data-demo-action]'?{dataset:{demoAction:'start'}}:null});
  const live=await shown;
  assert.equal(live.length,1);
  assert.match(live[0].html,/범위 검토 중.*계약 문서 검색 중/s);
  assert.equal(demo.current().running,true);
  finishStep({ok:true,json:async()=>({event:{action:'assess_scope',status:'ready'},
    state:{a1,a2:{assessments:[{}]},state_revision:1,progress}})});
  await new Promise(setImmediate);
  assert.equal(signal.aborted,true);
  assert.equal(demo.current().running,false);
  const rows=demo.current().events.filter(e=>e.agent==='Agent 2');
  assert.equal(rows.length,1);
  assert.equal(rows[0].tab,'scope');
  assert.match(demo.page(),/범위와 근거를 검토했습니다.*계약 문서 검색 2회 \(최근 검색 후보 3건\).*PM 답변이 필요합니다/s);
});

test('real NOT_REQUIRED never falls back to simulated estimate or document and registers safely',()=>{
  const session={id:'CR-UI-REAL1',text:'기존 범위 확인',created:'2026-11-09',events:[],step:6,ready:true,analysisId:'analysis-1',realA3:{status:'NOT_REQUIRED',estimates:[],questions:[]},realA2:{assessments:[{label:'in_scope'}]}};
  const snap=analysisSnapshot(session);
  assert.equal(snap.view.phase,'NOT_REQUIRED');
  assert.equal(snap.view.impact.estimates.length,0);
  const h=setup(new Map([['weave-analysis-preview-v1',JSON.stringify([session])]]));
  assert.equal(h.records[0].md,'산정 대상 없음');
  assert.equal(h.records[0].price,'—');
  assert.equal(h.records[0].scope,'범위 내');
  assert.match(h.demo.resultPreview(session.id,'draft'),/메일 초안 생성/);
  assert.doesNotMatch(h.demo.resultPreview(session.id,'draft'),/4,250,000/);
});

test('real amounts aggregate without turning null into zero, including zero amounts',()=>{
  const session={id:'CR-UI-REAL2',text:'다중 항목',created:'2026-11-09',events:[],step:6,ready:true,analysisId:'analysis-2',realA3:{status:'IMPACT_DRAFT_READY',estimates:[{totals:{md:0,price:0,cost:0}},{totals:{md:2,price:null,cost:20}}]}};
  const h=setup(new Map([['weave-analysis-preview-v1',JSON.stringify([session])]]));
  assert.equal(h.records[0].md,'2 MD');
  assert.equal(h.records[0].price,'미산정');
  session.realA3.estimates.pop();
  const zero=setup(new Map([['weave-analysis-preview-v1',JSON.stringify([session])]]));
  assert.equal(zero.records[0].md,'0 MD');
  assert.equal(zero.records[0].price,'0원');
});

test('unfinished real execution cannot fabricate a legacy preview',()=>{
  const snap=analysisSnapshot({id:'CR-UI-REAL3',text:'요청',created:'2026-11-09',events:[],step:6,analysisId:'a',realA3:null});
  assert.equal(snap.view,null);
});

test('additional information needed cannot be marked as finally approved',()=>{
  const session={id:'CR-UI-NEEDS',text:'추가 확인',created:'2026-11-09',events:[],step:6,ready:true,analysisId:'analysis-3',realA3:{status:'NEEDS_INPUT',estimates:[],questions:[]}};
  const h=setup(new Map([['weave-analysis-preview-v1',JSON.stringify([session])]]));
  h.demo.enter(session.id); h.click('final'); h.choice('approve'); h.click('answer');
  assert.notEqual(h.demo.current().approved,true);
  assert.match(h.demo.page(),/추가 확인이 필요한 산정 결과/);
});

test('final choices apply immediately and custom text is shown only for direct input',()=>{
  const session={id:'CR-UI-CHOICE',text:'테스트',created:'2026-11-09',events:[],step:6,ready:true,analysisId:'analysis-choice',realA3:{status:'NOT_REQUIRED',estimates:[],questions:[]}};
  const h=setup(new Map([['weave-analysis-preview-v1',JSON.stringify([session])]]));
  h.demo.enter(session.id); h.click('final');
  assert.doesNotMatch(h.demo.page(),/id="analysis-pm-other"/);
  h.choice('other');
  assert.match(h.demo.page(),/id="analysis-pm-other"/);
  assert.match(h.demo.page(),/입력 내용 반영/);
  h.input('analysis-pm-other','확인 후 수정'); h.click('answer');
  assert.equal(h.demo.current().hold,true);
  assert.ok(h.demo.current().events.some(event=>event.body==='확인 후 수정'));
  h.demo.current().emailDraft={subject:'초안',body:'본문'}; h.click('final'); h.choice('approve');
  assert.equal(h.demo.current().approved,true);
  assert.equal(h.records.length,1);
});

test('Agent 3 blocking question submits answer and displays the updated result',async t=>{
  const q={question_id:'Q-1',stage:'agent3',text:'저장 방식은?',reason:'확인 필요',target:'pm',blocking:true,status:'open'};
  const session={id:'CR-UI-QUESTION',caseId:'C1',stateRevision:7,text:'요청',created:'2026-11-09',events:[],step:6,ready:true,analysisId:'A1',realA3:{status:'NEEDS_INPUT',estimates:[],questions:[q]},realQuestions:[q]};
  const h=setup(new Map([['weave-analysis-preview-v1',JSON.stringify([session])]])); h.demo.enter(session.id);
  const original=globalThis.fetch; t.after(()=>{globalThis.fetch=original;});
  let request;
  globalThis.fetch=async(url,opts)=>{request={url,body:JSON.parse(opts.body)};return {ok:true,json:async()=>({state:{state_revision:10,status:'WAITING_PM_REVIEW',stage:'lead_review'},questions:[{...q,status:'resolved'}],a3:{status:'IMPACT_DRAFT_READY',estimates:[],questions:[]}})};};
  assert.match(h.demo.page(),/추가 질문에 답변하기/);
  h.click('agent3-question'); assert.match(h.demo.page(),/저장 방식은/);
  h.input('analysis-pm-other','기존 저장 기능 사용'); h.click('answer');
  await new Promise(resolve=>setTimeout(resolve,10));
  assert.equal(request.url,'/cases/C1/questions/Q-1/answer');
  assert.equal(request.body.state_revision,7); assert.equal(request.body.answer.text,'기존 저장 기능 사용');
  assert.equal(h.demo.current().stateRevision,10); assert.equal(h.demo.current().realA3.status,'IMPACT_DRAFT_READY');
  assert.match(h.demo.page(),/최종 검토하기/);
});

test('failed question submission keeps the answer visible for retry',async t=>{
  const q={question_id:'Q-2',stage:'agent3',text:'확인?',reason:'필수',target:'pm',blocking:true,status:'open'};
  const session={id:'CR-UI-FAIL',caseId:'C2',stateRevision:1,text:'요청',created:'2026-11-09',events:[],step:6,ready:true,analysisId:'A2',realA3:{status:'NEEDS_INPUT',estimates:[],questions:[q]}};
  const h=setup(new Map([['weave-analysis-preview-v1',JSON.stringify([session])]])); h.demo.enter(session.id);
  const original=globalThis.fetch; t.after(()=>{globalThis.fetch=original;});
  globalThis.fetch=async()=>({ok:false,json:async()=>({detail:'현재 상태가 변경됐습니다'})});
  h.click('agent3-question'); h.input('analysis-pm-other','보존할 답변'); h.click('answer');
  await new Promise(resolve=>setTimeout(resolve,10));
  assert.match(h.demo.page(),/보존할 답변/); assert.match(h.demo.page(),/현재 상태가 변경됐습니다/);
  assert.equal(h.demo.current().stateRevision,1);
});

test('contract evidence need is not presented as a PM question',()=>{
  const q={question_id:'Q-E',stage:'agent3',field:'more_evidence',text:'변경합의서를 조사하세요',target:'pm',blocking:true,status:'open'};
  const session={id:'CR-UI-EVIDENCE',caseId:'C3',stateRevision:1,text:'요청',created:'2026-11-09',events:[],step:6,ready:true,analysisId:'A3',realA3:{status:'NEEDS_INPUT',estimates:[],questions:[q]}};
  const h=setup(new Map([['weave-analysis-preview-v1',JSON.stringify([session])]])); h.demo.enter(session.id);
  assert.match(h.demo.page(),/추가 문서 조사 필요/);
  assert.match(h.demo.page(),/Agent 2 재조사 자동 연결은 아직 준비 중/);
  assert.doesNotMatch(h.demo.page(),/추가 질문에 답변하기/);
});

test('mail draft is generated from the latest server result before final confirmation',async t=>{
  const session={id:'CR-UI-MAIL',caseId:'C-MAIL',stateRevision:4,text:'요청',created:'2026-11-09',events:[],step:6,ready:true,analysisId:'A-MAIL',realA3:{meta:{result_id:'A3-1'},status:'IMPACT_DRAFT_READY',estimates:[],questions:[]}};
  const h=setup(new Map([['weave-analysis-preview-v1',JSON.stringify([session])]])); h.demo.enter(session.id);
  const previous=globalThis.fetch; t.after(()=>{globalThis.fetch=previous;}); let calls=0;
  globalThis.fetch=async(url,opts)=>{calls++; assert.equal(url,'/cases/C-MAIL/email-draft'); const body=JSON.parse(opts.body); assert.deepEqual(body,{state_revision:4,target_result_id:'A3-1'}); return {ok:true,json:async()=>({state_revision:5,draft:{subject:'협의 안내',body:'고객용 메일',model:'test-model'}})};};
  h.click('final'); h.choice('approve'); await new Promise(resolve=>setTimeout(resolve,10));
  assert.equal(calls,1); assert.notEqual(h.demo.current().approved,true);
  assert.equal(h.demo.current().stateRevision,5); assert.match(h.demo.page(),/고객용 메일/);
  h.click('final'); h.choice('approve'); assert.equal(h.demo.current().approved,true);
});

test('restoring a case loads real server data and does not invent final approval',async t=>{
  const sessionId='CR-UI-RESTORE';
  const snapshot={analysis_id:`UI-${sessionId}`,case_id:sessionId,contract_request:{input:{context:{case_id:sessionId},message_text:'복원 요청',received_at:'2026-11-09'}}};
  const live={state:{context:{analysis_id:`UI-${sessionId}`},state_revision:8,status:'WAITING_PM_REVIEW',stage:'lead_review'},a1:{items:[]},a2:{assessments:[]},a3:{status:'NOT_REQUIRED',estimates:[],questions:[]},questions:[]};
  const old=globalThis.fetch; t.after(()=>{globalThis.fetch=old;}); globalThis.fetch=async()=>({ok:true,json:async()=>live});
  const storage=new Map(), records=[];
  const demo=createAnalysisDemo({records,storage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},render:()=>{},navigate:()=>{},openAnalysis:()=>{},analysisApi:{get:async()=>snapshot}});
  demo.enter(sessionId); await new Promise(resolve=>setTimeout(resolve,10));
  assert.equal(demo.current().text,'복원 요청'); assert.equal(demo.current().stateRevision,8);
  assert.equal(demo.current().approved,false); assert.equal(records.length,1);
  assert.match(demo.page(),/메일 초안 생성/);
});

test('PM question dialog offers contextual quick choices and submits immediately on click',async t=>{
  const q={question_id:'Q-REUSE',stage:'agent2',text:'기존 사진 업로드 기능을 재사용할 수 있는지 확인이 필요합니다.',reason:'원문만으로 확정되지 않았습니다.',target:'pm',blocking:true,status:'open'};
  const session={id:'CR-UI-QCHOICE',caseId:'C-QC',stateRevision:3,text:'요청',created:'2026-11-10',events:[],step:3,ready:false,supervisor:true,supervisorQuestion:q};
  const h=setup(new Map([['weave-analysis-preview-v1',JSON.stringify([session])]]));
  const old=globalThis.fetch; t.after(()=>{globalThis.fetch=old;});
  let submitted=null;
  globalThis.fetch=async(url,opts)=>{
    if (url.endsWith('/answer')) {
      submitted=JSON.parse(opts.body);
      return {ok:true,json:async()=>({state_revision:4,a1:{items:[]},ready_for_review:true,agent3:{impact:{status:'NOT_REQUIRED',estimates:[]},snapshot_hash:'H1',documents:{email:{subject:'제목',body:'본문'}}}})};
    }
    if (url.endsWith('/step')) {
      return {ok:true,json:async()=>({event:{action:'draft_documents',status:'ready'},state:{state_revision:4,a1:{items:[]},ready_for_review:true,agent3:{impact:{status:'NOT_REQUIRED',estimates:[]},snapshot_hash:'H1',documents:{email:{subject:'제목',body:'본문'}}}}})};
    }
    return {ok:true,json:async()=>({state_revision:3,a1:{items:[]},question:q})};
  };
  h.demo.enter(session.id);
  await new Promise(resolve=>setTimeout(resolve,10));
  h.click('agent3-question');
  const html=h.demo.page();
  assert.match(html,/기존 기능 재사용 가능/);
  assert.match(html,/신규 구현 필요/);
  assert.match(html,/기타 · 직접 입력/);
  assert.doesNotMatch(html,/id="analysis-pm-other"/);
  h.choice('기존 기능을 재사용할 수 있습니다 (기존 모듈 활용)');
  await new Promise(resolve=>setTimeout(resolve,10));
  assert.deepEqual(submitted,{question_id:'Q-REUSE',text:'기존 기능을 재사용할 수 있습니다 (기존 모듈 활용)',state_revision:3});
});
