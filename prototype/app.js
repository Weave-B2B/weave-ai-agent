import { Agent1Api, Agent2Api, Agent3Api, ApiError, KbApi, MockPreview } from "./api.js?v=kb-new-project-1";
import { records, sampleItems, scenarioInput } from "./fixtures.js?v=common-rate-2";
import { createAnalysisDemo, analysisSnapshot } from "./analysis-demo.js";

const root = document.querySelector("#app");
const api = new Agent3Api();
const agent2 = new Agent2Api();
const agent1 = new Agent1Api();
const kbApi = new KbApi();
const STORAGE = "weave-prototype-agent3-session-v1";
const PHASE = { FRONTEND_PREVIEW: "가상 결과 · 읽기 전용", APPROVED: "PM 승인 완료 · 협의안 생성 대기", WAITING_IMPACT_APPROVAL: "PM 산정 검토 대기", WAITING_INPUT: "추가 정보 필요", WAITING_UPSTREAM_REVIEW: "상위 PM 검토 필요", DRAFT_READY: "내부 협의안 초안 준비", NOT_REQUIRED: "산정 대상 없음", FAILED: "산정 실패" };
const state = { page: "list", id: "CR-A-001", tab: "original", query: "", group: "열린 요청", filter: "전체", project: "전체", order: "기본", pageNo: 1, selectedItem: 0, mode: "api", scenario: "normal", inputSource: "sample", sampleInputText: "", pending: false, error: null, view: null, inputText: "", submittedInput: null, signature: null, edits: null, originalLines: [], conflicted: false, invalidated: false, ack: false, reviewer: "local-test-pm", reviewReason: "", editReason: "", registration: { project_id: "P-COM-101", project_name: "신규 변경 요청 프로젝트", case_id: "", channel: "email", channel_src: "GMAIL", baseline_id: "", received_at: "", sender_role: "고객사 PM", sender_name: "", subject: "", message_text: "" }, registrationError: "" };
let baseInput, agent1Inputs, agent2Input, agent2Inputs, previewClient, toastTimer, composingSearch = false, samples, activeCase;
let availableBaselines = [];
let ollamaModels = [];
const defaultModels = { gemini: "gemini-2.5-flash-lite", openai: "gpt-6-luna", ollama: "gpt-oss:20b" };
const defaultModel = provider => defaultModels[provider] || "";
const draftLab = { provider: "openai", model: "gpt-6-luna", region: "ap-northeast-2" };
const lab = { enabled: false, provider: "openai", model: "gpt-6-luna", base_url: "http://127.0.0.1:11434/v1", region: "ap-northeast-2", fakeText: "" };
const analysisDemo = createAnalysisDemo({ records, storage: sessionStorage, render, navigate, openAnalysis: id => { location.hash = id ? `#/analysis/${encodeURIComponent(id)}` : '#/analysis'; } });
const e = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const itemTypeLabel = value => ({ function: "기능", question: "확인", constraint: "제약", deadline: "기한", dependency: "의존성" }[value] || value || "항목");
const number = value => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("ko-KR", { maximumFractionDigits: 1 }) : "—";
const rateCardButton = id => id ? `<button type="button" class="rate-card-link" data-rate-card="${e(id)}" aria-label="${e(id)} 단가표 보기">${e(id)}</button>` : "미선택";
const money = value => typeof value === "number" && Number.isFinite(value) ? `${number(value)}원` : "미산정";
const icon = file => `<span class="icon"><img src="./assets/${file}.svg" alt=""></span>`;
const chip = (label, tone = "neutral") => `<span class="chip ${tone}">${icon(tone === "ai" ? "1b8e7" : tone === "success" ? "396d8" : tone === "info" ? "59318" : tone === "warning" ? "691d6" : "edb5c")}<span>${e(label)}</span></span>`;
const button = (label, action, options = {}) => `<button type="button" class="button ${options.class || ""}" data-action="${action}" ${options.disabled ? "disabled" : ""} ${options.title ? `title="${e(options.title)}"` : ""}>${options.icon ? icon(options.icon) : ""}${e(label)}</button>`;
const record = () => { const s=analysisDemo.byId(state.id); return s ? {...records.find(item=>item.id===s.id),id:s.id,api:true,analysisDemo:true,title:s.text.split('\n')[0],project:'모노마켓 · 화면 테스트',requester:'직접 입력',messageText:s.text,channel:'수동'} : records.find(item => item.id === state.id) || records[0]; };
const estimates = () => record().api ? state.view?.impact?.estimates || [] : [];
const unknownSchedule = () => estimates().some(item => item.schedule?.business_days == null);
const parsedInput = () => JSON.parse(state.inputText);
function inputChanged() { try { return !!state.view && JSON.stringify(parsedInput()) !== state.signature; } catch { return !!state.view; } }
function editsChanged() { return !!state.view && JSON.stringify(state.edits) !== JSON.stringify(estimates().map(item => ({ item_ids: item.item_ids, lines: item.lines }))); }
function safeCurrent() { return !record().analysisDemo && state.mode === "api" && record().api && !state.pending && !state.conflicted && !state.invalidated && !inputChanged(); }
function canApprove() { return safeCurrent() && !editsChanged() && state.view?.phase === "WAITING_IMPACT_APPROVAL" && estimates().length > 0 && estimates().every(item => item.status === "draft" && item.totals) && !(state.view.impact.questions || []).some(item => item.blocking && ["open", "answered"].includes(item.status)); }
function canEdit() { return safeCurrent() && estimates().length > 0 && estimates().every(item => item.lines?.length); }
function currentTotals() {
  const rows = estimates();
  if (!rows.length || rows.some(item => !item.totals)) return null;
  if (record().analysisDemo) return {md:6.5,price:4250000,cost:null};
  const totals = rows.reduce((sum, item) => ({ md: sum.md + item.totals.md, cost: sum.cost + item.totals.cost, price: sum.price + item.totals.price }), { md: 0, cost: 0, price: 0 });
  return totals;
}
function toast(message) { const el = document.querySelector("#toast"); el.textContent = message; el.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, 4500); }
function persist() {
  if (record().analysisDemo) return;
  if (state.mode !== "api" || !state.view) return;
  try { sessionStorage.setItem(STORAGE, JSON.stringify({ sessionId: state.view.session_id, inputSource: state.inputSource, sampleInputText: state.sampleInputText, inputText: state.inputText, submittedInput: state.submittedInput, signature: state.signature, originalLines: state.originalLines, invalidated: state.invalidated, labSettings: lab.enabled ? { provider: lab.provider, model: lab.model, base_url: lab.base_url, region: lab.region, fakeText: lab.fakeText } : null })); } catch { /* The UI still works when browser storage is unavailable. */ }
}
function forget() { try { sessionStorage.removeItem(STORAGE); } catch { /* Optional local storage. */ } }
function acceptView(view, submittedInput = null) {
  state.view = view;
  state.edits = structuredClone((view.impact?.estimates || []).map(item => ({ item_ids: item.item_ids, lines: item.lines })));
  state.ack = false; state.editReason = "";
  if (submittedInput) { state.submittedInput = structuredClone(submittedInput); state.signature = JSON.stringify(state.submittedInput); state.originalLines = structuredClone((view.impact.estimates || []).flatMap(item => item.lines)); state.conflicted = false; state.invalidated = false; }
  persist();
}
function navigate(id, tab = "original") { location.hash = id ? `#/request/${encodeURIComponent(id)}/${tab}` : "#/requests"; }
function route() {
  if (location.hash === '#/analysis' || location.hash.startsWith('#/analysis/')) { analysisDemo.enter(decodeURIComponent(location.hash.slice('#/analysis/'.length))); state.page = 'analysis'; render(); return; }
  if (location.hash === "#/register") { state.page = "register"; render(); return; }
  const project = location.hash.match(/^#\/projects(?:\/([^/]+))?$/);
  if (project) { state.page = "projects"; if (project[1]) kb.projectId = decodeURIComponent(project[1]); render(); loadKb(); return; }
  const match = location.hash.match(/^#\/request\/([^/]+)\/(original|scope|impact|draft)$/);
  if (match && (analysisDemo.byId(decodeURIComponent(match[1])) || records.some(item => (item.analysisDemo || item.api || item.datasetSample || item.id === "CR-001") && item.id === decodeURIComponent(match[1])))) {
    state.id = decodeURIComponent(match[1]); state.tab = match[2]; state.page = "detail";
    if (analysisDemo.byId(state.id)) loadAnalysisResult(analysisDemo.byId(state.id));
    if (!analysisDemo.byId(state.id) && record().api && samples && activeCase !== state.id) selectCase(state.id);
  }
  else state.page = "list";
  render(); window.scrollTo({ top: 0, behavior: "instant" });
}

function loadAnalysisResult(s) {
  const result=analysisSnapshot(s);
  activeCase=s.id; state.selectedItem=0; state.pending=false; state.mode='api'; state.error=null; state.invalidated=false; state.conflicted=false;
  agent1Inputs[s.id]=result.source; baseInput=result.input;
  Object.assign(a1,{result:s.step>=2?result.a1:null,sourceInput:result.source,pending:false,error:null,provider:s.provider==='local'?'local':'openai',model:s.provider==='local'?'로컬 LLM · 화면 시연':'gpt-6-luna · 화면 시연'});
  Object.assign(a2,{result:s.step>=3?result.a2:null,sourceInput:result.source,pending:false,error:null,inputSource:'agent1',review:null,choices:{}});
  state.inputSource='agent2'; state.inputText=JSON.stringify(result.input); state.submittedInput=result.input; state.signature=state.inputText;
  state.view=result.view; state.edits=result.view?.impact.estimates.map(item=>({item_ids:item.item_ids,lines:item.lines}))||null;
  state.originalLines=result.view?.impact.estimates.flatMap(item=>item.lines)||[];
}

function selectCase(id) {
  if (activeCase && activeCase !== id) rememberA1Case(activeCase);
  activeCase = id;
  agent2Input = agent2Inputs?.[id] ? structuredClone(agent2Inputs[id]) : null;
  a2.generation++; a2.result = null; a2.sourceInput = null; a2.review = null; a2.choices = {}; a2.pending = false; a2.error = null; a2.inputSource = "agent1"; a2.useActive = false;
  a1.generation++; a1.result = null; a1.sourceInput = null; a1.pending = false; a1.error = null;
  state.selectedItem = 0;
  baseInput = structuredClone(samples.inputs[id] || { context: agent1Inputs?.[id]?.context || {}, received_at: agent1Inputs?.[id]?.received_at || "", rate_card_id: "RC-SYNTH-2026H2-v2", items: [], constraints: [], pm_review: { items: [] }, a2_result: { assessments: [] } });
  state.scenario = "normal"; state.inputSource = "agent2"; state.sampleInputText = JSON.stringify(baseInput, null, 2); state.inputText = "";
  state.view = null; state.edits = null; state.submittedInput = null; state.signature = null;
  state.error = null; state.conflicted = false; state.invalidated = false; state.originalLines = [];
  restoreA1Case(id);
  if (id !== "CR-A-001") state.mode = "api";
  if (lab.enabled) lab.fakeText = JSON.stringify(samples.fake_responses[id] || lab.defaultFake, null, 2);
}

function sidebar() {
  const nav = [["요청 분석", "a1cb9"], ["대시보드", "1d0b4"], ["프로젝트", "7626c"], ["변경 요청", "f8eec", records.length], ["승인 대기", "bfedc", 3], ["청구", "e6313", 2]];
  return `<aside class="sidebar" aria-label="주 메뉴"><div class="brand"><span class="brand-mark">S</span><span class="brand-name">Scopeline</span><span class="mini-tag">MVP</span></div><div class="workspace"><span class="workspace-mark">한</span><div class="workspace-copy">한빛디지털<small>수행사 워크스페이스</small></div>${icon("b05aa")}</div><div class="nav-section">업무</div>${nav.map(([label, file, count]) => { const action = label === "요청 분석" ? "analysis" : label === "변경 요청" ? "list" : label === "프로젝트" && lab.enabled ? "projects" : null; const current = action && (action === "analysis" ? state.page === "analysis" : action === "projects" ? state.page === "projects" : !["projects","analysis"].includes(state.page)); return `<button class="nav ${current ? "active" : ""}" ${action ? `data-action="${action}" ${current ? 'aria-current="page"' : ""}` : 'disabled title="이번 구현 범위에 포함되지 않습니다"'}>${icon(file)}<span class="nav-label">${label}</span>${count ? `<span class="count">${count}</span>` : ""}</button>`; }).join("")}<div class="nav-section">관리</div><button class="nav" disabled title="이번 구현 범위 밖">${icon("ed47e")}<span class="nav-label">설정</span></button><div class="sidebar-bottom"><div class="disclaimer"><strong>${icon("6bb27")}AI는 제안까지만</strong>범위 확정·가격·승인·고객 발송은 담당자가 결정합니다.</div><div class="profile"><span class="avatar large">김</span><div class="profile-copy">김서윤<small>로컬 확인 · 테스트 PM</small></div></div></div></aside>`;
}
function topbar() {
  const path = state.page === "analysis" ? "요청 분석 / 통합 화면 테스트" : state.page === "projects" ? `프로젝트 / ${kb.projectId || "목록"} / 기준 문서` : state.page === "register" ? "변경 요청 / 등록" : state.page === "list" ? "변경 요청" : `변경 요청 / ${record().id} / ${{ original: "요청 원문", scope: "범위 검토", impact: "영향 산정", draft: "승인·협의" }[state.tab]}`;
  return `<header class="topbar"><span class="breadcrumb">${e(path)}</span><label class="top-search">${icon("776e1")}<input id="global-search" placeholder="CR ID, 프로젝트, 문서 검색" aria-label="전체 요청 검색"></label><button class="icon-button" data-action="help" aria-label="로컬 확인 안내">${icon("c360b")}</button><button class="icon-button" data-action="help" aria-label="도움말">${icon("a340e")}</button><span class="top-separator"></span><span class="avatar large">김</span></header>`;
}
function filteredRecords() {
  let result = records.filter(item => `${item.id} ${item.title} ${item.project} ${item.requester}`.toLowerCase().includes(state.query.toLowerCase()));
  if (state.filter !== "전체") result = result.filter(item => item.scope.includes(state.filter));
  if (state.project !== "전체") result = result.filter(item => item.project.includes(state.project));
  if (state.group !== "열린 요청") result = result.filter(item => item.process === state.group || (state.group === "완료·종료" && item.process === "완료"));
  if (state.order === "최근 접수순") result.sort((a, b) => Number(!a.api) - Number(!b.api) || b.date.localeCompare(a.date));
  return result;
}
function registrationPage() {
  const d = state.registration;
  const channelOptions = [["email", "GMAIL"], ["manual", "MEETING"]];
  const baselines = availableBaselines.filter(b => b.project_id === d.project_id.trim());
  return `<div class="page-heading"><div><h1>변경 요청 등록</h1><p class="subtitle">Agent 1이 직접 구조화할 신규 요청 원문을 등록합니다.</p></div><div class="button-group">${button("목록으로", "list", { class: "plain" })}</div></div>${state.registrationError ? `<div class="notice error">${e(state.registrationError)}</div>` : ""}<section class="card"><h2>요청 정보</h2><div class="settings-grid"><label class="field">프로젝트 ID<input id="registration-project" value="${e(d.project_id)}"></label><label class="field">판단 기준선<select id="registration-baseline"><option value="">기준선 선택</option>${baselines.map(b => `<option value="${e(b.id)}" ${d.baseline_id === b.id ? "selected" : ""}>${e(b.id)} · ${e(b.as_of)}</option>`).join("")}</select></label><label class="field">요청 ID<input id="registration-case" placeholder="예: CR-D-001" value="${e(d.case_id)}"></label><label class="field">프로젝트명<input id="registration-project-name" value="${e(d.project_name)}"></label><label class="field">채널<select id="registration-channel">${channelOptions.map(([value,label]) => `<option value="${value}" ${d.channel === value ? "selected" : ""}>${label}</option>`).join("")}</select></label><label class="field">채널 원본 값<input id="registration-channel-src" value="${e(d.channel_src)}"></label><label class="field">수신 일시<input id="registration-received" type="datetime-local" value="${e(d.received_at)}"></label><label class="field">요청자 역할<input id="registration-sender-role" value="${e(d.sender_role)}"></label><label class="field">요청자 이름<input id="registration-sender-name" value="${e(d.sender_name)}"></label></div><label class="field">제목<input id="registration-subject" value="${e(d.subject)}"></label><label class="field">요청 원문<textarea id="registration-message" rows="8" placeholder="고객이 전달한 요청 내용을 그대로 입력하세요.">${e(d.message_text)}</textarea></label><div class="form-actions">${button("등록하고 Agent 1 실행 화면 열기", "submit-registration", { class: "primary" })}</div></section>`;
}
function registerChangeRequest() {
  const d = state.registration;
  const required = [["project_id", "프로젝트 ID"], ["case_id", "요청 ID"], ["sender_name", "요청자 이름"], ["subject", "제목"], ["message_text", "요청 원문"]];
  const missing = required.find(([key]) => !String(d[key] || "").trim());
  if (missing) { state.registrationError = `${missing[1]}을(를) 입력해 주세요.`; render(); return; }
  const caseId = d.case_id.trim().toUpperCase();
  if (!/^CR-[A-Z0-9]+(?:-[A-Z0-9]+)*$/.test(caseId)) { state.registrationError = "요청 ID는 CR-D-001 형식으로 입력해 주세요."; render(); return; }
  if (records.some(item => item.id === caseId)) { state.registrationError = "이미 존재하는 요청 ID입니다."; render(); return; }
  const baseline = availableBaselines.find(b => b.id === d.baseline_id && b.project_id === d.project_id.trim());
  if (!baseline) { state.registrationError = "프로젝트에 등록된 판단 기준선을 선택해 주세요."; render(); return; }
  const receivedAt = d.received_at ? new Date(d.received_at).toISOString() : new Date().toISOString();
  const input = { context: { project_id: d.project_id.trim(), case_id: caseId, request_revision: 1, baseline_id: baseline.id, baseline_as_of: baseline.as_of, analysis_id: `AN-${caseId}-r1`, scenario_id: null }, channel: d.channel, channel_src: d.channel_src.trim() || null, received_at: receivedAt, sender_role: d.sender_role.trim(), sender_name: d.sender_name.trim(), subject: d.subject.trim(), message_text: d.message_text.trim(), pm_notes: [], answers: [] };
  agent1Inputs = agent1Inputs || {}; agent1Inputs[caseId] = input;
  records.unshift({ id: caseId, title: input.subject, project: d.project_name.trim() || "신규 변경 요청 프로젝트", requester: input.sender_name, requesterRole: input.sender_role, channel: input.channel_src || input.channel, date: receivedAt.slice(5, 10), receivedAt, messageText: input.message_text, datasetSample: true, scope: "미분석", process: "접수", approval: "미요청", customer: "미요청", md: "미산정", price: "—", owner: "—", elapsed: "—", api: true });
  state.registrationError = ""; state.id = caseId; state.tab = "original"; selectCase(caseId); navigate(caseId, "original");
}
function inbox() {
  const result = filteredRecords(), pages = Math.max(1, Math.ceil(result.length / 10));
  state.pageNo = Math.min(state.pageNo, pages);
  const shown = result.slice((state.pageNo - 1) * 10, state.pageNo * 10);
  const groups = ["열린 요청", "PM 검토 필요", "내부 승인 대기", "고객 협의 중", "실행 중", "완료·종료"];
  return `<div class="page-heading"><div><h1>변경 요청</h1><p class="subtitle">전체 프로젝트 · 합성 요청 ${records.length}건 · Agent 1 테스트 30건 포함</p></div><div class="button-group">${button("내보내기", "unused", { disabled: true, icon: "e95f2", title: "업무 내보내기는 이번 범위 밖" })}${button("변경 요청 등록", "register", { class: "primary", icon: "416f8" })}</div></div><div class="tabs" role="tablist" aria-label="요청 처리 상태">${groups.map(group => `<button class="tab" role="tab" aria-selected="${state.group === group}" data-group="${group}">${group}<small>${group === "열린 요청" ? records.length : records.filter(item => item.process === group).length}</small></button>`).join("")}</div><div class="filters"><label class="search-field">${icon("776e1")}<input id="request-search" placeholder="CR ID, 제목, 요청자 검색" aria-label="요청 검색" value="${e(state.query)}"></label><label class="filter">프로젝트<select id="project-filter" aria-label="프로젝트 필터">${["전체", ...new Set(records.map(item => item.project))].map(value => `<option ${state.project === value ? "selected" : ""}>${e(value)}</option>`).join("")}</select></label><label class="filter">범위 판단<select id="scope-filter" aria-label="범위 판단 필터">${["전체", "범위 밖", "판단 보류", "미분석"].map(value => `<option ${state.filter === value ? "selected" : ""}>${value}</option>`).join("")}</select></label><span class="filter">상업적 결정 · 예시</span><label class="filter last">정렬<select id="sort" aria-label="요청 정렬"><option ${state.order === "기본" ? "selected" : ""}>기본</option><option ${state.order === "최근 접수순" ? "selected" : ""}>최근 접수순</option></select></label></div><div class="table-wrap"><table class="inbox-table"><thead><tr>${["CR ID", "요청 · 프로젝트", "요청자 · 채널", "접수일", "범위 판단", "요청 처리", "내부 승인", "고객 협의", "영향 (MD · 금액)", "담당", "경과"].map(title => `<th>${title}</th>`).join("")}</tr></thead><tbody>${shown.length ? shown.map(item => `<tr><td class="id-cell"><button class="row-open" data-open="${item.id}">${item.id}${item.analysisDemo ? '<span class="api-marker">통합 화면 테스트</span>' : item.api ? '<span class="api-marker">API 연결 샘플</span>' : ""}</button></td><td class="title-cell"><button class="row-open" data-open="${item.id}"><strong>${e(item.title)}</strong><small>${e(item.project)}</small></button></td><td>${e(item.requester)}<small>${item.channel}</small></td><td>${item.date}</td><td>${chip(item.scope, item.scope.includes("가능성") ? "ai" : "neutral")}</td><td>${chip(item.process, "info")}</td><td>${chip(item.approval)}</td><td>${chip(item.customer)}</td><td class="numeric">${e(item.md)}<small>${e(item.price)}</small></td><td><span class="avatar">${item.owner}</span></td><td class="numeric">${item.elapsed}</td></tr>`).join("") : '<tr><td colspan="11"><div class="empty-state"><h3>검색 결과가 없습니다</h3><p>검색어나 필터를 바꿔 주세요.</p></div></td></tr>'}</tbody></table></div><div class="pagination"><span>${shown.length} / ${result.length}건 표시 · 범위 밖은 유상 확정을 의미하지 않습니다.</span><div class="button-group">${button("이전", "previous", { disabled: state.pageNo <= 1 })}<span>${state.pageNo} / ${pages}</span>${button("다음", "next", { disabled: state.pageNo >= pages })}</div></div>`;
}
function statusStrip() {
  const actual = record().api && state.view;
  const cells = [["요청 처리", "PM 검토 필요", "합성 입력"], ["범위 판단", "범위 밖", "상위 PM 검토: 예시"], ["상업적 결정", "미결정", "이번 범위 밖"], ["PM 산정 승인", actual ? state.view.approval?.approved ? "승인됨" : state.view.approval ? "반려 · 재검토" : "미승인" : "미요청", actual ? PHASE[state.view.phase] : "API 결과 없음"], ["고객 협의", "미요청", "외부 발송 없음"], ["실행", "미착수", "이번 범위 밖"], ["청구", "청구 대상 아님", "자동 확정 없음"]];
  return `<div class="status-strip">${cells.map(([label, value, sub]) => `<div class="status-cell"><span>${label}</span>${chip(value)}<small>${e(sub)}</small></div>`).join("")}</div>`;
}
function tabTestGuide() {
  const guides = {
    original: ["Agent 1", "요청 원문 탭에서 Agent 1 공급자와 모델을 선택하고 바로 실행할 수 있습니다."],
    scope: ["Agent 2", "범위 검토 입력과 Agent 2 공급자를 선택한 뒤 범위 판단을 실행할 수 있습니다."],
    impact: ["Agent 3", "영향 산정 탭에서 Agent 3 공급자와 모델을 선택하고 영향 산정을 실행할 수 있습니다."],
    draft: ["Agent 3", "승인·협의 탭에서 협의안을 생성할 Agent 3 공급자와 모델을 선택하고 실행할 수 있습니다."],
  };
  const [agent, description] = guides[state.tab] || guides.original;
  return `<div class="notice"><strong>테스트 · ${e(agent)}</strong>${e(description)}</div>`;
}
function detail() {
  const item = record();
  if (item.datasetSample) {
    const tabs = [["original", "요청 원문"], ["scope", "범위 검토"], ["impact", "영향 산정"], ["draft", "승인·협의"]];
    const actions = state.tab === "original" ? button(a1.pending ? "항목화 중…" : "Agent 1 실행", "run-a1", { class: "primary", disabled: a1.pending || !lab.enabled || !agent1Inputs?.[activeCase], icon: "a1cb9" }) : state.tab === "impact" ? button(state.pending ? "처리 중…" : "영향 산정 실행", "prepare", { class: "primary", disabled: state.pending }) : button("영향 산정으로", "go-impact", { class: "primary" });
    return `<div class="page-heading"><div><h1 class="detail-title"><span class="cr-id">${e(item.id)}</span>${e(item.title)}</h1><p class="subtitle">${e(item.project)} · 에이전트 테스트 요청</p></div><div class="button-group">${button("목록", "list", { class: "plain" })}${actions}</div></div>${tabTestGuide()}<div class="tabs" role="tablist" aria-label="변경 요청 상세 단계">${tabs.map(([key, label]) => `<button class="tab" role="tab" aria-selected="${state.tab === key}" data-tab="${key}">${label}</button>`).join("")}</div>${state.error ? errorBanner() : ""}${state.tab === "original" ? agent1Original(item) : state.tab === "scope" ? scope() : state.tab === "impact" ? impact() : draft()}`;
  }
  const tabs = [["original", "요청 원문"], ["scope", "범위 검토"], ["impact", "영향 산정"], ["draft", "승인·협의"]];
  let actions = state.tab === "original" ? button(a1.pending ? "항목화 중…" : "Agent 1 실행", "run-a1", { class: "primary", icon: "a1cb9", disabled: a1.pending || !lab.enabled || !agent1Inputs?.[activeCase] || !item.api }) : state.tab === "scope" ? button("영향 산정으로", "go-impact", { class: "primary", icon: "ec151" }) : state.tab === "impact" ? button(state.pending ? "처리 중…" : "영향 산정 실행", "prepare", { class: "primary", icon: "a1cb9", disabled: state.pending || !item.api }) : button("영향 산정으로", "go-impact", { icon: "ec991" });
  if (item.analysisDemo) actions = `<button class="button" data-demo-open="${e(item.id)}">통합 진행 기록으로</button>`;
  return `<div class="page-heading"><div><h1 class="detail-title"><span class="cr-id">${e(item.id)}</span>${e(item.title)}</h1></div><div class="button-group">${button("목록", "list", { class: "plain" })}${actions}</div></div><div class="detail-meta"><span>${icon("91f0e")}${e(item.project)}</span><span>${icon("e56db")}요청자 ${e(item.requester)} (고객 PM)</span><span>${icon("29cce")}${item.channel} · ${item.date} 수신</span><span>${icon("d6187")}판단 기준선 ${item.api ? "BL-2 · 합성" : "v1.2 · 예시"}</span><span><span class="avatar">김</span>담당 김서윤</span></div>${item.analysisDemo ? `<div class="notice"><strong>통합 분석 · 가상 결과</strong>기존 변경 요청 화면에서 시연 결과를 확인합니다. 실제 AI·문서 검색은 실행하지 않았으며 PM 확인은 통합 진행 기록에서 가능합니다.</div>` : tabTestGuide()}${!item.api ? `<div class="notice"><strong>조회용 예시 요청입니다.</strong>이 요청은 화면 흐름만 확인할 수 있습니다. 실제 Agent 3 연결은 ${button("CR-A-001 열기", "open-api", { class: "small" })}에서 확인해 주세요.</div>` : ""}${statusStrip()}<div class="tabs" role="tablist" aria-label="변경 요청 상세 단계">${tabs.map(([key, label]) => `<button class="tab" role="tab" aria-selected="${state.tab === key}" data-tab="${key}">${label}</button>`).join("")}${["실행·검수", "청구", "이력"].map(label => `<button class="tab" role="tab" aria-selected="false" disabled title="이번 구현 범위 밖">${label}</button>`).join("")}</div>${state.error ? errorBanner() : ""}${state.tab === "original" ? agent1Original(item) : state.tab === "scope" ? scope() : state.tab === "impact" ? impact() : draft()}${item.analysisDemo && state.tab === "draft" ? `<section class="card" style="margin-top:16px">${analysisDemo.resultPreview(item.id, "draft")}</section>` : ""}`;
}
function agent1Items() {
  if (a1.result?.items?.length) return a1.result.items;
  return baseInput?.items || [];
}
function agent1Original(item) {
  const source = agent1Inputs?.[activeCase];
  const message = source?.message_text || item?.messageText || "요청 원문 데이터를 찾을 수 없습니다.";
  const context = source?.context || baseInput?.context || {};
  const items = a1.result?.items || [];
  const questions = a1.result?.questions || [];
  const statusLabel = a1.pending ? "실행 중" : a1.result ? (a1.result.status === "NEEDS_INPUT" ? "추가 정보 필요" : "항목화 완료") : "실행 전";
  const statusTone = a1.pending ? "info" : a1.result?.status === "NEEDS_INPUT" ? "warning" : a1.result ? "success" : "neutral";
  return `${agent1Panel()}<div class="metric-grid">${metric("Agent 1 상태", statusLabel, a1.pending ? "모델 응답을 기다리는 중" : a1.result ? "최근 실행 결과" : "아직 실행하지 않았습니다", false)}${metric("추출 항목", `${items.length}<small>건</small>`, items.length ? "Agent 1 실행 결과 기준" : "실행 전 또는 결과 없음", false)}${metric("확인 질문", `${questions.length}<small>건</small>`, questions.length ? "추가 확인이 필요한 항목" : "현재 추가 질문 없음", false)}${metric("실행 모델", a1.provider === "openai" ? "GPT" : a1.provider === "gemini" ? "Gemini" : a1.provider === "bedrock" ? "Bedrock" : a1.provider === "local" ? "로컬 LLM" : "fixture", e(a1.model || "모델 미선택"), true)}</div><div class="columns impact"><div class="stack"><section class="card"><div class="card-heading"><div><h2>1. 요청 원문 입력</h2><p class="caption">실행에 전달되는 원문과 요청 컨텍스트입니다.</p></div><span class="chip subtle">${icon("83533")}원문 보존</span></div><div class="metadata-box"><dl><dt>요청 ID</dt><dd>${e(context.case_id || item?.id || "—")}</dd><dt>프로젝트</dt><dd>${e(context.project_id || item?.project || "—")}</dd><dt>보낸 사람</dt><dd>${e(context.sender_name || item?.requester || "—")}</dd><dt>채널</dt><dd>${e(context.channel || item?.channel || "—")}</dd></dl></div><div class="original-text"><strong>제목: ${e(context.subject || item?.title || "변경 요청")}</strong><p style="white-space:pre-wrap">${e(message)}</p></div></section></div><div class="stack"><section class="card"><div class="card-heading"><div><h2>2. Agent 1 항목화 결과</h2><p class="caption">실행 후 추출된 기능·제약·확인 항목을 확인합니다.</p></div>${chip(statusLabel, statusTone)}</div>${a1.pending ? '<div class="loading-line" role="status"><span class="inline-spinner"></span>Agent 1 요청 항목화를 실행하는 중입니다…</div>' : items.length ? items.map((x, index) => `<div class="list-item"><span class="item-no">${e(x.display_no || x.item_id || `R${index + 1}`)}</span><span class="mini-tag">${e(itemTypeLabel(x.item_type || x.intent || "항목"))}</span><div class="item-main"><strong>${e(x.text)}</strong><small>${x.spans?.length ? e(x.spans.map(span => span.text).join(" · ")) : "원문 구간 없음"}</small></div></div>`).join("") : `<div class="empty-state"><h3>${a1.result ? "항목화 결과가 없습니다" : "아직 실행 전입니다"}</h3><p>${a1.result ? "실행 상태와 응답을 확인해 주세요." : "모델 공급자와 모델을 선택한 뒤 Agent 1을 실행하세요."}</p></div>`}${a1.result?.meta ? `<details class="input-settings"><summary>실행 메타데이터</summary><div class="metadata-box"><dl><dt>결과 ID</dt><dd>${e(a1.result.meta.result_id || "—")}</dd><dt>모델</dt><dd>${e(a1.result.meta.model || a1.result.meta.agent_version || "—")}</dd><dt>버전</dt><dd>${e(a1.result.meta.agent_version || "—")}</dd><dt>토큰</dt><dd>${e(a1.result.meta.debug?.input_tokens ?? "—")} / ${e(a1.result.meta.debug?.output_tokens ?? "—")}</dd></dl></div></details>` : ""}</section><section class="card"><h2>3. 확인 질문</h2><p class="caption">Agent 1이 항목화 중 추가 확인이 필요한 질문입니다.</p>${questions.length ? questions.map((q, index) => `<div class="question"><strong>Q${index + 1} · ${e(q.text)}</strong><span class="muted">${e(q.reason || q.target || "확인 필요")}</span></div>`).join("") : '<p class="muted">실행 결과가 있으면 확인 질문이 여기에 표시됩니다.</p>'}</section></div></div>`;
}
function datasetOriginal(item) {
  const items = agent1Items();
  const label = a1.result ? "Agent 1 실행 결과" : "상위 합성 요청 항목";
  const caption = a1.result ? "아래 항목은 방금 실행한 Agent 1 결과입니다." : "Agent 1 실행 전 합성 예시 항목입니다.";
  const empty = a1.result ? "실행 결과 항목이 없습니다. Agent 1 상태를 확인해 주세요." : "상위 합성 요청 항목이 없습니다.";
  return `<div class="columns"><section class="card"><div class="card-heading"><div><h2>요청 원문</h2><p class="caption">Agent 1 테스트 데이터의 합성 요청입니다.</p></div></div><div class="metadata-box"><dl><dt>보낸 사람</dt><dd>${e(item.requester)} · ${e(item.requesterRole)}</dd><dt>수신 일시</dt><dd>${e(item.receivedAt)}</dd><dt>채널</dt><dd>${e(item.channel)}</dd></dl></div><div class="original-text"><strong>제목: ${e(item.title)}</strong><p style="white-space:pre-wrap">${e(item.messageText)}</p></div></section><section class="card"><h2>${label}</h2><p class="caption">${caption}</p>${items.length ? items.map(x => `<div class="list-item"><span class="item-no">${e(x.display_no || x.item_id)}</span><span><strong>${e(x.text)}</strong>${x.item_type || x.intent ? `<small class="tiny">${e(itemTypeLabel(x.item_type || "항목"))}${x.intent ? ` · ${e(x.intent)}` : ""}</small>` : ""}</span></div>`).join("") : `<p class="muted">${empty}</p>`}</section></div>`;
}
function datasetScope() {
  const labels = { in_scope: "범위 내", out_of_scope: "범위 밖", unclear: "판단 보류" };
  return `<section class="card"><h2>상위 범위 검토 · 합성 입력</h2><p class="caption">저장된 Agent 2 및 PM 검토 예시입니다. 이 화면은 해당 에이전트를 실행하지 않습니다.</p>${baseInput.a2_result.assessments.map(a => { const item = baseInput.items.find(x => x.item_id === a.item_id); const review = baseInput.pm_review.items.find(x => x.item_id === a.item_id); return `<div class="question"><strong>${e(item?.display_no)} · ${e(item?.text)}</strong><span>AI 예시: ${e(labels[a.label] || a.label)} · PM 예시: ${e(labels[review?.pm_label] || review?.pm_label)} · 산정 대상: ${review?.estimate ? "선택" : "아님"}</span><span class="muted">${e(a.rationale)}</span></div>`; }).join("")}${button("영향 산정으로", "go-impact", { class: "primary" })}</section>`;
}
function original() {
  const apiCase = record().api;
  const resultItems = a1.result?.items;
  const items = resultItems?.length
    ? resultItems.map(item => [item.display_no || item.item_id, itemTypeLabel(item.item_type || item.intent || "항목"), item.text])
    : apiCase ? [["R1", "기능", baseInput.items[0]?.text || ""], ...(baseInput.constraints?.length ? [["R2", "기한", baseInput.constraints[0].text]] : [])] : sampleItems.map(item => [item[0], item[1], item[2]]);
  const questions = a1.result?.questions?.length
    ? a1.result.questions.map((q, index) => `<div class="question"><strong>Q${index + 1} · ${e(q.text)}</strong><span class="muted">${e(q.reason)}</span></div>`).join("")
    : `<div class="question"><strong>Q1 · 기존 이미지 저장소를 재사용할 수 있나요?</strong><span class="muted">저장 방식에 따라 공수가 달라질 수 있습니다.</span></div><div class="question"><strong>Q2 · 사진 첨부는 이번 변경에 포함되나요?</strong><span class="muted">현재 입력은 사진 최대 3장을 포함한 합성 가정입니다.</span></div>`;
  return `<div class="columns"><div class="stack"><section class="card"><div class="card-heading"><div><h2>요청 원문</h2><p class="caption">원문은 조회용 합성 데이터이며 실제 수신함과 연결되지 않습니다.</p></div><span class="chip subtle">${icon("83533")}원문 보존</span></div><div class="metadata-box"><dl><dt>보낸 사람</dt><dd>${e(record().requester)} (고객 PM)</dd><dt>받는 사람</dt><dd>김서윤 (한빛디지털 · PM)</dd><dt>수신 일시</dt><dd>2026-${record().date} 09:12</dd><dt>채널</dt><dd>${record().channel} · 원문 예시</dd><dt>첨부</dt><dd>${icon("7502b")}첨부 업로드는 이번 범위 밖</dd></dl></div><div class="original-text"><strong>제목: [자사몰] 상품 리뷰 기능 요청드립니다</strong><p>안녕하세요. 지난 주간 회의에서 말씀드린 리뷰 기능을 정리해서 보내드립니다.</p>${(apiCase ? ["상세페이지에 구매자 별점과 후기 텍스트를 넣어 주세요.", "가능하면 사진도 3장까지 올리게 해주세요.", "변경 합의된 11월 24일 오픈일은 유지해야 합니다."] : ["상품 상세 페이지에서 별점(5점) 리뷰를 작성할 수 있게 해 주세요.", "텍스트 리뷰도 함께 작성·조회가 되어야 합니다.", "가능하면 사진도 여러 장 첨부할 수 있으면 좋겠습니다.", "리뷰는 실제 구매한 고객만 작성할 수 있어야 합니다.", "오픈 일정은 그대로 유지 부탁드립니다."]).map((line, i) => `<div class="highlight"><span class="number">${i + 1}</span><span>${line}</span></div>`).join("")}<p>검토 후 회신 부탁드립니다. 감사합니다.</p></div><div class="card-footer">${icon("2b0e7")}회색 하이라이트 = 항목으로 추출한 원문 위치</div></section><section class="card"><div class="card-heading"><div><h2>PM 메모</h2><p class="caption">실제 저장 기능은 연결 전입니다.</p></div>${button("메모 추가", "unused", { disabled: true, icon: "416f8" })}</div><p class="caption"><span class="avatar">김</span> 김서윤 · 로컬 예시</p><p style="margin-top:10px">사진 포함 여부와 기존 저장소 재사용 가정을 확인해야 합니다. 업무 확정·고객 전달은 별도 담당자가 결정합니다.</p></section></div><div class="stack"><section class="card"><div class="card-heading"><div><h2>${icon("2b0e7")} ${a1.result ? "Agent 1 실행 결과" : "AI 요청 항목화"}</h2><p class="caption">${a1.result ? "방금 실행한 Agent 1 요청 항목화 결과입니다." : "상위 Agent 1·2 결과의 합성 입력"}</p></div>${chip(a1.result ? "실행 완료" : "AI 제안", a1.result ? "success" : "ai")}</div>${items.map(item => `<div class="list-item"><span class="item-no">${e(item[0])}</span><span class="mini-tag">${e(item[1])}</span><div class="item-main"><strong>${e(item[2])}</strong><small>${icon("e0393")}${a1.result ? "Agent 1 실행 근거" : "원문 연결 · 합성 근거"}</small></div>${icon("e7746")}</div>`).join("")}<div class="card-footer">${button("항목 추가", "unused", { disabled: true, icon: "416f8" })}${button(a1.result ? "Agent 1 다시 실행" : "AI 재분석", "run-a1", { disabled: a1.pending || !lab.enabled || !agent1Inputs?.[activeCase], icon: "e4529" })}</div></section><section class="card"><h2>확인 질문</h2><p class="caption">${a1.result ? "Agent 1이 실행 결과에서 추가 확인이 필요한 항목입니다." : "실행 전 합성 확인 질문입니다."}</p>${questions}</section></div></div>`;
}
// ---- C04 범위 검토: Agent 2 실제 결과 (POST /agent2/assessments). 실행 전·조회 예시는 기존 합성 화면(scopeSample) ----
const A1_MEMORY_STORAGE = "weave-prototype-agent1-memory-v1";
const a1 = { result: null, sourceInput: null, pending: false, error: null, generation: 0, provider: 'openai', model: 'gpt-6-luna', region: 'ap-northeast-2' };
const a2 = { inputSource: 'agent1', useActive: false, result: null, sourceInput: null, review: null, choices: {}, pending: false, error: null, generation: 0, provider: 'openai', model: 'gpt-6-luna', region: 'ap-northeast-2' };
let a1Memory = {};
try { a1Memory = JSON.parse(sessionStorage.getItem(A1_MEMORY_STORAGE) || "{}") || {}; } catch { a1Memory = {}; }
function rememberA1Case(caseId = activeCase) {
  if (!caseId) return;
  a1Memory[caseId] = {
    result: a1.result ? structuredClone(a1.result) : null,
    sourceInput: a1.sourceInput ? structuredClone(a1.sourceInput) : null,
    error: a1.error ? structuredClone(a1.error) : null,
    provider: a1.provider,
    model: a1.model,
    region: a1.region,
  };
  try { sessionStorage.setItem(A1_MEMORY_STORAGE, JSON.stringify(a1Memory)); } catch { /* Browser memory still works when session storage is unavailable. */ }
}
function restoreA1Case(caseId) {
  const saved = a1Memory[caseId];
  if (!saved) return;
  a1.result = saved.result ? structuredClone(saved.result) : null;
  a1.sourceInput = saved.sourceInput ? structuredClone(saved.sourceInput) : null;
  a1.pending = false;
  a1.error = saved.error ? structuredClone(saved.error) : null;
  if (saved.provider) a1.provider = saved.provider;
  if (saved.model) a1.model = saved.model;
  if (saved.region) a1.region = saved.region;
}
function agentSettings(name, data) {
  if (record().analysisDemo) return `<div class="local-bar"><strong>통합 화면 테스트 결과 · 읽기 전용</strong><span>선택 모델: ${e(a1.model)}</span></div>`;
  if (!lab.enabled) return '<div class="notice">공급자를 선택하려면 서버를 <code>python prototype/run_demo.py --lab</code>로 실행하세요.</div>';
  const modelControl = data.provider === 'ollama'
    ? `<label class="field">Ollama 로컬 모델<select id="${name}-model" ${data.pending ? 'disabled' : ''}>${[...new Set([...ollamaModels, data.model].filter(Boolean))].map(model => `<option value="${e(model)}" ${model === data.model ? 'selected' : ''}>${e(model)}</option>`).join('')}</select></label>`
    : data.provider !== 'fake'
      ? `<label class="field">모델 ID<input id="${name}-model" value="${e(data.model)}" ${data.pending ? 'disabled' : ''}></label>`
      : '';
  return `<section class="card"><h2>${name === 'a1' ? 'Agent 1' : 'Agent 2'} 테스트 실행 설정</h2><p class="caption">가상 응답은 저장된 합성 사례를 반환합니다. Gemini·GPT·Bedrock은 외부 모델을 호출하며, Ollama는 로컬 모델을 호출합니다. 외부 API 인증 정보는 서버에서 사용합니다.</p><div class="button-group">${[['fake','가상 응답'],['gemini','Gemini API'],['openai','GPT API'],['bedrock','AWS Bedrock'],['ollama','Ollama (Local)']].map(([value,label]) => `<label class="check"><input type="radio" name="${name}-provider" value="${value}" ${data.provider === value ? 'checked' : ''} ${data.pending ? 'disabled' : ''}>${label}</label>`).join('')}</div>${modelControl}${data.provider === 'bedrock' ? `<label class="field">AWS 리전<input id="${name}-region" value="${e(data.region)}" ${data.pending ? 'disabled' : ''}></label>` : ''}</section>`;
}
async function runAgent1() {
  if (record().analysisDemo) return;
  if (!lab.enabled || a1.pending || !agent1Inputs?.[activeCase]) return;
  const caseId = activeCase, generation = ++a1.generation;
  const input = structuredClone(agent1Inputs[caseId]);
  const config = { provider: a1.provider, model: a1.model, region: a1.region };
  if (a2.inputSource === 'agent1') resetAgent2Result();
  a1.pending = true; a1.error = null; a1.result = null; a1.sourceInput = null; render();
  try {
    const result = await agent1.run(input, config);
    if (a1.generation !== generation || activeCase !== caseId) return;
    if (result.context?.case_id !== caseId || result.context?.project_id !== input.context.project_id) throw new Error('다른 요청의 Agent 1 결과가 반환되었습니다.');
    a1.result = result; a1.sourceInput = input; rememberA1Case(caseId); toast('Agent 1 요청 항목화 결과를 받았습니다.');
  } catch (error) { if (a1.generation === generation && activeCase === caseId) { a1.error = { status: error.status || 0, message: error.message }; rememberA1Case(caseId); } }
  finally { if (a1.generation === generation && activeCase === caseId) { a1.pending = false; rememberA1Case(caseId); render(); } }
}
function agent1Panel() {
  if (record().analysisDemo) return agentSettings('a1',a1);
  const label = a1.pending ? '요청 항목화 중… (최대 1~2분)' : a1.result ? '다시 실행' : 'Agent 1 요청 항목화 실행';
  const status = a1.result
    ? `${chip(a1.result.status === 'NEEDS_INPUT' ? '추가 정보 필요' : '항목화 완료', a1.result.status === 'NEEDS_INPUT' ? 'warning' : 'success')}<span class="tiny">결과 ${e(a1.result.meta?.result_id || '없음')} · ${e(a1.result.meta?.model || a1.result.meta?.agent_version || '서버 설정 확인')}</span>`
    : '<span class="muted">아래는 실행 전 합성 요청 항목입니다. 실행 방식은 선택한 Agent 1 공급자에 따릅니다.</span>';
  const error = a1.error ? `<div class="notice inline"><strong>Agent 1 실행 실패${a1.error.status ? ` (HTTP ${a1.error.status})` : ''}</strong>${e(a1.error.message)}</div>` : '';
  return `${agentSettings('a1', a1)}<div class="baseline">${icon('2b0e7')}<strong>Agent 1 요청 항목화</strong>${status}${button(label, 'run-a1', { class: 'primary small', icon: 'a1cb9', disabled: a1.pending || !lab.enabled || !agent1Inputs?.[activeCase] })}</div>${error}`;
}
const A2_LABEL = { in_scope: ["기존 범위 내", "success"], out_of_scope: ["범위 밖 가능성", "ai"], unclear: ["판단 보류", "warning"], approved_change: ["범위 내 (변경합의서 승인분)", "success"] };
const A2_LEVEL = { strong: "강", medium: "중", none: "근거 없음", interpretation_needed: "해석 필요" };
const A2_ROLE = { basis: "직접 근거", policy: "절차·원칙", superseding: "우선 문서", context: "참고" };
const DOC_NAME = { CTR: "계약서", SOW: "과업지시서", FSP: "기능명세서", QUO: "견적서", CO: "변경합의서" };
const docName = id => kbDoc(id)?.title || DOC_NAME[String(id).split("-")[0]] || "문서";
function resetAgent2Result() {
  a2.generation++; a2.pending = false; a2.result = null; a2.sourceInput = null;
  a2.review = null; a2.choices = {}; a2.error = null;
  if (state.inputSource === "agent2") { state.inputText = ""; if (state.view) state.invalidated = true; persist(); }
}
function selectAgent2Input(source) {
  if (!['sample', 'agent1'].includes(source) || source === a2.inputSource || a2.pending) return;
  resetAgent2Result(); a2.inputSource = source; render();
}
function agent1ToAgent2Input() {
  const out = a1.result, source = a1.sourceInput, current = agent1Inputs?.[activeCase];
  if (a1.pending || !out || !source || !current) throw new Error('먼저 요청 원문 탭에서 Agent 1을 실행해 주세요.');
  if (JSON.stringify(source) !== JSON.stringify(current) || JSON.stringify(out.context) !== JSON.stringify(source.context) || source.context?.case_id !== activeCase) throw new Error('현재 요청과 Agent 1 결과가 다릅니다. Agent 1을 다시 실행해 주세요.');
  if (out.status !== 'READY' || (out.questions || []).some(q => q.blocking && ['open', 'answered'].includes(q.status))) throw new Error('Agent 1의 필수 확인 사항을 해결한 뒤 다시 실행해 주세요.');
  if (!out.meta?.result_id) throw new Error('Agent 1 결과 ID가 없습니다. 다시 실행해 주세요.');
  const baseline = availableBaselines.find(b => b.id === source.context.baseline_id && b.project_id === source.context.project_id && b.as_of === source.context.baseline_as_of);
  if (!baseline) throw new Error('이 프로젝트의 유효한 기준선을 확인할 수 없습니다. 등록된 기준선으로 다시 시작해 주세요.');
  const items = (out.items || []).filter(x => x.item_type === 'function');
  if (!items.length) throw new Error('범위 판단할 기능 항목이 없습니다. Agent 1 항목을 확인해 주세요.');
  if (new Set(items.map(x => x.item_id)).size !== items.length || items.some(x => x.case_id !== activeCase)) throw new Error('Agent 1 항목의 요청 ID를 확인해 주세요.');
  return { context: structuredClone(source.context), message_text: source.message_text, a1_result_id: out.meta.result_id,
    items: structuredClone(items), constraints: structuredClone((out.items || []).filter(x => ['constraint', 'deadline', 'dependency'].includes(x.item_type))), answers: structuredClone(source.answers || []) };
}
function selectedAgent2Input() {
  if (a2.inputSource === 'agent1') return agent1ToAgent2Input();
  if (!agent2Input) throw new Error('이 요청에는 기존 샘플 입력이 없습니다. Agent 1 실행 결과를 선택해 주세요.');
  return structuredClone(agent2Input);
}
async function runAgent2() {
  if (record().analysisDemo) return;
  if (a2.pending) return;
  let input;
  try {
    input = selectedAgent2Input();
    if (a2.inputSource === 'agent1' && (!lab.enabled || a2.provider === 'fake')) throw new Error('Agent 1 결과를 분석하려면 Gemini·GPT·Bedrock·Ollama를 선택해 주세요. 가상 응답은 저장된 샘플입니다.');
  } catch (error) { a2.error = { status: 0, message: error.message }; render(); return; }
  const active = a2.useActive ? kbBaselineState(input.context)?.active : null;
  if (active && active.baseline_id !== input.context.baseline_id) input.context = { ...input.context, baseline_id: active.baseline_id, baseline_as_of: active.as_of };
  const caseId = activeCase, generation = ++a2.generation;
  a2.pending = true; a2.error = null; a2.result = null; a2.sourceInput = null; a2.review = null; a2.choices = {}; if (state.inputSource === "agent2") { state.inputText = ""; if (state.view) state.invalidated = true; persist(); } render();
  try {
    const config = lab.enabled ? { provider: a2.provider, model: a2.model, region: a2.region } : null;
    const result = await agent2.assess(input, config);
    if (a2.generation !== generation || activeCase !== caseId) return;
    if (JSON.stringify(result.context) !== JSON.stringify(input.context)) throw new Error("다른 요청 또는 기준선의 Agent 2 결과가 반환되었습니다.");
    const ids = input.items.map(x => x.item_id), assessed = (result.assessments || []).map(x => x.item_id);
    if (ids.length !== assessed.length || new Set(assessed).size !== ids.length || assessed.some(id => !ids.includes(id))) throw new Error('Agent 2 결과 항목이 전달한 입력과 일치하지 않습니다.');
    a2.result = result; a2.sourceInput = input; a2.review = null; a2.choices = {}; state.selectedItem = 0; toast("Agent 2 범위 판단 결과를 받았습니다.");
  }
  catch (error) { if (a2.generation === generation && activeCase === caseId) a2.error = { status: error instanceof ApiError ? error.status : 0, message: error.message }; }
  finally { if (a2.generation === generation && activeCase === caseId) { a2.pending = false; render(); } }
}
function agent2Bar() {
  if (record().analysisDemo) return `${agentSettings('a2',a2)}<section class="card"><h2>범위 판단 입력 · Agent 1 결과</h2><p class="caption">${e(a2.sourceInput?.context.case_id)} · ${a2.sourceInput?.items.length||0}개 원문 구간 · 가상 결과</p></section>`;
  const label = a2.pending ? "범위 판단 중… (최대 1~2분)" : a2.result ? "다시 실행" : "AI 범위 판단 실행";
  const status = a2.result ? `${chip(a2.result.status === "NEEDS_INPUT" ? "추가 정보 필요" : "PM 검토 대기", a2.result.status === "NEEDS_INPUT" ? "warning" : "info")}<span class="tiny">결과 ${e(a2.result.meta.result_id)} · ${e(a2.result.meta.model || a2.result.meta.agent_version || "서버 설정 확인")}</span>` : '<span class="muted">입력을 선택하고 내용을 확인한 뒤 범위 판단을 실행하세요.</span>';
  const failure = a2.error ? `<div class="notice inline"><strong>Agent 2 실행 실패${a2.error.status ? ` (HTTP ${a2.error.status})` : ""}</strong>${e(a2.error.message)}</div>` : "";
  let input, why = '';
  try { input = selectedAgent2Input(); } catch (error) { why = error.message; }
  const preview = input ? `<div class="input-preview-grid"><section class="input-preview-panel"><div class="input-preview-label">범위 판단 대상 · ${input.items.length}건</div>${input.items.map(x => `<div class="input-preview-item"><strong>${e(x.display_no)} · ${e(x.text)}</strong></div>`).join('')}</section><section class="input-preview-panel"><div class="input-preview-label">판단 기준선 · 일정과 제약</div><p>${e(input.context.baseline_id || '미선택')} · ${e(input.context.baseline_as_of || '날짜 없음')}</p>${(input.constraints || []).map(x => `<p>${e(x.text)}</p>`).join('') || '<p>전달된 일정·제약 없음</p>'}</section></div>` : `<p class="input-preview-muted">${e(why)}</p>`;
  const choice = `<section class="card"><h2>범위 판단 입력 선택</h2><fieldset class="field"><legend>입력 출처</legend><div class="button-group">${[['sample','기존 샘플 입력'],['agent1','Agent 1 실행 결과']].map(([v,l]) => `<label class="check"><input type="radio" name="a2-input-source" value="${v}" ${a2.inputSource === v ? 'checked' : ''} ${a2.pending ? 'disabled' : ''}>${l}</label>`).join('')}</div></fieldset><p class="caption">선택한 입력을 확인하고 Agent 2를 실행합니다. Agent 1 결과는 항목 검토 후 사용해 주세요.</p>${preview}${a2.inputSource === 'agent1' ? '<p class="tiny">가상 응답은 이 결과를 분석하지 않습니다. Gemini·GPT·Bedrock 중 하나를 선택하세요.</p>' : ''}</section>`;
  return `${choice}${agentSettings('a2', a2)}${baselineBanner()}<div class="baseline">${icon("2b0e7")}<strong>Agent 2 범위 판단</strong>${status}${button(label, "run-a2", { class: "primary small", icon: "a1cb9", disabled: a2.pending })}</div>${failure}`;
}
function scope() {
  if (!record().api) return scopeSample();
  if (!a2.result) return agent2Bar() + (a2.inputSource === "sample" && agent2Input ? (record().datasetSample ? datasetScope() : scopeSample()) : "");
  const out = a2.result, rows = out.assessments;
  const meta = Object.fromEntries([...(a2.sourceInput.items || []), ...(a2.sourceInput.constraints || [])].map(item => [item.item_id, item]));
  state.selectedItem = Math.min(state.selectedItem, Math.max(rows.length - 1, 0));
  const sel = rows[state.selectedItem];
  if (!sel) return agent2Bar() + '<section class="card"><div class="empty-state"><h3>판단할 기능 항목이 없습니다.</h3></div></section>';
  const label = item => A2_LABEL[item.label] || [item.label, "neutral"];
  const questions = (out.questions || []).filter(q => q.item_ids.includes(sel.item_id));
  const steps = out.meta.debug?.steps?.[sel.item_id] || [];
  const step = s => s.action === "search_docs" ? `검색 "${e(s.query)}" → ${e((s.hits || []).join(", ") || "결과 없음")}` : s.action === "get_section" ? `조항 열람 ${e(s.read || s.note || s.error || `${s.doc_id}:${s.section_id}`)}` : s.rejected ? `제출 되돌림 — ${e((s.gaps || []).join(" / "))}` : s.action === "submit_judgment" ? "판단 제출" : e(s.action);
  const list = (title, values) => values?.length ? `<h3 style="margin-top:16px">${title}</h3>${values.map(v => `<p class="caption">· ${e(v)}</p>`).join("")}` : "";
  return `${agent2Bar()}<div class="scope-grid"><div><section class="card"><div class="card-heading"><div><h2>항목별 범위 판단</h2><p class="caption">${record().analysisDemo ? "통합 화면의 가상 판정입니다. 실제 근거는 연결되지 않았습니다." : "AI 제안과 PM 판단은 분리합니다. 아래는 Agent 2 API 응답입니다. fixture 또는 LLM 사용 여부는 서버 설정과 결과 정보를 확인하세요."}</p></div><span class="mini-tag">전체 ${rows.length}</span></div><div class="table-wrap"><table class="scope-table"><thead><tr><th>요청 항목</th><th>AI 제안</th><th>근거 수준</th><th>PM 판단</th><th>근거</th></tr></thead><tbody>${rows.map((item, index) => `<tr class="${index === state.selectedItem ? "selected" : ""}"><td><button class="scope-select" data-item="${index}">${e(meta[item.item_id]?.display_no || item.item_id)} ${e(meta[item.item_id]?.text || "")}<small>${e(item.item_id)}</small></button></td><td>${chip(...label(item))}</td><td>${e(A2_LEVEL[item.evidence_level] || "—")}</td><td>${chip("미확정")}</td><td>${item.evidence.length}</td></tr>`).join("")}</tbody></table></div></section><div class="scope-evidence"><section class="card"><h2>고객 요청 · ${e(meta[sel.item_id]?.display_no || sel.item_id)}</h2><p class="caption">원문 위치</p>${(meta[sel.item_id]?.spans || []).map(span => `<div class="quote">${e(span.text)}</div>`).join("") || '<div class="quote">원문 구간 없음</div>'}<div class="notice inline">원문에 없는 요구는 AI가 추가하지 않습니다.</div></section><section class="stack">${sel.evidence.length ? sel.evidence.map(ev => `<article class="card"><h3>${icon("f3e38")} ${docName(ev.doc_id)} <span class="mini-tag">v${e(ev.doc_version)}</span> <span class="mini-tag">${e(ev.section_id)}</span></h3><p class="tiny">${e(ev.doc_id)} · ${ev.page ? `p.${ev.page} · ` : ""}${ev.line_start ?? "—"}~${ev.line_end ?? "—"}행 · ${e(A2_ROLE[ev.role] || ev.role)}</p><div class="quote">${e(ev.quote)}</div>${chip(ev.verified ? "원문 인용 검증됨" : "인용 미검증", ev.verified ? "success" : "warning")}${evSourceView(ev)}</article>`).join("") : '<article class="card"><h3>근거 없음</h3><p class="caption">검증된 근거가 없어 판단을 보류했습니다. 근거 없음은 범위 밖을 뜻하지 않습니다.</p></article>'}</section></div></div><aside class="card"><div class="card-heading"><h2>판단 · ${e(meta[sel.item_id]?.display_no || sel.item_id)}</h2><span class="tiny">${state.selectedItem + 1} / ${rows.length}</span></div><div class="reason-box"><h3>${icon("2b0e7")} AI 제안 · ${e(label(sel)[0])}</h3><p>${e(sel.rationale)}</p>${list("부족한 정보", sel.missing_info)}${list("불확실성", sel.uncertainties)}</div>${questions.length ? `<h3 style="margin-top:18px">확인 질문</h3>${questions.map(q => `<div class="question"><strong>${e(q.text)}</strong><span class="muted">${q.target === "client" ? "고객" : "PM"} 대상 · ${q.blocking ? "답변 전 진행 불가" : "진행 가능(참고)"} · ${e(q.reason)}</span></div>`).join("")}` : ""}${steps.length ? `<details style="margin-top:18px"><summary>에이전트 조사 과정 (${steps.length}단계)</summary>${steps.map((s, i) => `<p class="tiny">${i + 1}. ${step(s)}</p>`).join("")}</details>` : ""}<div class="notice inline"><strong>PM 판단 · 산정 대상 선택</strong>영향 산정 화면에서 이 결과를 선택한 뒤 항목별 PM 판단과 산정 대상을 직접 확인할 수 있습니다. 로컬 테스트용이며 실제 PM 범위 확정 API는 연결되지 않습니다.</div><div class="form-actions">${button("판단 확정", "unused", { disabled: true, title: "실제 PM 범위 확정 API는 이번 범위 밖" })}${button("영향 산정으로", "go-impact", { class: "primary", icon: "ec151" })}</div></aside></div>`;
}
function scopeSample() {
  const apiCase = record().api;
  const items = apiCase ? [["R1", "기능", baseInput.items[0].text, "범위 밖", "범위 밖", "강", "2"]] : sampleItems;
  state.selectedItem = Math.min(state.selectedItem, items.length - 1);
  const selected = items[state.selectedItem];
  const evidence = baseInput.a2_result.assessments[0].evidence;
  return `<div class="baseline">${icon("73e25")}<strong>판단 기준선 ${apiCase ? "BL-2" : "v1.2"}</strong><span class="muted">합성 상위 입력의 근거 · 이 버전 기준으로 검토</span><span class="mini-tag">계약서 v1.0</span><span class="mini-tag">기능명세 v1.0</span>${chip("실제 범위 확정 API 미연결")}</div><div class="scope-grid"><div><section class="card"><div class="card-heading"><div><h2>항목별 범위 판단</h2><p class="caption">AI 제안과 PM 판단은 분리합니다. 아래는 합성 입력 조회입니다.</p></div><span class="mini-tag">전체 ${items.length}</span></div><div class="table-wrap"><table class="scope-table"><thead><tr><th>요청 항목</th><th>AI 제안</th><th>근거 수준</th><th>PM 판단</th><th>근거</th></tr></thead><tbody>${items.map((item, index) => `<tr class="${index === state.selectedItem ? "selected" : ""}"><td><button class="scope-select" data-item="${index}">${item[0]} ${e(item[2])}<small>원문 ${index + 1}</small></button></td><td>${chip(item[3], "ai")}</td><td>${item[5]}</td><td>${chip(item[4])}</td><td>${item[6]}</td></tr>`).join("")}</tbody></table></div></section><div class="scope-evidence"><section class="card"><h2>고객 요청 · ${selected[0]}</h2><p class="caption">원문 위치와 연결된 합성 입력</p><div class="quote">${e(selected[2])}</div><h3>해석된 요구</h3><p class="caption">상품 상세 리뷰 작성과 선택적 사진 첨부</p><div class="notice inline">원문에 없는 요구는 AI가 추가하지 않습니다.</div></section><section class="stack">${evidence.map(item => `<article class="card"><h3>${icon("f3e38")} ${item.role === "basis" ? "기능명세서" : "계약서"} <span class="mini-tag">v${item.doc_version}</span></h3><p class="tiny">${e(item.doc_id)} · p.${item.page} · ${item.line_start}~${item.line_end}행</p><div class="quote">${e(item.quote)}</div>${chip("상위 입력의 검증된 인용", "success")}</article>`).join("")}</section></div></div><aside class="card"><div class="card-heading"><h2>판단 · ${selected[0]}</h2><span class="tiny">${state.selectedItem + 1} / ${items.length}</span></div><div class="reason-box"><h3>${icon("2b0e7")} AI 제안</h3><p>${e(baseInput.a2_result.assessments[0].rationale)}</p><h3 style="margin-top:16px">불확실성</h3><p>합성 근거이며 실제 계약·업무 확정은 별도입니다. 이번 UI는 Agent 1·2를 실행하지 않습니다.</p></div><h3 style="margin-top:18px">PM 판단 · 예시</h3>${["범위 밖", "기존 범위 내", "판단 보류"].map(label => `<div class="radio-option ${label === selected[4] ? "selected" : ""}"><span class="radio-dot"></span><div>${label}<div class="tiny">합성 상위 검토 데이터</div></div></div>`).join("")}<div class="notice inline"><strong>산정 대상 선택</strong>${apiCase ? '이 합성 입력은 PM이 R1을 <code>estimate=true</code>로 선택했습니다.' : "예시 요청은 조회 전용입니다. API 연결 샘플에서 산정을 진행할 수 있습니다."}</div><div class="form-actions">${button("판단 확정", "unused", { disabled: true, title: "실제 PM 범위 확정 API는 이번 범위 밖" })}${button("영향 산정으로", "go-impact", { class: "primary", icon: "ec151" })}</div></aside></div>`;
}
function errorBanner() {
  const status = state.error.status;
  const titles = { 0: "연결을 확인해 주세요", 404: "세션을 찾을 수 없습니다", 409: "현재 결과를 다시 확인해야 합니다", 422: "입력 또는 검토 조건을 확인해 주세요", 502: "모델 처리 또는 응답에 문제가 있습니다", 503: "서버의 산정 데이터를 사용할 수 없습니다" };
  return `<div class="notice error" role="alert"><strong>${titles[status] || "요청을 처리하지 못했습니다"}${state.error.local ? " · 화면 입력 확인" : status ? ` · ${state.mode === "mock" ? "미리보기 " : ""}HTTP ${status}` : ""}</strong>${e(state.error.message)}${status === 409 ? "<p>오래된 결과로 자동 재시도하지 않습니다. 최신 입력으로 다시 산정하거나 현재 상태를 확인하세요.</p>" : ""}</div>`;
}
function stateNotice() {
  if (record().analysisDemo) return '<div class="notice">통합 진행의 가상 결과를 기존 화면에서 조회합니다. 중간 API 실행·승인은 수행하지 않습니다.</div>';
  if (!record().api) return '<div class="notice">이 요청은 조회용 예시입니다. CR-A-001에서 실제 API 연결을 확인할 수 있습니다.</div>';
  if (!state.view) return `<div class="notice"><strong>영향 산정을 실행해 주세요.</strong>선택한 입력과 로컬 PM 검토를 확인한 뒤 Agent 3을 실행합니다.</div>`;
  let text = { WAITING_IMPACT_APPROVAL: "작업·공수·가정과 미산정 일정을 확인한 뒤 PM 산정 승인을 진행할 수 있습니다. 협의안은 아직 없습니다.", WAITING_INPUT: "필수 질문이나 누락된 정보를 확인해야 합니다. 정보가 해결되기 전에는 승인할 수 없습니다.", WAITING_UPSTREAM_REVIEW: "상위 범위 PM 검토가 끝나야 산정할 수 있습니다. 이 화면이 실제 상위 승인을 자동 생성하지 않습니다.", NOT_REQUIRED: "PM이 선택한 산정 대상이 없습니다. 0원 견적이 생성됐다는 의미가 아닙니다.", FAILED: "산정에 실패했습니다. 입력과 실행 환경을 확인하고 다시 실행해 주세요.", DRAFT_READY: "최신 PM 산정 승인으로 고객 전달용 메일 초안이 작성됐습니다. 고객 합의·발송·청구는 수행하지 않았습니다." }[state.view.phase] || "현재 결과 상태를 확인해 주세요.";
  return `<div class="notice"><strong>${e(PHASE[state.view.phase] || state.view.phase)}</strong>${text}</div>${inputChanged() || state.invalidated ? '<div class="notice error" role="status"><strong>현재 입력과 이전 결과가 다르거나 재확인이 필요합니다.</strong>아래 이전 결과는 참고용입니다. 다시 산정한 뒤 수정·승인해 주세요.</div>' : ""}${state.conflicted ? '<div class="notice error"><strong>버전 충돌로 변경 작업이 잠겼습니다.</strong>최신 입력으로 다시 산정해야 합니다. 조회만으로 충돌을 자동 해제하지 않습니다.</div>' : ""}${state.mode === "mock" ? '<div class="notice"><strong>읽기 전용 상태 미리보기</strong>실제 API를 호출하지 않았습니다. 수정·승인은 API 모드에서만 동작합니다.</div>' : ""}`;
}
function labControls() {
  if (record().analysisDemo) return agentSettings('a3',a1);
  if (state.mode === "mock") return `<div class="notice">읽기 전용 상태 미리보기에서는 모델을 실행하지 않습니다. 실행하려면 상단에서 기존 Agent 3 API를 선택하세요.</div>`;
  if (!lab.enabled) return `<div class="notice">모델 선택과 가상 응답 편집을 사용하려면 서버를 <code>python prototype/run_demo.py --lab</code>로 실행하세요.</div>`;
  const field = (id, label, value) => `<label class="field">${label}<input id="${id}" value="${e(value)}" ${state.pending ? "disabled" : ""}></label>`;
  const modelControl = lab.provider === "ollama"
    ? `<label class="field">Ollama 로컬 모델<select id="lab-model" ${state.pending ? "disabled" : ""}>${[...new Set([...ollamaModels, lab.model].filter(Boolean))].map(model => `<option value="${e(model)}" ${model === lab.model ? "selected" : ""}>${e(model)}</option>`).join("")}</select></label>`
    : lab.provider !== "fake" ? field("lab-model", "모델 ID", lab.model) : "";
  return `<section class="card"><h2>1. 테스트 실행 설정</h2><p class="caption">가상 응답은 비용 없이 확인합니다. Gemini·GPT·Bedrock 실행은 외부 모델을 호출하고, Ollama는 로컬 모델을 호출합니다. 키는 서버의 .env에서 읽습니다. 응답의 MD 값은 사용하지 않고 테스트 기준표로 계산합니다.</p><div class="settings-grid"><fieldset class="field"><legend>모델 공급자</legend><div class="button-group">${[["fake","가상 응답 (fake)"],["gemini","Gemini API"],["openai","GPT API"],["bedrock","AWS Bedrock"],["ollama","Ollama (Local)"]].map(([v,l]) => `<label class="check"><input type="radio" name="lab-provider" value="${v}" ${lab.provider === v ? "checked" : ""} ${state.pending ? "disabled" : ""}>${l}</label>`).join("")}</div></fieldset>${modelControl}${lab.provider === "bedrock" ? field("lab-region", "AWS 리전", lab.region) : ""}</div>${lab.provider === "fake" ? `<label class="field">가상 모델 응답 JSON<textarea id="lab-fake" class="json-input" spellcheck="false">${e(lab.fakeText)}</textarea></label>` : ""}<p class="tiny">2. 아래 테스트 입력을 확인한 뒤 오른쪽 위 ‘영향 산정 실행’을 누르세요. 3. 결과를 승인한 뒤 승인·협의 탭에서 협의안 모델을 선택하고 생성하세요.</p></section>`;
}
function switchInputSource(source) {
  if (!["sample", "agent2"].includes(source) || state.pending) return;
  if (state.inputSource === "sample") state.sampleInputText = state.inputText;
  state.inputSource = source;
  state.inputText = source === "sample" ? (state.sampleInputText || JSON.stringify(baseInput, null, 2)) : (a2.review ? JSON.stringify(a2.review, null, 2) : "");
  state.error = null;
  if (state.view) state.invalidated = true;
  persist(); render();
}
function liveReviewInput() {
  if (!a2.result || !a2.sourceInput || a2.pending) throw new Error("범위 검토에서 Agent 2를 먼저 실행해 주세요.");
  const out = a2.result, source = a2.sourceInput;
  if (out.context?.case_id !== state.id || JSON.stringify(out.context) !== JSON.stringify(source.context) || !out.meta?.result_id) throw new Error("현재 요청과 일치하는 Agent 2 결과가 필요합니다.");
  if (out.status !== "READY_FOR_REVIEW" || (out.questions || []).some(q => q.blocking && q.status === "open")) throw new Error("Agent 2의 필수 확인 사항을 먼저 해결해 주세요.");
  const ids = source.items.map(item => item.item_id), assessments = out.assessments || [];
  if (new Set(ids).size !== ids.length || assessments.length !== ids.length || new Set(assessments.map(a => a.item_id)).size !== ids.length || assessments.some(a => !ids.includes(a.item_id))) throw new Error("요청 항목과 Agent 2 판단 항목이 일치하지 않습니다.");
  const choices = assessments.map(a => {
    const choice = a2.choices[a.item_id] || {};
    if (!["in_scope", "out_of_scope", "unclear", "approved_change"].includes(choice.label)) throw new Error("모든 항목의 PM 판단을 선택해 주세요.");
    if (choice.estimate && choice.label !== "out_of_scope") throw new Error("범위 밖으로 판단한 항목만 산정할 수 있습니다.");
    if (choice.label !== a.label && !choice.reason?.trim()) throw new Error("AI 제안과 다른 판단에는 사유를 입력해 주세요.");
    return { item_id: a.item_id, ai_label: a.label, pm_label: choice.label, estimate: !!choice.estimate, reason: choice.reason?.trim() || "", evidence_refs: (a.evidence || []).filter(ev => ev.verified === true).map(ev => `${ev.doc_id}:${ev.section_id}`) };
  });
  return { ...structuredClone(baseInput), context: structuredClone(source.context), received_at: baseInput.received_at,
    items: structuredClone(source.items), constraints: structuredClone(source.constraints || []), answers: structuredClone(source.answers || []),
    a2_result: structuredClone(out), pm_review: { review_id: `local-pm-${crypto.randomUUID()}`, review_revision: 1,
      target_result_id: out.meta.result_id, reviewer: "로컬 테스트 PM", reviewed_at: new Date().toISOString(), status: "REVIEWED", items: choices } };
}
function confirmLiveReview() {
  try { a2.review = liveReviewInput(); state.inputText = JSON.stringify(a2.review, null, 2); state.error = null;
    if (state.view) state.invalidated = true; persist(); render(); toast("로컬 PM 검토를 적용했습니다. 영향 산정을 실행해 주세요.");
  } catch (error) { state.error = { status: 422, local: true, message: error.message }; render(); }
}
function invalidateLiveReview() {
  a2.review = null;
  if (state.inputSource === "agent2") { state.inputText = ""; if (state.view) state.invalidated = true; persist(); }
}
function inputSummary(input) {
  if (!input) return `<div class="input-preview-empty"><strong>아직 표시할 입력이 없습니다</strong><p>위에서 Agent 2 결과를 검토하고 PM 판단을 적용해 주세요.</p></div>`;
  const reviews = Object.fromEntries((input.pm_review?.items || []).map(r => [r.item_id, r]));
  const assessments = Object.fromEntries((input.a2_result?.assessments || []).map(a => [a.item_id, a]));
  const pmLabels = { out_of_scope: "범위 밖", in_scope: "기존 범위 내", unclear: "판단 보류", approved_change: "범위 내 (변경합의서 승인분)" };
  const reviewStatus = { REVIEWED: "검토 완료", PENDING: "검토 대기" }[input.pm_review?.status] || "미확인";
  const items = (input.items || []).map(item => {
    const a = assessments[item.item_id], r = reviews[item.item_id];
    return `<article class="input-preview-item"><div class="input-preview-item-title"><span class="input-preview-number">${e(item.display_no || "요청")}</span><strong>${e(item.text)}</strong></div><div class="input-preview-tags"><span>Agent 2 <b>${e(A2_LABEL[a?.label]?.[0] || a?.label || "미확인")}</b></span><span>PM 판단 <b>${e(pmLabels[r?.pm_label] || "미검토")}</b></span><span class="${r?.estimate ? "selected" : ""}">산정 대상: <b>${r?.estimate ? "선택" : "미선택"}</b></span></div></article>`;
  }).join("") || '<p class="input-preview-muted">등록된 요청 항목이 없습니다.</p>';
  const conditions = (input.constraints || []).map(item => `<li>${e(item.text)}</li>`).join("");
  return `<div class="input-preview-grid"><section class="input-preview-panel"><div class="input-preview-label">요청 항목 및 범위 판단 <span>${(input.items || []).length}건</span></div>${items}</section><section class="input-preview-panel"><div class="input-preview-label">일정 · 조건</div>${conditions ? `<ul class="input-preview-conditions">${conditions}</ul>` : '<p class="input-preview-muted">등록된 일정이나 조건이 없습니다.</p>'}<dl class="input-preview-meta"><div><dt>적용 단가표</dt><dd>${rateCardButton(input.rate_card_id)}</dd></div><div><dt>PM 검토</dt><dd>${e(reviewStatus)}</dd></div></dl></section></div>`;
}
function settings() {
  if (record().analysisDemo) return `<section class="card"><h2>영향 산정 입력 · Agent 2 결과</h2><p class="caption">고정 가상 산정입니다. PM 검토·확인 기록은 통합 진행 화면에 보존됩니다.</p><p>${e(analysisDemo.byId(state.id)?.answer||'PM 답변 미입력')}</p></section>`;
  const scenarios = [["normal", "선택한 요청의 기본 입력"], ["missing-rate", "QA 단가 누락"], ["upstream", "범위 PM 검토 전"], ["no-target", "산정 대상 없음"]];
  if (state.mode === "mock") scenarios.push(["failed", "산정 실패 상태"], ...[0, 404, 409, 422, 502, 503].map(code => [`error-${code}`, code ? `HTTP ${code} 오류` : "연결 실패"]));
  let current = null; try { current = parsedInput(); } catch { /* Show review guidance instead. */ }
  const reviewRows = a2.result && a2.sourceInput ? (a2.result.assessments || []).map(a => {
    const item = a2.sourceInput.items.find(i => i.item_id === a.item_id), c = a2.choices[a.item_id] || {};
    const choices = [["", "판단 선택"], ["out_of_scope", "범위 밖"], ["in_scope", "기존 범위 내"], ["unclear", "판단 보류"], ["approved_change", "범위 내 (변경합의서 승인분)"]];
    return `<div class="question"><strong>${e(item?.display_no || a.item_id)} · ${e(item?.text || "")}</strong><span>Agent 2 제안: ${e(A2_LABEL[a.label]?.[0] || a.label)}</span><label class="field">PM 판단<select data-pm-label="${e(a.item_id)}" ${state.pending ? "disabled" : ""}>${choices.map(([v,l]) => `<option value="${v}" ${c.label === v ? "selected" : ""}>${l}</option>`).join("")}</select></label><label class="check"><input type="checkbox" data-pm-estimate="${e(a.item_id)}" ${c.estimate ? "checked" : ""} ${c.label !== "out_of_scope" || state.pending ? "disabled" : ""}>이 항목 산정</label>${c.label && c.label !== a.label ? `<label class="field">AI 제안과 다른 판단의 사유<input data-pm-reason="${e(a.item_id)}" value="${e(c.reason || "")}"></label>` : ""}</div>`;
  }).join("") : '<p class="caption">범위 검토 탭에서 Agent 2를 실행해 주세요.</p>';
  return `<section class="card"><h2>2. 영향 산정 입력 선택</h2><fieldset class="field"><legend>입력 출처</legend><div class="button-group">${[["sample", "기존 샘플 입력"], ["agent2", "Agent 2 실행 결과"]].map(([v,l]) => `<label class="check"><input type="radio" name="input-source" value="${v}" ${state.inputSource === v ? "checked" : ""} ${state.pending ? "disabled" : ""}>${l}</label>`).join("")}</div></fieldset>${state.inputSource === "agent2" ? `<p class="caption">현재 요청의 Agent 2 결과를 PM이 직접 검토합니다. 선택과 기록은 이 브라우저의 로컬 테스트용입니다.</p>${a2.result ? `<p class="tiny">결과 ${e(a2.result.meta?.result_id || "없음")} · ${e(a2.result.meta?.model || a2.result.meta?.agent_version || "모델 정보 없음")} · ${e(a2.result.status)}</p>` : ""}${reviewRows}${button("PM 판단 적용", "confirm-live-review", { class: "small", disabled: !a2.result || state.pending })}${a2.review ? '<p class="tiny">현재 Agent 2 결과에 대한 로컬 PM 판단이 적용되었습니다.</p>' : '<p class="tiny">모든 항목의 판단을 선택하고 적용해 주세요. 범위 밖만 산정 대상으로 선택할 수 있습니다.</p>'}` : `<label class="field">샘플 시나리오<select id="scenario" ${state.pending ? "disabled" : ""}>${scenarios.map(([v,l]) => `<option value="${v}" ${state.scenario === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>`}<div class="input-preview-heading"><div><h3>산정 입력 미리보기</h3><p>선택한 입력의 주요 내용을 확인하세요.</p></div></div>${inputSummary(current)}<details class="input-settings input-preview-json"><summary>입력 JSON · 고급 확인</summary><label class="field">Agent 3 입력 JSON<textarea id="input-json" class="json-input" spellcheck="false" ${state.pending || !record().api || state.inputSource === "agent2" ? "readonly" : ""}>${e(state.inputText)}</textarea></label><div class="form-actions">${state.inputSource === "sample" ? button("기본 입력 복원", "reset-input", { disabled: state.pending }) : ""}${button("로컬 세션 지우기", "clear-session", { disabled: state.pending })}</div></details></section>`;
}
async function showRateCard(id) {
  const dialog = document.querySelector("#rate-card-dialog");
  const content = document.querySelector("#rate-card-content");
  document.querySelector("#rate-card-title").textContent = "단가표 · " + id;
  content.innerHTML = '<p class="rate-card-loading">단가표를 불러오는 중입니다…</p>';
  dialog.showModal();
  try {
    const response = await fetch(`/prototype-data/rate-cards/${encodeURIComponent(id)}`, { cache: "no-store" });
    const card = await response.json();
    if (!response.ok) throw new Error(typeof card?.detail === "string" ? card.detail : "단가표를 불러오지 못했습니다.");
    const current = state.view?.impact?.estimates?.some(row => row.rate_card_id === id) && state.view?.rate_card_fingerprint;
    const status = current ? (current === card.fingerprint ? '<div class="rate-card-status">현재 산정 결과와 동일한 기준입니다.</div>' : '<div class="notice error rate-card-status">단가표 또는 공수 기준이 산정 이후 변경되었습니다. 표시된 표는 현재 기준이며 산정을 다시 실행해 주세요.</div>') : '<div class="rate-card-status">현재 서버에 설정된 단가표입니다.</div>';
    const kind = card.synthetic === true ? '테스트용 합성 단가표입니다. 실제 계약이나 견적에 사용할 수 없습니다.' : '현재 서버에 설정된 단가표입니다. 사용 전 적용 기준을 확인해 주세요.';
    const rateMoney = value => typeof value === "number" && Number.isFinite(value) ? `${value.toLocaleString("ko-KR", { maximumFractionDigits: 20 })}원` : "미산정";
    const roles = [["PM", "프로젝트 관리"], ["UX", "UX / 디자인"], ["FE", "프론트엔드"], ["BE", "백엔드"], ["QA", "QA / 테스트"]];
    const rows = roles.map(([role, label]) => {
      const rate = card.rates?.[role];
      const field = key => lab.enabled ? `<input class="rate-edit-input" type="number" min="0" step="any" inputmode="decimal" data-rate-role="${role}" data-rate-field="${key}" value="${e(rate?.[key] ?? "")}" aria-label="${role} ${key === "cost" ? "원가" : "판매 단가"} 원/MD" placeholder="미설정">` : rateMoney(rate?.[key]);
      return `<tr><th scope="row">${e(label)} <span>${e(role)}</span></th><td>${field("cost")}</td><td>${field("price")}</td></tr>`;
    }).join("");
    content.innerHTML = `<p class="rate-card-description">${e(kind)}</p>${status}<div class="rate-card-meta"><span>단위: 원 / MD</span><span>부가세 별도</span><span>원가 · 내부 확인용</span></div><div class="table-wrap"><table class="rate-card-table"><thead><tr><th scope="col">직군</th><th scope="col">원가 / MD</th><th scope="col">판매 단가 / MD</th></tr></thead><tbody>${rows}</tbody></table></div><p class="rate-card-note">빈 칸은 미설정입니다. 공수는 별도의 작업별 기준으로 산정합니다.</p>${lab.enabled ? `<label class="field">단가 수정 사유<input id="rate-edit-reason" maxlength="500" placeholder="예: 계약 단가 검토"></label><div id="rate-edit-message" role="status" aria-live="polite"></div><div class="form-actions"><button type="button" class="button primary" id="save-rate-version">새 버전 저장 및 적용</button></div><p class="rate-card-note">원본 표는 보존되고 수정한 값은 이 서버의 로컬 단가표로 저장됩니다. 현재 산정이 있으면 저장 즉시 작업과 공수를 유지한 채 금액을 다시 계산하고 기존 승인을 해제합니다.</p>` : '<p class="rate-card-note">단가를 수정하려면 --lab 모드로 실행해 주세요.</p>'}`;
    if (lab.enabled) document.querySelector("#save-rate-version").addEventListener("click", () => saveRateVersion(card));
  } catch (error) {
    content.innerHTML = `<div class="notice error rate-card-error" role="alert"><strong>단가표를 확인할 수 없습니다.</strong>${e(error.message || "연결 상태를 확인해 주세요.")}</div>`;
  }
}
async function saveRateVersion(card) {
  const message = document.querySelector("#rate-edit-message"), save = document.querySelector("#save-rate-version");
  const showError = text => { message.innerHTML = `<div class="notice error rate-card-error" role="alert">${e(text)}</div>`; };
  let input;
  try { input = parsedInput(); } catch { return showError("먼저 유효한 산정 입력을 준비해 주세요."); }
  if (state.pending || state.mode !== "api" || input.rate_card_id !== card.id) return showError("현재 산정 입력에 선택된 단가표에서만 수정할 수 있습니다.");
  if (state.inputSource === "agent2" && !a2.review) return showError("먼저 Agent 2 결과의 PM 판단을 적용해 주세요.");
  if (state.view && editsChanged()) return showError("작업·공수의 미저장 수정 사항을 먼저 반영하거나 취소해 주세요.");
  const reason = document.querySelector("#rate-edit-reason").value.trim();
  if (!reason) return showError("단가 수정 사유를 입력해 주세요.");
  const rates = {};
  for (const role of ["PM", "UX", "FE", "BE", "QA"]) {
    rates[role] = {};
    for (const field of ["cost", "price"]) {
      const el = document.querySelector(`[data-rate-role="${role}"][data-rate-field="${field}"]`);
      const value = el.value.trim();
      if (value && (!el.validity.valid || !Number.isFinite(Number(value)) || Number(value) < 0)) return showError(`${role} 단가는 0 이상의 숫자로 입력해 주세요.`);
      rates[role][field] = value ? Number(value) : null;
    }
  }
  const before = state.inputText, activeId = state.id;
  const canReprice = !!state.view && !state.invalidated && !state.conflicted && !inputChanged() && state.edits?.length > 0;
  save.disabled = true; message.textContent = "단가표를 저장하는 중입니다…";
  try {
    const response = await fetch(`/prototype-data/rate-cards/${encodeURIComponent(card.id)}/versions`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_fingerprint: card.fingerprint, reason, rates }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result?.detail === "string" ? result.detail : "단가표를 저장하지 못했습니다.");
    if (state.id !== activeId || state.inputText !== before) {
      showError(`새 단가표 ${result.id}를 저장했지만 현재 입력이 바뀌어 자동 적용하지 않았습니다.`); return;
    }
    input.rate_card_id = result.id;
    state.inputText = JSON.stringify(input, null, 2);
    if (state.inputSource === "sample") state.sampleInputText = state.inputText;
    if (state.inputSource === "agent2" && a2.review) a2.review.rate_card_id = result.id;
    let detail = "영향 산정을 실행하면 새 단가로 계산합니다.";
    if (state.view) state.invalidated = true;
    if (canReprice) {
      try {
        const view = await api.edit(state.view.session_id, {
          state_revision: state.view.state_revision,
          target_result_id: state.view.impact.meta.result_id,
          estimates: state.edits, reason: `단가표 수정: ${reason}`, rate_card_id: result.id,
        });
        const originalLines = state.originalLines;
        acceptView(view, input);
        state.originalLines = originalLines;
        detail = "기존 작업과 공수는 유지하고 새 단가로 다시 계산했습니다. 기존 승인과 초안은 해제되어 다시 승인해야 합니다.";
      } catch (error) {
        if (error.status === 409) state.conflicted = true;
        detail = `단가표는 저장했지만 기존 결과 재계산에 실패했습니다: ${error.message || "연결 오류"}. 영향 산정을 다시 실행해 주세요.`;
      }
    }
    persist(); render();
    document.querySelector("#rate-card-content").innerHTML = `<div class="rate-save-success"><strong>새 단가표를 저장하고 입력에 적용했습니다.</strong><p>${e(result.id)}</p><p>${e(detail)}</p><button type="button" class="button" id="rate-save-done">확인</button></div>`;
    document.querySelector("#rate-save-done").addEventListener("click", () => document.querySelector("#rate-card-dialog").close());
  } catch (error) { showError(error.message || "단가표 저장에 실패했습니다."); save.disabled = false; }
}

function submittedConditions() {
  const current = record().api && state.view;
  const input = current ? state.submittedInput : null;
  const title = state.mode === "mock" ? "미리보기 예시 조건" : "산정에 제출한 조건";
  const content = input ? (input.constraints?.length ? input.constraints.map(item => `<p data-submitted-condition>${e(item.text)}</p>`).join("") : "<p>제출된 조건 없음</p>") : `<p>${current ? "이 세션의 제출 조건 기록이 없습니다. 최신 입력으로 다시 산정해 주세요." : "산정 실행 후 제출한 조건을 표시합니다."}</p>`;
  return `<div class="schedule-box" id="submitted-conditions"><h3>${title}</h3>${content}<p class="tiny">${state.mode === "mock" ? "저장된 합성 상태의 예시이며 실제 산정 실행이 아닙니다." : "이 결과를 받은 요청의 조건입니다. 미제출 편집과 실제 프로젝트 일정 확정 정보는 구분합니다."}</p></div>`;
}
function metric(label, value, detail, word = false) { return `<article class="metric"><div class="metric-label">${label}</div><div class="metric-value ${word ? "word" : ""}">${value}</div><div class="metric-detail">${detail}</div></article>`; }
function impact() {
  const totals = currentTotals(), list = estimates();
  const margin = totals && totals.price && typeof totals.cost==='number' ? ((totals.price - totals.cost) / totals.price) * 100 : null;
  const rate = list[0]?.rate_card_id || "응답 전";
  const current = record().api ? state.view : null;
  const original = state.originalLines;
  const writable = canEdit();
  const rows = list.map((item, i) => {
    const lines = state.edits?.[i]?.lines || item.lines;
    return lines.map((line, j) => {
      const initial = original.find(old => old.task === line.task && old.role === line.role && JSON.stringify(old.item_ids) === JSON.stringify(line.item_ids));
      const roles = ["PM", "UX", "FE", "BE", "QA"];
      return `<tr><td><input class="task-edit-input" type="text" value="${e(line.task)}" aria-label="작업 내용" data-edit-task="${i}:${j}" ${writable ? "" : "disabled"}><small>${e(line.item_ids.join(" · "))}</small></td><td><select class="role-edit-select" aria-label="직군" data-edit-role="${i}:${j}" ${writable ? "" : "disabled"}>${roles.map(role => `<option value="${role}" ${role === line.role ? "selected" : ""}>${role}</option>`).join("")}</select></td><td class="numeric">${initial ? number(initial.md) : "—"}</td><td><input class="md-input" id="md-${i}-${j}" type="number" min="0" step="0.5" value="${e(line.md)}" aria-label="${e(line.role)} 공수 MD" data-edit-md="${i}:${j}" ${writable ? "" : "disabled"}></td><td class="tiny">API 미제공</td><td class="tiny">API 미제공 ${writable ? `<button type="button" class="task-remove" data-remove-line="${i}:${j}" aria-label="${e(line.task || "새 작업")} 삭제">삭제</button>` : ""}</td></tr><tr class="assumption-row"><td colspan="6"><label class="tiny">가정 <input class="assumption-input" id="assumption-${i}-${j}" data-edit-assumption="${i}:${j}" value="${e(line.assumption)}" aria-label="${e(line.role)} 가정" ${writable ? "" : "disabled"}></label></td></tr>`;
    }).join("") + (writable ? `<tr><td colspan="6"><button type="button" class="button small" data-add-line="${i}">+ 작업 추가</button></td></tr>` : "");
  }).join("");
  return `${record().api ? "" : '<div class="notice">이 예시는 API 산정 대상이 아닙니다. CR-A-001을 열어 기존 API 흐름을 확인하세요.</div>'}${labControls()}${settings()}${state.pending ? '<p class="loading-line" role="status"><span class="inline-spinner"></span>기존 Agent 3 API 응답을 기다리는 중입니다…</p>' : ""}${stateNotice()}<div class="version-row"><span>산정 버전</span><span class="version-pill">선택된 입력: ${record().analysisDemo ? "Agent 2 · 통합 화면 시연" : state.inputSource === "agent2" ? "Agent 2 실행 결과 · 로컬 PM 검토" : "샘플"}</span><span class="version-pill current">${current ? `현재 v${current.state_revision}` : "실행 전"}</span><span style="margin-left:auto">단가표 ${rate === "응답 전" ? "응답 전" : rateCardButton(rate)} · VAT 별도</span>${button("현재 상태 확인", "refresh", { disabled: record().analysisDemo || !current || state.pending || state.mode !== "api", icon: "ec991" })}</div><div class="metric-grid">${metric("총 공수", totals ? `${number(totals.md)}<small>MD</small>` : "미산정", record().analysisDemo ? "고정 화면 시연값" : "응답의 코드 계산 결과", !totals)}${metric("예상 직접원가 · 내부", totals ? money(totals.cost) : "미산정", "합성 단가 기준 · 행별 단가 미제공", !totals)}${metric("고객 제안가 · 판매", totals ? money(totals.price) : "미산정", "실제 고객 합의 금액이 아닙니다", !totals)}${metric("예상 마진", margin === null ? "미산정" : `${number(margin)}<small>%</small>`, "API 총 원가·판매가의 표시용 비율", margin === null)}${metric("일정 영향", current && list.length && !unknownSchedule() ? "항목별 확인" : "미산정", "MD를 달력 일수로 환산하지 않습니다", true)}</div><div class="columns impact"><div class="stack"><section class="card"><div class="card-heading"><div><h2>작업 분해 · 공수</h2><p class="caption">작업 내용·직군·공수·가정을 수정하면 금액이 재계산되고 재승인이 필요합니다.</p></div>${chip(record().analysisDemo ? "가상 결과" : state.mode === "mock" ? "가상 상태" : "API 응답")}</div><div class="table-wrap"><table class="impact-table"><thead><tr><th>작업 · 연결 항목</th><th>직군</th><th>최초 MD</th><th>수정 MD</th><th>직군 단가</th><th>행별 금액</th></tr></thead><tbody>${rows || '<tr><td colspan="6"><div class="empty-state"><h3>표시할 산정 작업이 없습니다</h3><p>산정 실행 결과와 필요한 입력을 확인해 주세요.</p></div></td></tr>'}${totals ? `<tr class="total-row"><td>합계</td><td></td><td></td><td class="numeric">${number(totals.md)} MD</td><td colspan="2" class="numeric">${money(totals.price)}</td></tr>` : ""}</tbody></table></div><label class="field">수정 사유<input id="edit-reason" placeholder="공수·가정을 수정한 이유를 입력하세요" value="${e(state.editReason)}" ${writable ? "" : "disabled"}></label><p id="edit-notice" class="tiny" style="margin-top:8px">${editsChanged() ? "아직 저장하지 않은 수정이 있습니다. 수정 반영 후 승인해 주세요." : "단가는 단가표 이름을 눌러 수정할 수 있습니다. 일정은 직접 편집할 수 없습니다."}</p><div class="form-actions">${button("수정 반영·재계산", "edit", { disabled: !writable || !editsChanged(), icon: "ec991" })}${button("편집 취소", "cancel-edit", { disabled: !editsChanged() || state.pending })}</div><div class="notice inline">${icon("2b0e7")}AI가 작업과 테스트 기준을 제안하고 서버가 기준 공수와 단가로 계산합니다. 사례별 합성 기준이며 실제 견적에 사용할 수 없습니다. PM은 공수를 수동 수정할 수 있습니다.</div></section><section class="card"><div class="card-heading"><div><h2>일정 영향</h2><p class="caption">선행 작업·인력 정보가 없으면 임의 일수 대신 미산정으로 표시합니다.</p></div>${chip("확정 아님")}</div><div class="schedule-grid">${submittedConditions()}<div class="schedule-box"><h3>산정 결과의 근거</h3>${list.length ? list.map(item => `<p>${item.schedule.business_days == null ? "미산정" : `${number(item.schedule.business_days)} 영업일`}</p><p class="tiny">${e(item.schedule.reason || "항목별 근거 확인")}</p>`).join("") : '<p>산정 결과가 아직 없습니다.</p>'}</div></div>${(current?.impact.questions || []).map(question => `<div class="question"><strong>${question.blocking ? "필수 확인" : "확인 사항"} · ${e(question.text)}</strong><span class="muted">${e(question.reason || "필요한 정보 확인 후 다시 산정해 주세요.")}</span></div>`).join("")}</section></div><div class="stack"><section class="card"><div class="card-heading"><div><h2>PM 산정 검토</h2><p class="caption">상업 승인·고객 합의와 별개인 로컬 검토입니다.</p></div></div><div class="radio-option selected"><span class="radio-dot"></span><div>산정 결과 확인<div class="tiny">현재 결과와 일정 미산정의 한계를 확인합니다.</div></div></div><label class="field">검토자 · 로컬 테스트용<input id="reviewer" value="${e(state.reviewer)}" ${state.pending ? "disabled" : ""}></label><label class="field">검토 메모 / 반려 사유<textarea id="review-reason" placeholder="반려할 때는 사유가 필요합니다" ${state.pending ? "disabled" : ""}>${e(state.reviewReason)}</textarea></label>${unknownSchedule() ? `<label class="check"><input id="ack-schedule" type="checkbox" ${state.ack ? "checked" : ""} ${state.pending ? "disabled" : ""}><span>일정 영향이 미산정이며 추가 확인이 필요함을 확인했습니다.</span></label>` : ""}<div class="form-actions">${button("PM 산정 승인", "approve", { class: "primary", disabled: !canApprove() || (unknownSchedule() && !state.ack), icon: "6ce49" })}${button("반려", "reject", { disabled: !canApprove() })}</div><p class="tiny" style="margin-top:12px">수정되면 기존 승인·초안은 이력으로 남고 현재 결과는 재승인이 필요합니다.</p>${["APPROVED", "DRAFT_READY"].includes(current?.phase) ? `<div class="notice inline"><strong>PM 산정 승인 완료</strong>${button("협의안 생성 화면", "go-draft", { class: "small", icon: "ec151" })}</div>` : ""}</section><section class="card"><h2>산정 이력</h2><p class="caption">현재 API 세션의 실제 변경 이력</p>${history()}${current ? `<details style="margin-top:16px"><summary class="caption">결과 JSON · 버전</summary><pre class="raw-json">${e(JSON.stringify(current, null, 2))}</pre></details>` : '<p class="caption" style="margin-top:14px">산정 후 표시됩니다.</p>'}</section></div></div>`;
}
function history() {
  const list = record().api ? state.view?.history || [] : [];
  return list.length ? list.map((item, i) => `<details class="history-row"><summary>이전 결과 ${i + 1} · ${e(item.reason || "검토 변경")}</summary><p>${item.impact?.estimates?.map(row => `${row.totals ? number(row.totals.md) + " MD · " + money(row.totals.price) : "미산정"}`).join(" / ") || "승인 이력"}</p><p>이전 승인: ${item.approval?.approved ? "있음" : "없음"} · 이전 초안: ${item.draft ? "있음" : "없음"}</p></details>`).join("") : '<p class="caption" style="margin-top:14px">변경 이력이 없습니다.</p>';
}
function hasCurrentDraft() { if (record().analysisDemo) return !!state.view?.draft; return state.mode === "api" && record().api && !state.pending && !!state.view?.draft && state.view.phase === "DRAFT_READY" && !inputChanged() && !state.invalidated && !state.conflicted && !editsChanged(); }
function draftMarkdown() {
  const d = state.view?.draft;
  if (!d?.email_body) return "";
  return `제목: ${d.email_subject}\n\n${d.email_body}`;
}
function draft() {
  const view = record().api ? state.view : null;
  const valid = hasCurrentDraft();
  const d = valid ? view.draft : null;
  return `${stateNotice()}<div class="draft-versions"><div class="draft-version"><span>산정 결과</span><strong>${view ? `v${view.state_revision} · ${PHASE[view.phase] || view.phase}` : "아직 없음"}</strong><span class="tiny">${record().analysisDemo ? "통합 화면 테스트 기록" : "현재 API 세션 기준"}</span></div><div class="draft-version ${valid ? "current" : ""}"><span>고객 전달용 메일 초안</span><strong>${record().analysisDemo ? "가상 메일 초안" : valid ? "승인 결과로 작성됨" : "PM 산정 승인 후 생성"}</strong><span class="tiny">사용자 개발 착수 승인과 별개</span></div><div class="draft-version"><span>고객 협의·상업 승인</span><strong>이번 구현 범위 밖</strong><span class="tiny">고객 전달·합의·청구 미수행</span></div></div><div class="columns impact"><div class="stack"><section class="card"><div class="card-heading"><div><h2>고객 전달용 협의 메일 초안</h2><p class="caption">고객에게 보낼 메일 초안입니다. PM이 내용을 확인한 후 직접 전달해 주세요. ${record().analysisDemo ? "공수와 금액은 고정 화면 시연값입니다." : "공수와 금액은 승인된 서버 결과입니다."}</p></div><div class="button-group">${valid ? button("협의안 다시 생성", "generate-draft", { disabled: !safeCurrent(), class: "small" }) : ""}${button("복사", "copy-draft", { disabled: !valid, icon: "4ea67" })}${button("메일 초안 저장", "download-draft", { disabled: !valid, icon: "986b4" })}</div></div>${lab.enabled && !record().analysisDemo ? `<div class="settings-grid"><label class="field">협의안 생성 공급자<select id="draft-provider" ${state.pending ? "disabled" : ""}><option value="gemini" ${draftLab.provider === "gemini" ? "selected" : ""}>Gemini API</option><option value="openai" ${draftLab.provider === "openai" ? "selected" : ""}>GPT API</option><option value="fake" ${draftLab.provider === "fake" ? "selected" : ""}>가상 응답</option><option value="bedrock" ${draftLab.provider === "bedrock" ? "selected" : ""}>AWS Bedrock</option><option value="ollama" ${draftLab.provider === "ollama" ? "selected" : ""}>Ollama (Local)</option></select></label>${draftLab.provider === "ollama" ? `<label class="field">Ollama 로컬 모델<select id="draft-model" ${state.pending ? "disabled" : ""}>${[...new Set([...ollamaModels, draftLab.model].filter(Boolean))].map(model => `<option value="${e(model)}" ${model === draftLab.model ? "selected" : ""}>${e(model)}</option>`).join("")}</select></label>` : draftLab.provider !== "fake" ? `<label class="field">모델 ID<input id="draft-model" value="${e(draftLab.model)}" ${state.pending ? "disabled" : ""}></label>` : ""}${draftLab.provider === "bedrock" ? `<label class="field">AWS 리전<input id="draft-region" value="${e(draftLab.region)}"></label>` : ""}</div><p class="tiny">영향 산정 모델과 별도로 선택합니다. Gemini와 GPT는 각각 서버에 저장된 키를 사용합니다. Ollama는 로컬 모델을 사용합니다.</p>` : ""}${d ? `<div class="draft-shell"><article class="draft-paper"><h2>제목: ${e(d.email_subject || "변경 요청 협의 안내")}</h2><p class="tiny">작성 모델: ${e(view.draft_model || "확인 불가")} · PM 검토 후 고객 전달</p><section class="draft-section"><p style="white-space: pre-wrap">${e(d.email_body || "메일 초안을 다시 생성해 주세요.")}</p></section></article></div>` : `<div class="empty-state"><h3>아직 유효한 협의안 초안이 없습니다.</h3><p>${view?.phase === "APPROVED" ? "PM 승인 완료. 선택한 모델로 협의안을 생성하세요." : "영향 산정에서 최신 결과를 확인·승인해 주세요."}</p><div style="margin-top:16px">${view?.phase === "APPROVED" ? button(state.pending ? "LLM 협의안 생성 중…" : "LLM 협의안 생성", "generate-draft", { class: "primary", disabled: !safeCurrent() || editsChanged() }) : button("영향 산정으로", "go-impact", { icon: "ec151" })}</div></div>`}<div class="notice inline">${icon("d5e00")}시스템은 고객에게 자동 발송하지 않습니다. 계약 체결·고객 합의·청구는 수행하지 않습니다.</div></section></div><div class="stack"><section class="card"><div class="card-heading"><h2>${record().analysisDemo ? "통합 최종 확인" : "PM 산정 승인"}</h2>${chip(record().analysisDemo ? view?.demo_review ? "화면 테스트 확인됨" : "미확인" : view?.approval?.approved ? "승인" : "미승인")}</div><div class="metadata-box"><dl><dt>검토자</dt><dd>${e(view?.approval?.reviewer_ref || "—")}</dd><dt>검토 시각</dt><dd>${e(view?.approval?.reviewed_at || "—")}</dd><dt>대상 결과</dt><dd>${e(view?.approval?.target_result_id || "—")}</dd><dt>검토 사유</dt><dd>${e(view?.approval?.reason || "—")}</dd></dl></div><p class="caption">검토자 참조는 로컬 테스트 값이며 실제 PM 인증을 의미하지 않습니다.</p></section><section class="card"><h2>고객 전달 기록</h2><p class="caption">이번 범위에서는 전송·협의 기록을 생성하지 않습니다.</p><div class="form-actions">${button("고객 응답 기록", "unused", { disabled: true, icon: "6ce49" })}</div></section><section class="card"><h2>변경 이력</h2>${history()}</section></div></div>`;
}
// ---- 로컬 테스트 전용: 프로젝트 기준 문서 (문서 추가 → 색인 → 새 기준선). 실제 업로드·승인·기준선 관리는 백엔드 담당 ----
// 근거 카드 원문 미리보기: key(doc:page:start-end) → { open, pending, data, error }
const evSource = {};
const evKey = ev => `${ev.doc_id}:${ev.page}:${ev.line_start}-${ev.line_end ?? ev.line_start}`;
async function toggleEvSource(key) {
  const slot = evSource[key];
  if (!slot) return;
  slot.open = !slot.open;
  if (slot.open && !slot.data && !slot.pending) {
    const ev = slot.ev;
    slot.pending = true; slot.error = null; render();
    try { slot.data = await kbApi.lines(ev.doc_id, ev.page, ev.line_start, ev.line_end ?? ev.line_start); }
    catch (error) { slot.error = error.message; }
    finally { slot.pending = false; }
  }
  render();
}
function evSourceView(ev) {
  if (!lab.enabled || !ev.page || ev.line_start == null) return "";
  const key = evKey(ev), slot = evSource[key] ||= { ev, open: false, pending: false, data: null, error: null };
  const toggle = `<button type="button" class="button small" data-ev-source="${e(key)}">${slot.open ? "원문 접기" : "원문에서 보기"}</button>`;
  if (!slot.open) return `<div class="form-actions">${toggle}</div>`;
  const body = slot.pending ? '<p class="loading-line" role="status"><span class="inline-spinner"></span>원문을 불러오는 중…</p>'
    : slot.error ? `<p class="tiny">${e(slot.error)}</p>`
    : `<p class="tiny">${e(ev.doc_id)} · ${slot.data.page}/${slot.data.page_count}페이지 · 전체 ${slot.data.line_count}행 중</p><div class="source-lines">${slot.data.lines.map(l => `<div class="source-line ${l.hit ? "hit" : ""}"><span class="source-no">${l.no}</span><span>${e(l.text) || "&nbsp;"}</span></div>`).join("")}</div>`;
  return `<div class="form-actions">${toggle}</div>${body}`;
}
const kb = { data: null, pending: false, error: null, projectId: null, docType: "sow", files: [], results: [], creating: null, selection: {} };
const KB_TYPE = { contract: "계약서", sow: "과업지시서", functional_spec: "기능명세서", quote: "견적서", change_order: "변경합의서" };
const kbProjectData = id => kb.data?.projects.find(p => p.project_id === id);
const kbDoc = id => kb.data?.projects.flatMap(p => p.documents).find(d => d.doc_id === id && d.source === "lab");
function kbBaselineState(ctx) {
  const project = ctx && kbProjectData(ctx.project_id);
  if (!project) return null;
  return { project, current: project.baselines.find(b => b.baseline_id === ctx.baseline_id), active: project.baselines.find(b => b.baseline_id === project.active_baseline_id) };
}
function kbSelection(projectId = kb.projectId) {
  if (!kb.selection[projectId]) {
    const p = kbProjectData(projectId), active = p?.baselines.find(b => b.baseline_id === p.active_baseline_id);
    kb.selection[projectId] = new Set([...(active?.document_ids || []), ...(p?.documents || []).filter(d => d.source === "lab" && !d.in_baseline).map(d => d.doc_id)]);
  }
  return kb.selection[projectId];
}
async function loadKb() {
  if (!lab.enabled || kb.pending) return;
  try { kb.data = await kbApi.projects(); kb.error = null; if (!kb.projectId || !kbProjectData(kb.projectId) || kbProjectData(kb.projectId).draft) kb.projectId = kb.data.projects.find(x => !x.draft)?.project_id || null; }
  catch (error) { kb.error = error.message; }
  render();
}
async function kbOperation(action, message) {
  if (kb.pending) return;
  kb.pending = true; kb.error = null; render();
  try { await action(); kb.selection = {}; kb.data = await kbApi.projects(); if (message) toast(message); }
  catch (error) { kb.error = error.message; }
  finally { kb.pending = false; render(); }
}
function kbUpload() {
  const files = [...kb.files], projectId = kb.projectId;
  if (!files.length) { kb.error = "PDF 파일을 선택해 주세요."; render(); return; }
  if (files.some(file => file.type && file.type !== "application/pdf")) { kb.error = "PDF 파일만 올릴 수 있습니다."; render(); return; }
  return kbOperation(async () => {
    kb.results = [];
    for (const file of files) {   // 서버 색인은 한 건씩 처리한다
      const content = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1] || ""); reader.onerror = () => reject(new Error(`${file.name}을 읽지 못했습니다.`)); reader.readAsDataURL(file); });
      kb.results.push({ filename: file.name, ...await kbApi.upload(projectId, { filename: file.name, doc_type: kb.docType, content_base64: content }) });
    }
    kb.files = [];
  }, `문서 ${files.length}건 색인을 마쳤습니다. 결과를 확인해 주세요.`);
}
function kbRemoveDoc(docId) { return kbOperation(() => kbApi.remove(docId), "테스트 문서를 목록에서 뺐습니다."); }
function kbCreateBaseline() {
  const ids = [...kbSelection()], projectId = kb.projectId;
  if (!ids.length) { kb.error = "새 기준선에 넣을 문서를 하나 이상 선택해 주세요."; render(); return; }
  return kbOperation(async () => { const bl = await kbApi.createBaseline(projectId, ids); kb.results = []; a2.result = null; invalidateLiveReview(); toast(`${bl.baseline_id}를 만들었습니다. ${bl.supersedes || "이전 기준선"}은 폐기됨으로 바뀝니다.`); });
}
function kbReset() {
  if (!confirm("테스트로 추가한 문서와 기준선을 모두 지웁니다. 원본 데이터와 vector-db 벡터는 남습니다. 계속할까요?")) return;
  return kbOperation(async () => { await kbApi.reset(); kb.results = []; kb.creating = null; a2.useActive = false; a2.result = null; invalidateLiveReview(); }, "테스트 문서·기준선을 초기화했습니다.");
}
function baselineBanner() {
  if (!lab.enabled) return "";
  const ctx = (a2.inputSource === "agent1" ? (a1.sourceInput || agent1Inputs?.[activeCase])?.context : null) || agent2Input?.context;
  const info = kbBaselineState(ctx);
  if (!info?.current) return "";
  const { current, active } = info, link = button("기준 문서 보기", "projects", { class: "small", icon: "ec151" });
  if (current.status === "ACTIVE") return `<div class="baseline">${icon("73e25")}<strong>판단 기준선 ${e(current.baseline_id)}</strong>${chip("현재", "success")}<span class="muted">기준일 ${e(current.as_of)} · 문서 ${current.document_ids.length}개</span>${link}</div>`;
  return `<div class="notice"><strong>이 요청의 기준선 ${e(current.baseline_id)}는 ${e(current.superseded_by)}로 대체되었습니다.</strong>Agent 2는 폐기된 기준선으로 새 판단을 하지 않습니다 (BASELINE_SUPERSEDED). 테스트로 현재 기준선을 적용해 실행할 수 있습니다.<label class="check"><input id="a2-use-active" type="checkbox" ${a2.useActive ? "checked" : ""} ${a2.pending ? "disabled" : ""}><span>현재 기준선 ${e(active?.baseline_id || "")} (기준일 ${e(active?.as_of || "")})으로 실행 · 테스트용</span></label>${link}</div>`;
}
// ---- 새 프로젝트: 정보 입력 → 기준 문서 업로드·색인 → 첫 기준선(BL-1) 생성으로 확정 ----
const BASE_TYPES = ["contract", "sow", "functional_spec", "quote"];   // 변경합의서는 프로젝트를 만든 뒤 새 기준선으로
const guessDocType = name => ({ CTR: "contract", SOW: "sow", FSP: "functional_spec", QUO: "quote" })[String(name).split("-")[0].toUpperCase()] || "sow";
const today = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
function startNewProject() { kb.creating = { step: 1, project_id: "", name: "", as_of: today(), files: [], uploaded: [], include: new Set(), error: null }; render(); }
async function npStart() {
  const c = kb.creating, id = c.project_id.trim().toUpperCase();
  if (!/^[A-Z][A-Z0-9]*(-[A-Z0-9]+)+$/.test(id)) { c.error = "프로젝트 ID는 영문 대문자·숫자와 하이픈으로 입력해 주세요 (예: P-COM-104)."; render(); return; }
  if (!c.name.trim()) { c.error = "프로젝트 이름을 입력해 주세요."; render(); return; }
  if (!c.as_of || c.as_of > today()) { c.error = "기준일은 오늘 또는 그 이전 날짜로 입력해 주세요."; render(); return; }
  kb.pending = true; c.error = null; render();
  try {
    const created = await kbApi.createProject(id, c.name.trim());
    c.project_id = id; c.step = 2;
    kb.data = await kbApi.projects();
    const existing = kbProjectData(id)?.documents || [];   // 작성 중이던 프로젝트면 올린 문서를 이어서 쓴다
    c.uploaded = existing.map(d => ({ filename: d.title, doc_type: d.doc_type, doc_id: d.doc_id, status: "READY", sections: d.section_count }));
    c.include = new Set(c.uploaded.filter(u => BASE_TYPES.includes(u.doc_type)).map(u => u.doc_id));
    if (created.documents.length) toast(`작성 중이던 ${id}를 이어서 진행합니다.`);
  } catch (error) { c.error = error.message; }
  finally { kb.pending = false; render(); }
}
async function npUpload() {
  const c = kb.creating, files = [...c.files];
  if (!files.length) return;
  kb.pending = true; c.error = null; render();
  try {
    for (const item of files) {
      const content = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(",")[1] || ""); reader.onerror = () => reject(new Error(`${item.file.name}을 읽지 못했습니다.`)); reader.readAsDataURL(item.file); });
      const out = await kbApi.upload(c.project_id, { filename: item.file.name, doc_type: item.docType, content_base64: content });
      const ready = out.result.status === "READY" && out.document;
      c.uploaded.push({ filename: item.file.name, doc_type: item.docType, doc_id: ready ? out.document.doc_id : null, status: out.result.status, sections: ready ? out.document.section_count : 0,
        error: ready ? null : `${out.result.failedStage || ""} · ${out.result.errorCode || ""} ${out.result.errorMessage || ""}`.trim() });
      if (ready) c.include.add(out.document.doc_id);
      c.files = c.files.filter(f => f !== item); render();
    }
  } catch (error) { c.error = error.message; }
  finally { kb.pending = false; render(); }
}
async function npCreate() {
  const c = kb.creating, ids = [...c.include];
  kb.pending = true; c.error = null; render();
  try {
    const bl = await kbApi.createBaseline(c.project_id, ids, c.as_of);
    kb.creating = null; kb.selection = {}; kb.data = await kbApi.projects();
    toast(`${c.project_id} 프로젝트를 만들었습니다. 기준선 ${bl.baseline_id}`);
    location.hash = `#/projects/${encodeURIComponent(c.project_id)}`;
  } catch (error) { c.error = error.message; }
  finally { kb.pending = false; render(); }
}
function newProjectView() {
  const c = kb.creating, busy = kb.pending;
  const error = c.error ? `<div class="notice error"><strong>처리하지 못했습니다</strong>${e(c.error)}</div>` : "";
  const steps = ["프로젝트 정보", "기준 문서 업로드", "첫 기준선 만들기"].map((label, i) => `<span class="version-pill ${c.step === i + 1 || (c.step === 2 && i === 2 && c.uploaded.length) ? "current" : ""}">${i + 1}. ${label}</span>`).join("");
  const head = `<section class="card"><div class="card-heading"><div><h2>새 프로젝트</h2><p class="caption">기준 문서를 올리고 첫 기준선(BL-1)을 만들면 프로젝트가 확정됩니다. 그 전까지는 목록에 보이지 않습니다.</p></div>${button("취소", "np-cancel", { disabled: busy })}</div><div class="version-row">${steps}</div>`;
  if (c.step === 1) return `${error}${head}<div class="settings-grid"><label class="field">프로젝트 ID<input id="np-id" value="${e(c.project_id)}" placeholder="P-COM-104" autocomplete="off" ${busy ? "disabled" : ""}></label><label class="field">프로젝트 이름<input id="np-name" value="${e(c.name)}" placeholder="신규 커머스 구축" ${busy ? "disabled" : ""}></label><label class="field">기준일 (첫 기준선)<input id="np-asof" type="date" value="${e(c.as_of)}" max="${today()}" ${busy ? "disabled" : ""}></label></div><p class="tiny" style="margin-top:8px">작성 중이던 프로젝트 ID를 입력하면 이미 올린 문서를 이어서 씁니다.</p><div class="form-actions">${button(busy ? "확인 중…" : "다음: 기준 문서 업로드", "np-start", { class: "primary", disabled: busy })}</div></section>`;
  const picked = c.files.map((f, i) => `<li class="np-file"><span>${e(f.file.name)} <small>${Math.ceil(f.file.size / 1024)} KB</small></span><select data-np-type="${i}" aria-label="${e(f.file.name)} 문서 종류" ${busy ? "disabled" : ""}>${BASE_TYPES.map(t => `<option value="${t}" ${f.docType === t ? "selected" : ""}>${KB_TYPE[t]}</option>`).join("")}</select></li>`).join("");
  const rows = c.uploaded.map(u => `<tr><td><strong>${e(u.filename)}</strong>${u.doc_id ? `<small>${e(u.doc_id)}</small>` : ""}</td><td>${e(KB_TYPE[u.doc_type] || u.doc_type)}</td><td>${u.status === "READY" ? chip(`READY · 조항 ${u.sections}개`, "success") : chip("FAILED", "warning")}${u.error ? `<small>${e(u.error)}</small>` : ""}</td><td>${u.doc_id ? `<label class="check" style="margin:0"><input type="checkbox" data-np-include="${e(u.doc_id)}" ${c.include.has(u.doc_id) ? "checked" : ""} ${busy ? "disabled" : ""}><span>BL-1에 포함</span></label>` : ""}</td></tr>`).join("");
  const types = new Set(c.uploaded.filter(u => c.include.has(u.doc_id)).map(u => u.doc_type));
  const ready = types.has("contract") || types.has("sow");
  return `${error}${head}<p class="caption"><strong>${e(c.project_id)}</strong> · ${e(c.name)} · 기준일 ${e(c.as_of)}</p>
<div class="field">기준 문서 PDF<label class="file-picker ${busy ? "disabled" : ""}"><input id="np-file" class="file-input" type="file" accept="application/pdf" multiple ${busy ? "disabled" : ""}><span class="button small">파일 선택</span><span class="${c.files.length ? "" : "muted"}">${c.files.length ? `파일 선택 완료 (${c.files.length}개)` : "선택된 파일 없음"}</span></label>${picked ? `<ul class="file-names">${picked}</ul>` : ""}</div>
<p class="tiny" style="margin-top:8px">파일마다 문서 종류를 확인해 주세요. 파일 이름이 CTR·SOW·FSP·QUO로 시작하면 자동으로 고릅니다. 변경합의서는 프로젝트를 만든 뒤 추가합니다.</p>
<div class="form-actions">${button(busy ? "색인 중…" : "업로드·색인", "np-upload", { class: "primary", disabled: busy || !c.files.length, icon: "416f8" })}</div>${busy ? '<p class="loading-line" role="status"><span class="inline-spinner"></span>처리 중입니다. 임베딩 호출로 파일당 수십 초 걸릴 수 있습니다…</p>' : ""}
${rows ? `<div class="table-wrap" style="margin-top:16px"><table><thead><tr><th>문서</th><th>종류</th><th>색인</th><th>첫 기준선</th></tr></thead><tbody>${rows}</tbody></table></div>` : ""}
<div class="form-actions" style="margin-top:16px">${button(`${c.project_id} 만들기 (BL-1)`, "np-create", { class: "primary", disabled: busy || !ready, icon: "6ce49" })}<span class="tiny">${ready ? `선택 ${c.include.size}개로 첫 기준선을 만듭니다.` : "계약서나 과업지시서를 하나 이상 올리고 포함해 주세요."}</span></div></section>`;
}
function projectsPage() {
  const heading = `<div class="page-heading"><div><h1>프로젝트 · 기준 문서</h1><p class="subtitle">로컬 테스트 전용 · 실제 문서 업로드·승인·기준선 관리는 백엔드 담당입니다.</p></div><div class="button-group">${button("새 프로젝트", "kb-new-project", { class: "primary", disabled: kb.pending || !lab.enabled || !kb.data || !!kb.creating })}${button("새로고침", "kb-refresh", { disabled: kb.pending, icon: "e4529" })}${button("테스트 데이터 초기화", "kb-reset", { disabled: kb.pending || !lab.enabled })}</div></div>`;
  if (!lab.enabled) return heading + '<div class="notice">문서 추가는 <code>python prototype/run_demo.py --lab</code>로 실행한 서버에서만 사용할 수 있습니다.</div>';
  if (!kb.data) return heading + (kb.error ? `<div class="notice error">${e(kb.error)}</div>` : '<p class="loading-line" role="status"><span class="inline-spinner"></span>프로젝트를 불러오는 중입니다…</p>');
  if (kb.creating) return heading + newProjectView();
  const p = kbProjectData(kb.projectId);
  if (!p) return heading + '<div class="empty-state"><h3>프로젝트가 없습니다.</h3></div>';
  const selected = kbSelection(), active = p.baselines.find(b => b.baseline_id === p.active_baseline_id);
  const tabs = `<div class="tabs" role="tablist" aria-label="프로젝트">${kb.data.projects.filter(x => !x.draft).map(x => `<button class="tab" role="tab" aria-selected="${x.project_id === p.project_id}" data-kb-project="${e(x.project_id)}" ${x.name ? `title="${e(x.name)}"` : ""}>${e(x.project_id)}<small>${x.documents.length}</small></button>`).join("")}</div>`;
  const error = kb.error ? `<div class="notice error"><strong>처리하지 못했습니다</strong>${e(kb.error)}</div>` : "";
  const name = id => { const d = p.documents.find(x => x.doc_id === id); return d ? (d.source === "lab" ? d.title : d.doc_id) : id; };
  const baselineRows = [...p.baselines].reverse().map(b => `<tr><td><strong>${e(b.baseline_id)}</strong>${b.supersedes ? `<small>${e(b.supersedes)} 대체</small>` : ""}</td><td>${e(b.as_of)}</td><td>${b.status === "ACTIVE" ? chip("현재", "success") : chip(`폐기됨 · ${b.superseded_by}로 대체`)}</td><td>${b.document_ids.map(id => `<span class="mini-tag">${e(name(id))}</span>`).join(" ")}</td><td>${b.source === "lab" ? chip("테스트 추가", "ai") : "원본"}</td></tr>`).join("");
  const docRows = p.documents.map(d => `<tr><td><strong>${e(d.source === "lab" ? d.title : d.doc_id)}</strong>${d.source === "lab" ? `<small>${e(d.doc_id)}</small>` : ""}</td><td>${e(KB_TYPE[d.doc_type] || d.doc_type)}</td><td class="numeric">${d.section_count}</td><td>${active?.document_ids.includes(d.doc_id) ? chip("포함", "info") : d.in_baseline ? "이전 기준선" : chip("미포함")}</td><td>${d.source === "lab" ? chip("테스트 추가", "ai") : "원본"}</td><td><label class="check" style="margin:0"><input type="checkbox" data-kb-include="${e(d.doc_id)}" ${selected.has(d.doc_id) ? "checked" : ""} ${kb.pending ? "disabled" : ""}><span>새 기준선에 포함</span></label></td><td>${d.source === "lab" && !d.in_baseline ? `<button type="button" class="button small" data-kb-remove="${e(d.doc_id)}" ${kb.pending ? "disabled" : ""}>빼기</button>` : ""}</td></tr>`).join("");
  const result = kb.results.map(({ filename, result: last, document: doc }) => last.status === "READY"
    ? `<div class="notice inline"><strong>${e(filename)} · READY · 조항 ${last.sectionCount}개 · ${e(last.extractionVersion)}</strong>문서 버전 ${e(last.documentVersionId)} · 임베딩 ${e(last.embeddingProfileId)}<details style="margin-top:8px"><summary class="caption">조항 미리보기</summary>${(doc?.sections || []).map(s => `<p class="caption">· ${e(s.section_id)} ${e(s.heading)} (p.${s.page})</p>`).join("")}</details><p class="tiny" style="margin-top:6px">아직 기준선에는 들어가지 않았습니다. 아래 ‘새 기준선 만들기’로 추가해 주세요.</p></div>`
    : `<div class="notice error inline"><strong>${e(filename)} · FAILED · ${e(last.failedStage)} · ${e(last.errorCode)}</strong>${e(last.errorMessage || "")}${last.retryable ? " (다시 시도 가능)" : ""}</div>`).join("");
  const cases = Object.entries(agent2Inputs || {}).filter(([, input]) => input.context?.project_id === p.project_id);
  const usedBy = cases.length ? `<p class="caption" style="margin-top:12px">이 프로젝트의 테스트 요청: ${cases.map(([id, input]) => `<button type="button" class="rate-card-link" data-open="${e(id)}">${e(id)}</button> <span class="tiny">(${e(input.context.baseline_id)})</span>`).join(" · ")}</p>` : "";
  const upload = `<section class="card"><div class="card-heading"><div><h2>테스트 문서 추가</h2><p class="caption">PDF → 조항 분할 → 청크 ID 계산 → 임베딩 → vector-db 저장. 백엔드 색인 요청(A-MQ-01)과 같은 파이프라인을 씁니다.</p></div></div><div class="settings-grid"><div class="field">PDF 파일<label class="file-picker ${kb.pending ? "disabled" : ""}"><input id="kb-file" class="file-input" type="file" accept="application/pdf" multiple ${kb.pending ? "disabled" : ""}><span class="button small">파일 선택</span><span class="${kb.files.length ? "" : "muted"}">${kb.files.length ? `파일 선택 완료 (${kb.files.length}개)` : "선택된 파일 없음"}</span></label>${kb.files.length ? `<ul class="file-names">${kb.files.map(f => `<li>${e(f.name)} <small>${Math.ceil(f.size / 1024)} KB</small></li>`).join("")}</ul>` : ""}</div><label class="field">문서 종류<select id="kb-doc-type" ${kb.pending ? "disabled" : ""}>${Object.entries(KB_TYPE).map(([v, l]) => `<option value="${v}" ${kb.docType === v ? "selected" : ""}>${l}</option>`).join("")}</select></label></div><p class="tiny" style="margin-top:8px">계약 문서 양식(조항 머리줄 끝 C-01·S-02 등, 기능명세 표)만 조항으로 나뉩니다. 스캔본은 지원하지 않습니다.</p><div class="form-actions">${button(kb.pending ? "처리 중…" : "업로드·색인", "kb-upload", { class: "primary", disabled: kb.pending || !kb.files.length, icon: "416f8" })}</div>${kb.pending ? '<p class="loading-line" role="status"><span class="inline-spinner"></span>색인 중입니다. 임베딩 호출로 수십 초 걸릴 수 있습니다…</p>' : ""}${result}</section>`;
  const create = `<section class="card"><div class="card-heading"><div><h2>새 기준선 만들기</h2><p class="caption">선택한 문서로 새 버전을 만듭니다. 현재 기준선 ${e(active?.baseline_id || "없음")}은 폐기됨(SUPERSEDED)으로 바뀌고, 그 기준선으로 요청하면 Agent 2가 판단하지 않습니다.</p></div></div><p class="caption">선택 ${selected.size}개: ${[...selected].map(id => `<span class="mini-tag">${e(name(id))}</span>`).join(" ") || "없음"}</p><div class="form-actions">${button("새 기준선 만들기", "kb-create-baseline", { class: "primary", disabled: kb.pending || !selected.size, icon: "6ce49" })}</div>${usedBy}</section>`;
  return `${heading}${tabs}${error}<section class="card"><div class="card-heading"><div><h2>기준선 버전</h2><p class="caption">현재 기준선만 새 판단에 쓰입니다. 폐기된 기준선은 조회·재현용으로 남습니다.</p></div></div><div class="table-wrap"><table><thead><tr><th>기준선</th><th>기준일</th><th>상태</th><th>문서</th><th>출처</th></tr></thead><tbody>${baselineRows}</tbody></table></div></section><section class="card" style="margin-top:24px"><div class="card-heading"><div><h2>문서</h2><p class="caption">문서를 선택해 새 기준선을 만들 수 있습니다. 테스트로 추가한 문서는 기준선에 넣기 전까지 뺄 수 있습니다.</p></div></div><div class="table-wrap"><table><thead><tr><th>문서</th><th>종류</th><th>조항</th><th>현재 기준선</th><th>출처</th><th>새 기준선</th><th></th></tr></thead><tbody>${docRows}</tbody></table></div></section><div class="columns" style="margin-top:24px">${upload}${create}</div><div class="notice inline">${icon("d5e00")}로컬 테스트 전용입니다. 테스트 문서·기준선은 .prototype-kb/에만 저장되며 원본 data/kb는 바뀌지 않습니다. 초기화해도 vector-db 벡터는 남습니다(같은 문서 ID면 재사용).</div>`;
}
function render() {
  const active = document.activeElement;
  const focus = active?.id ? { id: active.id, start: active.selectionStart, end: active.selectionEnd } : null;
  root.innerHTML = `<div class="shell">${sidebar()}<div class="right">${topbar()}<main id="main" class="content" tabindex="-1">${state.page === "analysis" ? analysisDemo.page() : state.page === "projects" ? projectsPage() : state.page === "register" ? registrationPage() : state.page === "list" ? inbox() : detail()}<p class="footer-note">합성 예시로 동작을 확인하는 로컬 화면입니다. AI 서버는 메모리 세션을 사용하며 CN 업무 DB·실제 사용자 인증과 연결되지 않았습니다.</p></main></div></div>`;
  if (state.page === "analysis") analysisDemo.syncDialog();
  if (focus) { const target = document.getElementById(focus.id); if (target && !target.disabled) { target.focus({ preventScroll: true }); if (typeof focus.start === "number" && target.setSelectionRange && !["number", "checkbox"].includes(target.type)) target.setSelectionRange(focus.start, focus.end); } }
}
function updateSearchResults() {
  // Keep the focused input in place while typing, including IME composition.
  const fragment = document.createElement("template");
  fragment.innerHTML = inbox();
  for (const selector of [".table-wrap", ".pagination"]) {
    root.querySelector(selector)?.replaceWith(fragment.content.querySelector(selector));
  }
}
function updateFormControls() {
  const approve = root.querySelector('[data-action="approve"]');
  const reject = root.querySelector('[data-action="reject"]');
  const edit = root.querySelector('[data-action="edit"]');
  const cancel = root.querySelector('[data-action="cancel-edit"]');
  if (approve) approve.disabled = !canApprove() || (unknownSchedule() && !state.ack);
  if (reject) reject.disabled = !canApprove();
  if (edit) edit.disabled = !canEdit() || !editsChanged();
  if (cancel) cancel.disabled = !editsChanged() || state.pending;
  const note = document.querySelector("#edit-notice");
  if (note) note.textContent = editsChanged() ? "아직 저장하지 않은 수정이 있습니다. 수정 반영 후 승인해 주세요." : "단가는 단가표 이름을 눌러 수정할 수 있습니다. 일정은 직접 편집할 수 없습니다.";
}
async function operation(action) {
  if (record().analysisDemo) return;
  if (state.pending) return;
  state.pending = true; state.error = null; render();
  try { await action(); }
  catch (error) {
    state.error = { status: error instanceof ApiError ? error.status : 422, local: !(error instanceof ApiError), message: error.message };
    if (error.status === 409) state.conflicted = true;
    if (error.status === 404) { state.view = null; state.edits = null; if (state.mode === "api") forget(); }
  } finally { state.pending = false; persist(); render(); }
}
async function prepare() {
  if (!record().api) return;
  let input;
  try {
    if (state.inputSource === "agent2" && (!a2.review || !a2.result || a2.review.a2_result.meta.result_id !== a2.result.meta.result_id || JSON.stringify(a2.review) !== JSON.stringify(parsedInput()))) throw new Error("현재 Agent 2 결과를 실행하고 PM 판단을 적용해 주세요.");
    input = structuredClone(parsedInput());
    if (input.context?.case_id !== baseInput.context.case_id || input.context?.project_id !== baseInput.context.project_id) throw new Error("현재 선택한 요청의 case_id와 project_id를 유지해 주세요.");
  } catch (error) { state.error = { status: 422, local: true, message: error.message }; render(); return; }
  await operation(async () => {
    state.invalidated = true;
    let config = {};
    if (lab.enabled && state.mode === "api") {
      let fake_response = null;
      if (lab.provider === "fake") { try { fake_response = JSON.parse(lab.fakeText); } catch { throw new ApiError(422, "가상 응답 JSON을 확인해 주세요."); } }
      config = { provider: lab.provider, model: lab.model, base_url: lab.base_url, region: lab.region, fake_response };
    }
    const view = state.mode === "api" ? await api.prepare(input, config) : await previewClient.prepare(state.scenario);
    if (state.id !== input.context.case_id) return;
    acceptView(view, state.mode === "api" ? input : baseInput); toast(state.mode === "api" ? "기존 API의 산정 결과를 받았습니다." : "실제 API 호출 없이 상태를 미리봅니다.");
  });
}
async function refresh() {
  if (!state.view || state.mode !== "api") return;
  const id = state.view.session_id;
  await operation(async () => { acceptView(await api.get(id)); toast("현재 세션을 조회했습니다. 충돌이 있으면 새로 산정해 주세요."); });
}
async function edit() {
  if (!canEdit()) return;
  if (!state.editReason.trim()) { state.error = { status: 422, local: true, message: "공수·가정을 수정한 사유를 입력해 주세요." }; render(); return; }
  if (state.edits.some(item => !item.lines.length || item.lines.some(line => !line.task.trim()))) { state.error = { status: 422, local: true, message: "각 산정 항목에 하나 이상의 작업과 작업 내용을 입력해 주세요." }; render(); return; }
  const invalid = state.edits.some(item => item.lines.some(line => !Number.isFinite(line.md) || line.md < 0 || (line.md * 2) % 1));
  if (invalid) { state.error = { status: 422, local: true, message: "MD는 0 이상인 유한한 수이며 0.5 단위여야 합니다." }; render(); return; }
  await operation(async () => {
    const view = await api.edit(state.view.session_id, { state_revision: state.view.state_revision, target_result_id: state.view.impact.meta.result_id, estimates: state.edits, reason: state.editReason.trim() });
    acceptView(view); toast("금액을 다시 계산했습니다. 최신 결과를 다시 승인해 주세요.");
  });
}
async function review(approved) {
  if (!canApprove()) return;
  if (!state.reviewer.trim() || (!approved && !state.reviewReason.trim())) { state.error = { status: 422, local: true, message: !state.reviewer.trim() ? "검토자 참조를 입력해 주세요." : "반려 사유를 입력해 주세요." }; render(); return; }
  if (approved && unknownSchedule() && !state.ack) return;
  const input = { state_revision: state.view.state_revision, target_result_id: state.view.impact.meta.result_id, snapshot_hash: state.view.snapshot_hash, request_id: crypto.randomUUID(), reviewer_ref: state.reviewer.trim(), approved, reason: state.reviewReason.trim(), acknowledge_unknown_schedule: state.ack };
  await operation(async () => { acceptView(await api.review(state.view.session_id, input)); toast(approved ? "PM 산정 승인이 완료됐습니다. 협의안 화면에서 생성해 주세요." : "반려 사유를 기록했습니다. 초안은 생성하지 않았습니다."); });
}
async function generateDraft() {
  if (!safeCurrent() || editsChanged() || !state.view?.approval?.approved || !["APPROVED", "DRAFT_READY"].includes(state.view.phase)) return;
  const input = { state_revision: state.view.state_revision, review_id: state.view.approval.review_id };
  await operation(async () => { acceptView(await api.draft(state.view.session_id, input, draftLab)); toast("협의안 문안이 생성됐습니다. 내용을 검토해 주세요."); });
}
async function restore() {
  let saved;
  try { saved = JSON.parse(sessionStorage.getItem(STORAGE) || "null"); } catch { return; }
  if (!saved?.sessionId || typeof saved.inputText !== "string") return;
  try { if (JSON.parse(saved.inputText)?.context?.case_id !== baseInput.context.case_id) return; } catch { return; }
  if (lab.enabled && saved.labSettings && saved.labSettings.provider !== lab.provider) saved.invalidated = true;
  state.inputSource = saved.inputSource === "agent2" ? "agent2" : "sample"; state.sampleInputText = saved.sampleInputText || JSON.stringify(baseInput, null, 2); state.inputText = saved.inputText; state.signature = saved.signature; state.originalLines = Array.isArray(saved.originalLines) ? saved.originalLines : [];
  // v1 sessions stored the successful request as signature, separately from editable inputText.
  try { state.submittedInput = structuredClone(saved.submittedInput || JSON.parse(saved.signature)); } catch { state.submittedInput = null; }
  state.invalidated = saved.invalidated === true;
  await operation(async () => { acceptView(await api.get(saved.sessionId)); });
}

root.addEventListener("cancel", event => { if (event.target.id === "analysis-pm-dialog") analysisDemo.cancel(); }, true);
root.addEventListener("click", event => {
  if (analysisDemo.click(event.target)) return;
  const open = event.target.closest("[data-open]"); if (open) {
    if (!records.some(item => item.id === open.dataset.open && (item.analysisDemo || item.api || item.datasetSample || item.id === "CR-001"))) return toast("목록 표시용 예시입니다. Agent 1 테스트 요청과 CR-001에서 원문을 확인할 수 있습니다.");
    return navigate(open.dataset.open);
  }
  const tab = event.target.closest("[data-tab]"); if (tab) return navigate(state.id, tab.dataset.tab);
  const group = event.target.closest("[data-group]"); if (group) { state.group = group.dataset.group; state.pageNo = 1; render(); return; }
  const item = event.target.closest("[data-item]"); if (item) { state.selectedItem = Number(item.dataset.item); render(); return; }
  const addLine = event.target.closest("[data-add-line]"); if (addLine && canEdit()) { const i = Number(addLine.dataset.addLine); state.edits[i].lines.push({ task: "", item_ids: [...state.edits[i].item_ids], role: "FE", md: 0.5, assumption: "" }); render(); return; }
  const removeLine = event.target.closest("[data-remove-line]"); if (removeLine && canEdit()) { const [i,j] = removeLine.dataset.removeLine.split(":").map(Number); state.edits[i].lines.splice(j, 1); render(); return; }
  const kbProject = event.target.closest("[data-kb-project]"); if (kbProject) { location.hash = `#/projects/${encodeURIComponent(kbProject.dataset.kbProject)}`; return; }
  const evButton = event.target.closest("[data-ev-source]"); if (evButton) { toggleEvSource(evButton.dataset.evSource); return; }
  const kbRemove = event.target.closest("[data-kb-remove]"); if (kbRemove && !kb.pending) { kbRemoveDoc(kbRemove.dataset.kbRemove); return; }
  const cardButton = event.target.closest("[data-rate-card]"); if (cardButton) { showRateCard(cardButton.dataset.rateCard); return; }
  const control = event.target.closest("[data-action]"); if (!control || control.disabled) return;
  const action = control.dataset.action;
  if (action === "list") navigate(null);
  if (action === "projects") location.hash = `#/projects${kb.projectId ? `/${encodeURIComponent(kb.projectId)}` : ""}`;
  if (action === "kb-upload") kbUpload();
  if (action === "kb-create-baseline") kbCreateBaseline();
  if (action === "kb-reset") kbReset();
  if (action === "kb-refresh") loadKb();
  if (action === "kb-new-project") startNewProject();
  if (action === "np-start") npStart();
  if (action === "np-upload") npUpload();
  if (action === "np-create") npCreate();
  if (action === "np-cancel") { kb.creating = null; render(); }
if (action === "register" || action === "analysis") { location.hash = "#/analysis"; }
  if (action === "submit-registration") registerChangeRequest();
  if (action === "open-api") navigate("CR-A-001", "impact");
  if (action === "go-scope") navigate(state.id, "scope");
  if (action === "go-impact") navigate(state.id, "impact");
  if (action === "go-draft") navigate(state.id, "draft");
  if (action === "help") document.querySelector("#help-dialog").showModal();
  if (action === "previous") { state.pageNo--; render(); }
  if (action === "next") { state.pageNo++; render(); }
  if (action === "prepare") prepare();
  if (action === "confirm-live-review") confirmLiveReview();
  if (action === "run-a1") runAgent1();
  if (action === "run-a2") runAgent2();
  if (action === "refresh") refresh();
  if (action === "edit") edit();
  if (action === "approve") review(true);
  if (action === "generate-draft") generateDraft();
  if (action === "reject") review(false);
  if (action === "cancel-edit") { state.edits = structuredClone(estimates().map(item => ({ item_ids: item.item_ids, lines: item.lines }))); state.editReason = ""; render(); }
  if (action === "reset-input") { state.scenario = "normal"; state.inputText = JSON.stringify(baseInput, null, 2); state.sampleInputText = state.inputText; render(); }
  if (action === "clear-session") { forget(); state.view = null; state.edits = null; state.submittedInput = null; state.signature = null; state.error = null; state.conflicted = false; state.invalidated = false; state.originalLines = []; render(); toast("브라우저의 로컬 세션 참조를 지웠습니다. 서버 데이터 삭제를 의미하지 않습니다."); }
  if (action === "copy-draft" && hasCurrentDraft()) navigator.clipboard.writeText(draftMarkdown()).then(() => toast("내부 협의안 초안을 복사했습니다.")).catch(() => toast("클립보드 접근을 사용할 수 없습니다. Markdown 저장을 이용해 주세요."));
  if (action === "download-draft" && hasCurrentDraft()) { const blob = new Blob([draftMarkdown()], { type: "text/markdown;charset=utf-8" }); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = `${state.view.impact.context.case_id}_내부_협의안_초안.md`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
});
root.addEventListener("input", event => {
  if (kb.creating && ["np-id", "np-name", "np-asof"].includes(event.target.id)) { kb.creating[{ "np-id": "project_id", "np-name": "name", "np-asof": "as_of" }[event.target.id]] = event.target.value; return; }
  const target = event.target;
  analysisDemo.input(target);
  if (target.id === "request-search") { state.query = target.value; state.pageNo = 1; if (!event.isComposing && !composingSearch) updateSearchResults(); }
  if (["lab-fake", "lab-model", "lab-url", "lab-region"].includes(target.id) && state.view && state.mode === "api") { state.invalidated = true; persist(); updateFormControls(); }
  if (target.id === "draft-model") draftLab.model = target.value;
  if (target.id === "draft-region") draftLab.region = target.value;
  if (target.id === "lab-fake") lab.fakeText = target.value;
  if (target.id === "lab-model") lab.model = target.value;
  if (target.id === "lab-url") lab.base_url = target.value;
  if (target.id === "lab-region") lab.region = target.value;
  if (target.id?.startsWith("registration-")) {
    const key = { "registration-project": "project_id", "registration-project-name": "project_name", "registration-case": "case_id", "registration-channel": "channel", "registration-channel-src": "channel_src", "registration-received": "received_at", "registration-sender-role": "sender_role", "registration-sender-name": "sender_name", "registration-subject": "subject", "registration-message": "message_text" }[target.id];
    if (key) state.registration[key] = target.value;
  }
  if (target.id === "a1-model") { a1.model = target.value; a1.error = null; rememberA1Case(activeCase); }
  if (target.id === "a1-region") { a1.region = target.value; a1.error = null; rememberA1Case(activeCase); }
  if (target.id === "a2-model") { a2.model = target.value; a2.result = null; invalidateLiveReview(); }
  if (target.id === "a2-region") { a2.region = target.value; a2.result = null; invalidateLiveReview(); }
  if (target.id === "input-json" && state.inputSource === "sample") { state.inputText = target.value; state.sampleInputText = target.value; updateFormControls(); }
  if (target.dataset.pmReason) { const c = a2.choices[target.dataset.pmReason] || (a2.choices[target.dataset.pmReason] = {}); c.reason = target.value; invalidateLiveReview(); }
  if (target.id === "edit-reason") state.editReason = target.value;
  if (target.id === "reviewer") state.reviewer = target.value;
  if (target.id === "review-reason") state.reviewReason = target.value;
  if (target.dataset.editTask) { const [i, j] = target.dataset.editTask.split(":").map(Number); state.edits[i].lines[j].task = target.value; updateFormControls(); }
  if (target.dataset.editMd) { const [i, j] = target.dataset.editMd.split(":").map(Number); state.edits[i].lines[j].md = target.value === "" ? NaN : Number(target.value); updateFormControls(); }
  if (target.dataset.editAssumption) { const [i, j] = target.dataset.editAssumption.split(":").map(Number); state.edits[i].lines[j].assumption = target.value; updateFormControls(); }
});
root.addEventListener("compositionstart", event => { if (event.target.id === "request-search") composingSearch = true; });
root.addEventListener("compositionend", event => { if (event.target.id === "request-search") { composingSearch = false; state.query = event.target.value; state.pageNo = 1; updateSearchResults(); } });
root.addEventListener("change", async event => {
  const target = event.target;
  analysisDemo.change(target);
  if (target.id === "registration-channel") state.registration.channel = target.value;
  if (target.id === "registration-project") { state.registration.baseline_id = ""; render(); }
  if (target.id === "registration-baseline") state.registration.baseline_id = target.value;
  if (target.name === "a2-input-source") selectAgent2Input(target.value);
  if (target.dataset.editRole) { const [i,j] = target.dataset.editRole.split(":").map(Number); state.edits[i].lines[j].role = target.value; render(); }
  if (target.name === "input-source") switchInputSource(target.value);
  if (target.id === "input-json" && state.inputSource === "sample") render();
  if (target.dataset.pmLabel) { const c = a2.choices[target.dataset.pmLabel] || (a2.choices[target.dataset.pmLabel] = {}); c.label = target.value; if (c.label !== "out_of_scope") c.estimate = false; invalidateLiveReview(); render(); }
  if (target.dataset.pmEstimate) { const c = a2.choices[target.dataset.pmEstimate] || (a2.choices[target.dataset.pmEstimate] = {}); c.estimate = target.checked; invalidateLiveReview(); render(); }
  if (target.id === "draft-provider") { draftLab.provider = target.value; draftLab.model = defaultModel(target.value); render(); }
  if (target.id === "draft-model") { draftLab.model = target.value; }
  if (target.id === "lab-model") { lab.model = target.value; if (state.view && state.mode === "api") { state.invalidated = true; persist(); } }
  if (target.id === "a1-model") { a1.model = target.value; a1.error = null; rememberA1Case(activeCase); }
  if (target.id === "a2-model") { a2.model = target.value; a2.result = null; invalidateLiveReview(); }
  if (target.name === "lab-provider") { if (state.view && state.mode === "api") { state.invalidated = true; persist(); } lab.provider = target.value; lab.model = defaultModel(target.value); render(); }
  if (target.name === "a1-provider" || target.name === "a2-provider") { const agent = target.name === "a1-provider" ? a1 : a2; agent.provider = target.value; agent.model = defaultModel(target.value); agent.error = null; if (agent === a1) rememberA1Case(activeCase); if (agent === a2) resetAgent2Result(); render(); }
  if (target.id === "kb-file") { kb.files = [...(target.files || [])]; kb.results = []; render(); }
  if (target.id === "kb-doc-type") kb.docType = target.value;
  if (target.id === "np-file" && kb.creating) { kb.creating.files = [...(target.files || [])].map(file => ({ file, docType: guessDocType(file.name) })); render(); }
  if (target.dataset.npType && kb.creating) kb.creating.files[Number(target.dataset.npType)].docType = target.value;
  if (target.dataset.npInclude && kb.creating) { target.checked ? kb.creating.include.add(target.dataset.npInclude) : kb.creating.include.delete(target.dataset.npInclude); render(); }
  if (target.dataset.kbInclude) { const set = kbSelection(); target.checked ? set.add(target.dataset.kbInclude) : set.delete(target.dataset.kbInclude); render(); }
  if (target.id === "a2-use-active") { a2.useActive = target.checked; a2.result = null; a2.error = null; invalidateLiveReview(); render(); }
  if (target.id === "scope-filter") { state.filter = target.value; state.pageNo = 1; render(); }
  if (target.id === "project-filter") { state.project = target.value; state.pageNo = 1; render(); }
  if (target.id === "sort") { state.order = target.value; state.pageNo = 1; render(); }
  if (target.id === "ack-schedule") { state.ack = target.checked; updateFormControls(); }
  if (target.id === "scenario") { state.scenario = target.value; state.inputText = JSON.stringify(scenarioInput(baseInput, state.scenario), null, 2); state.sampleInputText = state.inputText; render(); }
  if (target.id === "mode") {
    state.mode = target.value; state.view = null; state.edits = null; state.submittedInput = null; state.signature = null; state.error = null; state.conflicted = false; state.invalidated = false; state.scenario = "normal"; state.inputSource = "agent2"; state.sampleInputText = JSON.stringify(baseInput, null, 2); state.inputText = ""; render();
    if (state.mode === "api") await restore();
  }
});
root.addEventListener("keydown", event => { if (event.target.id === "global-search" && event.key === "Enter") { state.query = event.target.value; state.group = "열린 요청"; state.pageNo = 1; navigate(null); if (state.page === "list") render(); } });
document.querySelector('[data-action="close-help"]').addEventListener("click", () => document.querySelector("#help-dialog").close());
document.querySelector("#close-rate-card").addEventListener("click", () => document.querySelector("#rate-card-dialog").close());
window.addEventListener("hashchange", route);

try {
  const [inputResponse, previewResponse, sampleResponse] = await Promise.all([fetch(new URL("./data/agent3-input.json?v=common-rate-2", import.meta.url)), fetch(new URL("./data/agent3-preview.json", import.meta.url)), fetch(new URL("./data/agent3-samples.json?v=common-rate-2", import.meta.url))]);
  if (!inputResponse.ok || !previewResponse.ok || !sampleResponse.ok) throw new Error("합성 입력 파일을 불러오지 못했습니다.");
  baseInput = await inputResponse.json(); samples = await sampleResponse.json(); samples.inputs["CR-A-001"] = baseInput; previewClient = new MockPreview(await previewResponse.json()); state.inputText = JSON.stringify(baseInput, null, 2);
  try { const response = await fetch("/prototype-lab/defaults"); if (response.ok) { const defaults = await response.json(); if (defaults.fake_response && defaults.model) { lab.enabled = true; api.lab = true; lab.defaultFake = defaults.fake_response; lab.fakeText = JSON.stringify(defaults.fake_response, null, 2); if (defaults.default_models) Object.assign(defaultModels, defaults.default_models); if (Array.isArray(defaults.ollama_models)) ollamaModels = defaults.ollama_models.filter(Boolean); lab.model = defaultModel(lab.provider); try { const response = await fetch("/prototype-lab/baselines"); const data = await response.json(); if (response.ok && Array.isArray(data)) availableBaselines = data; } catch { /* 기준선이 없으면 Agent 1 연결을 차단한다. */ } } } } catch { /* Ordinary server retains the existing API mode. */ }
  try { const a2Response = await fetch(new URL("./data/agent2-inputs.json", import.meta.url)); if (a2Response.ok) agent2Inputs = await a2Response.json(); }
  catch { /* Agent 2 입력이 없어도 나머지 화면은 동작한다 (실행 버튼만 비활성). */ }
  try { const a1Response = await fetch(new URL("./data/agent1-inputs.json", import.meta.url)); if (a1Response.ok) agent1Inputs = await a1Response.json(); }
  catch { /* Agent 1 입력이 없어도 다른 화면은 동작한다. */ }
  selectCase("CR-A-001"); if (!location.hash) location.hash = "#/analysis"; route(); if (state.page !== "analysis" && !record().analysisDemo) await restore();
  if (lab.enabled) loadKb();
} catch (error) {
  root.innerHTML = `<main class="content"><h1>화면을 준비하지 못했습니다.</h1><p style="margin-top:16px">${e(error.message)}</p><p>로컬 서버와 prototype/data 파일을 확인해 주세요.</p></main>`;
}
