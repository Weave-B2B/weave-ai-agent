// Local integrated prototype: Supervisor API executes server-side; legacy preview paths remain for historical sessions.
const KEY = 'weave-analysis-preview-v1';
const SAMPLE = '상품 상세페이지에 구매자 별점과 후기 텍스트를 추가해 주세요.\n가능하면 사진도 최대 3장까지 첨부할 수 있으면 좋겠습니다.\n기존 오픈 일정은 유지하고, 추가 비용과 변경 협의안도 확인 부탁드립니다.';
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function eventHtml(x, id, active) {
  const title = x.tab ? `<button class="analysis-event-link" data-demo-tab="${esc(x.tab)}" data-demo-id="${esc(id)}">${esc(x.title)} <span>결과 보기 ↗</span></button>` : `<h3>${esc(x.title)}</h3>`;
  const usage = x.tools || [];
  const current = active && usage.findLast(t => t.inFlight > 0)?.title;
  const tools = usage.map(t => `${t.label} ${t.count}회${t.matches != null ? ` (최근 검색 후보 ${t.matches}건)` : ''}${t.errors ? ` (오류 ${t.errors}회)` : ''}`).join(' · ');
  const detail = current || tools;
  return `<article class="analysis-event"${x.seq ? ` data-progress-step="${esc(x.seq)}"` : ''}><span class="analysis-agent">${esc(x.agent)}</span>${title}${x.body ? `<p>${esc(x.body)}</p>` : ''}${detail ? `<p class="analysis-tool-summary">${esc(detail)}</p>` : ''}</article>`;
}
const tabs = [['original', 'Agent 1 · 요청 분석'], ['scope', 'Agent 2 · 범위 검토'], ['impact', 'Agent 3 · 영향 산정'], ['draft', '문서 초안']];
const won = n => typeof n === 'number' && Number.isFinite(n) ? `${n.toLocaleString('ko-KR')}원` : '미산정';
const realRun = s => !!s.analysisId || !!s.realA1 || !!s.realA2 || !!s.realA3 || !!s.supervisor;
function totalsFor(s) {
  const rows = s.realA3?.estimates || [];
  if (!rows.length || rows.some(row => !row.totals)) return null;
  return Object.fromEntries(['md', 'cost', 'price'].map(key => [key, rows.every(row => typeof row.totals[key] === 'number' && Number.isFinite(row.totals[key])) ? rows.reduce((sum, row) => sum + row.totals[key], 0) : null]));
}
const MODELS = { gpt: { label: 'GPT', model: 'gpt-6-luna' }, local: { label: '로컬 LLM', model: 'gpt-oss:20b' } };

export function analysisTitle(s) {
  const features = (s.realA1?.items || []).filter(i => i.item_type === 'function');
  const first = features[0]?.text || s.text?.split(/[\n。!?]/)[0] || '신규 변경 요청';
  const label = first.split(/\s+[—–]\s+/)[0].replace(/\s+/g, ' ').trim();
  const short = Array.from(label).slice(0, 48).join('');
  return `${short}${Array.from(label).length > 48 ? '…' : ''}${features.length > 1 ? ` 외 ${features.length - 1}건` : ''}`;
}

// View adapter only: do not submit these provisional records to an agent API.
export function analysisSnapshot(s) {
  const context = { project_id: 'P-COM-101', case_id: s.id, request_revision: 1, baseline_id: 'P-COM-101:BL-2', baseline_as_of: '2026-10-05', analysis_id: `UI-${s.id}` };
  const meta = stage => ({ result_id: `ui-${stage}-${s.id}`, model: `화면 시연 · ${MODELS[s.provider || 'gpt'].label}`, agent_version: 'frontend-preview', debug: {} });
  const real = s.realA1?.items?.length ? s.realA1 : null;
  const items = real ? real.items : (s.items || []).map((text, i) => ({ item_id: `${s.id}-${i + 1}`, case_id: s.id, display_no: `R${i + 1}`, item_type: 'function', intent: 'change_request', text, spans: [] }));
  const source = { context, message_text: s.text, received_at: s.created, sender_name: '직접 입력', sender_role: 'PM', channel: 'manual', items, constraints: real ? items.filter(item => ['constraint', 'deadline', 'dependency'].includes(item.item_type)) : [], answers: s.answers || [] };
  const a1 = real ? { ...real, context: real.context || context, meta: real.meta || meta('a1') } : { context, status: 'READY', items, questions: [], meta: meta('a1') };
  const a2 = s.realA2 ? { ...s.realA2, context: s.realA2.context || context, meta: s.realA2.meta || meta('a2') } : realRun(s) ? null : { context, status: 'READY_FOR_REVIEW', assessments: items.map(i => ({ item_id: i.item_id, label: 'out_of_scope', rationale: '화면 흐름 확인을 위한 범위 밖 가상 예시입니다. 실제 계약 문서를 검색한 결과가 아닙니다.', evidence_level: 'none', evidence: [], missing_info: [], uncertainties: ['실제 근거 문서 미연결'] })), questions: [], meta: meta('a2') };
  const lines = [['FE', '화면 변경 · 고정 예시', 3], ['BE', '내부 API 변경 · 고정 예시', 3], ['QA', '동작 확인 · 고정 예시', 0.5]].map(([role, task, md]) => ({ role, task, md, item_ids: items.map(i => i.item_id), assumption: s.answer || '미확정' }));
  const input = { ...source, rate_card_id: '', a2_result: a2, pm_review: { status: 'PENDING', items: [] } };
  const a3 = s.realA3 ? { ...s.realA3, context: s.realA3.context || context, meta: s.realA3.meta || meta('a3') } : null;
  const view = a3 ? { session_id: `ui-${s.id}`, state_revision: 1, phase: a3.status === 'NOT_REQUIRED' ? 'NOT_REQUIRED' : 'ORCHESTRATION', approval: s.runBacked && s.approved ? {approved:true,review_id:s.supervisorReview?.review_id,reviewer_ref:'prototype-pm',target_result_id:a3.meta.result_id} : null, demo_review:!!s.approved, history:[], impact:a3, draft: s.emailDraft ? { email_subject: s.emailDraft.subject, email_body: s.emailDraft.body } : s.realA3.draft || null, draft_model: s.emailDraft?.model || a3.meta.model } : !realRun(s) && s.step >= 6 ? { session_id: `ui-${s.id}`, state_revision: 1, phase: 'FRONTEND_PREVIEW', approval: null, demo_review: !!s.approved, history: [], impact: { context, meta: meta('a3'), questions: [], estimates: [{ item_ids: items.map(i => i.item_id), status: 'draft', lines, totals: { md: 6.5, cost: null, price: 4250000 }, rate_card_id: '', schedule: { business_days: null, reason: '화면 시연용이며 실제 일정은 미산정' } }] }, draft: s.ready ? { email_subject: '[화면 시연] 변경 요청 협의 초안', email_body: `안녕하세요. 요청 사항에 대한 검토용 초안입니다.\n\n${s.text}\n\n확인 조건: ${s.answer}\n\n공수·금액은 고정 화면 시연값이며 실제 견적이 아닙니다. 고객 협의와 일정 확인이 필요합니다.` } : null, draft_model: meta('a3').model } : null;
  return { source, input, a1, a2, view };
}

