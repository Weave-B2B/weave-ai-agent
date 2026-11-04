// Run: node --experimental-vm-modules --test prototype/tests/submitted-input.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFile } from 'node:fs/promises';
import { webcrypto } from 'node:crypto';

const appUrl = new URL('../app.js', import.meta.url);
const input = JSON.parse(await readFile(new URL('../data/agent3-input.json', import.meta.url), 'utf8'));
const response = JSON.parse(await readFile(new URL('../data/agent3-preview.json', import.meta.url), 'utf8'));
const appSource = await readFile(appUrl, 'utf8');
const analysisSource = await readFile(new URL('../analysis-demo.js', import.meta.url), 'utf8');
const fixtureSource = await readFile(new URL('../fixtures.js', import.meta.url), 'utf8');
const requestSamplesSource = await readFile(new URL('../data/request-samples.js', import.meta.url), 'utf8');
const samples = JSON.parse(await readFile(new URL('../data/agent3-samples.json', import.meta.url), 'utf8'));
const agent2Inputs = JSON.parse(await readFile(new URL('../data/agent2-inputs.json', import.meta.url), 'utf8'));
const agent1Inputs = JSON.parse(await readFile(new URL('../data/agent1-inputs.json', import.meta.url), 'utf8'));
const storageKey = 'weave-prototype-agent3-session-v1';
const changedInput = text => { const result = structuredClone(input); result.constraints[0].text = text; return result; };
class ApiError extends Error { constructor(status, message) { super(message); this.status = status; } }

// Execute the actual app handlers with only browser I/O and the API transport replaced.
// Exporting internal handlers here keeps production code free of test hooks.
async function boot(options = {}) {
  const store = options.store || new Map();
  const callbacks = options.callbacks || {};
  class FakeApi {
    prepare(body) { return callbacks.prepare ? callbacks.prepare(body) : structuredClone(response); }
    get(id) { return callbacks.get ? callbacks.get(id) : structuredClone(response); }
    review(id, body) { return callbacks.review(id, body); }
  }
  const node = { innerHTML: '', addEventListener() {}, querySelector() { return null; }, textContent: '', hidden: true };
  const context = vm.createContext({
    structuredClone, URL, console, setTimeout, clearTimeout, crypto: webcrypto,
    document: { activeElement: null, querySelector() { return node; } },
    location: { hash: options.hash ?? '#/request/CR-A-001/impact' }, window: { addEventListener() {}, scrollTo() {} },
    sessionStorage: { getItem: key => store.get(key) || null, setItem: (key, value) => store.set(key, value), removeItem: key => store.delete(key) },
    fetch: async url => {
      const path = String(url);
      if (path.includes('/prototype-lab/baselines')) return { ok: true, json: async () => [{ id: 'P-COM-101:BL-2', project_id: 'P-COM-101', as_of: '2026-10-05' }, { id: 'P-NEW-001:BL-1', project_id: 'P-NEW-001', as_of: '2026-10-01' }] };
      if (path.includes('/prototype-lab/defaults')) return { ok: !!options.labDefaults, json: async () => structuredClone(options.labDefaults) };
      const value = path.includes('agent3-input') ? input : path.includes('agent3-samples') ? samples : path.includes('agent2-inputs') ? agent2Inputs : path.includes('agent1-inputs') ? agent1Inputs : response;
      return { ok: true, json: async () => structuredClone(value) };
    },
  });
  const transport = new vm.SyntheticModule(['Agent1Api', 'Agent2Api', 'Agent3Api', 'ApiError', 'KbApi', 'MockPreview'], function () {
    this.setExport('Agent1Api', class { run(body, config) { return callbacks.agent1?.(body, config); } });
    this.setExport('Agent2Api', class { assess(body, config) {
      if (callbacks.assess) return callbacks.assess(body, config);
      throw new Error('Agent 2 is not exercised in this test');
    } });
    this.setExport('KbApi', class { projects() { return callbacks.kb ? callbacks.kb() : Promise.reject(new Error('kb not exercised')); } createProject(...args) { return callbacks.createProject(...args); } createBaseline(...args) { return callbacks.createBaseline(...args); } });
    this.setExport('Agent3Api', FakeApi); this.setExport('ApiError', ApiError);
    this.setExport('MockPreview', class { constructor() {} });
  }, { context });
  const fixtures = new vm.SourceTextModule(fixtureSource, { context });
  const analysisModule = new vm.SourceTextModule(analysisSource, { context });
  const requestSamples = new vm.SourceTextModule(requestSamplesSource, { context });
  const module = new vm.SourceTextModule(`${appSource}\nexport { analysisDemo, route, detail, state, prepare, restore, acceptView, inputChanged, submittedConditions, tabTestGuide, review, canApprove, runAgent1, runAgent2, selectCase, scope, a1, a2, lab, switchInputSource, liveReviewInput, confirmLiveReview, invalidateLiveReview, inputSummary, selectAgent2Input, agent1ToAgent2Input, selectedAgent2Input, registrationPage, registerChangeRequest, agent1Items, agent1Panel, datasetOriginal, kb, loadKb, baselineBanner, projectsPage, startNewProject, npStart, npCreate }; export const cleanup = () => clearTimeout(toastTimer);`, {
    context, identifier: appUrl.href, initializeImportMeta(meta) { meta.url = appUrl.href; },
  });
  await module.link(specifier => {
    if (specifier.split('?')[0] === './api.js') return transport;
    if (specifier === './analysis-demo.js') return analysisModule;
    if (specifier.split('?')[0] === './fixtures.js') return fixtures;
    if (specifier === './data/request-samples.js') return requestSamples;
    throw new Error(`Unexpected module: ${specifier}`);
  });
  await module.evaluate();
  return { ...module.namespace, store };
}

