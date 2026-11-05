// Frontend-only interaction preview. No model, business API or backend writes.
const KEY = 'weave-analysis-preview-v1';
const SAMPLE = '상품 상세페이지에 구매자 별점과 후기 텍스트를 추가해 주세요.\n가능하면 사진도 최대 3장까지 첨부할 수 있으면 좋겠습니다.\n기존 오픈 일정은 유지하고, 추가 비용과 변경 협의안도 확인 부탁드립니다.';
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const tabs = [['original','Agent 1 · 요청 분석'],['scope','Agent 2 · 범위 검토'],['impact','Agent 3 · 영향 산정'],['draft','문서 초안']];
const won = n => `${n.toLocaleString('ko-KR')}원`;
const MODELS = {gpt:{label:'GPT',model:'gpt-6-luna'},local:{label:'로컬 LLM',model:'로컬 모델 · 연결 예정'}};

// View adapter only: do not submit these provisional records to an agent API.
export function analysisSnapshot(s) {
  const context={project_id:'P-COM-101',case_id:s.id,request_revision:1,baseline_id:'P-COM-101:BL-2',baseline_as_of:'2026-10-05',analysis_id:`UI-${s.id}`};
  const meta=stage=>({result_id:`ui-${stage}-${s.id}`,model:`화면 시연 · ${MODELS[s.provider||'gpt'].label}`,agent_version:'frontend-preview',debug:{}});
  const real=s.realA1?.items?.length?s.realA1:null;
  const items=real?real.items:(s.items||[]).map((text,i)=>({item_id:`${s.id}-${i+1}`,case_id:s.id,display_no:`R${i+1}`,item_type:'function',intent:'change_request',text,spans:[]}));
  const source={context,message_text:s.text,received_at:s.created,sender_name:'직접 입력',sender_role:'PM',channel:'manual',items,constraints:[],answers:[]};
  const a1=real?{...real,context:real.context||context,meta:real.meta||meta('a1')}:{context,status:'READY',items,questions:[],meta:meta('a1')};
  const a2=s.realA2?{...s.realA2,context:s.realA2.context||context,meta:s.realA2.meta||meta('a2')}:{context,status:'READY_FOR_REVIEW',assessments:items.map(i=>({item_id:i.item_id,label:'out_of_scope',rationale:'화면 흐름 확인을 위한 범위 밖 가상 예시입니다. 실제 계약 문서를 검색한 결과가 아닙니다.',evidence_level:'none',evidence:[],missing_info:[],uncertainties:['실제 근거 문서 미연결']})),questions:[],meta:meta('a2')};
  const lines=[['FE','화면 변경 · 고정 예시',3],['BE','내부 API 변경 · 고정 예시',3],['QA','동작 확인 · 고정 예시',0.5]].map(([role,task,md])=>({role,task,md,item_ids:items.map(i=>i.item_id),assumption:s.answer||'미확정'}));
  const input={...source,rate_card_id:'',a2_result:a2,pm_review:{status:'PENDING',items:[]}};
  const view=s.step>=6?{session_id:`ui-${s.id}`,state_revision:1,phase:'FRONTEND_PREVIEW',approval:null,demo_review:!!s.approved,history:[],impact:{context,meta:meta('a3'),questions:[],estimates:[{item_ids:items.map(i=>i.item_id),status:'draft',lines,totals:{md:6.5,cost:null,price:4250000},rate_card_id:'',schedule:{business_days:null,reason:'화면 시연용이며 실제 일정은 미산정'}}]},draft:s.ready?{email_subject:'[화면 시연] 변경 요청 협의 초안',email_body:`안녕하세요. 요청 사항에 대한 검토용 초안입니다.\n\n${s.text}\n\n확인 조건: ${s.answer}\n\n공수·금액은 고정 화면 시연값이며 실제 견적이 아닙니다. 고객 협의와 일정 확인이 필요합니다.`}:null,draft_model:meta('a3').model}:null;
  return {source,input,a1,a2,view};
}

