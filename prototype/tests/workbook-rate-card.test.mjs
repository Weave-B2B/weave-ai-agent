import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile} from 'node:fs/promises';
const source=(await readFile(new URL('../workbook-rate-card.js',import.meta.url),'utf8')).replace(/checkStatus\(\);\s*$/,'');

function setup() {
  const nodes=new Map(),requests=[],responses=[],alerts=[];
  let sequence=0;
  const get=id=>{
    if(!nodes.has(id))nodes.set(id,{value:'',innerHTML:'',hidden:false,disabled:false,isConnected:true,
      events:{},addEventListener(type,fn){this.events[type]=fn;},focus(){}});
    return nodes.get(id);
  };
  const ctx={document:{getElementById:get},crypto:{randomUUID:()=>`op-${++sequence}`},
    setTimeout:()=>1,clearTimeout(){},alert:msg=>alerts.push(msg),
    fetch:async (url,options={})=>{requests.push({url,...options});const r=responses.shift();if(!r)throw Error('Unexpected fetch');
      return {ok:r.status===undefined||r.status<400,status:r.status||200,json:async()=>r.body};}};
  vm.createContext(ctx);
  vm.runInContext(source+';globalThis.h={renderPreview,renderOrchestration,runOrchestration,refreshOrchestration};',ctx);
  get('project-select').value='project-uuid';get('cr-select').value='request-uuid';
  return {get,requests,responses,alerts,h:ctx.h,click:async id=>get(id).events.click({currentTarget:get(id)})};
}
const base={runId:'run-uuid',status:'waiting_input',stateRevision:7,documents:{},estimateSummary:{},questions:[]};

test('uploaded names and spreadsheet errors cannot inject HTML',()=>{
  const h=setup(),attack='<img src=x onerror="window.review=1">';
  h.h.renderPreview({status:'ERROR',documentVersionId:'doc-uuid',sourceSha256:'a'.repeat(64),
    originalFilename:attack,registerable:false,canRegister:false,errors:[{cell:'C12',code:'INVALID_RATE',message:attack}],
    roleRates:[{jobRoleCode:'BE',jobRoleName:attack}]});
  assert.doesNotMatch(h.get('preview-area').innerHTML,/<img|onerror="/);
  assert.match(h.get('preview-area').innerHTML,/&lt;img/);
  h.h.renderOrchestration({...base,status:'waiting_final_review',documents:{email:{subject:attack,body:attack}}});
  assert.doesNotMatch(h.get('orch-result').innerHTML,/<img/);
});

test('a choice answers through the backend and displays the next real review state',async()=>{
  const h=setup();
  h.h.renderOrchestration({...base,questions:[{question_id:'q1',status:'open',blocking:true,text:'재사용 가능한가요?',choices:['가능','불가']}]});
  assert.match(h.get('orch-result').innerHTML,/PM 답변 필요/);
  assert.doesNotMatch(h.get('orch-result').innerHTML,/실행 완료/);
  h.responses.push({body:{...base,status:'waiting_final_review',stateRevision:9,questions:[]}});
  await h.click('answer-choice-0');
  const body=JSON.parse(h.requests[0].body);
  assert.match(h.requests[0].url,/\/orchestrations\/answers$/);
  assert.equal(body.questionId,'q1');assert.equal(body.answer,'가능');assert.equal(body.expectedStateRevision,7);
  assert.match(h.get('orch-result').innerHTML,/최종 검토 대기/);
});

test('other input is required and uses the same answer contract',async()=>{
  const h=setup();h.h.renderOrchestration({...base,questions:[{question_id:'q1',status:'open',text:'담당자는?',choices:[]}]});
  await h.click('answer-other');assert.equal(h.get('answer-input-area').hidden,false);
  await h.click('send-answer');assert.equal(h.requests.length,0);
  h.get('answer-text').value='백엔드 담당자가 확인했습니다.';
  h.responses.push({body:{...base,stateRevision:8}});await h.click('send-answer');
  assert.equal(JSON.parse(h.requests[0].body).answer,'백엔드 담당자가 확인했습니다.');
});

test('failed answer retries reuse the operation id and reject a different project',async()=>{
  const h=setup();h.h.renderOrchestration({...base,questions:[{question_id:'q1',status:'open',text:'확인?',choices:['예']}]});
  h.responses.push({status:502,body:{detail:'응답 유실'}});await h.click('answer-choice-0');
  h.responses.push({body:{...base,status:'waiting_final_review',stateRevision:8}});await h.click('answer-choice-0');
  assert.equal(JSON.parse(h.requests[0].body).operationId,JSON.parse(h.requests[1].body).operationId);
  h.get('project-select').value='another-project';await h.click('review-approve');
  assert.equal(h.requests.length,2);assert.match(h.alerts.at(-1),/바뀌었습니다/);
});

test('review sends only a decision and revision; hold exposes resume',async()=>{
  const h=setup();h.h.renderOrchestration({...base,status:'waiting_final_review',stateRevision:10});
  h.responses.push({body:{...base,status:'waiting',stateRevision:11,reason:'PM 보류'}});await h.click('review-hold');
  const body=JSON.parse(h.requests[0].body);
  assert.equal(body.decision,'hold');assert.equal(body.expectedStateRevision,10);assert.equal('bundle' in body,false);
  assert.match(h.get('orch-result').innerHTML,/보류 해제 후 계속/);
  h.responses.push({body:{...base,status:'completed',stateRevision:12}});await h.click('resume-run');
  assert.match(h.requests[1].url,/\/orchestrations\/resume$/);
  assert.match(h.get('orch-result').innerHTML,/최종 검토 완료/);
});