function saved(store) { return JSON.parse(store.get(storageKey)); }

test('integrated preview loads the existing agent screens and never runs model APIs', async t => {
  const s={id:'CR-UI-TEST1234',text:'고객 원문 테스트',items:['고객 원문 테스트'],created:'2026-11-03T09:00:00Z',events:[],provider:'local',step:6,ready:true,approved:false,answer:'기존 기능 사용',md:6.5,price:4250000};
  const store=new Map([['weave-analysis-preview-v1',JSON.stringify([s])]]);
  let calls=0;
  const app=await boot({store,hash:`#/request/${s.id}/original`,callbacks:{agent1:()=>{calls++;},assess:()=>{calls++;},prepare:()=>{calls++;}}});
  t.after(app.cleanup);
  assert.match(app.detail(),/1\. 요청 원문 입력/);
  assert.match(app.detail(),/2\. Agent 1 항목화 결과/);
  assert.match(app.detail(),/로컬 LLM/);
  for (const tab of ['original','scope','impact','draft']) {
    app.state.tab=tab;
    const html=app.detail();
    assert.match(html,/class="detail-title"/);
    assert.match(html,/class="detail-meta"/);
    assert.match(html,/요청 처리/);
    for (const label of ['요청 원문','범위 검토','영향 산정','승인·협의','실행·검수','청구','이력']) assert.ok(html.includes(label));
    assert.match(html,/data-action="list"/);
    assert.match(html,/통합 진행 기록으로/);
    assert.doesNotMatch(html,/data-action="(?:run-a1|prepare)"/);
  }
  app.state.tab='scope'; assert.match(app.detail(),/항목별 범위 판단/);
  app.state.tab='impact'; assert.match(app.detail(),/작업 분해 · 공수/);
  app.state.tab='draft'; assert.match(app.detail(),/고객 전달용 협의 메일 초안/);
  assert.match(app.detail(),/변경합의서 초안/);
  await app.runAgent1(); await app.runAgent2(); await app.prepare();
  assert.equal(calls,0);
  assert.equal(store.has(storageKey),false);
});

test('fresh session defaults to GPT and the previous agent results', async t => {
  const app = await boot({ labDefaults: { model: 'fake-ui-demo', fake_response: {} } });
  t.after(app.cleanup);
  assert.equal(app.a1.provider, 'openai');
  assert.equal(app.a2.provider, 'openai');
  assert.equal(app.lab.provider, 'openai');
  assert.equal(app.lab.model, 'gpt-6-luna');
  assert.equal(app.a2.inputSource, 'agent1');
  assert.equal(app.state.inputSource, 'agent2');
});