export function createAnalysisDemo({records, storage, render, navigate, openAnalysis, schedule = setTimeout, analysisApi = null}) {
  let sessions = [], currentId = null, text = '', modal = null, selected = '', other = '', error = '', provider='gpt';
  try { const saved = JSON.parse(storage.getItem(KEY) || '[]'); if (Array.isArray(saved)) sessions = saved.filter(s => s?.id?.startsWith('CR-UI-') && typeof s.text === 'string' && Array.isArray(s.events)); } catch { /* optional browser storage */ }
  const current = () => sessions.find(s => s.id === currentId);
  sessions.forEach(s => { s.running=false; }); // Refresh restores records, never fabricates a running timer.
  const save = () => { try { storage.setItem(KEY, JSON.stringify(sessions)); } catch { /* current page remains usable */ } };
  const byId = id => sessions.find(s => s.id === id);
  function addRecord(s) {
    if (!s.ready) return;
    const row = {id:s.id,title:s.text.trim().split('\n')[0].slice(0,65),project:'모노마켓 · 화면 테스트',requester:'직접 입력',channel:'수동',date:s.created.slice(5,10),scope:'범위 밖 · 예시',process:s.approved?'검토 완료':s.hold?'보류':'PM 검토 필요',approval:s.approved?'PM 확인 · 화면 테스트':'미확인',customer:'미발송',md:`${s.md} MD · 예시`,price:won(s.price),owner:'김',elapsed:'방금',analysisDemo:true};
    const old = records.find(r => r.id === s.id); if (old) Object.assign(old,row); else records.unshift(row);
  }
  sessions.forEach(addRecord);
  function event(s, agent, title, body, tab) { s.events.push({agent,title,body,tab,time:new Date().toISOString()}); save(); }
  function show(kind) { modal=kind; selected=''; other=''; error=''; render(); }
  async function start() {
    if (!text.trim()) { error='고객 요청 내용을 먼저 입력해 주세요.'; render(); return; }
    if (current()?.running) return;
    const s = {id:`CR-UI-${crypto.randomUUID().slice(0,8).toUpperCase()}`,text:text.trim(),provider,created:new Date().toISOString(),events:[],step:0,running:true,ready:false,approved:false,answer:''};
    sessions.unshift(s); currentId=s.id; error=''; save(); openAnalysis(s.id); render();
    event(s,'매니저','요청분석 Workflow를 시작합니다','실제 Orchestration API를 호출합니다.');
    const inp = {
      context:{project_id:'P-COM-101',case_id:s.id,request_revision:1,baseline_id:'P-COM-101:BL-2',baseline_as_of:'2026-10-05',analysis_id:`UI-${s.id}`},
      received_at:s.created,
      sender_role:'PM',
      sender_name:'직접 입력',
      channel:'manual',
      channel_src:'PROTOTYPE',
      subject:'요청분석',
      message_text:s.text,
      answers:[]
    };
    try {
      if (!analysisApi) throw new Error('Analysis API가 연결되지 않았습니다.');
      const result=await analysisApi.start(inp,'RC-SYNTH-2026H2-v2');
      s.analysisId=result.analysis_id; s.caseId=result.case_id;
      s.realA1=result.structured_request?.structured||null;
      s.realA2=result.scope_analysis?.scope||null;
      s.realA3=result.cost_estimation?.cost||null;
      s.stateRevision=result.workflow?.state_revision||0;
      s.schedule=result.schedule_reference||null;
      s.items=(s.realA1?.items||[]).map(item=>item.text);
      event(s,'Agent 1','요청 항목을 구조화했습니다',`${s.items.length}개 항목을 확인했습니다.`,'original');
      s.step=2; save(); render();
      if (result.workflow?.stage!=='pm_items') { s.running=false; event(s,'매니저','Agent 1이 추가 확인을 요청했습니다','질문에 답한 뒤 다음 단계로 진행할 수 있습니다.','original'); save(); render(); return; }
      await runScope(s);
    } catch (err) {
      s.running=false; s.error=err.message||'요청분석 실행에 실패했습니다.';
      event(s,'매니저','Workflow 실행 실패',s.error);
      error=s.error; save(); render();
    }
  }
  async function resume(s,body) {
    const response=await fetch(`/cases/${encodeURIComponent(s.caseId)}/resume`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({state_revision:s.stateRevision,...body})});
    const data=await response.json().catch(()=>null);
    if (!response.ok) throw new Error(data?.detail||'Workflow 재개에 실패했습니다.');
    s.stateRevision=data.state.state_revision;
    return data;
  }
  // Agent 1 항목을 그대로 Agent 2에 넘기고, 판정 결과가 나온 뒤에 PM 확인을 받는다.
  async function runScope(s) {
    const confirmed=await resume(s,{kind:'items',items:s.realA1.items});
    s.realA2=confirmed.a2||null;
    const count=confirmed.a2?.assessments?.length||0;
    if (!s.realA2 || confirmed.state.stage!=='pm_review') {
      s.running=false; event(s,'Agent 2','범위 검토가 추가 확인을 요청했습니다',`${count}개 항목 판단 중 확인이 필요합니다.`,'scope'); save(); render(); return;
    }
    s.step=3; s.running=false;
    event(s,'Agent 2','문서 기반 범위 판정을 마쳤습니다',`${count}개 항목의 판정과 근거를 확인해 주세요.${s.schedule?` 기존 일정: 계약 종료 ${s.schedule.contract_end}.`:''}`,'scope');
    save(); render(); if (currentId===s.id) show('review');
  }
  function advance(s) {
    if (!s.running) return;
    if (s.step===0) { event(s,'매니저','요청을 확인하고 있습니다','화면 시연 시나리오를 시작합니다. 실제 AI는 호출하지 않습니다.'); s.step=1; }
    schedule(() => {
      if (!s.running) return;
      if (s.step===1) {
        s.items = s.text.split(/\n+/).filter(Boolean);
        event(s,'Agent 1','요청 항목을 정리했습니다',`${s.items.length}개 원문 구간을 표시합니다. 항목화와 의도 판단은 실제 모델 연결 후 수행합니다.`,'original');
        s.step=2; render(); advance(s);
      } else if (s.step===2) {
        event(s,'Agent 2','문서 조사 결과를 확인합니다','가상 조사 완료 · 범위 밖 변경 예시입니다. 실제 계약 문서를 검색한 결과가 아닙니다.','scope');
        s.step=3; s.running=false; save(); render(); if (currentId===s.id) show('question');
      } else if (s.step===4) {
        event(s,'Agent 1','PM 답변을 요청 조건에 반영했습니다',s.answer,'original'); s.step=5; render(); advance(s);
      } else if (s.step===5) {
        s.md=6.5; s.price=4250000;
        event(s,'Agent 3','임시 산정 결과를 준비했습니다','6.5 MD · 고객 제안가 4,250,000원. 이 숫자는 입력과 무관한 고정 시연값입니다. 일정은 미산정입니다.','impact');
        s.step=6; render(); advance(s);
      } else if (s.step===6) {
        s.ready=true; s.running=false;
        event(s,'매니저','메일과 변경합의서 초안이 준비됐습니다','변경 요청 목록에 추가했습니다. 결과를 최종 검토해 주세요.','draft');
        addRecord(s); save(); render();
      }
    },650);
    save(); render();
  }
  async function answer() {
    const s=current(); if (!s) return;
    if (!selected || (selected==='other'&&!other.trim())) { error='선택하거나 기타 내용을 입력해 주세요.'; render(); return; }
    const value=selected==='other'?other.trim():selected;
    if (modal==='final') {
      modal=null;
      if (selected==='approve') { s.approved=true; s.hold=false; event(s,'PM','최종 검토를 완료했습니다','화면 테스트에서 확인한 결과입니다. 고객 합의·발송은 하지 않았습니다.','draft'); addRecord(s); }
      else if (selected==='hold') { s.approved=false; s.hold=true; event(s,'PM','검토를 보류했습니다','현재 결과와 이력을 보존합니다.'); addRecord(s); }
      else { s.approved=false; s.hold=true; event(s,'PM','수정 요청을 기록했습니다',value); addRecord(s); }
      save(); render(); return;
    }
    if (modal==='review'&&selected==='hold') { modal=null; s.hold=true; s.running=false; event(s,'PM','판정 확인을 보류했습니다','현재 결과와 이력을 보존합니다.'); save(); render(); return; }
    s.answer=value; modal=null;
    event(s,'PM','확인 내용을 전달했습니다',value);
    if (s.analysisId && s.realA2) {
      s.hold=false; s.running=true; render();
      try {
        const itemsById=new Map(s.realA1.items.map(item=>[item.item_id,item]));
        const reviewItems=(s.realA2.assessments||[]).map(assessment=>{
          const item=itemsById.get(assessment.item_id);
          const label=assessment.label;
          return {item_id:assessment.item_id,ai_label:label,pm_label:label,estimate:label==='out_of_scope'&&['change_request','estimate_request'].includes(item?.intent),reason:selected==='other'?value:'',evidence_refs:[]};
        });
        const reviewed=await resume(s,{kind:'pm_review',review:{
          review_id:`PMR-${s.id}-${Date.now()}`,
          review_revision:1,
          target_result_id:s.realA2.meta.result_id,
          reviewer:'prototype-pm',
          reviewed_at:new Date().toISOString(),
          status:'REVIEWED',
          items:reviewItems
        }});
        s.realA3=reviewed.a3||null;
        if (reviewed.a3) {
          event(s,'Agent 3','영향 산정 결과를 준비했습니다',reviewed.a3.status==='NOT_REQUIRED'?'산정 대상 항목이 없습니다.':'공수·비용 산정 결과가 준비됐습니다.','impact');
          s.step=6; s.ready=true;
          s.md=reviewed.a3.estimates?.[0]?.totals?.md||null;
          s.price=reviewed.a3.estimates?.[0]?.totals?.price||null;
        } else {
          s.step=3; s.ready=false;
        }
        s.running=false; modal=null; save(); render(); return;
      } catch (err) {
        s.running=false; error=err.message||'Workflow 재개에 실패했습니다.';
        event(s,'매니저','Workflow 재개 실패',error); save(); render(); return;
      }
    }
    if (value==='추가 정보 확인 후 진행') { s.hold=true; s.running=false; save(); render(); return; }
    s.hold=false; s.step=4; s.running=true; advance(s);
  }
  function stageLinks(s) {
    return tabs.map(([tab,label],index) => `<button class="analysis-stage" data-demo-tab="${tab}" data-demo-id="${esc(s.id)}" ${s.step < [2,3,6,6][index]?'disabled':''}><span class="analysis-stage-no">${index+1}</span><span>${label}</span><span aria-hidden="true">↗</span></button>`).join('');
  }
  function preview(s,tab='draft') {
    if (s.step < ({original:2,scope:3,impact:6,draft:6}[tab]||6)) return '<h2>아직 준비되지 않은 결과입니다</h2><p class="caption">통합 진행 기록에서 다음 작업을 진행해 주세요.</p>';
    if (tab==='original') return `<h2>고객 요청 원문</h2><p class="analysis-original">${esc(s.text)}</p><h3>Agent 1 구조화 결과</h3>${(s.realA1?.items||[]).map((item,i)=>`<div class="analysis-item"><span>${esc(item.display_no||`R${i+1}`)}</span><p><strong>${esc(TYPE_LABELS[item.item_type]||item.item_type||'항목')}</strong> · ${esc(item.text)}</p></div>`).join('')||'<p class="caption">구조화 항목이 없습니다.</p>'}<p class="caption">Orchestration Agent 1 결과입니다.</p>${s.answer?`<div class="notice">PM 답변: ${esc(s.answer)}</div>`:''}`;
    if (tab==='scope') {
      return `<h2>범위 검토 결과</h2>${scopeSummary(s)||'<p class="caption">범위 판단 결과가 없습니다.</p>'}<p class="caption">Orchestration Agent 2 결과입니다.</p>`;
    }
    if (tab==='impact') {
      if (s.realA3?.status==='NOT_REQUIRED'||!s.realA3?.estimates?.length) return `<h2>영향 산정</h2><div class="notice">${esc(s.realA3?.status==='NOT_REQUIRED'?'산정 대상이 없어 공수·비용을 산정하지 않았습니다.':'영향 산정 결과가 아직 없습니다.')}</div>`;
      const estimate=s.realA3.estimates[0];
      return `<h2>영향 산정 결과</h2><div class="analysis-numbers"><div><small>총 공수</small><strong>${estimate.totals?.md ?? '—'} <small>MD</small></strong></div><div><small>고객 제안가 · VAT 별도</small><strong>${estimate.totals?.price!=null?won(estimate.totals.price):'미산정'}</strong></div></div><p class="caption">Orchestration Agent 3 결과입니다.</p>`;
    }
    return `<h2>변경합의서 초안</h2><p class="caption">고객 협의 전 · 화면 시연용 · ${s.approved?'PM 확인됨':'PM 미확인'}</p><div class="analysis-document"><h3>변경 범위 협의서</h3><p>프로젝트: 모노마켓 · 화면 테스트</p><h3>1. 고객 요청</h3><p>${esc(s.text)}</p><h3>2. 검토 조건</h3><p>${esc(s.answer||'추가 확인 필요')}</p><h3>3. 공수 및 금액</h3><p>예상 공수 6.5 MD / 제안 금액 4,250,000원 (VAT 별도)<br>고정된 시연값이며 실제 산정 결과가 아닙니다.</p><h3>4. 일정 및 확인 사항</h3><p>일정 영향·적용일·검수 기준은 미정입니다. 양측의 협의와 서명 전에는 확정되지 않습니다.</p></div><details><summary>고객 전달용 메일 초안 보기</summary><p class="analysis-original">안녕하세요. 요청 주신 변경 사항에 대한 검토 초안을 전달드립니다.\n\n${esc(s.text)}\n\n세부 범위와 일정은 협의가 필요합니다. 첨부 초안을 검토해 주세요.\n※ 화면 시연용 예시이며 실제 발송하지 않았습니다.</p></details>`;
  }
  const TYPE_LABELS={function:'기능',question:'확인',constraint:'제약',deadline:'기한',dependency:'의존성'};
  const LABELS={in_scope:'범위 내',out_of_scope:'범위 밖',unclear:'판단 보류'};
  function scheduleBlock(s) {
    const ref=s.schedule; if (!ref) return '';
    const names=(s.realA1?.items||[]).filter(i=>ref.item_ids.includes(i.item_id)).map(i=>i.text).join(' / ');
    return `<div class="notice"><strong>기존 일정 확인</strong>${names?` · 요청: ${esc(names)}`:''}<br>계약 종료(최종 인수)일 ${esc(ref.contract_end)} · 근거 ${esc(ref.contract_end_ref)}${ref.applied_change_orders.length?` · 반영된 변경합의서 ${esc(ref.applied_change_orders.join(', '))}`:''}<br><span class="caption">${esc(ref.note)}</span></div>`;
  }
  function scopeSummary(s) {
    const items=new Map((s.realA1?.items||[]).map(i=>[i.item_id,i]));
    const rows=(s.realA2?.assessments||[]).map(a=>`<div class="analysis-item"><span>${esc(items.get(a.item_id)?.display_no||'')}</span><div><strong>${esc(LABELS[a.label]||a.label)}</strong> · ${esc(items.get(a.item_id)?.text||'')}<p>${esc(a.rationale||'판단 근거가 없습니다.')}</p>${(a.evidence||[]).length?`<p class="caption">근거 ${a.evidence.length}건: ${esc(a.evidence.map(e=>[e.doc_id,e.section_id].filter(Boolean).join(' ')).join(', '))}</p>`:'<p class="caption">인용된 근거 문서가 없습니다.</p>'}</div></div>`).join('');
    return scheduleBlock(s)+rows;
  }
  function dialog() {
    const s=current(); if (!modal||!s) return '';
    const final=modal==='final';
    const review=modal==='review';
    const choices=final?[['approve','현재 초안으로 확인 완료'],['hold','보류하고 나중에 확인'],['other','수정 요청 직접 입력']]:review?[['범위 판정 결과 확인 완료, 영향 산정 진행','확인하고 영향 산정 진행'],['hold','보류하고 나중에 확인'],['other','수정·추가 조건 직접 입력']]:[['기존 기능을 재사용하는 가정으로 초안 작성','기존 기능 재사용으로 진행'],['추가 정보 확인 후 진행','추가 정보를 확인한 뒤 진행'],['other','기타 · 직접 입력']];
    return `<dialog id="analysis-pm-dialog" aria-labelledby="analysis-pm-title"><div class="dialog-heading"><h2 id="analysis-pm-title">${final?'최종 결과를 확인해 주세요':review?'범위 판정 결과를 확인해 주세요':'진행 조건을 확인해 주세요'}</h2><button class="button plain" data-demo-action="close-dialog" aria-label="확인 창 닫기">닫기</button></div><p>${final?'범위 판단·산정·문서 초안을 한 번에 확인합니다. 실제 고객 발송 전 PM 검토입니다.':review?'Agent 2가 계약 문서를 근거로 판정한 결과입니다. 확인하면 Agent 3 영향 산정을 이어서 실행합니다.':'확인 내용을 선택한 뒤 Agent 2 범위 검토와 Agent 3 영향 산정을 이어서 실행합니다.'}</p>${review?scopeSummary(s):''}${final&&s.realA3?.estimates?.[0]?.totals?`<div class="notice">예상 공수 ${esc(s.realA3.estimates[0].totals.md)} MD · 판매가 ${won(s.realA3.estimates[0].totals.price)}</div>`:''}<fieldset class="analysis-choices"><legend class="caption">${final?'검토 결과':review?'확인 방법':'진행 방법'} 선택</legend>${choices.map(([v,l])=>`<label class="analysis-choice"><input type="radio" name="analysis-pm-choice" value="${esc(v)}" ${selected===v?'checked':''}><span>${l}</span></label>`).join('')}</fieldset><label class="field">기타 내용<textarea id="analysis-pm-other" placeholder="추가 조건이나 수정할 내용을 입력하세요." ${selected!=='other'?'disabled':''}>${esc(other)}</textarea></label>${error?`<p class="analysis-error" role="alert">${esc(error)}</p>`:''}<div class="form-actions"><button class="button primary" data-demo-action="answer">${final?'검토 결과 반영':'답변하고 계속'}</button></div></dialog>`;
  }
  function page() {
    const s=current();
    const heading=`<div class="page-heading"><div><p class="analysis-eyebrow">MULTI-AGENT WORKSPACE <span>실행 연동</span></p><h1>고객 요청, 여기서 시작하세요</h1><p class="subtitle">요청을 붙여넣으면 Agent 1, Agent 2, Agent 3 Workflow가 순서대로 실행됩니다.</p></div><button class="button" data-demo-action="new" ${s?.running?'disabled':''}>+ 새 요청</button></div>`;
    if (!s) return heading+`<section class="analysis-compose card"><div class="card-heading"><h2>고객 요청사항</h2><span class="mini-tag">모노마켓 · 테스트 프로젝트</span></div><label class="field"><span class="sr-only">고객 요청 원문</span><textarea id="analysis-request" placeholder="고객이 보낸 메일이나 메시지를 그대로 붙여넣으세요.\n\n기능 추가, 일정 조건, 견적 요청이 함께 있어도 괜찮아요." maxlength="12000">${esc(text)}</textarea></label><div class="analysis-compose-footer"><label class="analysis-model-picker">모델 <select id="analysis-model" aria-label="분석 모델"><option value="gpt" ${provider==='gpt'?'selected':''}>GPT · gpt-6-luna</option><option value="local" ${provider==='local'?'selected':''}>로컬 LLM</option></select></label><button class="button plain" data-demo-action="sample">예시 요청 넣기</button><button class="button primary" data-demo-action="start">요청 분석 시작 →</button></div>${error?`<p class="analysis-error" role="alert">${esc(error)}</p>`:''}</section><div class="analysis-intro">${[['01','필요한 에이전트가 분석','요청 정리와 문서 조사, 영향 산정의 진행 과정을 확인합니다.'],['02','필요한 순간에만 질문','PM 확인 후 범위 검토와 영향 산정을 이어서 실행합니다.'],['03','결과와 기록을 한곳에','Workflow 결과와 단계별 기록을 한 화면에서 확인합니다.']].map(([n,h,p])=>`<div><span>${n}</span><h3>${h}</h3><p>${p}</p></div>`).join('')}</div><p class="analysis-demo-note">실제 분석 API와 Orchestration Engine을 호출합니다.</p>${sessions.length?`<section class="card"><h2>이전 요청 분석</h2>${sessions.map(x=>`<button class="analysis-history" data-demo-open="${esc(x.id)}"><strong>${esc(x.text.slice(0,55))}</strong><span>${x.ready?'결과 준비':'진행 기록'} ↗</span></button>`).join('')}</section>`:''}`;
    return heading+`<div class="analysis-run-heading"><span class="mini-tag">${esc(s.id)}</span><strong>${s.running?'진행 중':s.hold?'보류':s.approved?'PM 확인 완료':s.ready?'최종 검토 대기':'PM 답변 대기'}</strong><span class="caption">${esc(MODELS[s.provider||'gpt'].label)} 선택 · Orchestration 실행</span></div><div class="analysis-workspace"><section class="card analysis-feed"><div class="card-heading"><h2>진행 기록</h2><span class="caption">기록을 누르면 해당 결과로 이동합니다</span></div><div class="analysis-request-bubble"><small>고객 요청</small><p>${esc(s.text)}</p></div><div class="analysis-events" aria-live="polite">${s.events.map(x=>`<article class="analysis-event"><span class="analysis-agent">${esc(x.agent)}</span>${x.tab?`<button class="analysis-event-link" data-demo-tab="${x.tab}" data-demo-id="${esc(s.id)}">${esc(x.title)} <span>결과 보기 ↗</span></button>`:`<h3>${esc(x.title)}</h3>`}<p>${esc(x.body)}</p></article>`).join('')}</div>${s.running?'<p class="loading-line" role="status"><span class="inline-spinner"></span>다음 작업을 준비하고 있습니다…</p>':!s.ready?'<button class="button primary" data-demo-action="question">진행 조건 확인하기</button>':`<div class="form-actions"><button class="button primary" data-demo-action="final">${s.approved?'최종 결과 다시 확인':'최종 검토하기'}</button><button class="button" data-demo-action="list">변경 요청 목록</button></div>`}</section><aside class="analysis-result"><section class="card"><h2>단계별 결과</h2><p class="caption">완료된 작업의 결과를 열어볼 수 있습니다.</p><div class="analysis-stages">${stageLinks(s)}</div></section>${s.ready?`<section class="card">${preview(s)}<p class="analysis-demo-note">Orchestration 결과를 현재 화면 형식으로 표시합니다.</p></section>`:`<section class="card analysis-result-empty"><span>◎</span><h3>결과가 이곳에 쌓입니다</h3><p>PM 확인 후 영향 산정과 문서 초안 미리보기가 이어집니다.</p></section>`}</aside></div>${dialog()}`;
  }
  return {page,byId,current,resultPreview(id,tab) { const s=byId(id); return s?preview(s,tab):''; },
    enter(id) { currentId=byId(id)?id:null; modal=null; error=''; },
    syncDialog() { if (modal) document.querySelector('#analysis-pm-dialog')?.showModal?.(); },
    input(target) { if (target.id==='analysis-request') text=target.value; if (target.id==='analysis-pm-other') other=target.value; },
    change(target) { if (target.id==='analysis-model' && MODELS[target.value]) { provider=target.value; render(); } if (target.name==='analysis-pm-choice') { selected=target.value; error=''; render(); } },
    cancel() { modal=null; error=''; },
    click(target) {
      const tab=target.closest('[data-demo-tab]'); if (tab) { navigate(tab.dataset.demoId,tab.dataset.demoTab); return true; }
      const open=target.closest('[data-demo-open]'); if (open) { openAnalysis(open.dataset.demoOpen); return true; }
      const action=target.closest('[data-demo-action]')?.dataset.demoAction; if (!action) return false;
      if (action==='start') start();
      if (action==='sample') { text=SAMPLE; render(); }
      if (action==='new'&&!current()?.running) { text=''; openAnalysis(); }
      if (action==='question') { if (current()?.analysisId) show(current().realA2?'review':'question'); else if (current()?.step!==3) { current().running=true; advance(current()); } else show('question'); }
      if (action==='final') show('final');
      if (action==='close-dialog') { modal=null; error=''; render(); }
      if (action==='answer') answer();
      if (action==='list') navigate(null);
      return true;
    }
  };
}