export function createAnalysisDemo({ records, storage, render, updateProgress = render, navigate, openAnalysis, schedule = setTimeout, analysisApi = null }) {
  let sessions = [], currentId = null, text = '', modal = null, selected = '', other = '', error = '', provider = 'gpt';
  try { const saved = JSON.parse(storage.getItem(KEY) || '[]'); if (Array.isArray(saved)) sessions = saved.filter(s => s?.id?.startsWith('CR-UI-') && typeof s.text === 'string' && Array.isArray(s.events)); } catch { /* optional browser storage */ }
  const current = () => sessions.find(s => s.id === currentId);
  const pendingQuestion = s => s?.supervisor ? s.supervisorQuestion : (s?.realQuestions || s?.realA3?.questions || []).find(q => q.stage === 'agent3' && q.field !== 'more_evidence' && q.blocking && ['open', 'answered'].includes(q.status));
  const pendingEvidence = s => s?.supervisor ? null : (s?.realQuestions || s?.realA3?.questions || []).find(q => q.stage === 'agent3' && q.field === 'more_evidence' && q.blocking && ['open', 'answered'].includes(q.status));
  sessions.forEach(s => { s.running = false; s.progressStep = null; }); // Refresh restores records, never fabricates a running timer.
  const save = () => { try { storage.setItem(KEY, JSON.stringify(sessions)); } catch { /* current page remains usable */ } };
  const byId = id => sessions.find(s => s.id === id);
  function addRecord(s) {
    if (!s.ready) return;
    const totals = realRun(s) ? totalsFor(s) : { md: s.md, price: s.price };
    const absent = s.realA3?.status === 'NOT_REQUIRED';
    const labels = [...new Set((s.realA2?.assessments || []).map(a => ({ in_scope: '범위 내', out_of_scope: '범위 밖', unclear: '판단 보류' }[a.label] || '판단 확인 필요')))];
    const row = { id: s.id, title: analysisTitle(s), project: '모노마켓 · 화면 테스트', requester: '직접 입력', channel: '수동', date: s.created.slice(5, 10), scope: realRun(s) ? (labels.join(' · ') || '미분석') : '범위 밖 · 예시', process: s.approved ? '검토 완료' : s.hold ? '보류' : 'PM 검토 필요', approval: s.approved ? 'PM 확인 · 화면 테스트' : '미확인', customer: '미발송', md: absent ? '산정 대상 없음' : totals?.md != null ? `${totals.md} MD${realRun(s) ? '' : ' · 예시'}` : '미산정', price: absent ? '—' : won(totals?.price), owner: '김', elapsed: '방금', analysisDemo: true };
    const old = records.find(r => r.id === s.id); if (old) Object.assign(old, row); else records.unshift(row);
  }
  sessions.forEach(addRecord);
  function event(s, agent, title, body, tab) { s.events.push({ agent, title, body, tab, time: new Date().toISOString() }); save(); }
  function finishProgress(s, agent, title, body, tab) {
    const step = s.events.find(row => row.seq === s.progressStep);
    s.progressStep = null;
    if (step) { Object.assign(step, { title, body, tab }); save(); }
    else event(s, agent, title, body, tab);
  }
  async function restoreCase(id) {
    if (!analysisApi || !id || byId(id)) return;
    try {
      const runResponse = await fetch(`/prototype/runs/cases/${encodeURIComponent(id)}`);
      if (runResponse.ok) {
        const run = await runResponse.json();
        if ((run.storage === 'local-sqlite' || run.storage === 'backend-postgresql') && run.case_id === id && run.input?.context?.case_id === id) {
          const s = { id, caseId: id, text: run.input.message_text, created: run.input.received_at,
            provider: run.provider, analysisId: run.input.context.analysis_id, events: [], supervisor: true, runBacked: true, running: false, backendConnected: !!run.backend_connected };
          applySupervisorState(s, run); sessions.unshift(s); addRecord(s); save();
          if (currentId === id) render();
          return;
        }
      } else if (runResponse.status !== 404) throw new Error('로컬 Run 상태를 불러오지 못했습니다.');
      const result = await analysisApi.get(`UI-${id}`);
      if (result.case_id !== id || result.analysis_id !== `UI-${id}` || result.contract_request?.input?.context?.case_id !== id) throw new Error('분석 식별자가 일치하지 않습니다.');
      const response = await fetch(`/cases/${encodeURIComponent(id)}/state`);
      if (!response.ok) throw new Error('실행 상태를 불러오지 못했습니다.');
      const live = await response.json();
      if (live.state?.context?.analysis_id !== result.analysis_id) throw new Error('분석 버전이 변경됐습니다.');
      if (byId(id)) return;
      const inp = result.contract_request.input;
      const a1 = live.a1 || null, a2 = live.a2 || null, a3 = live.a3 || null;
      const events = [];
      if (a1) events.push({ agent: 'Agent 1', title: '기존 요청 분석 결과를 불러왔습니다', body: `${a1.items.length}개 항목`, tab: 'original' });
      if (a2) events.push({ agent: 'Agent 2', title: '기존 범위 검토 결과를 불러왔습니다', body: `${a2.assessments.length}개 항목`, tab: 'scope' });
      if (a3) events.push({ agent: 'Agent 3', title: '기존 영향 산정 결과를 불러왔습니다', body: '서버에 남아 있는 결과입니다.', tab: 'impact' });
      const s = {
        id, text: inp.message_text, provider: 'gpt', created: inp.received_at, events, analysisId: result.analysis_id, caseId: id,
        stateRevision: live.state.state_revision, workflowStage: live.state.stage, workflowStatus: live.state.status,
        realA1: a1, realA2: a2, realA3: a3, realQuestions: live.questions || [], emailDraft: live.email_draft || null,
        items: (a1?.items || []).map(item => item.text), step: a3 ? 6 : a2 ? 3 : a1 ? 2 : 0, ready: !!a3, running: false, approved: false, answer: ''
      };
      sessions.unshift(s); addRecord(s); save(); if (currentId === id) render();
    } catch (err) { if (currentId === id) { currentId = null; error = err.message || '기존 분석을 불러오지 못했습니다.'; render(); } }
  }
  function show(kind) {
    modal = kind;
    const q = kind === 'agent3-question' ? pendingQuestion(current()) : null;
    const isReuse = /재사용|기존/i.test(q?.text || '') || /재사용|기존/i.test(q?.reason || '');
    selected = kind === 'agent3-question' && !isReuse && !q?.choices?.length ? 'other' : '';
    other = ''; error = ''; render();
  }
  async function start() {
    if (!text.trim()) { error = '고객 요청 내용을 먼저 입력해 주세요.'; render(); return; }
    if (current()?.running) return;
    const s = { id: `CR-UI-${crypto.randomUUID().slice(0, 8).toUpperCase()}`, text: text.trim(), provider, created: new Date().toISOString(), events: [], step: 0, running: true, ready: false, approved: false, answer: '', supervisor: true, runBacked: !!analysisApi };
    sessions.unshift(s); currentId = s.id; error = ''; save(); openAnalysis(s.id); render();
    if (!analysisApi) {
      delete s.supervisor;
      advance(s);
      return;
    }
    event(s, '총괄', '요청 분석을 시작합니다', '');
    render();
    const inp = {
      context: { project_id: 'P-COM-101', case_id: s.id, request_revision: 1, baseline_id: 'P-COM-101:BL-2', baseline_as_of: '2026-10-05', analysis_id: `UI-${s.id}` },
      received_at: s.created,
      sender_role: 'PM',
      sender_name: '직접 입력',
      channel: 'manual',
      channel_src: 'PROTOTYPE',
      subject: '요청분석',
      message_text: s.text,
      answers: []
    };
    s.caseId = s.id; s.analysisId = inp.context.analysis_id;
    try {
      if (!analysisApi) throw new Error('Analysis API가 연결되지 않았습니다.');
      const result = await supervisorRequest('', { input: inp, rate_card_id: 'RC-SYNTH-2026H2-v2', provider: s.provider }, s);
      s.caseId = s.id; s.analysisId = inp.context.analysis_id;
      applySupervisorState(s, result);
      finishProgress(s, 'Agent 1', '요청 항목을 구조화했습니다', `${s.realA1?.items?.length || 0}개 항목을 확인했습니다.`, 'original');
      save(); render();
      await runSupervisor(s);
      return;
      /* Legacy sessions continue to use the original workflow below. */
      /*
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
      */
    } catch (err) {
      stopSupervisor(s, '실행 실패', err.message || '요청분석 실행에 실패했습니다.', !!err.retryable);
    }
  }
  function addProgress(s, events = []) {
    const changed = new Set();
    for (const { seq, kind, agent, title, parent_seq, tool, phase, label, matches } of events) {
      if (seq <= (s.progressCursor || 0)) continue;
      s.progressCursor = seq;
      if (kind === 'step') {
        const step = { seq, agent, title, tools: [] };
        s.events.push(step);
        s.progressStep = seq;
        changed.add(step);
      }
      if (kind === 'tool') {
        const step = s.events.find(row => row.seq === parent_seq);
        if (!step) continue;
        let usage = step.tools.find(row => row.tool === tool);
        if (phase === 'start') {
          if (!usage) {
            usage = { tool, label, title, count: 0, inFlight: 0, errors: 0 };
            step.tools.push(usage);
          }
          usage.count++;
          usage.inFlight++;
        } else if (usage) {
          usage.inFlight--;
          if (phase === 'done') usage.matches = matches;
          if (phase === 'error') usage.errors++;
        }
        changed.add(step);
      }
    }
    if (changed.size) save();
    return [...changed];
  }
  function watchProgress(s) {
    const controller = new AbortController();
    const url = `${supervisorBase(s)}/${encodeURIComponent(s.id)}/progress`;
    async function poll() {
      try {
        const response = await fetch(`${url}?after=${s.progressCursor || 0}`, { signal: controller.signal, cache: 'no-store' });
        if (response.ok) {
          const { events } = await response.json();
          if (controller.signal.aborted) return;
          const changed = addProgress(s, events);
          if (changed.length && currentId === s.id) {
            updateProgress(changed.map(x => ({ seq: x.seq, html: eventHtml(x, s.id, x.seq === s.progressStep) })));
          }
        }
      } catch { /* The completed request also returns the latest progress. */ }
      if (!controller.signal.aborted) timer = setTimeout(poll, 500);
    }
    let timer = setTimeout(poll, 500);
    return () => { clearTimeout(timer); controller.abort(); };
  }
  const supervisorBase = s => s?.runBacked ? '/prototype/runs/cases' : '/prototype/supervisor/cases';
  async function supervisorRequest(path, body, activitySession) {
    if (activitySession) activitySession.progressStep = null;
    const stopWatching = activitySession && watchProgress(activitySession);
    try {
      const response = await fetch(`${supervisorBase(activitySession || current())}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
      const data = await response.json().catch(() => null);
      if (!response.ok) {
        const failure = new Error(typeof data?.detail === 'string' ? data.detail : data?.detail?.message || data?.message || '총괄 실행에 실패했습니다.');
        failure.retryable = !!(data?.detail?.retryable || data?.retryable);
        throw failure;
      }
      return data;
    } finally {
      stopWatching?.();
    }
  }
  async function refreshSession(id) {
    const s = byId(id);
    if (!s?.runBacked) return s;
    const response = await fetch(`${supervisorBase(s)}/${encodeURIComponent(s.caseId)}`);
    const data = await response.json();
    if (!response.ok || data.case_id !== s.caseId) throw new Error('저장된 Run 결과를 확인하지 못했습니다.');
    applySupervisorState(s, data); addRecord(s); save();
    return s;
  }
  function applySupervisorState(s, data) {
    addProgress(s, data.progress);
    s.stateRevision = data.state_revision;
    s.realA1 = data.a1 || s.realA1; s.realA2 = data.a2 || null; s.realA3 = data.agent3?.impact || null;
    s.items = (s.realA1?.items || []).map(i => i.text);
    s.supervisorQuestion = data.question ? { ...data.question, text: data.question.topic || data.question.text } : null;
    s.emailDraft = data.agent3?.documents?.email || null;
    s.agreementDraft = data.agent3?.documents?.agreement || null;
    s.runStorage = data.storage || null;
    s.backendConnected = !!data.backend_connected;
    s.llmCalls = data.llm_calls ?? null;
    s.answers = data.answers || s.answers || [];
    if (s.answers.length) s.answer = s.answers.map(a => a.text).join('\n');
    s.supervisorSnapshotHash = data.agent3?.snapshot_hash || null;
    s.readyForReview = !!data.ready_for_review;
    s.approved = data.review?.decision === 'approve';
    s.supervisorReview = data.review || null;
    s.ready = !!(s.emailDraft && data.agent3?.documents);
    s.hold = !!data.hold_reason;
    s.supervisorHoldReason = data.hold_reason || null;
    if (data.run_status === 'FAILED') s.supervisorStop = { title: '실행이 중단되었습니다', reason: '진행 기록을 확인한 뒤 실패한 단계만 다시 시도할 수 있습니다.', retryable: (data.llm_calls || 0) < 80 };
    s.step = s.realA3 ? 6 : s.realA2 ? 3 : s.realA1 ? 2 : 0;
  }
  async function runSupervisor(s) {
    let automaticRetry = false;
    for (let i = 0; i < 12; i++) {
      const response = await supervisorRequest(`/${encodeURIComponent(s.caseId)}/step`, s.runBacked ? { state_revision: s.stateRevision } : undefined, s);
      applySupervisorState(s, response.state);
      const e = response.event;
      const labels = {
        analyze_request: ['Agent 1', '요청 항목을 다시 정리했습니다', 'original', `${s.items.length}개 항목을 확인했습니다.`],
        assess_scope: ['Agent 2', '범위와 근거를 검토했습니다', 'scope', `${s.realA2?.assessments?.length || 0}개 항목의 범위 검토 결과가 있습니다.`],
        estimate_impact: ['Agent 3', '영향 산정 결과를 준비했습니다', 'impact', s.realA3?.status === 'NOT_REQUIRED' ? '산정 대상이 없습니다.' : '공수·비용과 추가 확인 사항을 확인할 수 있습니다.'],
        draft_documents: ['Agent 3', '문서 초안을 작성했습니다', 'draft', '고객에게 전달할 초안을 검토할 수 있습니다.'],
        link_quote: ['총괄', '견적 대상을 연결했습니다']
      };
      const [agent, title, tab, summary] = labels[e.action] || ['총괄', '다음 작업을 확인했습니다'];
      if (e.status === 'error') {
        const reason = e.error?.message || e.reason || '총괄 실행에 실패했습니다.';
        if (e.error?.retryable && !automaticRetry && i < 11) {
          automaticRetry = true;
          finishProgress(s, agent, '실행 중 오류가 발생해 자동 재시도합니다', reason);
          render();
          continue;
        }
        stopSupervisor(s, '실행 실패', reason, !!e.error?.retryable);
        return;
      }
      if (e.status === 'waiting' || e.status === 'waiting_input') {
        if (s.supervisorQuestion) {
          if (s.progressStep) finishProgress(s, agent, '추가 확인이 필요합니다', summary, tab);
          s.running = false;
          event(s, '총괄', 'PM 답변이 필요합니다', s.supervisorQuestion.text);
          render();
          if (currentId === s.id) show('agent3-question');
        } else {
          stopSupervisor(s, s.supervisorHoldReason ? '분석 보류' : '분석 중단',
            s.supervisorHoldReason || e.reason || '현재 상태에서 실행할 작업이 없습니다.');
        }
        return;
      }
      if (['waiting_final_review', 'completed'].includes(e.status)) {
        s.running = false; s.ready = true; addRecord(s); save(); render(); return;
      }
      finishProgress(s, agent, title, summary || '', tab);
      if (s.readyForReview) { s.running = false; s.ready = true; addRecord(s); save(); render(); return; }
      save(); render();
    }
    stopSupervisor(s, '실행 횟수 제한', '자동 분석이 실행 횟수 제한에 도달했습니다. 진행 기록에서 마지막 결과를 확인할 수 있습니다.');
  }
  function stopSupervisor(s, title, reason, retryable = false) {
    s.running = false;
    s.supervisorStop = { title, reason, retryable };
    finishProgress(s, '총괄', title, reason);
    render();
  }
  function supervisorControls(s) {
    if (s.supervisorHoldReason) {
      return `<div class="notice"><strong>분석 보류</strong><p>${esc(s.supervisorHoldReason)}</p><button class="button" data-demo-action="continue-supervisor">보류 해제 후 재개</button></div>`;
    }
    if (s.supervisorStop || s.error) {
      const stop = s.supervisorStop || { title: '실행 실패', reason: s.error };
      return `<div class="notice" role="status"><strong>${esc(stop.title)}</strong><p>${esc(stop.reason)}</p>${stop.retryable ? '<button class="button" data-demo-action="continue-supervisor">실패한 단계 다시 시도</button>' : ''}</div>`;
    }
    if (s.readyForReview || s.approved) {
      return `<div class="form-actions"><button class="button primary" data-demo-action="final">${s.approved ? '최종 결과 다시 확인' : '최종 검토하기'}</button><button class="button" data-demo-action="list">변경 요청 목록</button></div>`;
    }
    return '<p class="caption">분석 상태를 확인하고 있습니다.</p>';
  }
  async function continueSupervisor(s) {
    if (!s?.supervisor || s.running || s.approved) return;
    s.running = true; s.supervisorStop = null; s.error = ''; error = ''; render();
    try {
      if (s.runBacked) {
        const response = await fetch(`${supervisorBase(s)}/${encodeURIComponent(s.caseId)}`);
        const data = await response.json();
        if (!response.ok) throw new Error('최신 Run 상태를 확인하지 못했습니다.');
        applySupervisorState(s, data); s.supervisorStop = null;
      }
      if (s.supervisorHoldReason) {
        const data = await supervisorRequest(`/${encodeURIComponent(s.caseId)}/resume`, { state_revision: s.stateRevision });
        applySupervisorState(s, data);
        event(s, '총괄', '보류 상태를 해제했습니다', '최신 상태에서 다음 작업을 선택합니다.');
      }
      s.hold = false; save(); render();
      await runSupervisor(s);
    } catch (err) { stopSupervisor(s, '실행 실패', err.message || '총괄 재개에 실패했습니다.', !!err.retryable); }
  }
  async function resume(s, body) {
    const response = await fetch(`/cases/${encodeURIComponent(s.caseId)}/resume`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ state_revision: s.stateRevision, ...body }) });
    const data = await response.json().catch(() => null);
    if (!response.ok) throw new Error(data?.detail || 'Workflow 재개에 실패했습니다.');
    s.stateRevision = data.state.state_revision;
    return data;
  }
  // Agent 1 항목을 그대로 Agent 2에 넘기고, 판정 결과가 나온 뒤에 PM 확인을 받는다.
  async function runScope(s) {
    const confirmed = await resume(s, { kind: 'items', items: s.realA1.items });
    s.realA2 = confirmed.a2 || null;
    const count = confirmed.a2?.assessments?.length || 0;
    if (!s.realA2 || confirmed.state.stage !== 'pm_review') {
      s.running = false; event(s, 'Agent 2', '범위 검토가 추가 확인을 요청했습니다', `${count}개 항목 판단 중 확인이 필요합니다.`, 'scope'); save(); render(); return;
    }
    s.step = 3; s.running = false;
    event(s, 'Agent 2', '문서 기반 범위 판정을 마쳤습니다', `${count}개 항목의 판정과 근거를 확인해 주세요.${s.schedule ? ` 기존 일정: 계약 종료 ${s.schedule.contract_end}.` : ''}`, 'scope');
    save(); render(); if (currentId === s.id) show('review');
  }
  function advance(s) {
    if (!s.running) return;
    if (s.step === 0) { event(s, '매니저', '요청을 확인하고 있습니다', '화면 시연 시나리오를 시작합니다. 실제 AI는 호출하지 않습니다.'); s.step = 1; }
    schedule(() => {
      if (!s.running) return;
      if (s.step === 1) {
        s.items = s.text.split(/\n+/).filter(Boolean);
        event(s, 'Agent 1', '요청 항목을 정리했습니다', `${s.items.length}개 원문 구간을 표시합니다. 항목화와 의도 판단은 실제 모델 연결 후 수행합니다.`, 'original');
        s.step = 2; render(); advance(s);
      } else if (s.step === 2) {
        event(s, 'Agent 2', '문서 조사 결과를 확인합니다', '가상 조사 완료 · 범위 밖 변경 예시입니다. 실제 계약 문서를 검색한 결과가 아닙니다.', 'scope');
        s.step = 3; s.running = false; save(); render(); if (currentId === s.id) show('question');
      } else if (s.step === 4) {
        event(s, 'Agent 1', 'PM 답변을 요청 조건에 반영했습니다', s.answer, 'original'); s.step = 5; render(); advance(s);
      } else if (s.step === 5) {
        s.md = 6.5; s.price = 4250000;
        event(s, 'Agent 3', '임시 산정 결과를 준비했습니다', '6.5 MD · 고객 제안가 4,250,000원. 이 숫자는 입력과 무관한 고정 시연값입니다. 일정은 미산정입니다.', 'impact');
        s.step = 6; render(); advance(s);
      } else if (s.step === 6) {
        s.ready = true; s.running = false;
        event(s, '매니저', '메일과 변경합의서 초안이 준비됐습니다', '변경 요청 목록에 추가했습니다. 결과를 최종 검토해 주세요.', 'draft');
        addRecord(s); save(); render();
      }
    }, 650);
    save(); render();
  }
  async function generateEmail(s) {
    if (!s?.caseId || !s.realA3?.meta?.result_id || s.running) return;
    s.running = true; s.draftError = ''; render();
    try {
      const response = await fetch(`/cases/${encodeURIComponent(s.caseId)}/email-draft`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ state_revision: s.stateRevision, target_result_id: s.realA3.meta.result_id }) });
      const data = await response.json().catch(() => null);
      if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : data?.detail?.message || '메일 초안 생성에 실패했습니다.');
      s.emailDraft = data.draft; s.stateRevision = data.state_revision; s.running = false;
      event(s, 'Agent 3', '고객 협의 메일 초안을 작성했습니다', '고객 발송 전 PM 확인이 필요합니다.', 'draft');
      save(); render();
    } catch (err) { s.running = false; s.draftError = err.message || '메일 초안 생성에 실패했습니다.'; save(); render(); }
  }
  async function answer() {
    const s = current(); if (!s || !modal || s.running) return;
    if (!selected || (selected === 'other' && !other.trim())) { error = '선택하거나 기타 내용을 입력해 주세요.'; render(); return; }
    const value = selected === 'other' ? other.trim() : selected;
    if (modal === 'agent3-question') {
      const q = pendingQuestion(s); if (!q) { error = '확인할 질문이 없습니다. 현재 결과를 다시 확인해 주세요.'; render(); return; }
      s.running = true; error = ''; render();
      try {
        if (s.supervisor) {
          const answerKey = JSON.stringify([q.question_id, value, s.stateRevision]);
          if (s.pendingAnswerKey !== answerKey) { s.pendingAnswerOperation = crypto.randomUUID(); s.pendingAnswerKey = answerKey; }
          const data = await supervisorRequest(`/${encodeURIComponent(s.caseId)}/answer`, { question_id: q.question_id, text: value, state_revision: s.stateRevision,
            ...(s.runBacked ? { operation_id: s.pendingAnswerOperation } : {}) }, s);
          delete s.pendingAnswerOperation;
          applySupervisorState(s, data); modal = null; other = '';
          if (s.progressStep) finishProgress(s, 'Agent 1', 'PM 답변을 검토했습니다', s.supervisorQuestion ? '추가 설명이 필요합니다.' : '답변을 반영해 다음 작업을 진행합니다.');
          event(s, 'PM', '질문에 답변했습니다', value); save(); render();
          await runSupervisor(s); return;
        }
        const response = await fetch(`/cases/${encodeURIComponent(s.caseId)}/questions/${encodeURIComponent(q.question_id)}/answer`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ state_revision: s.stateRevision, answer: { answer_id: `ANS-${crypto.randomUUID()}`, question_id: q.question_id, text: value, answered_by: 'pm', answered_at: new Date().toISOString(), verified: false } }) });
        const data = await response.json().catch(() => null);
        if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : '답변 저장에 실패했습니다. 현재 상태를 확인해 주세요.');
        s.stateRevision = data.state.state_revision; s.workflowStage = data.state.stage; s.workflowStatus = data.state.status;
        s.realQuestions = data.questions || []; s.emailDraft = null; s.realA1 = data.a1 || s.realA1; s.realA2 = data.a2 || s.realA2; s.realA3 = data.a3 || null;
        if (s.realA3) { const totals = totalsFor(s); s.md = totals?.md ?? null; s.price = totals?.price ?? null; s.step = 6; }
        s.ready = !!s.realA3; s.running = false; modal = null; other = '';
        event(s, 'PM', '질문에 답변했습니다', value);
        if (data.state.status === 'FAILED') event(s, '매니저', '재실행에 실패했습니다', data.state.error?.message || '실행 오류를 확인해 주세요.');
        else if (pendingQuestion(s)) event(s, 'Agent 3', '추가 확인이 필요합니다', pendingQuestion(s).text, 'impact');
        else if (s.realA3) event(s, 'Agent 3', '답변을 반영한 결과를 확인해 주세요', '최신 산정 결과입니다.', 'impact');
        else event(s, '매니저', '상위 단계 확인이 필요합니다', '변경된 결과를 확인한 뒤 다시 진행해 주세요.');
        save(); render(); if (currentId === s.id && !pendingEvidence(s) && pendingQuestion(s)) show('agent3-question'); return;
      } catch (err) { s.running = false; error = err.message || '답변 전달에 실패했습니다.'; save(); render(); return; }
    }
    if (modal === 'final') {
      if (s.supervisor) {
        if (!s.supervisorSnapshotHash || !s.readyForReview) { error = '최신 문서와 산정 결과가 최종 검토 가능한 상태인지 확인해 주세요.'; render(); return; }
        const decision = selected === 'approve' ? 'approve' : selected === 'hold' ? 'hold' : 'reject';
        s.running = true; error = ''; render();
        try {
          const reviewKey = JSON.stringify([decision, value, s.stateRevision, s.supervisorSnapshotHash]);
          if (s.pendingReviewKey !== reviewKey) { s.pendingReviewOperation = crypto.randomUUID(); s.pendingReviewKey = reviewKey; }
          const data = await supervisorRequest(`/${encodeURIComponent(s.caseId)}/review`, {
            state_revision: s.stateRevision, expected_snapshot_hash: s.supervisorSnapshotHash,
            decision, reason: decision === 'approve' ? '' : decision === 'hold' ? 'PM 검토 보류' : value,
            acknowledge_unknown_schedule: decision === 'approve' && !!s.ackUnknownSchedule,
            ...(s.runBacked ? { operation_id: s.pendingReviewOperation } : {})
          });
          delete s.pendingReviewOperation;
          applySupervisorState(s, data); s.running = false; modal = null;
          event(s, 'PM', decision === 'approve' ? '최종 검토를 승인했습니다' : decision === 'hold' ? '최종 검토를 보류했습니다' : '수정을 요청했습니다',
            decision === 'approve' ? '최신 산정과 문서의 서버 승인 기록을 확인했습니다. 고객 합의나 발송은 진행하지 않았습니다.' : value, 'draft');
          addRecord(s); save(); render(); return;
        } catch (err) { s.running = false; error = err.message || '서버 검토 기록에 실패했습니다.'; save(); render(); return; }
      }
      if (selected === 'approve' && s.realA3 && !['IMPACT_DRAFT_READY', 'NOT_REQUIRED'].includes(s.realA3.status)) { error = '추가 확인이 필요한 산정 결과입니다. 확인을 완료한 후 다시 검토해 주세요.'; render(); return; }
      if (selected === 'approve' && s.supervisor && !s.emailDraft) { error = '실제 문서 초안이 생성된 뒤 확인해 주세요.'; render(); return; }
      if (selected === 'approve' && realRun(s) && !s.emailDraft) { modal = null; await generateEmail(s); return; }
      modal = null;
      if (selected === 'approve') { s.approved = true; s.hold = false; event(s, 'PM', '최종 검토를 완료했습니다', '화면 테스트에서 확인한 결과입니다. 고객 합의·발송은 하지 않았습니다.', 'draft'); addRecord(s); }
      else if (selected === 'hold') { s.approved = false; s.hold = true; event(s, 'PM', '검토를 보류했습니다', '현재 결과와 이력을 보존합니다.'); addRecord(s); }
      else { s.approved = false; s.hold = true; event(s, 'PM', '수정 요청을 기록했습니다', value); addRecord(s); }
      save(); render(); return;
    }
    if (modal === 'review' && selected === 'hold') { modal = null; s.hold = true; s.running = false; event(s, 'PM', '판정 확인을 보류했습니다', '현재 결과와 이력을 보존합니다.'); save(); render(); return; }
    s.answer = value; modal = null;
    event(s, 'PM', '확인 내용을 전달했습니다', value);
    if (s.analysisId && s.realA2) {
      s.hold = false; s.running = true; render();
      try {
        const itemsById = new Map(s.realA1.items.map(item => [item.item_id, item]));
        const reviewItems = (s.realA2.assessments || []).map(assessment => {
          const item = itemsById.get(assessment.item_id);
          const label = assessment.label;
          return { item_id: assessment.item_id, ai_label: label, pm_label: label, estimate: label === 'out_of_scope' && ['change_request', 'estimate_request'].includes(item?.intent), reason: selected === 'other' ? value : '', evidence_refs: [] };
        });
        const reviewed = await resume(s, {
          kind: 'pm_review', review: {
            review_id: `PMR-${s.id}-${Date.now()}`,
            review_revision: 1,
            target_result_id: s.realA2.meta.result_id,
            reviewer: 'prototype-pm',
            reviewed_at: new Date().toISOString(),
            status: 'REVIEWED',
            items: reviewItems
          }
        });
        s.realA3 = reviewed.a3 || null; s.emailDraft = null; s.realQuestions = reviewed.questions || reviewed.a3?.questions || []; s.workflowStage = reviewed.state.stage; s.workflowStatus = reviewed.state.status;
        if (reviewed.a3) {
          event(s, 'Agent 3', '영향 산정 결과를 준비했습니다', reviewed.a3.status === 'NOT_REQUIRED' ? '산정 대상 항목이 없습니다.' : reviewed.a3.estimates?.length ? '공수·비용 산정 결과를 확인해 주세요.' : '산정 결과가 없거나 추가 확인이 필요합니다.', 'impact');
          s.step = 6; s.ready = true;
          const totals = totalsFor(s);
          s.md = totals?.md ?? null;
          s.price = totals?.price ?? null;
        } else {
          s.step = 3; s.ready = false;
        }
        s.running = false; modal = null; save(); render(); if (currentId === s.id && !pendingEvidence(s) && pendingQuestion(s)) show('agent3-question'); return;
      } catch (err) {
        s.running = false; error = err.message || 'Workflow 재개에 실패했습니다.';
        event(s, '매니저', 'Workflow 재개 실패', error); save(); render(); return;
      }
    }
    if (value === '추가 정보 확인 후 진행') { s.hold = true; s.running = false; save(); render(); return; }
    s.hold = false; s.step = 4; s.running = true; advance(s);
  }
  function stageLinks(s) {
    return tabs.map(([tab, label], index) => `<button class="analysis-stage" data-demo-tab="${tab}" data-demo-id="${esc(s.id)}" ${s.step < [2, 3, 6, 6][index] ? 'disabled' : ''}><span class="analysis-stage-no">${index + 1}</span><span>${label}</span><span aria-hidden="true">↗</span></button>`).join('');
  }
  function preview(s, tab = 'draft') {
    if (s.step < ({ original: 2, scope: 3, impact: 6, draft: 6 }[tab] || 6)) return '<h2>아직 준비되지 않은 결과입니다</h2><p class="caption">통합 진행 기록에서 다음 작업을 진행해 주세요.</p>';
    if (tab === 'original') return `<h2>고객 요청 원문</h2><p class="analysis-original">${esc(s.text)}</p><h3>Agent 1 구조화 결과</h3>${(s.realA1?.items || []).map((item, i) => `<div class="analysis-item"><span>${esc(item.display_no || `R${i + 1}`)}</span><p><strong>${esc(TYPE_LABELS[item.item_type] || item.item_type || '항목')}</strong> · ${esc(item.text)}</p></div>`).join('') || '<p class="caption">구조화 항목이 없습니다.</p>'}<p class="caption">Orchestration Agent 1 결과입니다.</p>${s.answer ? `<div class="notice">PM 답변: ${esc(s.answer)}</div>` : ''}`;
    if (tab === 'scope') {
      return `<h2>범위 검토 결과</h2>${scopeSummary(s) || '<p class="caption">범위 판단 결과가 없습니다.</p>'}<p class="caption">Orchestration Agent 2 결과입니다.</p>`;
    }
    if (tab === 'impact') {
      if (s.realA3?.status === 'NOT_REQUIRED' || !s.realA3?.estimates?.length) return `<h2>영향 산정</h2><div class="notice">${esc(s.realA3?.status === 'NOT_REQUIRED' ? '산정 대상이 없어 공수·비용을 산정하지 않았습니다.' : '영향 산정 결과가 아직 없습니다.')}</div>`;
      const estimate = s.realA3.estimates[0];
      const t = totalsFor(s) || {};
      const margin = t.price && typeof t.cost === 'number' ? ((t.price - t.cost) / t.price * 100).toFixed(1) : null;
      const rate = estimate.rate_card_id ? `<button type="button" class="rate-card-link" data-rate-card="${esc(estimate.rate_card_id)}" aria-label="${esc(estimate.rate_card_id)} 단가표 보기">${esc(estimate.rate_card_id)}</button>` : '미선택';
      const lines = (s.realA3.estimates.flatMap(row => row.lines || [])).map(l => `<tr><td>${esc(l.task)}</td><td>${esc(l.role)}</td><td>${esc(l.md)}</td></tr>`).join('');
      return `<h2>영향 산정 결과</h2><div class="analysis-numbers"><div><small>총 공수</small><strong>${t.md ?? '미산정'} <small>MD</small></strong></div><div><small>예상 직접원가 · 내부 확인용</small><strong>${typeof t.cost === 'number' ? won(t.cost) : '미산정'}</strong></div><div><small>고객 제안가 · VAT 별도</small><strong>${t.price != null ? won(t.price) : '미산정'}</strong></div></div><p class="caption">단가표 ${rate}${margin !== null ? ` · 예상 마진 ${margin}%` : ''}</p>${lines ? `<table class="rate-card-table"><thead><tr><th>작업</th><th>직군</th><th>MD</th></tr></thead><tbody>${lines}</tbody></table>` : ''}<p class="caption">Orchestration Agent 3 결과입니다.</p>`;
    }
    if (realRun(s)) {
      const draft = s.emailDraft;
      if (draft) return `<h2>고객 협의 메일 초안</h2><p class="caption">고객 협의 전 초안 · ${esc(draft.model || '모델 미확인')} · 자동 발송 없음</p><div class="analysis-document"><h3>제목: ${esc(draft.subject)}</h3><p class="analysis-original">${esc(draft.body)}</p></div>${s.agreementDraft ? `<details class="analysis-document"><summary>변경합의서 초안 보기</summary><p class="analysis-original">${esc(s.agreementDraft.body)}</p></details>` : ''}${s.supervisor ? '' : `<button class="button" data-demo-action="generate-email" ${s.running ? 'disabled' : ''}>메일 초안 다시 생성</button>`}${s.draftError ? `<p class="analysis-error" role="alert">${esc(s.draftError)}</p>` : ''}`;
      return `<h2>고객 협의 메일 초안</h2><div class="notice">${s.running ? '메일 초안을 작성하고 있습니다.' : '산정 결과를 바탕으로 고객에게 전달할 메일 초안을 생성합니다.'}</div>${s.draftError ? `<p class="analysis-error" role="alert">${esc(s.draftError)}</p>` : ''}<div class="form-actions"><button class="button" data-demo-action="generate-email" ${s.running || pendingQuestion(s) || pendingEvidence(s) ? 'disabled' : ''}>메일 초안 생성</button></div>`;
    }
    return `<h2>변경합의서 초안</h2><p class="caption">고객 협의 전 · 화면 시연용 · ${s.approved ? 'PM 확인됨' : 'PM 미확인'}</p><div class="analysis-document"><h3>변경 범위 협의서</h3><p>프로젝트: 모노마켓 · 화면 테스트</p><h3>1. 고객 요청</h3><p>${esc(s.text)}</p><h3>2. 검토 조건</h3><p>${esc(s.answer || '추가 확인 필요')}</p><h3>3. 공수 및 금액</h3><p>예상 공수 6.5 MD / 제안 금액 4,250,000원 (VAT 별도)<br>고정된 시연값이며 실제 산정 결과가 아닙니다.</p><h3>4. 일정 및 확인 사항</h3><p>일정 영향·적용일·검수 기준은 미정입니다. 양측의 협의와 서명 전에는 확정되지 않습니다.</p></div><details><summary>고객 전달용 메일 초안 보기</summary><p class="analysis-original">안녕하세요. 요청 주신 변경 사항에 대한 검토 초안을 전달드립니다.\n\n${esc(s.text)}\n\n세부 범위와 일정은 협의가 필요합니다. 첨부 초안을 검토해 주세요.\n※ 화면 시연용 예시이며 실제 발송하지 않았습니다.</p></details>`;
  }
  const TYPE_LABELS = { function: '기능', question: '확인', constraint: '제약', deadline: '기한', dependency: '의존성' };
  const LABELS = { in_scope: '범위 내', out_of_scope: '범위 밖', unclear: '판단 보류' };
  function scheduleBlock(s) {
    const ref = s.schedule; if (!ref) return '';
    const names = (s.realA1?.items || []).filter(i => ref.item_ids.includes(i.item_id)).map(i => i.text).join(' / ');
    return `<div class="notice"><strong>기존 일정 확인</strong>${names ? ` · 요청: ${esc(names)}` : ''}<br>계약 종료(최종 인수)일 ${esc(ref.contract_end)} · 근거 ${esc(ref.contract_end_ref)}${ref.applied_change_orders.length ? ` · 반영된 변경합의서 ${esc(ref.applied_change_orders.join(', '))}` : ''}<br><span class="caption">${esc(ref.note)}</span></div>`;
  }
  function scopeSummary(s) {
    const items = new Map((s.realA1?.items || []).map(i => [i.item_id, i]));
    const rows = (s.realA2?.assessments || []).map(a => `<div class="analysis-item"><span>${esc(items.get(a.item_id)?.display_no || '')}</span><div><strong>${esc(LABELS[a.label] || a.label)}</strong> · ${esc(items.get(a.item_id)?.text || '')}<p>${esc(a.rationale || '판단 근거가 없습니다.')}</p>${(a.evidence || []).length ? `<p class="caption">근거 ${a.evidence.length}건: ${esc(a.evidence.map(e => [e.doc_id, e.section_id].filter(Boolean).join(' ')).join(', '))}</p>` : '<p class="caption">인용된 근거 문서가 없습니다.</p>'}</div></div>`).join('');
    return scheduleBlock(s) + rows;
  }
  function dialog() {
    const s = current(); if (!modal || !s) return '';
    if (modal === 'final' && s.runBacked && s.approved) {
      return `<dialog id="analysis-pm-dialog" aria-labelledby="analysis-pm-title"><div class="dialog-heading"><h2 id="analysis-pm-title">최종 검토 완료</h2><button class="button plain" data-demo-action="close-dialog">닫기</button></div><p>이 결과와 문서 초안의 최종 검토가 저장되었습니다. 고객 발송·계약 체결은 수행하지 않았습니다.</p></dialog>`;
    }
    const final = modal === 'final';
    const review = modal === 'review';
    if (modal === 'agent3-question') {
      const q = pendingQuestion(s);
      const isReuse = /재사용|기존/i.test(q?.text || '') || /재사용|기존/i.test(q?.reason || '');
      const questionChoices = q?.choices?.length ? [...q.choices.map(value => [value, value]), ...(q.allow_other === false ? [] : [['other', '기타 · 직접 입력']])] : isReuse ? [
        ['기존 기능을 재사용할 수 있습니다 (기존 모듈 활용)', '기존 기능 재사용 가능'],
        ['신규 구현이 필요합니다 (별도 스토리지/모듈 구축)', '신규 구현 필요'],
        ['other', '기타 · 직접 입력']
      ] : null;
      return `<dialog id="analysis-pm-dialog" aria-labelledby="analysis-pm-title"><div class="dialog-heading"><h2 id="analysis-pm-title">추가 확인이 필요합니다</h2><button class="button plain" data-demo-action="close-dialog" aria-label="확인 창 닫기">닫기</button></div><p>${esc(q?.text || '질문을 불러오지 못했습니다.')}</p><p class="caption">${esc(q?.reason || '')}</p>${questionChoices ? `<fieldset class="analysis-choices"><legend class="caption">확인 내용 선택</legend>${questionChoices.map(([v, l]) => `<label class="analysis-choice"><input type="radio" name="analysis-pm-choice" value="${esc(v)}" ${selected === v ? 'checked' : ''}><span>${l}</span></label>`).join('')}</fieldset>` : ''}${(!questionChoices || selected === 'other') ? `<label class="field">답변<textarea id="analysis-pm-other" placeholder="확인한 내용을 입력해 주세요.">${esc(other)}</textarea></label><div class="form-actions"><button class="button primary" data-demo-action="answer" ${s.running ? 'disabled' : ''}>답변하고 계속</button></div>` : ''}${error ? `<p class="analysis-error" role="alert">${esc(error)}</p>` : ''}</dialog>`;
    }
    const choices = final ? [['approve', '결과 확인 완료'], ['hold', '보류하고 나중에 확인'], ['other', '수정 요청 직접 입력']] : review ? [['범위 판정 결과 확인 완료, 영향 산정 진행', '확인하고 영향 산정 진행'], ['hold', '보류하고 나중에 확인'], ['other', '수정·추가 조건 직접 입력']] : [['기존 기능을 재사용하는 가정으로 초안 작성', '기존 기능 재사용으로 진행'], ['추가 정보 확인 후 진행', '추가 정보를 확인한 뒤 진행'], ['other', '기타 · 직접 입력']];
    return `<dialog id="analysis-pm-dialog" aria-labelledby="analysis-pm-title"><div class="dialog-heading"><h2 id="analysis-pm-title">${final ? '최종 결과를 확인해 주세요' : review ? '범위 판정 결과를 확인해 주세요' : '진행 조건을 확인해 주세요'}</h2><button class="button plain" data-demo-action="close-dialog" aria-label="확인 창 닫기">닫기</button></div><p>${final ? (realRun(s) ? (s.emailDraft ? '범위 판단·산정과 메일 초안을 확인합니다. 고객 발송 전 검토입니다.' : '확인을 선택하면 먼저 고객 협의 메일 초안을 생성합니다. 초안을 검토한 뒤 다시 확인해 주세요.') : '범위 판단·산정·문서 초안을 한 번에 확인합니다. 실제 고객 발송 전 PM 검토입니다.') : review ? 'Agent 2가 계약 문서를 근거로 판정한 결과입니다. 확인하면 Agent 3 영향 산정을 이어서 실행합니다.' : '확인 내용을 선택한 뒤 Agent 2 범위 검토와 Agent 3 영향 산정을 이어서 실행합니다.'}</p>${review ? scopeSummary(s) : ''}${final && s.supervisor && (s.realA3?.estimates || []).some(e => e.schedule?.business_days == null) ? `<label class="analysis-choice"><input type="checkbox" id="analysis-ack-schedule" ${s.ackUnknownSchedule ? 'checked' : ''}><span>일정 미산정 사실을 확인했습니다. 고객에게 확정 일정으로 안내하지 않습니다.</span></label>` : ''}${final && totalsFor(s) ? `<div class="notice">예상 공수 ${esc(totalsFor(s).md ?? '미산정')} MD · 판매가 ${won(totalsFor(s).price)}</div>` : ''}<fieldset class="analysis-choices"><legend class="caption">${final ? '검토 결과' : review ? '확인 방법' : '진행 방법'} 선택</legend>${choices.map(([v, l]) => `<label class="analysis-choice"><input type="radio" name="analysis-pm-choice" value="${esc(v)}" ${selected === v ? 'checked' : ''}><span>${l}</span></label>`).join('')}</fieldset>${selected === 'other' ? `<label class="field">기타 내용<textarea id="analysis-pm-other" placeholder="추가 조건이나 수정할 내용을 입력하세요.">${esc(other)}</textarea></label><div class="form-actions"><button class="button primary" data-demo-action="answer">입력 내용 반영</button></div>` : ''}${final && s.supervisor ? `<div class="form-actions"><button class="button primary" data-demo-action="answer" ${!selected || s.running ? 'disabled' : ''}>최종 검토 저장</button></div>` : ''}${error ? `<p class="analysis-error" role="alert">${esc(error)}</p>` : ''}</dialog>`;
  }
  function page() {
    const s = current();
    const heading = `<div class="page-heading"><div><p class="analysis-eyebrow">MULTI-AGENT WORKSPACE <span>실행 연동</span></p><h1>고객 요청, 여기서 시작하세요</h1><p class="subtitle">요청을 분석한 뒤 Supervisor가 필요한 전문 에이전트를 선택하고, 질문과 최종 검토를 연결합니다.</p></div><button class="button" data-demo-action="new" ${s?.running ? 'disabled' : ''}>+ 새 요청</button></div>`;
    if (!s) return heading + `<section class="analysis-compose card"><div class="card-heading"><h2>고객 요청사항</h2><span class="mini-tag">모노마켓 · 테스트 프로젝트</span><span class="caption" style="margin-left:auto;font-weight:600;color:var(--text-muted, #64748b);">${(analysisApi && (sessions.some(x => x.backendConnected) || true)) ? '· 백엔드 DB 연결 (PostgreSQL)' : '· 로컬 저장'}</span></div><label class="field"><span class="sr-only">고객 요청 원문</span><textarea id="analysis-request" placeholder="고객이 보낸 메일이나 메시지를 그대로 붙여넣으세요.\n\n기능 추가, 일정 조건, 견적 요청이 함께 있어도 괜찮아요." maxlength="12000">${esc(text)}</textarea></label><div class="analysis-compose-footer"><label class="analysis-model-picker">모델 <select id="analysis-model" aria-label="분석 모델"><option value="gpt" ${provider === 'gpt' ? 'selected' : ''}>${MODELS.gpt.label}</option><option value="local" ${provider === 'local' ? 'selected' : ''}>${MODELS.local.label}</option></select></label><button class="button plain" data-demo-action="sample">예시 요청 넣기</button><button class="button primary" data-demo-action="start">요청 분석 시작 →</button></div>${error ? `<p class="analysis-error" role="alert">${esc(error)}</p>` : ''}</section><div class="analysis-intro">${[['01', '필요한 에이전트가 분석', '요청 정리와 문서 조사, 영향 산정의 진행 과정을 확인합니다.'], ['02', '필요한 순간에만 질문', 'PM 확인 후 범위 검토와 영향 산정을 이어서 실행합니다.'], ['03', '결과와 기록을 한곳에', 'Workflow 결과와 단계별 기록을 한 화면에서 확인합니다.']].map(([n, h, p]) => `<div><span>${n}</span><h3>${h}</h3><p>${p}</p></div>`).join('')}</div><p class="analysis-demo-note">공통 Run 실행 · 백엔드 DB(PostgreSQL)에 분석 상태와 결과가 영속화됩니다.</p>${sessions.length ? `<section class="card"><h2>이전 요청 분석</h2>${sessions.map(x => `<button class="analysis-history" data-demo-open="${esc(x.id)}"><strong>${esc(x.text.slice(0, 55))}</strong><span>${x.ready ? '결과 준비' : '진행 기록'} ↗</span></button>`).join('')}</section>` : ''}`;
    return heading + `<div class="analysis-run-heading"><span class="mini-tag">${esc(s.id)}</span><strong>${s.running ? '진행 중' : s.hold ? '보류' : s.approved ? 'PM 확인 완료' : pendingEvidence(s) ? '추가 근거 조사 필요' : pendingQuestion(s) ? '추가 확인 필요' : s.ready ? '최종 검토 대기' : 'PM 답변 대기'}</strong><span class="caption">${esc(MODELS[s.provider || 'gpt'].label)} 선택${s.runBacked ? (s.backendConnected ? ' · 백엔드 DB 연결 (PostgreSQL)' : ' · 로컬 저장 · 백엔드 미연결') : ''}${s.llmCalls != null ? ` · 모델 호출 시도 ${s.llmCalls}회` : ''} · ${realRun(s) ? 'Orchestration 실행' : '실제 AI 호출 없음'}</span></div><div class="analysis-workspace"><section class="card analysis-feed"><div class="card-heading"><h2>진행 기록</h2><span class="caption">기록을 누르면 해당 결과로 이동합니다</span></div><div class="analysis-request-bubble"><small>고객 요청</small><p>${esc(s.text)}</p></div><div class="analysis-events" aria-live="polite">${s.events.map(x => eventHtml(x, s.id, s.running && x.seq === s.progressStep)).join('')}</div>${s.running ? '<p class="loading-line" role="status"><span class="inline-spinner"></span>요청 분석을 진행하고 있습니다…</p>' : pendingEvidence(s) ? `<div class="notice">추가 문서 조사 필요: ${esc(pendingEvidence(s).text)}. Agent 2 재조사 자동 연결은 아직 준비 중입니다.</div>` : pendingQuestion(s) ? '<button class="button primary" data-demo-action="agent3-question">추가 질문에 답변하기</button>' : s.supervisor ? supervisorControls(s) : !s.ready ? '<button class="button primary" data-demo-action="question">진행 조건 확인하기</button>' : `<div class="form-actions"><button class="button primary" data-demo-action="final">${s.approved ? '최종 결과 다시 확인' : '최종 검토하기'}</button><button class="button" data-demo-action="list">변경 요청 목록</button></div>`}</section><aside class="analysis-result"><section class="card"><h2>단계별 결과</h2><p class="caption">완료된 작업의 결과를 열어볼 수 있습니다.</p><div class="analysis-stages">${stageLinks(s)}</div></section>${s.ready ? `<section class="card">${preview(s)}<p class="analysis-demo-note">${realRun(s) ? '메일 초안은 고객 발송 전 PM 검토가 필요합니다.' : '화면 시연용 가상 결과입니다.'}</p></section>` : `<section class="card analysis-result-empty"><span>◎</span><h3>결과가 이곳에 쌓입니다</h3><p>PM 확인 후 영향 산정과 문서 초안 미리보기가 이어집니다.</p></section>`}</aside></div>${dialog()}`;
  }
  return {
    refresh: refreshSession,
    page, byId, current, resultPreview(id, tab) { const s = byId(id); return s ? preview(s, tab) : ''; },
    enter(id) {
      currentId = byId(id) ? id : null; modal = null; error = '';
      const s = current();
      if (s?.supervisor && s.caseId && !s.running) {
        fetch(`${supervisorBase(s)}/${encodeURIComponent(s.caseId)}`)
          .then(async response => { const data = await response.json().catch(() => null); if (!response.ok) throw new Error(data?.detail || '서버 실행 상태를 확인할 수 없습니다.'); return data; })
          .then(data => {
            if (currentId !== id || s.running) return;
            applySupervisorState(s, data);
            save(); render();
            if (!s.supervisorQuestion && !s.supervisorHoldReason && !s.readyForReview && !s.approved && !s.supervisorStop && !s.error) {
              return continueSupervisor(s);
            }
          })
          .catch(err => { if (currentId === id) stopSupervisor(s, '실행 상태 조회 실패', err.message || '총괄 세션을 불러오지 못했습니다.'); });
      }
      if (id && !currentId && analysisApi) { currentId = id; restoreCase(id); }
    },
    syncDialog() { if (modal) document.querySelector('#analysis-pm-dialog')?.showModal?.(); },
    input(target) { if (target.id === 'analysis-request') text = target.value; if (target.id === 'analysis-pm-other') other = target.value; },
    change(target) { if (target.id === 'analysis-ack-schedule' && current()) { current().ackUnknownSchedule = target.checked; save(); return; } if (target.id === 'analysis-model' && MODELS[target.value]) { provider = target.value; render(); } if (target.name === 'analysis-pm-choice') { if (!modal || current()?.running) return; selected = target.value; error = ''; if (selected === 'other') render(); else return answer(); } },
    cancel() { modal = null; error = ''; },
    click(target) {
      const tab = target.closest('[data-demo-tab]'); if (tab) { navigate(tab.dataset.demoId, tab.dataset.demoTab); return true; }
      const open = target.closest('[data-demo-open]'); if (open) { openAnalysis(open.dataset.demoOpen); return true; }
      const action = target.closest('[data-demo-action]')?.dataset.demoAction; if (!action) return false;
      if (action === 'start') start();
      if (action === 'sample') { text = SAMPLE; render(); }
      if (action === 'new' && !current()?.running) { text = ''; provider = 'gpt'; openAnalysis(); }
      if (action === 'question') { if (current()?.analysisId) show(current().realA2 ? 'review' : 'question'); else if (current()?.step !== 3) { current().running = true; advance(current()); } else show('question'); }
      if (action === 'generate-email') generateEmail(current());
      if (action === 'continue-supervisor') continueSupervisor(current());
      if (action === 'agent3-question') show('agent3-question');
      if (action === 'final') show('final');
      if (action === 'close-dialog') { modal = null; error = ''; render(); }
      if (action === 'answer') answer();
      if (action === 'list') navigate(null);
      return true;
    }
  };
}