test('Agent2 scope execution preserves the Agent3 sample and developer lab settings', async t => {
  let received;
  const result = structuredClone(input.a2_result);
  const app = await boot({
    labDefaults: { model: 'test-model', fake_response: { estimates: [] } },
    callbacks: { assess: body => { received = body; return { ...structuredClone(result), context: body.context }; } },
  });
  t.after(app.cleanup);
  app.selectAgent2Input('sample');
  const original = app.state.inputText;
  await app.runAgent2();
  assert.equal(received.context.case_id, 'CR-A-001');
  assert.equal(app.a2.result.meta.result_id, result.meta.result_id);
  assert.equal(app.lab.enabled, true);
  assert.equal(app.lab.model, 'gpt-6-luna');
  assert.equal(app.state.inputText, original);
  assert.equal(app.state.view, null);
});

test('superseded baseline is flagged and can be replaced by the current one for a lab run', async t => {
  let received;
  const projects = { projects: [{ project_id: 'P-COM-101', active_baseline_id: 'P-COM-101:BL-3', documents: [
    { doc_id: 'CTR-A-001-v1.0', title: 'CTR-A-001-v1.0', doc_type: 'contract', section_count: 9, source: 'dataset', in_baseline: true },
    { doc_id: '7f0c0e5a-0000-4000-8000-000000000001', title: '과업지시서-개정', doc_type: 'sow', section_count: 7, source: 'lab', in_baseline: true }],
    baselines: [
      { baseline_id: 'P-COM-101:BL-2', as_of: '2026-10-05', document_ids: ['CTR-A-001-v1.0'], status: 'SUPERSEDED', superseded_by: 'P-COM-101:BL-3', source: 'dataset' },
      { baseline_id: 'P-COM-101:BL-3', as_of: '2026-11-03', document_ids: ['CTR-A-001-v1.0', '7f0c0e5a-0000-4000-8000-000000000001'], status: 'ACTIVE', supersedes: 'P-COM-101:BL-2', source: 'lab' }] }], doc_types: [] };
  const app = await boot({
    labDefaults: { model: 'test-model', fake_response: { estimates: [] } },
    callbacks: { kb: async () => structuredClone(projects), assess: body => { received = body; return { ...structuredClone(input.a2_result), context: body.context }; } },
  });
  t.after(app.cleanup);
  await app.loadKb();
  app.selectAgent2Input('sample');
  assert.match(app.baselineBanner(), /BASELINE_SUPERSEDED/);
  assert.match(app.projectsPage(), /과업지시서-개정/);
  assert.match(app.projectsPage(), /폐기됨 · P-COM-101:BL-3로 대체/);
  await app.runAgent2();
  assert.equal(received.context.baseline_id, 'P-COM-101:BL-2');      // 기본은 요청의 기준선 그대로 (서버가 거절)
  app.a2.useActive = true;
  await app.runAgent2();
  assert.equal(received.context.baseline_id, 'P-COM-101:BL-3');
  assert.equal(received.context.baseline_as_of, '2026-11-03');
  app.selectCase('CR-A-003');
  assert.equal(app.a2.useActive, false);
});

test('Agent2 sends each selected case and keeps Agent3 input independent', async t => {
  const received = [];
  const app = await boot({ callbacks: { assess: body => {
    received.push(body);
    return structuredClone(samples.inputs[body.context.case_id].a2_result);
  } } });
  t.after(app.cleanup);
  for (const id of ['CR-A-003', 'CR-B-002', 'CR-C-001']) {
    app.state.id = id; app.selectCase(id);
    app.selectAgent2Input('sample');
    const before = app.state.inputText;
    await app.runAgent2();
    assert.equal(received.at(-1).context.case_id, id);
    assert.equal(app.a2.result.context.case_id, id);
    assert.equal(app.state.inputText, before);
    assert.match(app.scope(), /Agent 2/);
  }
});

test('late Agent2 response cannot appear on another case', async t => {
  let resolve;
  const app = await boot({ callbacks: { assess: () => new Promise(done => { resolve = done; }) } });
  t.after(app.cleanup);
  app.state.id = 'CR-A-003'; app.selectCase('CR-A-003');
  app.selectAgent2Input('sample');
  const work = app.runAgent2();
  app.state.id = 'CR-B-002'; app.selectCase('CR-B-002');
  resolve(structuredClone(samples.inputs['CR-A-003'].a2_result));
  await work;
  assert.equal(app.a2.result, null);
  assert.equal(app.a2.pending, false);
});

