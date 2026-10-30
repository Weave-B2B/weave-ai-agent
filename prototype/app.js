import { Agent1Api, Agent2Api, Agent3Api, ApiError, MockPreview } from "./api.js";
import { records, sampleItems, scenarioInput } from "./fixtures.js";

const root = document.querySelector("#app");
const api = new Agent3Api();
const agent2 = new Agent2Api();
const agent1 = new Agent1Api();
const STORAGE = "weave-prototype-agent3-session-v1";
const PHASE = { WAITING_IMPACT_APPROVAL: "PM 산정 검토 대기", WAITING_INPUT: "추가 정보 필요", WAITING_UPSTREAM_REVIEW: "상위 PM 검토 필요", DRAFT_READY: "내부 협의안 초안 준비", NOT_REQUIRED: "산정 대상 없음", FAILED: "산정 실패" };
const state = { page: "list", id: "CR-A-001", tab: "original", query: "", group: "열린 요청", filter: "전체", project: "전체", order: "기본", pageNo: 1, selectedItem: 0, mode: "api", scenario: "normal", pending: false, error: null, view: null, inputText: "", submittedInput: null, signature: null, edits: null, originalLines: [], conflicted: false, invalidated: false, ack: false, reviewer: "local-test-pm", reviewReason: "", editReason: "" };
let baseInput, agent1Inputs, agent2Input, agent2Inputs, previewClient, toastTimer, composingSearch = false, samples, activeCase;
const lab = { enabled: false, provider: "fake", model: "gemini-2.5-flash-lite", base_url: "http://127.0.0.1:11434/v1", region: "ap-northeast-2", fakeText: "" };
const e = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const number = value => typeof value === "number" && Number.isFinite(value) ? value.toLocaleString("ko-KR", { maximumFractionDigits: 1 }) : "—";
const money = value => typeof value === "number" && Number.isFinite(value) ? `${number(value)}원` : "미산정";
const icon = file => `<span class="icon"><img src="./assets/${file}.svg" alt=""></span>`;
const chip = (label, tone = "neutral") => `<span class="chip ${tone}">${icon(tone === "ai" ? "1b8e7" : tone === "success" ? "396d8" : tone === "info" ? "59318" : tone === "warning" ? "691d6" : "edb5c")}<span>${e(label)}</span></span>`;
const button = (label, action, options = {}) => `<button type="button" class="button ${options.class || ""}" data-action="${action}" ${options.disabled ? "disabled" : ""} ${options.title ? `title="${e(options.title)}"` : ""}>${options.icon ? icon(options.icon) : ""}${e(label)}</button>`;
const record = () => records.find(item => item.id === state.id) || records[0];
const estimates = () => record().api ? state.view?.impact?.estimates || [] : [];
const unknownSchedule = () => estimates().some(item => item.schedule?.business_days == null);
const parsedInput = () => JSON.parse(state.inputText);
function inputChanged() { try { return !!state.view && JSON.stringify(parsedInput()) !== state.signature; } catch { return !!state.view; } }
function editsChanged() { return !!state.view && JSON.stringify(state.edits) !== JSON.stringify(estimates().map(item => ({ item_ids: item.item_ids, lines: item.lines }))); }
function safeCurrent() { return state.mode === "api" && record().api && !state.pending && !state.conflicted && !state.invalidated && !inputChanged(); }
function canApprove() { return safeCurrent() && !editsChanged() && state.view?.phase === "WAITING_IMPACT_APPROVAL" && estimates().length > 0 && estimates().every(item => item.status === "draft" && item.totals) && !(state.view.impact.questions || []).some(item => item.blocking && ["open", "answered"].includes(item.status)); }
function canEdit() { return safeCurrent() && estimates().length > 0 && estimates().every(item => item.lines?.length); }
function currentTotals() {
  const rows = estimates();
  if (!rows.length || rows.some(item => !item.totals)) return null;
  const totals = rows.reduce((sum, item) => ({ md: sum.md + item.totals.md, cost: sum.cost + item.totals.cost, price: sum.price + item.totals.price }), { md: 0, cost: 0, price: 0 });
  return totals;
}
function toast(message) { const el = document.querySelector("#toast"); el.textContent = message; el.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, 4500); }
function persist() {
  if (state.mode !== "api" || !state.view) return;
  try { sessionStorage.setItem(STORAGE, JSON.stringify({ sessionId: state.view.session_id, inputText: state.inputText, submittedInput: state.submittedInput, signature: state.signature, originalLines: state.originalLines, invalidated: state.invalidated, labSettings: lab.enabled ? { provider: lab.provider, model: lab.model, base_url: lab.base_url, region: lab.region, fakeText: lab.fakeText } : null })); } catch { /* The UI still works when browser storage is unavailable. */ }
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
  const match = location.hash.match(/^#\/request\/([^/]+)\/(original|scope|impact|draft)$/);
  if (match && records.some(item => (item.api || item.datasetSample || item.id === "CR-001") && item.id === decodeURIComponent(match[1]))) {
    state.id = decodeURIComponent(match[1]); state.tab = match[2]; state.page = "detail";
    if (record().api && samples && activeCase !== state.id) selectCase(state.id);
  }
  else state.page = "list";
  render(); window.scrollTo({ top: 0, behavior: "instant" });
}

function selectCase(id) {
  activeCase = id;
  agent2Input = agent2Inputs?.[id] ? structuredClone(agent2Inputs[id]) : null;
  a2.generation++; a2.result = null; a2.pending = false; a2.error = null;
  a1.generation++; a1.result = null; a1.pending = false; a1.error = null;
  state.selectedItem = 0;
  baseInput = structuredClone(samples.inputs[id]);
  state.scenario = "normal"; state.inputText = JSON.stringify(baseInput, null, 2);
  state.view = null; state.edits = null; state.submittedInput = null; state.signature = null;
  state.error = null; state.conflicted = false; state.invalidated = false; state.originalLines = [];
  if (id !== "CR-A-001") state.mode = "api";
  if (lab.enabled) lab.fakeText = JSON.stringify(samples.fake_responses[id] || lab.defaultFake, null, 2);
}

function sidebar() {
  const nav = [["대시보드", "1d0b4"], ["프로젝트", "7626c"], ["변경 요청", "f8eec", records.length], ["승인 대기", "bfedc", 3], ["청구", "e6313", 2]];
  return `<aside class="sidebar" aria-label="주 메뉴"><div class="brand"><span class="brand-mark">S</span><span class="brand-name">Scopeline</span><span class="mini-tag">MVP</span></div><div class="workspace"><span class="workspace-mark">한</span><div class="workspace-copy">한빛디지털<small>수행사 워크스페이스</small></div>${icon("b05aa")}</div><div class="nav-section">업무</div>${nav.map(([label, file, count]) => `<button class="nav ${label === "변경 요청" ? "active" : ""}" ${label === "변경 요청" ? 'data-action="list" aria-current="page"' : 'disabled title="이번 구현 범위에 포함되지 않습니다"'}>${icon(file)}<span class="nav-label">${label}</span>${count ? `<span class="count">${count}</span>` : ""}</button>`).join("")}<div class="nav-section">관리</div><button class="nav" disabled title="이번 구현 범위 밖">${icon("ed47e")}<span class="nav-label">설정</span></button><div class="sidebar-bottom"><div class="disclaimer"><strong>${icon("6bb27")}AI는 제안까지만</strong>범위 확정·가격·승인·고객 발송은 담당자가 결정합니다.</div><div class="profile"><span class="avatar large">김</span><div class="profile-copy">김서윤<small>로컬 확인 · 테스트 PM</small></div></div></div></aside>`;
}
function topbar() {
  const path = state.page === "list" ? "변경 요청" : `변경 요청 / ${record().id} / ${{ original: "요청 원문", scope: "범위 검토", impact: "영향 산정", draft: "승인·협의" }[state.tab]}`;
  return `<header class="topbar"><span class="breadcrumb">${e(path)}</span><label class="top-search">${icon("776e1")}<input id="global-search" placeholder="CR ID, 프로젝트, 문서 검색" aria-label="전체 요청 검색"></label><button class="icon-button" data-action="help" aria-label="로컬 확인 안내">${icon("c360b")}</button><button class="icon-button" data-action="help" aria-label="도움말">${icon("a340e")}</button><span class="top-separator"></span><span class="avatar large">김</span></header>`;
}
function localBar() {
  const model = record().api && state.view?.impact?.meta?.model;
  const modelLabel = state.mode === "mock" ? "가상 상태 미리보기" : typeof model === "string" && model ? `API 응답 ${model}` : "응답 정보 없음 · 서버 설정은 확인 불가";
  return `<div class="local-bar"><strong>로컬 확인용</strong><span>목록·원문·범위: 합성 예시</span><span>모델: ${e(modelLabel)}</span><span class="spacer"></span><label class="mode-control">연결<select id="mode" aria-label="화면 연결 모드" ${state.pending ? "disabled" : ""}><option value="api" ${state.mode === "api" ? "selected" : ""}>기존 Agent 3 API</option>${record().id === "CR-A-001" ? `<option value="mock" ${state.mode === "mock" ? "selected" : ""}>상태 미리보기 · 읽기 전용</option>` : ""}</select></label>${button("안내", "help", { class: "small" })}</div>`;
}
function filteredRecords() {
  let result = records.filter(item => `${item.id} ${item.title} ${item.project} ${item.requester}`.toLowerCase().includes(state.query.toLowerCase()));
  if (state.filter !== "전체") result = result.filter(item => item.scope.includes(state.filter));
  if (state.project !== "전체") result = result.filter(item => item.project.includes(state.project));
  if (state.group !== "열린 요청") result = result.filter(item => item.process === state.group || (state.group === "완료·종료" && item.process === "완료"));
  if (state.order === "최근 접수순") result.sort((a, b) => Number(!a.api) - Number(!b.api) || b.date.localeCompare(a.date));
  return result;
}
function inbox() {
  const result = filteredRecords(), pages = Math.max(1, Math.ceil(result.length / 10));
  state.pageNo = Math.min(state.pageNo, pages);
  const shown = result.slice((state.pageNo - 1) * 10, state.pageNo * 10);
  const groups = ["열린 요청", "PM 검토 필요", "내부 승인 대기", "고객 협의 중", "실행 중", "완료·종료"];
  return `<div class="page-heading"><div><h1>변경 요청</h1><p class="subtitle">전체 프로젝트 · 합성 요청 ${records.length}건 · Agent 1 테스트 30건 포함</p></div><div class="button-group">${button("내보내기", "unused", { disabled: true, icon: "e95f2", title: "업무 내보내기는 이번 범위 밖" })}${button("변경 요청 등록", "unused", { class: "primary", disabled: true, icon: "416f8", title: "이번에는 기존 합성 요청을 조회합니다" })}</div></div>${localBar()}<div class="tabs" role="tablist" aria-label="요청 처리 상태">${groups.map(group => `<button class="tab" role="tab" aria-selected="${state.group === group}" data-group="${group}">${group}<small>${group === "열린 요청" ? records.length : records.filter(item => item.process === group).length}</small></button>`).join("")}</div><div class="filters"><label class="search-field">${icon("776e1")}<input id="request-search" placeholder="CR ID, 제목, 요청자 검색" aria-label="요청 검색" value="${e(state.query)}"></label><label class="filter">프로젝트<select id="project-filter" aria-label="프로젝트 필터">${["전체", ...new Set(records.map(item => item.project))].map(value => `<option ${state.project === value ? "selected" : ""}>${e(value)}</option>`).join("")}</select></label><label class="filter">범위 판단<select id="scope-filter" aria-label="범위 판단 필터">${["전체", "범위 밖", "판단 보류", "미분석"].map(value => `<option ${state.filter === value ? "selected" : ""}>${value}</option>`).join("")}</select></label><span class="filter">상업적 결정 · 예시</span><label class="filter last">정렬<select id="sort" aria-label="요청 정렬"><option ${state.order === "기본" ? "selected" : ""}>기본</option><option ${state.order === "최근 접수순" ? "selected" : ""}>최근 접수순</option></select></label></div><div class="table-wrap"><table class="inbox-table"><thead><tr>${["CR ID", "요청 · 프로젝트", "요청자 · 채널", "접수일", "범위 판단", "요청 처리", "내부 승인", "고객 협의", "영향 (MD · 금액)", "담당", "경과"].map(title => `<th>${title}</th>`).join("")}</tr></thead><tbody>${shown.length ? shown.map(item => `<tr><td class="id-cell"><button class="row-open" data-open="${item.id}">${item.id}${item.api ? '<span class="api-marker">API 연결 샘플</span>' : ""}</button></td><td class="title-cell"><button class="row-open" data-open="${item.id}"><strong>${e(item.title)}</strong><small>${e(item.project)}</small></button></td><td>${e(item.requester)}<small>${item.channel}</small></td><td>${item.date}</td><td>${chip(item.scope, item.scope.includes("가능성") ? "ai" : "neutral")}</td><td>${chip(item.process, "info")}</td><td>${chip(item.approval)}</td><td>${chip(item.customer)}</td><td class="numeric">${e(item.md)}<small>${e(item.price)}</small></td><td><span class="avatar">${item.owner}</span></td><td class="numeric">${item.elapsed}</td></tr>`).join("") : '<tr><td colspan="11"><div class="empty-state"><h3>검색 결과가 없습니다</h3><p>검색어나 필터를 바꿔 주세요.</p></div></td></tr>'}</tbody></table></div><div class="pagination"><span>${shown.length} / ${result.length}건 표시 · 범위 밖은 유상 확정을 의미하지 않습니다.</span><div class="button-group">${button("이전", "previous", { disabled: state.pageNo <= 1 })}<span>${state.pageNo} / ${pages}</span>${button("다음", "next", { disabled: state.pageNo >= pages })}</div></div>`;
}
function statusStrip() {
  const actual = record().api && state.view;
  const cells = [["요청 처리", "PM 검토 필요", "합성 입력"], ["범위 판단", "범위 밖", "상위 PM 검토: 예시"], ["상업적 결정", "미결정", "이번 범위 밖"], ["PM 산정 승인", actual ? state.view.approval?.approved ? "승인됨" : state.view.approval ? "반려 · 재검토" : "미승인" : "미요청", actual ? PHASE[state.view.phase] : "API 결과 없음"], ["고객 협의", "미요청", "외부 발송 없음"], ["실행", "미착수", "이번 범위 밖"], ["청구", "청구 대상 아님", "자동 확정 없음"]];
  return `<div class="status-strip">${cells.map(([label, value, sub]) => `<div class="status-cell"><span>${label}</span>${chip(value)}<small>${e(sub)}</small></div>`).join("")}</div>`;
}
function detail() {
  const item = record();
  if (item.datasetSample) {
    const tabs = [["original", "요청 원문"], ["scope", "범위 검토"], ["impact", "영향 산정"], ["draft", "승인·협의"]];
    const actions = state.tab === "impact" ? button(state.pending ? "처리 중…" : "영향 산정 실행", "prepare", { class: "primary", disabled: state.pending }) : button("영향 산정으로", "go-impact", { class: "primary" });
    return `<div class="page-heading"><div><h1 class="detail-title"><span class="cr-id">${e(item.id)}</span>${e(item.title)}</h1><p class="subtitle">${e(item.project)} · 에이전트 테스트 요청</p></div><div class="button-group">${button("목록", "list", { class: "plain" })}${actions}</div></div>${localBar()}<div class="notice"><strong>독립 테스트</strong>Agent 1·2를 각각 실행할 수 있습니다. Agent 2는 저장된 Agent 1 항목을, Agent 3은 별도의 합성 Agent 2·PM 검토 결과를 사용합니다.</div><div class="tabs" role="tablist" aria-label="변경 요청 상세 단계">${tabs.map(([key, label]) => `<button class="tab" role="tab" aria-selected="${state.tab === key}" data-tab="${key}">${label}</button>`).join("")}</div>${state.error ? errorBanner() : ""}${state.tab === "original" ? agent1Panel() + datasetOriginal(item) : state.tab === "scope" ? scope() : state.tab === "impact" ? impact() : draft()}`;
  }
  const tabs = [["original", "요청 원문"], ["scope", "범위 검토"], ["impact", "영향 산정"], ["draft", "승인·협의"]];
  let actions = state.tab === "original" ? button("범위 검토 시작", "go-scope", { class: "primary", icon: "ec151" }) : state.tab === "scope" ? button("영향 산정으로", "go-impact", { class: "primary", icon: "ec151" }) : state.tab === "impact" ? button(state.pending ? "처리 중…" : "영향 산정 실행", "prepare", { class: "primary", icon: "a1cb9", disabled: state.pending || !item.api }) : button("영향 산정으로", "go-impact", { icon: "ec991" });
  return `<div class="page-heading"><div><h1 class="detail-title"><span class="cr-id">${e(item.id)}</span>${e(item.title)}</h1></div><div class="button-group">${button("목록", "list", { class: "plain" })}${actions}</div></div><div class="detail-meta"><span>${icon("91f0e")}${e(item.project)}</span><span>${icon("e56db")}요청자 ${e(item.requester)} (고객 PM)</span><span>${icon("29cce")}${item.channel} · ${item.date} 수신</span><span>${icon("d6187")}판단 기준선 ${item.api ? "BL-2 · 합성" : "v1.2 · 예시"}</span><span><span class="avatar">김</span>담당 김서윤</span></div>${localBar()}${!item.api ? `<div class="notice"><strong>조회용 예시 요청입니다.</strong>이 요청은 화면 흐름만 확인할 수 있습니다. 실제 Agent 3 연결은 ${button("CR-A-001 열기", "open-api", { class: "small" })}에서 확인해 주세요.</div>` : ""}${statusStrip()}<div class="tabs" role="tablist" aria-label="변경 요청 상세 단계">${tabs.map(([key, label]) => `<button class="tab" role="tab" aria-selected="${state.tab === key}" data-tab="${key}">${label}</button>`).join("")}${["실행·검수", "청구", "이력"].map(label => `<button class="tab" role="tab" aria-selected="false" disabled title="이번 구현 범위 밖">${label}</button>`).join("")}</div>${state.error ? errorBanner() : ""}${state.tab === "original" ? (item.api ? agent1Panel() : "") + original() : state.tab === "scope" ? scope() : state.tab === "impact" ? impact() : draft()}`;
}
function datasetOriginal(item) {
  return `<div class="columns"><section class="card"><div class="card-heading"><div><h2>요청 원문</h2><p class="caption">Agent 1 테스트 데이터의 합성 요청입니다.</p></div></div><div class="metadata-box"><dl><dt>보낸 사람</dt><dd>${e(item.requester)} · ${e(item.requesterRole)}</dd><dt>수신 일시</dt><dd>${e(item.receivedAt)}</dd><dt>채널</dt><dd>${e(item.channel)}</dd></dl></div><div class="original-text"><strong>제목: ${e(item.title)}</strong><p style="white-space:pre-wrap">${e(item.messageText)}</p></div></section><section class="card"><h2>상위 합성 요청 항목</h2><p class="caption">Agent 1을 실행하지 않고 준비된 예시를 표시합니다.</p>${baseInput.items.map(x => `<div class="list-item"><span class="item-no">${e(x.display_no)}</span><span>${e(x.text)}</span></div>`).join("")}</section></div>`;
}
function datasetScope() {
  const labels = { in_scope: "범위 내", out_of_scope: "범위 밖", unclear: "판단 보류" };
  return `<section class="card"><h2>상위 범위 검토 · 합성 입력</h2><p class="caption">저장된 Agent 2 및 PM 검토 예시입니다. 이 화면은 해당 에이전트를 실행하지 않습니다.</p>${baseInput.a2_result.assessments.map(a => { const item = baseInput.items.find(x => x.item_id === a.item_id); const review = baseInput.pm_review.items.find(x => x.item_id === a.item_id); return `<div class="question"><strong>${e(item?.display_no)} · ${e(item?.text)}</strong><span>AI 예시: ${e(labels[a.label] || a.label)} · PM 예시: ${e(labels[review?.pm_label] || review?.pm_label)} · 산정 대상: ${review?.estimate ? "선택" : "아님"}</span><span class="muted">${e(a.rationale)}</span></div>`; }).join("")}${button("영향 산정으로", "go-impact", { class: "primary" })}</section>`;
}
function original() {
  const apiCase = record().api;
  return `<div class="columns"><div class="stack"><section class="card"><div class="card-heading"><div><h2>요청 원문</h2><p class="caption">원문은 조회용 합성 데이터이며 실제 수신함과 연결되지 않습니다.</p></div><span class="chip subtle">${icon("83533")}원문 보존</span></div><div class="metadata-box"><dl><dt>보낸 사람</dt><dd>${e(record().requester)} (고객 PM)</dd><dt>받는 사람</dt><dd>김서윤 (한빛디지털 · PM)</dd><dt>수신 일시</dt><dd>2026-${record().date} 09:12</dd><dt>채널</dt><dd>${record().channel} · 원문 예시</dd><dt>첨부</dt><dd>${icon("7502b")}첨부 업로드는 이번 범위 밖</dd></dl></div><div class="original-text"><strong>제목: [자사몰] 상품 리뷰 기능 요청드립니다</strong><p>안녕하세요. 지난 주간 회의에서 말씀드린 리뷰 기능을 정리해서 보내드립니다.</p>${(apiCase ? ["상세페이지에 구매자 별점과 후기 텍스트를 넣어 주세요.", "가능하면 사진도 3장까지 올리게 해주세요.", "변경 합의된 11월 24일 오픈일은 유지해야 합니다."] : ["상품 상세 페이지에서 별점(5점) 리뷰를 작성할 수 있게 해 주세요.", "텍스트 리뷰도 함께 작성·조회가 되어야 합니다.", "가능하면 사진도 여러 장 첨부할 수 있으면 좋겠습니다.", "리뷰는 실제 구매한 고객만 작성할 수 있어야 합니다.", "오픈 일정은 그대로 유지 부탁드립니다."]).map((line, i) => `<div class="highlight"><span class="number">${i + 1}</span><span>${line}</span></div>`).join("")}<p>검토 후 회신 부탁드립니다. 감사합니다.</p></div><div class="card-footer">${icon("2b0e7")}회색 하이라이트 = 항목으로 추출한 원문 위치</div></section><section class="card"><div class="card-heading"><div><h2>PM 메모</h2><p class="caption">실제 저장 기능은 연결 전입니다.</p></div>${button("메모 추가", "unused", { disabled: true, icon: "416f8" })}</div><p class="caption"><span class="avatar">김</span> 김서윤 · 로컬 예시</p><p style="margin-top:10px">사진 포함 여부와 기존 저장소 재사용 가정을 확인해야 합니다. 업무 확정·고객 전달은 별도 담당자가 결정합니다.</p></section></div><div class="stack"><section class="card"><div class="card-heading"><div><h2>${icon("2b0e7")} AI 요청 항목화</h2><p class="caption">상위 Agent 1·2 결과의 합성 입력</p></div>${chip("AI 제안", "ai")}</div>${(apiCase ? [["R1", "기능", baseInput.items[0].text], ["R2", "기한", baseInput.constraints[0].text]] : sampleItems).map(item => `<div class="list-item"><span class="item-no">${item[0]}</span><span class="mini-tag">${item[1]}</span><div class="item-main"><strong>${e(item[2])}</strong><small>${icon("e0393")}원문 연결 · 합성 근거</small></div>${icon("e7746")}</div>`).join("")}<div class="card-footer">${button("항목 추가", "unused", { disabled: true, icon: "416f8" })}${button("AI 재분석", "unused", { disabled: true, icon: "e4529" })}</div></section><section class="card"><h2>확인 질문</h2><p class="caption">이번에는 질문·답변의 실제 저장과 A1 재분석을 연결하지 않습니다.</p><div class="question"><strong>Q1 · 기존 이미지 저장소를 재사용할 수 있나요?</strong><span class="muted">저장 방식에 따라 공수가 달라질 수 있습니다.</span></div><div class="question"><strong>Q2 · 사진 첨부는 이번 변경에 포함되나요?</strong><span class="muted">현재 입력은 사진 최대 3장을 포함한 합성 가정입니다.</span></div></section></div></div>`;
}
// ---- C04 범위 검토: Agent 2 실제 결과 (POST /agent2/assessments). 실행 전·조회 예시는 기존 합성 화면(scopeSample) ----
const a1 = { result: null, pending: false, error: null, generation: 0, provider: 'fake', model: 'gemini-2.5-flash-lite', region: 'ap-northeast-2' };
const a2 = { result: null, pending: false, error: null, generation: 0, provider: 'fake', model: 'gemini-2.5-flash-lite', region: 'ap-northeast-2' };
function agentSettings(name, data) {
  if (!lab.enabled) return '<div class="notice">공급자를 선택하려면 서버를 <code>python prototype/run_demo.py --lab</code>로 실행하세요.</div>';
  return `<section class="card"><h2>${name === 'a1' ? 'Agent 1' : 'Agent 2'} 테스트 실행 설정</h2><p class="caption">가상 응답은 저장된 합성 사례를 반환합니다. Gemini·Bedrock은 외부 모델을 호출하며 인증 정보는 서버에서 사용합니다.</p><div class="button-group">${[['fake','가상 응답'],['gemini','Gemini API'],['bedrock','AWS Bedrock']].map(([value,label]) => `<label class="check"><input type="radio" name="${name}-provider" value="${value}" ${data.provider === value ? 'checked' : ''} ${data.pending ? 'disabled' : ''}>${label}</label>`).join('')}</div>${data.provider !== 'fake' ? `<label class="field">모델 ID<input id="${name}-model" value="${e(data.model)}" ${data.pending ? 'disabled' : ''}></label>` : ''}${data.provider === 'bedrock' ? `<label class="field">AWS 리전<input id="${name}-region" value="${e(data.region)}" ${data.pending ? 'disabled' : ''}></label>` : ''}</section>`;
}
async function runAgent1() {
  if (!lab.enabled || a1.pending || !agent1Inputs?.[activeCase]) return;
  const caseId = activeCase, generation = ++a1.generation;
  const input = structuredClone(agent1Inputs[caseId]);
  const config = { provider: a1.provider, model: a1.model, region: a1.region };
  a1.pending = true; a1.error = null; a1.result = null; render();
  try {
    const result = await agent1.run(input, config);
    if (a1.generation !== generation || activeCase !== caseId) return;
    if (result.context?.case_id !== caseId || result.context?.project_id !== input.context.project_id) throw new Error('다른 요청의 Agent 1 결과가 반환되었습니다.');
    a1.result = result; toast('Agent 1 요청 항목화 결과를 받았습니다.');
  } catch (error) { if (a1.generation === generation && activeCase === caseId) a1.error = { status: error.status || 0, message: error.message }; }
  finally { if (a1.generation === generation && activeCase === caseId) { a1.pending = false; render(); } }
}
function agent1Panel() {
  const error = a1.error ? `<div class="notice error">${e(a1.error.message)}</div>` : '';
  const result = a1.result ? `<section class="card"><h2>Agent 1 실행 결과</h2><p class="caption">${e(a1.result.meta.model || a1.result.meta.agent_version || 'fixture')} · ${e(a1.result.status)}</p>${a1.result.items.map(item => `<div class="list-item"><span class="item-no">${e(item.display_no)}</span><span>${e(item.text)}</span></div>`).join('')}${(a1.result.questions || []).map(q => `<div class="question"><strong>${e(q.text)}</strong><span>${e(q.reason)}</span></div>`).join('')}<p class="caption">이 결과는 Agent 2에 자동 전달되지 않습니다.</p></section>` : '';
  return `${agentSettings('a1', a1)}<div class="form-actions">${button(a1.pending ? '요청 항목화 중…' : 'Agent 1 요청 항목화 실행', 'run-a1', { class: 'primary', disabled: a1.pending || !lab.enabled || !agent1Inputs?.[activeCase] })}</div>${error}${result}`;
}
const A2_LABEL = { in_scope: ["기존 범위 내", "success"], out_of_scope: ["범위 밖 가능성", "ai"], unclear: ["판단 보류", "warning"], approved_change: ["승인된 변경 (재청구 안 함)", "info"] };
const A2_LEVEL = { strong: "강", medium: "중", none: "근거 없음", interpretation_needed: "해석 필요" };
const A2_ROLE = { basis: "직접 근거", policy: "절차·원칙", superseding: "우선 문서", context: "참고" };
const DOC_NAME = { CTR: "계약서", SOW: "과업지시서", FSP: "기능명세서", QUO: "견적서", CO: "변경합의서" };
const docName = id => DOC_NAME[String(id).split("-")[0]] || "문서";
async function runAgent2() {
  if (a2.pending || !agent2Input) return;
  const caseId = activeCase, generation = ++a2.generation;
  const input = structuredClone(agent2Input);
  a2.pending = true; a2.error = null; a2.result = null; render();
  try {
    const config = lab.enabled ? { provider: a2.provider, model: a2.model, region: a2.region } : null;
    const result = await agent2.assess(input, config);
    if (a2.generation !== generation || activeCase !== caseId) return;
    if (result.context?.case_id !== caseId || result.context?.project_id !== input.context.project_id) throw new Error("다른 요청의 Agent 2 결과가 반환되었습니다.");
    a2.result = result; state.selectedItem = 0; toast("Agent 2 범위 판단 결과를 받았습니다.");
  }
  catch (error) { if (a2.generation === generation && activeCase === caseId) a2.error = { status: error instanceof ApiError ? error.status : 0, message: error.message }; }
  finally { if (a2.generation === generation && activeCase === caseId) { a2.pending = false; render(); } }
}
function agent2Bar() {
  const label = a2.pending ? "범위 판단 중… (최대 1~2분)" : a2.result ? "다시 실행" : "AI 범위 판단 실행";
  const status = a2.result ? `${chip(a2.result.status === "NEEDS_INPUT" ? "추가 정보 필요" : "PM 검토 대기", a2.result.status === "NEEDS_INPUT" ? "warning" : "info")}<span class="tiny">결과 ${e(a2.result.meta.result_id)} · ${e(a2.result.meta.model || a2.result.meta.agent_version || "서버 설정 확인")}</span>` : '<span class="muted">아래는 실행 전 합성 결과입니다. 실행 방식은 서버의 Agent 2 설정에 따릅니다.</span>';
  const failure = a2.error ? `<div class="notice inline"><strong>Agent 2 실행 실패${a2.error.status ? ` (HTTP ${a2.error.status})` : ""}</strong>${e(a2.error.message)}</div>` : "";
  return `${agentSettings('a2', a2)}<div class="baseline">${icon("2b0e7")}<strong>Agent 2 범위 판단</strong>${status}${button(label, "run-a2", { class: "primary small", icon: "a1cb9", disabled: a2.pending || !agent2Input })}</div>${failure}`;
}
function scope() {
  if (!record().api) return scopeSample();
  if (!a2.result) return agent2Bar() + (record().datasetSample ? datasetScope() : scopeSample());
  const out = a2.result, rows = out.assessments;
  const meta = Object.fromEntries([...(agent2Input.items || []), ...(agent2Input.constraints || [])].map(item => [item.item_id, item]));
  state.selectedItem = Math.min(state.selectedItem, Math.max(rows.length - 1, 0));
  const sel = rows[state.selectedItem];
  if (!sel) return agent2Bar() + '<section class="card"><div class="empty-state"><h3>판단할 기능 항목이 없습니다.</h3></div></section>';
  const label = item => A2_LABEL[item.label] || [item.label, "neutral"];
  const questions = (out.questions || []).filter(q => q.item_ids.includes(sel.item_id));
  const steps = out.meta.debug?.steps?.[sel.item_id] || [];
  const step = s => s.action === "search_docs" ? `검색 "${e(s.query)}" → ${e((s.hits || []).join(", ") || "결과 없음")}` : s.action === "get_section" ? `조항 열람 ${e(s.read || s.note || s.error || `${s.doc_id}:${s.section_id}`)}` : s.rejected ? `제출 되돌림 — ${e((s.gaps || []).join(" / "))}` : s.action === "submit_judgment" ? "판단 제출" : e(s.action);
  const list = (title, values) => values?.length ? `<h3 style="margin-top:16px">${title}</h3>${values.map(v => `<p class="caption">· ${e(v)}</p>`).join("")}` : "";
  return `${agent2Bar()}<div class="scope-grid"><div><section class="card"><div class="card-heading"><div><h2>항목별 범위 판단</h2><p class="caption">AI 제안과 PM 판단은 분리합니다. 아래는 Agent 2 API 응답입니다. fixture 또는 LLM 사용 여부는 서버 설정과 결과 정보를 확인하세요.</p></div><span class="mini-tag">전체 ${rows.length}</span></div><div class="table-wrap"><table class="scope-table"><thead><tr><th>요청 항목</th><th>AI 제안</th><th>근거 수준</th><th>PM 판단</th><th>근거</th></tr></thead><tbody>${rows.map((item, index) => `<tr class="${index === state.selectedItem ? "selected" : ""}"><td><button class="scope-select" data-item="${index}">${e(meta[item.item_id]?.display_no || item.item_id)} ${e(meta[item.item_id]?.text || "")}<small>${e(item.item_id)}</small></button></td><td>${chip(...label(item))}</td><td>${e(A2_LEVEL[item.evidence_level] || "—")}</td><td>${chip("미확정")}</td><td>${item.evidence.length}</td></tr>`).join("")}</tbody></table></div></section><div class="scope-evidence"><section class="card"><h2>고객 요청 · ${e(meta[sel.item_id]?.display_no || sel.item_id)}</h2><p class="caption">원문 위치</p>${(meta[sel.item_id]?.spans || []).map(span => `<div class="quote">${e(span.text)}</div>`).join("") || '<div class="quote">원문 구간 없음</div>'}<div class="notice inline">원문에 없는 요구는 AI가 추가하지 않습니다.</div></section><section class="stack">${sel.evidence.length ? sel.evidence.map(ev => `<article class="card"><h3>${icon("f3e38")} ${docName(ev.doc_id)} <span class="mini-tag">v${e(ev.doc_version)}</span> <span class="mini-tag">${e(ev.section_id)}</span></h3><p class="tiny">${e(ev.doc_id)} · ${ev.page ? `p.${ev.page} · ` : ""}${ev.line_start ?? "—"}~${ev.line_end ?? "—"}행 · ${e(A2_ROLE[ev.role] || ev.role)}</p><div class="quote">${e(ev.quote)}</div>${chip(ev.verified ? "원문 인용 검증됨" : "인용 미검증", ev.verified ? "success" : "warning")}</article>`).join("") : '<article class="card"><h3>근거 없음</h3><p class="caption">검증된 근거가 없어 판단을 보류했습니다. 근거 없음은 범위 밖을 뜻하지 않습니다.</p></article>'}</section></div></div><aside class="card"><div class="card-heading"><h2>판단 · ${e(meta[sel.item_id]?.display_no || sel.item_id)}</h2><span class="tiny">${state.selectedItem + 1} / ${rows.length}</span></div><div class="reason-box"><h3>${icon("2b0e7")} AI 제안 · ${e(label(sel)[0])}</h3><p>${e(sel.rationale)}</p>${list("부족한 정보", sel.missing_info)}${list("불확실성", sel.uncertainties)}</div>${questions.length ? `<h3 style="margin-top:18px">확인 질문</h3>${questions.map(q => `<div class="question"><strong>${e(q.text)}</strong><span class="muted">${q.target === "client" ? "고객" : "PM"} 대상 · ${q.blocking ? "답변 전 진행 불가" : "진행 가능(참고)"} · ${e(q.reason)}</span></div>`).join("")}` : ""}${steps.length ? `<details style="margin-top:18px"><summary>에이전트 조사 과정 (${steps.length}단계)</summary>${steps.map((s, i) => `<p class="tiny">${i + 1}. ${step(s)}</p>`).join("")}</details>` : ""}<div class="notice inline"><strong>PM 판단 · 산정 대상 선택</strong>PM 범위 확정 API는 아직 연결 전입니다. 영향 산정(Agent 3)은 기존 합성 A2 결과를 입력으로 사용합니다.</div><div class="form-actions">${button("판단 확정", "unused", { disabled: true, title: "실제 PM 범위 확정 API는 이번 범위 밖" })}${button("영향 산정으로", "go-impact", { class: "primary", icon: "ec151" })}</div></aside></div>`;
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
  if (!record().api) return '<div class="notice">이 요청은 조회용 예시입니다. CR-A-001에서 실제 API 연결을 확인할 수 있습니다.</div>';
  if (!state.view) return `<div class="notice"><strong>영향 산정을 실행해 주세요.</strong>상위 입력은 합성 예시입니다. 기존 API의 계산·수정·PM 승인 흐름을 확인할 수 있습니다.</div>`;
  let text = { WAITING_IMPACT_APPROVAL: "작업·공수·가정과 미산정 일정을 확인한 뒤 PM 산정 승인을 진행할 수 있습니다. 협의안은 아직 없습니다.", WAITING_INPUT: "필수 질문이나 누락된 정보를 확인해야 합니다. 정보가 해결되기 전에는 승인할 수 없습니다.", WAITING_UPSTREAM_REVIEW: "상위 범위 PM 검토가 끝나야 산정할 수 있습니다. 이 화면이 실제 상위 승인을 자동 생성하지 않습니다.", NOT_REQUIRED: "PM이 선택한 산정 대상이 없습니다. 0원 견적이 생성됐다는 의미가 아닙니다.", FAILED: "산정에 실패했습니다. 입력과 실행 환경을 확인하고 다시 실행해 주세요.", DRAFT_READY: "최신 PM 산정 승인으로 내부 초안이 작성됐습니다. 고객 합의·발송·청구는 수행하지 않았습니다." }[state.view.phase] || "현재 결과 상태를 확인해 주세요.";
  return `<div class="notice"><strong>${e(PHASE[state.view.phase] || state.view.phase)}</strong>${text}</div>${inputChanged() || state.invalidated ? '<div class="notice error" role="status"><strong>현재 입력과 이전 결과가 다르거나 재확인이 필요합니다.</strong>아래 이전 결과는 참고용입니다. 다시 산정한 뒤 수정·승인해 주세요.</div>' : ""}${state.conflicted ? '<div class="notice error"><strong>버전 충돌로 변경 작업이 잠겼습니다.</strong>최신 입력으로 다시 산정해야 합니다. 조회만으로 충돌을 자동 해제하지 않습니다.</div>' : ""}${state.mode === "mock" ? '<div class="notice"><strong>읽기 전용 상태 미리보기</strong>실제 API를 호출하지 않았습니다. 수정·승인은 API 모드에서만 동작합니다.</div>' : ""}`;
}
function labControls() {
  if (state.mode === "mock") return `<div class="notice">읽기 전용 상태 미리보기에서는 모델을 실행하지 않습니다. 실행하려면 상단에서 기존 Agent 3 API를 선택하세요.</div>`;
  if (!lab.enabled) return `<div class="notice">모델 선택과 가상 응답 편집을 사용하려면 서버를 <code>python prototype/run_demo.py --lab</code>로 실행하세요.</div>`;
  const field = (id, label, value) => `<label class="field">${label}<input id="${id}" value="${e(value)}" ${state.pending ? "disabled" : ""}></label>`;
  return `<section class="card"><h2>1. 테스트 실행 설정</h2><p class="caption">가상 응답은 비용 없이 확인합니다. Gemini·Bedrock 실행은 외부 모델을 호출합니다. 키는 서버의 .env에서 읽습니다. 응답의 MD 값은 사용하지 않고 테스트 기준표로 계산합니다.</p><div class="settings-grid"><fieldset class="field"><legend>모델 공급자</legend><div class="button-group">${[["fake","가상 응답 (fake)"],["gemini","Gemini API"],["bedrock","AWS Bedrock"]].map(([v,l]) => `<label class="check"><input type="radio" name="lab-provider" value="${v}" ${lab.provider === v ? "checked" : ""} ${state.pending ? "disabled" : ""}>${l}</label>`).join("")}</div></fieldset>${lab.provider !== "fake" ? field("lab-model", "모델 ID", lab.model) : ""}${lab.provider === "bedrock" ? field("lab-region", "AWS 리전", lab.region) : ""}</div>${lab.provider === "fake" ? `<label class="field">가상 모델 응답 JSON<textarea id="lab-fake" class="json-input" spellcheck="false">${e(lab.fakeText)}</textarea></label>` : ""}<p class="tiny">2. 아래 테스트 입력을 확인한 뒤 오른쪽 위 ‘영향 산정 실행’을 누르세요. 3. 결과 검토 후 일정 미산정을 체크하고 승인하면 협의안이 작성됩니다.</p></section>`;
}
function settings() {
  const scenarios = [["normal", "선택한 요청의 기본 입력"], ["missing-rate", "QA 단가 누락"], ["upstream", "범위 PM 검토 전"], ["no-target", "산정 대상 없음"]];
  if (state.mode === "mock") scenarios.push(["failed", "산정 실패 상태"], ...[0, 404, 409, 422, 502, 503].map(code => [`error-${code}`, code ? `HTTP ${code} 오류` : "연결 실패"]));
  return `<details class="input-settings"><summary>테스트 입력 · 상위 합성 데이터와 실행 상태</summary><div class="settings-grid"><label class="field">시나리오<select id="scenario" ${state.pending || !record().api ? "disabled" : ""}>${scenarios.map(([value, label]) => `<option value="${value}" ${state.scenario === value ? "selected" : ""}>${label}</option>`).join("")}</select></label><div class="field">현재 연결<div class="notice inline">${state.mode === "api" ? "실제 기존 API 호출 · 모델명은 응답 정보에 표시" : "저장된 합성 응답으로 상태만 미리보기"}<p class="tiny">외부 모델 공급자는 브라우저에서 확인할 수 없습니다.</p></div></div></div><label class="field">Agent 1·2 및 PM 검토에서 받은 합성 입력 JSON<textarea id="input-json" class="json-input" spellcheck="false" ${state.pending || !record().api ? "disabled" : ""}>${e(state.inputText)}</textarea></label><div class="form-actions">${button("기본 입력 복원", "reset-input", { disabled: state.pending })}${button("로컬 세션 지우기", "clear-session", { disabled: state.pending })}</div></details>`;
}
function submittedConditions() {
  const current = record().api && state.view;
  const input = current ? state.submittedInput : null;
  const title = state.mode === "mock" ? "미리보기 예시 조건" : "산정에 제출한 조건 · 합성 입력";
  const content = input ? (input.constraints?.length ? input.constraints.map(item => `<p data-submitted-condition>${e(item.text)}</p>`).join("") : "<p>제출된 조건 없음</p>") : `<p>${current ? "이 세션의 제출 조건 기록이 없습니다. 최신 입력으로 다시 산정해 주세요." : "산정 실행 후 제출한 조건을 표시합니다."}</p>`;
  return `<div class="schedule-box" id="submitted-conditions"><h3>${title}</h3>${content}<p class="tiny">${state.mode === "mock" ? "저장된 합성 상태의 예시이며 실제 산정 실행이 아닙니다." : "이 결과를 받은 요청의 조건입니다. 미제출 편집과 실제 프로젝트 일정 확정 정보는 구분합니다."}</p></div>`;
}
function metric(label, value, detail, word = false) { return `<article class="metric"><div class="metric-label">${label}</div><div class="metric-value ${word ? "word" : ""}">${value}</div><div class="metric-detail">${detail}</div></article>`; }
function impact() {
  const totals = currentTotals(), list = estimates();
  const margin = totals && totals.price ? ((totals.price - totals.cost) / totals.price) * 100 : null;
  const rate = list[0]?.rate_card_id || "응답 전";
  const current = record().api ? state.view : null;
  const original = state.originalLines;
  const writable = canEdit();
  let position = 0;
  const rows = list.map((item, i) => item.lines.map((line, j) => {
    const initial = original[position++];
    const edit = state.edits?.[i]?.lines?.[j] || line;
    return `<tr><td><strong style="font-weight:500">${e(line.task)}</strong><small>${e(line.item_ids.join(" · "))}</small></td><td>${e(line.role)}</td><td class="numeric">${initial ? number(initial.md) : "—"}</td><td><input class="md-input" id="md-${i}-${j}" type="number" min="0" step="0.5" value="${e(edit.md)}" aria-label="${e(line.role)} 공수 MD" data-edit-md="${i}:${j}" ${writable ? "" : "disabled"}></td><td class="tiny">API 미제공</td><td class="tiny">API 미제공</td></tr><tr class="assumption-row"><td colspan="6"><label class="tiny">가정 <input class="assumption-input" id="assumption-${i}-${j}" data-edit-assumption="${i}:${j}" value="${e(edit.assumption)}" aria-label="${e(line.role)} 가정" ${writable ? "" : "disabled"}></label></td></tr>`;
  }).join("")).join("");
  return `${record().api ? "" : '<div class="notice">이 예시는 API 산정 대상이 아닙니다. CR-A-001을 열어 기존 API 흐름을 확인하세요.</div>'}${labControls()}${settings()}${state.pending ? '<p class="loading-line" role="status"><span class="inline-spinner"></span>기존 Agent 3 API 응답을 기다리는 중입니다…</p>' : ""}${stateNotice()}<div class="version-row"><span>산정 버전</span><span class="version-pill">상위 입력 · 합성 fixture</span><span class="version-pill current">${current ? `현재 v${current.state_revision}` : "실행 전"}</span><span style="margin-left:auto">단가표 ${e(rate)} · VAT 별도</span>${button("현재 상태 확인", "refresh", { disabled: !current || state.pending || state.mode !== "api", icon: "ec991" })}</div><div class="metric-grid">${metric("총 공수", totals ? `${number(totals.md)}<small>MD</small>` : "미산정", "응답의 코드 계산 결과", !totals)}${metric("예상 직접원가 · 내부", totals ? money(totals.cost) : "미산정", "합성 단가 기준 · 행별 단가 미제공", !totals)}${metric("고객 제안가 · 판매", totals ? money(totals.price) : "미산정", "실제 고객 합의 금액이 아닙니다", !totals)}${metric("예상 마진", margin === null ? "미산정" : `${number(margin)}<small>%</small>`, "API 총 원가·판매가의 표시용 비율", margin === null)}${metric("일정 영향", current && list.length && !unknownSchedule() ? "항목별 확인" : "미산정", "MD를 달력 일수로 환산하지 않습니다", true)}</div><div class="columns impact"><div class="stack"><section class="card"><div class="card-heading"><div><h2>작업 분해 · 공수</h2><p class="caption">공수·가정을 수정하면 기존 API가 금액을 재계산하고 재승인을 요구합니다.</p></div>${chip(state.mode === "mock" ? "가상 상태" : "API 응답")}</div><div class="table-wrap"><table class="impact-table"><thead><tr><th>작업 · 연결 항목</th><th>직군</th><th>최초 MD</th><th>수정 MD</th><th>직군 단가</th><th>행별 금액</th></tr></thead><tbody>${rows || '<tr><td colspan="6"><div class="empty-state"><h3>표시할 산정 작업이 없습니다</h3><p>산정 실행 결과와 필요한 입력을 확인해 주세요.</p></div></td></tr>'}${totals ? `<tr class="total-row"><td>합계</td><td></td><td></td><td class="numeric">${number(totals.md)} MD</td><td colspan="2" class="numeric">${money(totals.price)}</td></tr>` : ""}</tbody></table></div><label class="field">수정 사유<input id="edit-reason" placeholder="공수·가정을 수정한 이유를 입력하세요" value="${e(state.editReason)}" ${writable ? "" : "disabled"}></label><p id="edit-notice" class="tiny" style="margin-top:8px">${editsChanged() ? "아직 저장하지 않은 수정이 있습니다. 수정 반영 후 승인해 주세요." : "직접 금액·일정 편집은 현재 계약에 포함되지 않습니다."}</p><div class="form-actions">${button("수정 반영·재계산", "edit", { disabled: !writable || !editsChanged(), icon: "ec991" })}${button("편집 취소", "cancel-edit", { disabled: !editsChanged() || state.pending })}</div><div class="notice inline">${icon("2b0e7")}AI가 작업과 테스트 기준을 제안하고 서버가 기준 공수와 단가로 계산합니다. 사례별 합성 기준이며 실제 견적에 사용할 수 없습니다. PM은 공수를 수동 수정할 수 있습니다.</div></section><section class="card"><div class="card-heading"><div><h2>일정 영향</h2><p class="caption">선행 작업·인력 정보가 없으면 임의 일수 대신 미산정으로 표시합니다.</p></div>${chip("확정 아님")}</div><div class="schedule-grid">${submittedConditions()}<div class="schedule-box"><h3>산정 결과의 근거</h3>${list.length ? list.map(item => `<p>${item.schedule.business_days == null ? "미산정" : `${number(item.schedule.business_days)} 영업일`}</p><p class="tiny">${e(item.schedule.reason || "항목별 근거 확인")}</p>`).join("") : '<p>산정 결과가 아직 없습니다.</p>'}</div></div>${(current?.impact.questions || []).map(question => `<div class="question"><strong>${question.blocking ? "필수 확인" : "확인 사항"} · ${e(question.text)}</strong><span class="muted">${e(question.reason || "필요한 정보 확인 후 다시 산정해 주세요.")}</span></div>`).join("")}</section></div><div class="stack"><section class="card"><div class="card-heading"><div><h2>PM 산정 검토</h2><p class="caption">상업 승인·고객 합의와 별개인 로컬 검토입니다.</p></div></div><div class="radio-option selected"><span class="radio-dot"></span><div>산정 결과 확인<div class="tiny">현재 결과와 일정 미산정의 한계를 확인합니다.</div></div></div><label class="field">검토자 · 로컬 테스트용<input id="reviewer" value="${e(state.reviewer)}" ${state.pending ? "disabled" : ""}></label><label class="field">검토 메모 / 반려 사유<textarea id="review-reason" placeholder="반려할 때는 사유가 필요합니다" ${state.pending ? "disabled" : ""}>${e(state.reviewReason)}</textarea></label>${unknownSchedule() ? `<label class="check"><input id="ack-schedule" type="checkbox" ${state.ack ? "checked" : ""} ${state.pending ? "disabled" : ""}><span>일정 영향이 미산정이며 추가 확인이 필요함을 확인했습니다.</span></label>` : ""}<div class="form-actions">${button("승인하고 협의안 작성", "approve", { class: "primary", disabled: !canApprove() || (unknownSchedule() && !state.ack), icon: "6ce49" })}${button("반려", "reject", { disabled: !canApprove() })}</div><p class="tiny" style="margin-top:12px">수정되면 기존 승인·초안은 이력으로 남고 현재 결과는 재승인이 필요합니다.</p>${current?.phase === "DRAFT_READY" ? `<div class="notice inline"><strong>PM 산정 승인 완료</strong>${button("내부 초안 보기", "go-draft", { class: "small", icon: "ec151" })}</div>` : ""}</section><section class="card"><h2>산정 이력</h2><p class="caption">현재 API 세션의 실제 변경 이력</p>${history()}${current ? `<details style="margin-top:16px"><summary class="caption">결과 JSON · 버전</summary><pre class="raw-json">${e(JSON.stringify(current, null, 2))}</pre></details>` : '<p class="caption" style="margin-top:14px">산정 후 표시됩니다.</p>'}</section></div></div>`;
}
function history() {
  const list = record().api ? state.view?.history || [] : [];
  return list.length ? list.map((item, i) => `<details class="history-row"><summary>이전 결과 ${i + 1} · ${e(item.reason || "검토 변경")}</summary><p>${item.impact?.estimates?.map(row => `${row.totals ? number(row.totals.md) + " MD · " + money(row.totals.price) : "미산정"}`).join(" / ") || "승인 이력"}</p><p>이전 승인: ${item.approval?.approved ? "있음" : "없음"} · 이전 초안: ${item.draft ? "있음" : "없음"}</p></details>`).join("") : '<p class="caption" style="margin-top:14px">변경 이력이 없습니다.</p>';
}
function hasCurrentDraft() { return state.mode === "api" && record().api && !state.pending && !!state.view?.draft && state.view.phase === "DRAFT_READY" && !inputChanged() && !state.invalidated && !state.conflicted && !editsChanged(); }
function draftMarkdown() {
  const d = state.view?.draft;
  if (!d) return "";
  return `# 협의안 초안 · 내부 검토용\n\n## 요약\n\n${d.summary}\n\n## 범위 판단과 근거\n\n${d.scope}\n\n## 작업·공수·일정\n\n${d.impact}\n\n## 금액\n\n${d.cost || "미산정"}\n\n## 확인 필요 사항\n\n${d.open_questions.map(q => `- ${q}`).join("\n") || "없음"}\n`;
}
function draft() {
  const view = record().api ? state.view : null;
  const valid = hasCurrentDraft();
  const d = valid ? view.draft : null;
  return `${stateNotice()}<div class="draft-versions"><div class="draft-version"><span>산정 결과</span><strong>${view ? `v${view.state_revision} · ${PHASE[view.phase] || view.phase}` : "아직 없음"}</strong><span class="tiny">현재 API 세션 기준</span></div><div class="draft-version ${valid ? "current" : ""}"><span>내부 협의안 초안</span><strong>${valid ? "승인 결과로 작성됨" : "PM 산정 승인 후 생성"}</strong><span class="tiny">사용자 개발 착수 승인과 별개</span></div><div class="draft-version"><span>고객 협의·상업 승인</span><strong>이번 구현 범위 밖</strong><span class="tiny">고객 전달·합의·청구 미수행</span></div></div><div class="columns impact"><div class="stack"><section class="card"><div class="card-heading"><div><h2>협의안 · 내부 검토용</h2><p class="caption">표시된 API 세션의 PM 산정 승인 결과에서 반환된 초안입니다.</p></div><div class="button-group">${button("복사", "copy-draft", { disabled: !valid, icon: "4ea67" })}${button("Markdown 저장", "download-draft", { disabled: !valid, icon: "986b4" })}</div></div>${d ? `<div class="draft-shell"><article class="draft-paper"><h2>${e(d.summary)}</h2><p class="tiny" style="margin-top:10px">요청 ${e(view.impact.context.case_id)} · 내부 초안 · VAT 별도</p>${[["1. 범위 판단과 근거", d.scope], ["2. 작업·공수·일정", d.impact], ["3. 금액", d.cost || "미산정"], ["4. 확인 필요 사항", d.open_questions.join("\n") || "없음"]].map(([title, text]) => `<section class="draft-section"><h3>${title}</h3><p>${e(text)}</p></section>`).join("")}</article></div>` : `<div class="empty-state"><h3>아직 유효한 협의안 초안이 없습니다.</h3><p>영향 산정에서 최신 결과를 확인·승인한 뒤 볼 수 있습니다.</p><div style="margin-top:16px">${button("영향 산정으로", "go-impact", { icon: "ec151" })}</div></div>`}<div class="notice inline">${icon("d5e00")}시스템은 고객에게 자동 발송하지 않습니다. 계약 체결·고객 합의·청구는 수행하지 않습니다.</div></section></div><div class="stack"><section class="card"><div class="card-heading"><h2>PM 산정 승인</h2>${chip(view?.approval?.approved ? "승인" : "미승인")}</div><div class="metadata-box"><dl><dt>검토자</dt><dd>${e(view?.approval?.reviewer_ref || "—")}</dd><dt>검토 시각</dt><dd>${e(view?.approval?.reviewed_at || "—")}</dd><dt>대상 결과</dt><dd>${e(view?.approval?.target_result_id || "—")}</dd><dt>검토 사유</dt><dd>${e(view?.approval?.reason || "—")}</dd></dl></div><p class="caption">검토자 참조는 로컬 테스트 값이며 실제 PM 인증을 의미하지 않습니다.</p></section><section class="card"><h2>고객 전달 기록</h2><p class="caption">이번 범위에서는 전송·협의 기록을 생성하지 않습니다.</p><div class="form-actions">${button("고객 응답 기록", "unused", { disabled: true, icon: "6ce49" })}</div></section><section class="card"><h2>변경 이력</h2>${history()}</section></div></div>`;
}
function render() {
  const active = document.activeElement;
  const focus = active?.id ? { id: active.id, start: active.selectionStart, end: active.selectionEnd } : null;
  root.innerHTML = `<div class="shell">${sidebar()}<div class="right">${topbar()}<main id="main" class="content" tabindex="-1">${state.page === "list" ? inbox() : detail()}<p class="footer-note">합성 예시로 동작을 확인하는 로컬 화면입니다. AI 서버는 메모리 세션을 사용하며 CN 업무 DB·실제 사용자 인증과 연결되지 않았습니다.</p></main></div></div>`;
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
  if (note) note.textContent = editsChanged() ? "아직 저장하지 않은 수정이 있습니다. 수정 반영 후 승인해 주세요." : "직접 금액·일정 편집은 현재 계약에 포함되지 않습니다.";
}
async function operation(action) {
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
  await operation(async () => { acceptView(await api.review(state.view.session_id, input)); toast(approved ? "PM 산정 승인으로 내부 협의안 초안을 작성했습니다." : "반려 사유를 기록했습니다. 초안은 생성하지 않았습니다."); });
}
async function restore() {
  let saved;
  try { saved = JSON.parse(sessionStorage.getItem(STORAGE) || "null"); } catch { return; }
  if (!saved?.sessionId || typeof saved.inputText !== "string") return;
  try { if (JSON.parse(saved.inputText)?.context?.case_id !== baseInput.context.case_id) return; } catch { return; }
  if (lab.enabled && saved.labSettings) { Object.assign(lab, saved.labSettings); if (lab.provider === "local") { lab.provider = "fake"; saved.invalidated = true; } }
  state.inputText = saved.inputText; state.signature = saved.signature; state.originalLines = Array.isArray(saved.originalLines) ? saved.originalLines : [];
  // v1 sessions stored the successful request as signature, separately from editable inputText.
  try { state.submittedInput = structuredClone(saved.submittedInput || JSON.parse(saved.signature)); } catch { state.submittedInput = null; }
  state.invalidated = saved.invalidated === true;
  await operation(async () => { acceptView(await api.get(saved.sessionId)); });
}

root.addEventListener("click", event => {
  const open = event.target.closest("[data-open]"); if (open) {
    if (!records.some(item => item.id === open.dataset.open && (item.api || item.datasetSample || item.id === "CR-001"))) return toast("목록 표시용 예시입니다. Agent 1 테스트 요청과 CR-001에서 원문을 확인할 수 있습니다.");
    return navigate(open.dataset.open);
  }
  const tab = event.target.closest("[data-tab]"); if (tab) return navigate(state.id, tab.dataset.tab);
  const group = event.target.closest("[data-group]"); if (group) { state.group = group.dataset.group; state.pageNo = 1; render(); return; }
  const item = event.target.closest("[data-item]"); if (item) { state.selectedItem = Number(item.dataset.item); render(); return; }
  const control = event.target.closest("[data-action]"); if (!control || control.disabled) return;
  const action = control.dataset.action;
  if (action === "list") navigate(null);
  if (action === "open-api") navigate("CR-A-001", "impact");
  if (action === "go-scope") navigate(state.id, "scope");
  if (action === "go-impact") navigate(state.id, "impact");
  if (action === "go-draft") navigate(state.id, "draft");
  if (action === "help") document.querySelector("#help-dialog").showModal();
  if (action === "previous") { state.pageNo--; render(); }
  if (action === "next") { state.pageNo++; render(); }
  if (action === "prepare") prepare();
  if (action === "run-a1") runAgent1();
  if (action === "run-a2") runAgent2();
  if (action === "refresh") refresh();
  if (action === "edit") edit();
  if (action === "approve") review(true);
  if (action === "reject") review(false);
  if (action === "cancel-edit") { state.edits = structuredClone(estimates().map(item => ({ item_ids: item.item_ids, lines: item.lines }))); state.editReason = ""; render(); }
  if (action === "reset-input") { state.scenario = "normal"; state.inputText = JSON.stringify(baseInput, null, 2); render(); }
  if (action === "clear-session") { forget(); state.view = null; state.edits = null; state.submittedInput = null; state.signature = null; state.error = null; state.conflicted = false; state.invalidated = false; state.originalLines = []; render(); toast("브라우저의 로컬 세션 참조를 지웠습니다. 서버 데이터 삭제를 의미하지 않습니다."); }
  if (action === "copy-draft" && hasCurrentDraft()) navigator.clipboard.writeText(draftMarkdown()).then(() => toast("내부 협의안 초안을 복사했습니다.")).catch(() => toast("클립보드 접근을 사용할 수 없습니다. Markdown 저장을 이용해 주세요."));
  if (action === "download-draft" && hasCurrentDraft()) { const blob = new Blob([draftMarkdown()], { type: "text/markdown;charset=utf-8" }); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = `${state.view.impact.context.case_id}_내부_협의안_초안.md`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
});
root.addEventListener("input", event => {
  const target = event.target;
  if (target.id === "request-search") { state.query = target.value; state.pageNo = 1; if (!event.isComposing && !composingSearch) updateSearchResults(); }
  if (["lab-fake", "lab-model", "lab-url", "lab-region"].includes(target.id) && state.view && state.mode === "api") { state.invalidated = true; persist(); updateFormControls(); }
  if (target.id === "lab-fake") lab.fakeText = target.value;
  if (target.id === "lab-model") lab.model = target.value;
  if (target.id === "lab-url") lab.base_url = target.value;
  if (target.id === "lab-region") lab.region = target.value;
  if (target.id === "a1-model") { a1.model = target.value; a1.result = null; }
  if (target.id === "a1-region") { a1.region = target.value; a1.result = null; }
  if (target.id === "a2-model") { a2.model = target.value; a2.result = null; }
  if (target.id === "a2-region") { a2.region = target.value; a2.result = null; }
  if (target.id === "input-json") { state.inputText = target.value; updateFormControls(); }
  if (target.id === "edit-reason") state.editReason = target.value;
  if (target.id === "reviewer") state.reviewer = target.value;
  if (target.id === "review-reason") state.reviewReason = target.value;
  if (target.dataset.editMd) { const [i, j] = target.dataset.editMd.split(":").map(Number); state.edits[i].lines[j].md = target.value === "" ? NaN : Number(target.value); updateFormControls(); }
  if (target.dataset.editAssumption) { const [i, j] = target.dataset.editAssumption.split(":").map(Number); state.edits[i].lines[j].assumption = target.value; updateFormControls(); }
});
root.addEventListener("compositionstart", event => { if (event.target.id === "request-search") composingSearch = true; });
root.addEventListener("compositionend", event => { if (event.target.id === "request-search") { composingSearch = false; state.query = event.target.value; state.pageNo = 1; updateSearchResults(); } });
root.addEventListener("change", async event => {
  const target = event.target;
  if (target.name === "lab-provider") { if (state.view && state.mode === "api") { state.invalidated = true; persist(); } lab.provider = target.value; lab.model = target.value === "gemini" ? "gemini-2.5-flash-lite" : ""; render(); }
  if (target.name === "a1-provider" || target.name === "a2-provider") { const agent = target.name === "a1-provider" ? a1 : a2; agent.provider = target.value; agent.model = target.value === "gemini" ? "gemini-2.5-flash-lite" : ""; agent.result = null; agent.error = null; render(); }
  if (target.id === "scope-filter") { state.filter = target.value; state.pageNo = 1; render(); }
  if (target.id === "project-filter") { state.project = target.value; state.pageNo = 1; render(); }
  if (target.id === "sort") { state.order = target.value; state.pageNo = 1; render(); }
  if (target.id === "ack-schedule") { state.ack = target.checked; updateFormControls(); }
  if (target.id === "scenario") { state.scenario = target.value; state.inputText = JSON.stringify(scenarioInput(baseInput, state.scenario), null, 2); render(); }
  if (target.id === "mode") {
    state.mode = target.value; state.view = null; state.edits = null; state.submittedInput = null; state.signature = null; state.error = null; state.conflicted = false; state.invalidated = false; state.scenario = "normal"; state.inputText = JSON.stringify(baseInput, null, 2); render();
    if (state.mode === "api") await restore();
  }
});
root.addEventListener("keydown", event => { if (event.target.id === "global-search" && event.key === "Enter") { state.query = event.target.value; state.group = "열린 요청"; state.pageNo = 1; navigate(null); if (state.page === "list") render(); } });
document.querySelector('[data-action="close-help"]').addEventListener("click", () => document.querySelector("#help-dialog").close());
window.addEventListener("hashchange", route);

try {
  const [inputResponse, previewResponse, sampleResponse] = await Promise.all([fetch(new URL("./data/agent3-input.json", import.meta.url)), fetch(new URL("./data/agent3-preview.json", import.meta.url)), fetch(new URL("./data/agent3-samples.json", import.meta.url))]);
  if (!inputResponse.ok || !previewResponse.ok || !sampleResponse.ok) throw new Error("합성 입력 파일을 불러오지 못했습니다.");
  baseInput = await inputResponse.json(); samples = await sampleResponse.json(); samples.inputs["CR-A-001"] = baseInput; previewClient = new MockPreview(await previewResponse.json()); state.inputText = JSON.stringify(baseInput, null, 2);
  try { const response = await fetch("/prototype-lab/defaults"); if (response.ok) { const defaults = await response.json(); if (defaults.fake_response && defaults.model) { lab.enabled = true; api.lab = true; lab.defaultFake = defaults.fake_response; lab.fakeText = JSON.stringify(defaults.fake_response, null, 2); lab.model = defaults.model; } } } catch { /* Ordinary server retains the existing API mode. */ }
  try { const a2Response = await fetch(new URL("./data/agent2-inputs.json", import.meta.url)); if (a2Response.ok) agent2Inputs = await a2Response.json(); }
  catch { /* Agent 2 입력이 없어도 나머지 화면은 동작한다 (실행 버튼만 비활성). */ }
  try { const a1Response = await fetch(new URL("./data/agent1-inputs.json", import.meta.url)); if (a1Response.ok) agent1Inputs = await a1Response.json(); }
  catch { /* Agent 1 입력이 없어도 다른 화면은 동작한다. */ }
  selectCase("CR-A-001"); route(); await restore();
} catch (error) {
  root.innerHTML = `<main class="content"><h1>화면을 준비하지 못했습니다.</h1><p style="margin-top:16px">${e(error.message)}</p><p>로컬 서버와 prototype/data 파일을 확인해 주세요.</p></main>`;
}