test('Agent 1 and Agent 2 use their independently selected providers', async t => {
  let a1Call, a2Call;
  const app = await boot({ labDefaults: { model: 'default', fake_response: {} }, callbacks: {
    agent1: (body, config) => { a1Call = { body, config }; return { context: body.context, items: [], meta: {}, status: 'READY' }; },
    assess: (body, config) => { a2Call = { body, config }; return samples.inputs[body.context.case_id].a2_result; },
  } });
  t.after(app.cleanup);
  app.state.id = 'CR-A-003'; app.selectCase('CR-A-003');
  app.a1.provider = 'gemini'; app.a1.model = 'gemini-2.5-flash-lite';
  app.a2.provider = 'bedrock'; app.a2.model = 'model-id';
  app.selectAgent2Input('sample');
  await app.runAgent1(); await app.runAgent2();
  assert.equal(a1Call.body.context.case_id, 'CR-A-003');
  assert.equal(a1Call.config.provider, 'gemini');
  assert.equal(a2Call.body.context.case_id, 'CR-A-003');
  assert.equal(a2Call.config.provider, 'bedrock');
  assert.equal(app.a2.result.context.case_id, 'CR-A-003');
});

test('successful prepare keeps its submitted conditions when editor changes during await', async t => {
  let resolve, received;
  const pending = new Promise(done => { resolve = done; });
  const app = await boot({ callbacks: { prepare: body => { received = body; return pending; } } });
  t.after(app.cleanup);
  app.switchInputSource('sample');
  const sent = changedInput('제출한 오픈일 2026-12-01 유지');
  app.state.inputText = JSON.stringify(sent);
  const work = app.prepare();
  app.state.inputText = JSON.stringify(changedInput('미제출 오픈일 2027-01-20'));
  resolve(structuredClone(response)); await work;
  assert.equal(received.constraints[0].text, sent.constraints[0].text);
  assert.equal(app.state.submittedInput.constraints[0].text, sent.constraints[0].text);
  assert.equal(app.state.signature, JSON.stringify(sent));
  assert.equal(app.inputChanged(), true);
  assert.match(app.submittedConditions(), /2026-12-01/);
  assert.doesNotMatch(app.submittedConditions(), /2027-01-20/);
  assert.equal(saved(app.store).submittedInput.constraints[0].text, sent.constraints[0].text);
  assert.match(saved(app.store).inputText, /2027-01-20/);
});

test('failed new prepare retains successful snapshot and invalidation after reload', async t => {
  const app = await boot({ callbacks: { prepare: () => { throw new ApiError(502, 'offline failure'); } } });
  t.after(app.cleanup);
  app.switchInputSource('sample');
  const sent = changedInput('성공 조건 2026-12-03');
  app.acceptView(structuredClone(response), sent);
  app.state.inputText = JSON.stringify(changedInput('실패한 새 조건 2027-02-01'));
  await app.prepare();
  assert.match(app.submittedConditions(), /2026-12-03/);
  assert.doesNotMatch(app.submittedConditions(), /2027-02-01/);
  assert.equal(app.state.error.status, 502);
  const restored = await boot({ store: app.store }); t.after(restored.cleanup);
  assert.match(restored.submittedConditions(), /2026-12-03/);
  assert.equal(restored.state.invalidated, true);
  assert.equal(restored.inputChanged(), true);
  assert.equal(restored.canApprove(), false);
});

test('legacy signature restores submitted conditions instead of unsaved editor text', async t => {
  const legacy = changedInput('기존 세션 제출 조건 2026-12-05');
  const store = new Map([[storageKey, JSON.stringify({ sessionId: response.session_id, signature: JSON.stringify(legacy), inputText: JSON.stringify(changedInput('미제출 조건 2027-03-01')) })]]);
  const app = await boot({ store }); t.after(app.cleanup);
  assert.match(app.submittedConditions(), /2026-12-05/);
  assert.doesNotMatch(app.submittedConditions(), /2027-03-01/);
  assert.equal(app.inputChanged(), true);
});

test('missing legacy snapshot shows unknown, and multiple/empty conditions stay truthful', async t => {
  const store = new Map([[storageKey, JSON.stringify({ sessionId: response.session_id, inputText: JSON.stringify(input) })]]);
  const app = await boot({ store }); t.after(app.cleanup);
  assert.match(app.submittedConditions(), /제출 조건 기록이 없습니다/);
  const sent = changedInput('<script>untrusted</script>');
  sent.constraints.push({ ...sent.constraints[0], text: '두 번째 조건' });
  app.acceptView(structuredClone(response), sent);
  assert.match(app.submittedConditions(), /&lt;script&gt;/);
  assert.match(app.submittedConditions(), /두 번째 조건/);
  app.acceptView(structuredClone(response), { ...sent, constraints: [] });
  assert.match(app.submittedConditions(), /제출된 조건 없음/);
});

test('PM approval and reload retain submitted snapshot and response model label', async t => {
  const sent = changedInput('초안과 일치할 조건 2026-12-10');
  const view = structuredClone(response); view.impact.meta.model = 'fake:snapshot-regression';
  const app = await boot({ callbacks: { review: (id, body) => ({ ...structuredClone(view), phase: 'DRAFT_READY', approval: { approved: body.approved }, draft: { impact: `조건: ${sent.constraints[0].text}` } }) } });
  t.after(app.cleanup);
  app.state.inputText = JSON.stringify(sent); app.acceptView(view, sent);
  app.state.ack = true; await app.review(true);
  assert.equal(app.state.view.phase, 'DRAFT_READY');
  assert.match(app.state.view.draft.impact, /2026-12-10/);
  assert.match(app.submittedConditions(), /2026-12-10/);
  assert.equal(app.state.view.impact.meta.model, 'fake:snapshot-regression');
  const restored = await boot({ store: app.store, callbacks: { get: () => structuredClone(app.state.view) } }); t.after(restored.cleanup);
  assert.match(restored.submittedConditions(), /2026-12-10/);
  assert.equal(restored.inputChanged(), false);
});


test('Agent2 result requires explicit PM decisions and carries its own context into Agent3', async t => {
  let sent;
  const app = await boot({ callbacks: {
    assess: body => ({ ...structuredClone(input.a2_result), context: structuredClone(body.context), meta: { ...input.a2_result.meta, result_id: 'new-agent2-result' } }),
    prepare: body => { sent = body; return structuredClone(response); },
  } });
  t.after(app.cleanup);
  app.selectAgent2Input('sample'); await app.runAgent2(); app.switchInputSource('agent2');
  assert.throws(() => app.liveReviewInput(), /PM 판단/);
  const id = app.a2.result.assessments[0].item_id;
  app.a2.choices[id] = { label: 'out_of_scope', estimate: true, reason: '' };
  app.confirmLiveReview();
  assert.equal(app.a2.review.pm_review.target_result_id, 'new-agent2-result');
  assert.equal(app.a2.review.context.scenario_id, null);
  assert.equal(app.a2.review.received_at, input.received_at);
  assert.equal(app.a2.review.pm_review.items[0].estimate, true);
  assert.match(app.inputSummary(app.a2.review).replace(/<[^>]+>/g, ""), /산정 대상: 선택/);
  await app.prepare();
  assert.equal(sent.a2_result.meta.result_id, 'new-agent2-result');
  assert.equal(sent.pm_review.reviewer, '로컬 테스트 PM');
  assert.equal(sent.context.scenario_id, null);
  app.invalidateLiveReview();
  assert.equal(app.state.invalidated, true);
  app.switchInputSource('sample');
  assert.equal(JSON.parse(app.state.inputText).pm_review.review_id, input.pm_review.review_id);
});

test('Agent2 blocks unresolved questions, invalid decisions, and mismatched results', async t => {
  const app = await boot({ callbacks: { assess: body => ({ ...structuredClone(input.a2_result), context: structuredClone(body.context) }) } });
  t.after(app.cleanup); app.selectAgent2Input('sample'); await app.runAgent2(); app.switchInputSource('agent2');
  const id = app.a2.result.assessments[0].item_id;
  app.a2.choices[id] = { label: 'in_scope', estimate: true };
  assert.throws(() => app.liveReviewInput(), /범위 밖/);
  app.a2.choices[id] = { label: 'in_scope', estimate: false };
  assert.throws(() => app.liveReviewInput(), /사유/);
  app.a2.choices[id] = { label: 'out_of_scope', estimate: true };
  app.a2.result.status = 'NEEDS_INPUT';
  assert.throws(() => app.liveReviewInput(), /필수 확인/);
  app.a2.result.status = 'READY_FOR_REVIEW'; app.a2.result.context.request_revision++;
  assert.throws(() => app.liveReviewInput(), /일치/);
});

test('registered change request creates Agent 1 input and runs Agent 1', async t => {
  let received;
  const app = await boot({ labDefaults: { model: 'test-model', fake_response: {} }, callbacks: {
    agent1: (body, config) => { received = { body, config }; return { context: body.context, items: [], meta: { result_id: 'A1-new' }, status: 'READY' }; },
  } });
  t.after(app.cleanup);
  Object.assign(app.state.registration, {
    project_id: 'P-NEW-001', project_name: '신규 프로젝트', case_id: 'CR-D-001', baseline_id: 'P-NEW-001:BL-1',
    channel: 'email', channel_src: 'GMAIL', received_at: '2026-11-02T10:00',
    sender_role: '고객사 PM', sender_name: '신규 요청자', subject: '신규 기능 요청', message_text: '관리 화면에 신규 기능을 추가해 주세요.',
  });
  app.registerChangeRequest();
  assert.equal(app.state.id, 'CR-D-001');
  await app.runAgent1();
  assert.equal(received.body.context.case_id, 'CR-D-001');
  assert.equal(received.body.subject, '신규 기능 요청');
  assert.equal(received.body.message_text, '관리 화면에 신규 기능을 추가해 주세요.');
  assert.equal(app.a1.result.meta.result_id, 'A1-new');
});

test('Agent1 execution bar matches Agent2 and replaces upper synthetic items with live results', async t => {
  const app = await boot({ labDefaults: { model: 'test-model', fake_response: {} }, callbacks: {
    agent1: body => ({ context: body.context, items: [{ item_id: 'CR-A-003-1', case_id: 'CR-A-003', display_no: 'R1', item_type: 'function', intent: 'change_request', text: '관리자 주문목록을 엑셀로 내려받는다.', spans: [] }], questions: [], meta: { result_id: 'A1-live', model: 'test-model', agent_version: 'fixture' }, status: 'READY' }),
  } });
  t.after(app.cleanup);
  app.state.id = 'CR-A-003'; app.selectCase('CR-A-003');
  const barBefore = app.agent1Panel();
  assert.match(barBefore, /class="baseline"/);
  assert.match(barBefore, /Agent 1 요청 항목화 실행/);
  await app.runAgent1();
  assert.equal(app.agent1Items()[0].display_no, 'R1');
  assert.equal(app.agent1Items()[0].text, '관리자 주문목록을 엑셀로 내려받는다.');
  const panel = app.agent1Panel();
  assert.match(panel, /다시 실행/);
  assert.match(app.datasetOriginal({ requester: '한유진', requesterRole: '고객사 운영', receivedAt: '2026-10-07T10:22:00+09:00', channel: 'SMS', title: '주문 엑셀 내려받기', messageText: '관리자 주문목록을 엑셀로 내려받아 주세요.' }), /Agent 1 실행 결과/);
});

function sampleAgent1Result(source) {
  return { context: structuredClone(source.context), status: 'READY', meta: { result_id: 'A1-live-test', model: 'fake' },
    items: [
      { item_id: `${source.context.case_id}-1`, case_id: source.context.case_id, display_no: 'R1', item_type: 'function', intent: 'change_request', text: '사용자가 확인한 새로운 기능', spans: [], source: 'ai', feature_candidates: [], inferred: false },
      { item_id: `${source.context.case_id}-2`, case_id: source.context.case_id, display_no: 'R2', item_type: 'deadline', intent: 'constraint', text: '출시일 유지', spans: [], source: 'ai', feature_candidates: [], inferred: false },
      { item_id: `${source.context.case_id}-3`, case_id: source.context.case_id, display_no: 'R3', item_type: 'question', intent: 'confirmation', text: '견적 여부', spans: [], source: 'ai', feature_candidates: [], inferred: false },
    ], questions: [] };
}

test('Agent 1 output is mapped to Agent 2 with source, baseline and conditions preserved', async t => {
  let captured;
  const app = await boot({ labDefaults: { model: 'test-model', fake_response: {} }, callbacks: {
    agent1: body => sampleAgent1Result(body),
    assess: body => { captured = structuredClone(body); return { context: body.context, status: 'READY_FOR_REVIEW', assessments: [{ item_id: body.items[0].item_id, label: 'out_of_scope', evidence: [] }], questions: [], meta: { result_id: 'A2-live-test', debug: {} } }; },
  } });
  t.after(app.cleanup);
  await app.runAgent1(); app.selectAgent2Input('agent1'); app.a2.provider = 'gemini';
  assert.match(app.scope(), /사용자가 확인한 새로운 기능/);
  await app.runAgent2();
  assert.equal(captured.a1_result_id, 'A1-live-test');
  assert.equal(captured.message_text, agent1Inputs['CR-A-001'].message_text);
  assert.equal(captured.context.baseline_id, 'P-COM-101:BL-2');
  assert.deepEqual(captured.items.map(x => x.text), ['사용자가 확인한 새로운 기능']);
  assert.deepEqual(captured.constraints.map(x => x.text), ['출시일 유지']);
  assert.equal(app.a2.result.meta.result_id, 'A2-live-test');
  assert.match(app.scope(), /사용자가 확인한 새로운 기능/);
  app.a2.choices[captured.items[0].item_id] = { label: 'out_of_scope', estimate: true };
  assert.equal(app.liveReviewInput().a2_result.meta.result_id, 'A2-live-test');
  assert.equal(app.liveReviewInput().items[0].text, '사용자가 확인한 새로운 기능');
  app.selectAgent2Input('sample');
  assert.equal(app.a2.result, null); assert.equal(app.a2.review, null);
  assert.equal(app.selectedAgent2Input().items[0].text, agent2Inputs['CR-A-001'].items[0].text);
});

test('Agent 1 blocked questions, foreign context, and fake Agent 2 cannot pass as live analysis', async t => {
  let count = 0;
  const app = await boot({ labDefaults: { model: 'test-model', fake_response: {} }, callbacks: {
    agent1: body => sampleAgent1Result(body), assess: () => { count++; throw new Error('unexpected'); },
  } }); t.after(app.cleanup);
  await app.runAgent1(); app.selectAgent2Input('agent1'); app.a2.provider = 'fake';
  await app.runAgent2(); assert.equal(count, 0); assert.match(app.a2.error.message, /가상 응답/);
  app.a2.provider = 'gemini'; app.a1.result.status = 'NEEDS_INPUT';
  await app.runAgent2(); assert.equal(count, 0); assert.match(app.a2.error.message, /필수 확인/);
  app.a1.result.status = 'READY'; app.a1.result.context.baseline_id = 'wrong';
  await app.runAgent2(); assert.equal(count, 0); assert.match(app.a2.error.message, /다릅니다/);
});

test('rerunning Agent 1 invalidates Agent 2 result and Agent 3 input from it', async t => {
  const app = await boot({ labDefaults: { model: 'test-model', fake_response: {} }, callbacks: { agent1: body => sampleAgent1Result(body) } }); t.after(app.cleanup);
  await app.runAgent1(); app.selectAgent2Input('agent1');
  app.a2.result = { meta: { result_id: 'old' } }; app.a2.review = { old: true }; app.state.inputSource = 'agent2'; app.state.view = structuredClone(response);
  await app.runAgent1();
  assert.equal(app.a2.result, null); assert.equal(app.a2.review, null); assert.equal(app.state.inputText, ''); assert.equal(app.state.invalidated, true);
});

test('Agent2 can run on the Agent1 result: function items to items, constraints kept, quote requests dropped', async t => {
  let received;
  const item = (n, type) => ({ item_id: `CR-A-001-${n}`, case_id: 'CR-A-001', display_no: `R${n}`, item_type: type, intent: 'change_request', text: `항목 ${n}`, spans: [] });
  const a1Out = body => ({ context: structuredClone(body.context), meta: { result_id: 'a1-live' }, status: 'READY', questions: [],
    items: [item(1, 'function'), item(2, 'deadline'), item(3, 'question')] });
  const app = await boot({ labDefaults: { model: 'test-model', fake_response: {} }, callbacks: { agent1: a1Out, assess: body => { received = body; return { ...structuredClone(input.a2_result), context: structuredClone(body.context) }; } } });
  t.after(app.cleanup);
  app.selectAgent2Input('agent1');
  await app.runAgent2();
  assert.equal(received, undefined);
  assert.match(app.a2.error.message, /Agent 1을 실행/);
  app.lab.enabled = true; await app.runAgent1(); app.a2.provider = 'gemini';
  await app.runAgent2();
  assert.equal(received.a1_result_id, 'a1-live');
  assert.equal(received.message_text, agent1Inputs['CR-A-001'].message_text);
  assert.deepEqual(received.items.map(i => i.item_id), ['CR-A-001-1']);
  assert.deepEqual(received.constraints.map(i => i.item_id), ['CR-A-001-2']);
  assert.equal(app.a2.sourceInput.a1_result_id, 'a1-live');
  await app.runAgent1();
  assert.equal(app.a2.result, null);
});

test('Agent1 result without function items or awaiting input does not reach Agent2', async t => {
  let status = 'NEEDS_INPUT';
  const app = await boot({ labDefaults: { model: 'test-model', fake_response: {} }, callbacks: { agent1: body => ({ context: structuredClone(body.context), meta: { result_id: 'a1' }, status, questions: [], items: [] }) } });
  t.after(app.cleanup); app.lab.enabled = true;
  await app.runAgent1(); app.selectAgent2Input('agent1');
  assert.throws(() => app.agent1ToAgent2Input(), /필수 확인/);
  status = 'READY'; await app.runAgent1();
  assert.throws(() => app.agent1ToAgent2Input(), /기능 항목이 없습니다/);
});

test('new project wizard resumes a draft and creates the first baseline from contract or SOW', async t => {
  let projects = { projects: [
    { project_id: 'P-COM-101', name: null, draft: false, active_baseline_id: 'P-COM-101:BL-2', documents: [], baselines: [{ baseline_id: 'P-COM-101:BL-2', as_of: '2026-10-05', document_ids: [], status: 'ACTIVE', source: 'dataset' }] },
    { project_id: 'P-NEW-9', name: '신규', draft: true, active_baseline_id: null, baselines: [], documents: [
      { doc_id: 'd-quo', title: 'QUO-견적', doc_type: 'quote', section_count: 3, source: 'lab', in_baseline: false },
      { doc_id: 'd-sow', title: 'SOW-과업', doc_type: 'sow', section_count: 7, source: 'lab', in_baseline: false }] }], doc_types: [] };
  let baselineCall;
  const app = await boot({
    labDefaults: { model: 'test-model', fake_response: { estimates: [] } },
    callbacks: {
      kb: async () => structuredClone(projects),
      createProject: async id => ({ project_id: id, name: '신규', documents: ['d-quo', 'd-sow'] }),
      createBaseline: async (id, ids, asOf) => { baselineCall = { id, ids, asOf }; return { baseline_id: `${id}:BL-1` }; },
    },
  });
  t.after(app.cleanup);
  await app.loadKb();
  assert.equal(app.kb.projectId, 'P-COM-101');                 // 작성 중 프로젝트는 목록에 나오지 않는다
  assert.doesNotMatch(app.projectsPage(), /P-NEW-9/);
  app.startNewProject();
  app.kb.creating.project_id = 'p-new-9'; app.kb.creating.name = '신규'; app.kb.creating.as_of = '2026-09-01';
  await app.npStart();
  assert.equal(app.kb.creating.step, 2);
  assert.equal(app.kb.creating.project_id, 'P-NEW-9');
  assert.deepEqual([...app.kb.creating.include].sort(), ['d-quo', 'd-sow']);
  assert.match(app.projectsPage(), /P-NEW-9 만들기 \(BL-1\)/);
  app.kb.creating.include.delete('d-sow');
  assert.match(app.projectsPage(), /계약서나 과업지시서를 하나 이상/);
  app.kb.creating.include.add('d-sow');
  projects.projects[1].draft = false;
  await app.npCreate();
  assert.equal(JSON.stringify(baselineCall), JSON.stringify({ id: 'P-NEW-9', ids: ['d-quo', 'd-sow'], asOf: '2026-09-01' }));
  assert.equal(app.kb.creating, null);
});
