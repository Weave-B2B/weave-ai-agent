const $ = (id) => document.getElementById(id);

let latestPreview = null;
let currentRun = null;
let currentRunScope = null;
let previewProject = null;
let busy = false;
let pollTimer = null;
const operations = new Map();

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// 화면 표시용 복사본만 변환한다. API로 보내는 입력과 식별자는 원본을 유지한다.
function htmlValues(value) {
  if (typeof value === 'string') return escapeHtml(value);
  if (Array.isArray(value)) return value.map(htmlValues);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k,v]) => [k,htmlValues(v)]));
  return value;
}

function operationId(action, payload) {
  const key = JSON.stringify([selectedProjectId(), selectedCrId(), action, payload]);
  if (!operations.has(key)) operations.set(key, `proto-${crypto.randomUUID()}`);
  return [key, operations.get(key)];
}

function authHeaders() {
  const headers = {};
  const token = $("auth-token").value.trim();
  if (token) {
    headers["Authorization"] = token.startsWith("Bearer ") ? token : `Bearer ${token}`;
    const cid = $("company-id-jwt").value.trim();
    if (cid) headers["X-Company-Id"] = cid;
  } else {
    const devCompany = $("dev-company-id").value.trim();
    const devMember = $("dev-member-id").value.trim();
    const devRole = $("dev-member-role").value;
    if (devCompany) headers["X-Dev-Company-Id"] = devCompany;
    if (devMember) headers["X-Dev-Member-Id"] = devMember;
    if (devRole) headers["X-Dev-Member-Role"] = devRole;
  }
  return headers;
}

function selectedProjectId() {
  return $("project-id-manual").value.trim() || $("project-select").value.trim();
}

function selectedCrId() {
  return $("cr-id-manual").value.trim() || $("cr-select").value.trim();
}

async function api(path, options = {}) {
  const resp = await fetch(`/prototype-lab/backend${path}`, {
    ...options,
    headers: { ...authHeaders(), ...(options.headers || {}) },
  });
  const body = await resp.json().catch(() => null);
  if (!resp.ok) {
    const detail = body?.detail;
    const msg = typeof detail === "string"
      ? detail
      : detail?.message || body?.message || JSON.stringify(detail || body);
    throw new Error(`HTTP ${resp.status}: ${msg}`);
  }
  return body;
}

function fmtMoney(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString("ko-KR") : String(v);
}

async function checkStatus() {
  const badge = $("connection-badge");
  try {
    const st = await fetch("/prototype-lab/backend/status").then((r) => r.json());
    if (st.backend_connected) {
      badge.className = "badge ok";
      badge.textContent = `실제 백엔드 DB 연결됨 (${st.backend_base_url})`;
    } else {
      badge.className = "badge err";
      badge.textContent = st.backend_base_url
        ? `백엔드 연결 실패 (${st.backend_base_url})`
        : "BACKEND_BASE_URL 미설정 (가상 모드 전환 안 함)";
    }
  } catch (e) {
    badge.className = "badge err";
    badge.textContent = "PROTOTYPE_LAB=1 백엔드 브릿지 비활성";
  }
}

async function loadProjects() {
  const res = await api("/projects");
  const list = res.content || res.projects || [];
  const sel = $("project-select");
  sel.innerHTML = '<option value="">프로젝트를 선택하세요</option>';
  for (const p of list) {
    const opt = document.createElement("option");
    opt.value = p.projectId || p.id;
    opt.textContent = `${p.projectName || p.name} (${opt.value.slice(0, 8)}…)`;
    sel.appendChild(opt);
  }
}

async function refreshProjectContext() {
  const pid = selectedProjectId();
  if (!pid) return alert("프로젝트를 선택하거나 UUID를 입력해 주세요.");
  const [rcRes, crRes] = await Promise.all([
    api(`/projects/${encodeURIComponent(pid)}/rate-cards/current`),
    api(`/projects/${encodeURIComponent(pid)}/change-requests`).catch(() => ({ content: [] })),
  ]);
  const box = $("current-rate-card-box");
  box.hidden = false;
  const rc = htmlValues(rcRes.currentRateCard);
  if (rc) {
    box.className = "box ok";
    box.innerHTML = `<strong>현재 유효 공식 단가표:</strong> v${rc.rateCardVersionNo} · UUID <code>${rc.rateCardVersionId}</code> · 적용일 ${rc.rateEffectiveDate} · 1MD=${rc.hoursPerMd}h · 통화 ${rc.rateCurrency}`;
  } else {
    box.className = "box warn";
    box.innerHTML = "<strong>현재 유효 공식 단가표 없음</strong> — 아래에서 엑셀 단가표를 등록해야 금액 산정을 실행할 수 있습니다.";
  }

  const crSel = $("cr-select");
  crSel.innerHTML = '<option value="">변경 요청을 선택하세요</option>';
  for (const c of crRes.content || crRes.items || crRes.changeRequests || []) {
    const opt = document.createElement("option");
    opt.value = c.changeRequestId || c.id;
    opt.textContent = `${c.title || c.changeRequestTitle || opt.value} (${opt.value.slice(0, 8)}…)`;
    crSel.appendChild(opt);
  }
}

function renderPreview(rawPreview) {
  latestPreview = rawPreview;
  previewProject = selectedProjectId();
  const preview = htmlValues(rawPreview);
  const area = $("preview-area");
  const statusMap = {
    READY: ["info", "신규/변경 단가표 등록 가능"],
    UNCHANGED: ["ok", "기존 단가표와 동일 (새 버전을 만들지 않고 출처만 연결)"],
    SCHEDULED: ["warn", `미래 적용일(${preview.rateEffectiveDate}) 예약 등록`],
    BLANK: ["warn", "단가표 시트 미작성 (기존 프로젝트 단가표 유지 가능)"],
    MISSING_SHEET: ["warn", "단가표 시트 없음 (일정 자료로만 사용 가능)"],
    ERROR: ["err", "오류가 있어 등록할 수 없습니다"],
  };
  const [cls, label] = statusMap[preview.status] || ["info", preview.status];

  let html = `<div class="box ${cls}">
    <strong>미리보기 상태:</strong> ${label}<br/>
    문서 버전: <code>${preview.documentVersionId}</code> (v${preview.documentVersionNo}, ${preview.originalFilename}) · SHA-256: <code>${preview.sourceSha256.slice(0, 16)}…</code><br/>
    적용 시작일: <strong>${preview.rateEffectiveDate || "—"}</strong> · 1MD 기준 시간: <strong>${preview.hoursPerMd || "—"}h</strong> · 통화: <strong>${preview.rateCurrency || "—"}</strong>
  </div>`;

  if (preview.permissionNotice) {
    html += `<div class="box warn">${preview.permissionNotice}</div>`;
  }
  if (preview.errors?.length) {
    html += `<div class="box err"><strong>검증 오류 (${preview.errors.length}건):</strong><ul>${preview.errors
      .map((e) => `<li><code>${e.cell}</code> [${e.code}] ${e.message}</li>`)
      .join("")}</ul></div>`;
  }
  if (preview.zeroRateWarnings?.length) {
    html += `<div class="box warn"><strong>0원 단가 확인 필요:</strong><ul>${preview.zeroRateWarnings
      .map((w) => `<li><code>${w.cell}</code> ${w.message}</li>`)
      .join("")}</ul>
      <label style="flex-direction:row;align-items:center;gap:6px;margin-top:6px;">
        <input id="confirm-zero-rates" type="checkbox" /> 0원 단가가 의도된 값임을 확인했습니다.
      </label></div>`;
  }

  if (preview.roleRates?.length) {
    html += `<table>
      <thead><tr><th>직군</th><th>현재 원가</th><th>업로드 원가</th><th>현재 판매가</th><th>업로드 판매가</th><th>변경</th></tr></thead>
      <tbody>${preview.roleRates
        .map(
          (r) => `<tr class="${r.changed ? "changed" : ""}">
          <td><strong>${r.jobRoleCode}</strong> (${r.jobRoleName || ""})</td>
          <td class="num">${fmtMoney(r.currentInternalRate)}</td>
          <td class="num">${fmtMoney(r.importedInternalRate)}</td>
          <td class="num">${fmtMoney(r.currentSellingRate)}</td>
          <td class="num">${fmtMoney(r.importedSellingRate)}</td>
          <td>${r.changed ? "변경됨" : "동일"}</td>
        </tr>`
        )
        .join("")}</tbody></table>`;
  }

  const disabled = !preview.registerable || !preview.canRegister;
  html += `<div class="row" style="margin-top:12px;">
    <button id="btn-confirm-import" class="primary" ${disabled ? "disabled" : ""}>
      ${preview.unchanged ? "기존 단가표 버전과 출처 연결" : "프로젝트 단가표로 등록"}
    </button>
    <span id="confirm-result-msg" class="muted"></span>
  </div>`;
  area.innerHTML = html;

  $("btn-confirm-import")?.addEventListener("click", wrap(confirmImport));
}

async function uploadAndPreview() {
  const pid = selectedProjectId();
  const file = $("xlsx-file").files[0];
  if (!pid) return alert("프로젝트를 먼저 선택하세요.");
  if (!file) return alert("업로드할 .xlsx 파일을 선택하세요.");
  const form = new FormData();
  form.append("file", file);
  form.append("document_title", $("doc-title").value || file.name);
  form.append("approve_for_schedule", $("approve-schedule").checked ? "true" : "false");

  const uploaded = await api(`/projects/${encodeURIComponent(pid)}/workbook-upload`, {
    method: "POST",
    body: form,
  });
  const docVersionId = uploaded.document?.documentVersionId || uploaded.document?.latestVersionId;
  $("doc-version-id-manual").value = docVersionId || "";
  if (!docVersionId) throw new Error("문서 버전 ID를 받지 못했습니다.");
  await previewDocumentVersion(docVersionId);
}

async function previewDocumentVersion(docVersionId) {
  const pid = selectedProjectId();
  if (!pid || !docVersionId) return alert("프로젝트와 문서 버전 UUID를 확인해 주세요.");
  const preview = await api(`/projects/${encodeURIComponent(pid)}/rate-card-imports/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ documentVersionId: docVersionId }),
  });
  renderPreview(preview);
}

async function confirmImport() {
  if (!latestPreview) return;
  const pid = selectedProjectId();
  if (pid !== previewProject) throw new Error("프로젝트가 변경되었습니다. 단가표를 다시 미리보기해주세요.");
  const confirmZero = Boolean($("confirm-zero-rates")?.checked);
  const [key, opId] = operationId('confirm', [latestPreview.documentVersionId, latestPreview.normalizedInputHash, confirmZero]);
  const res = await api(`/projects/${encodeURIComponent(pid)}/rate-card-imports/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      documentVersionId: latestPreview.documentVersionId,
      sourceSha256: latestPreview.sourceSha256,
      normalizedInputHash: latestPreview.normalizedInputHash,
      expectedCurrentRateCardVersionId: latestPreview.currentRateCardVersionId,
      operationId: opId,
      confirmZeroRates: confirmZero,
    }),
  });
  const display = htmlValues(res);
  $("confirm-result-msg").innerHTML =
    `<span class="badge ok">${display.status}</span> v${display.rateCardVersionNo} · UUID <code>${display.rateCardVersionId}</code> · 적용일 ${display.rateEffectiveDate}`;
  operations.delete(key);
  latestPreview = null;
  $("btn-confirm-import").disabled = true;
  await refreshProjectContext();
}

async function runOrchestration(mode) {
  const pid = selectedProjectId();
  const cid = selectedCrId();
  if (!pid || !cid) return alert("프로젝트와 변경 요청 UUID를 모두 선택해 주세요.");
  const path = mode === "recalculate"
    ? `/projects/${encodeURIComponent(pid)}/change-requests/${encodeURIComponent(cid)}/orchestrations/recalculate`
    : `/projects/${encodeURIComponent(pid)}/change-requests/${encodeURIComponent(cid)}/orchestrations/start`;
  const payload = mode === "recalculate"
    ? { reason: "프로토타입 새 단가표 재산정", autoExecute: true }
    : { autoExecute: true };
  const res = await api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  renderOrchestration(res);
}

function orchestrationPath(action) {
  return `/projects/${encodeURIComponent(selectedProjectId())}/change-requests/${encodeURIComponent(selectedCrId())}/orchestrations/${action}`;
}

async function refreshOrchestration() {
  renderOrchestration(await api(orchestrationPath('status')));
}

async function submitRunAction(action, extra = {}) {
  if (!currentRun || currentRunScope !== JSON.stringify([selectedProjectId(), selectedCrId()])) {
    throw new Error('프로젝트 또는 변경 요청이 바뀌었습니다. 현재 분석 상태를 먼저 조회해주세요.');
  }
  const payload = {expectedStateRevision: currentRun.stateRevision, ...extra};
  const [key, id] = operationId(action, payload);
  const result = await api(orchestrationPath(action), {
    method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({...payload,operationId:id}),
  });
  operations.delete(key);
  renderOrchestration(result);
}

function renderOrchestration(raw) {
  clearTimeout(pollTimer);
  currentRun = raw;
  currentRunScope = JSON.stringify([selectedProjectId(), selectedCrId()]);
  const view = htmlValues(raw);
  const labels = {completed:'최종 검토 완료',waiting_input:'PM 답변 필요',waiting_final_review:'최종 검토 대기',
    waiting:'검토 보류',running:'분석 진행 중',ready:'실행 준비',error:'실행 실패'};
  let html = `<div class="box ${raw.status==='completed'?'ok':'info'}"><strong>${labels[raw.status] || view.status}</strong>
    · Run <code>${view.runId}</code> · revision ${view.stateRevision}${view.reason?`<p>${view.reason}</p>`:''}</div>`;
  const pricing = raw.estimateSummary?.pricing;
  if (pricing) html += `<p>공급가액 ${escapeHtml(fmtMoney(pricing.supply_amount))} · 부가세 ${escapeHtml(fmtMoney(pricing.vat_amount))}
    · 합계 ${escapeHtml(fmtMoney(pricing.total_with_vat))}</p><p class="muted">산정에 사용한 공식 단가표: ${escapeHtml(pricing.rate_card_id)}</p>`;
  const workbook = raw.estimateSummary?.impact_workbook?.source;
  if (workbook) html += `<p class="muted">사용한 일정 자료: ${escapeHtml(workbook.title)} · v${escapeHtml(workbook.version)}
    · 문서 버전 ${escapeHtml(workbook.documentVersionId)} · SHA-256 ${escapeHtml(workbook.sha256)}</p>`;
  const lines = (raw.estimateSummary?.estimates || []).flatMap(e => e.lines || []);
  if (lines.length) html += `<table><thead><tr><th>산정 작업</th><th>직군</th><th>공수(MD)</th></tr></thead><tbody>${lines.map(line =>
    `<tr><td>${escapeHtml(line.task)}</td><td>${escapeHtml(line.role)}</td><td class="num">${escapeHtml(line.md ?? '미산정')}</td></tr>`).join('')}</tbody></table>`;
  const documents = Object.entries(raw.documents || {});
  for (const [kind,doc] of documents) html += `<article><h3>${escapeHtml(doc.subject || kind)}</h3><pre>${escapeHtml(doc.body)}</pre></article>`;
  const question = (raw.questions || []).find(q => q.status === 'open' && q.blocking !== false);
  if (raw.status === 'waiting_input' && question) {
    html += `<section class="box info"><h3>${escapeHtml(question.text)}</h3><div class="row">`;
    (question.choices || []).forEach((choice,i) => {html += `<button id="answer-choice-${i}">${escapeHtml(choice)}</button>`;});
    html += `<button id="answer-other">기타 직접 입력</button></div><div id="answer-input-area" hidden>
      <label>답변<textarea id="answer-text" maxlength="4000" rows="3"></textarea></label><button id="send-answer">답변하고 계속</button></div></section>`;
  }
  if (raw.status === 'waiting_final_review') {
    html += `<p>위 산정 결과와 문서 초안을 확인하고 검토 결과를 선택해주세요.</p><div class="row">
      <button id="review-approve" class="primary">확인 완료 · 최종 승인</button><button id="review-hold">보류</button>
      <button id="review-reject">수정 필요 · 반려</button></div>`;
  }
  if (raw.status === 'waiting') html += '<button id="resume-run">보류 해제 후 계속</button>';
  html += `<details><summary>상세 응답</summary><pre>${escapeHtml(JSON.stringify(raw,null,2))}</pre></details>`;
  $('orch-result').innerHTML = html;
  if (raw.status === 'waiting_input' && question) {
    (question.choices || []).forEach((choice,i) => $(`answer-choice-${i}`)?.addEventListener('click', wrap(() =>
      submitRunAction('answers',{questionId:question.question_id,answer:choice,disposition:choice==='모름'?'unknown':'provided'}))));
    $('answer-other')?.addEventListener('click', () => { $('answer-input-area').hidden=false; $('answer-text').focus(); });
    $('send-answer')?.addEventListener('click', wrap(() => {
      const answer = $('answer-text').value.trim();
      if (!answer) throw new Error('답변을 입력해주세요.');
      return submitRunAction('answers',{questionId:question.question_id,answer,disposition:'provided'});
    }));
  }
  for (const decision of ['approve','hold','reject']) $(`review-${decision}`)?.addEventListener('click',wrap(() =>
    submitRunAction('final-review',{decision})));
  $('resume-run')?.addEventListener('click',wrap(() => submitRunAction('resume')));
  if (raw.status === 'running') pollTimer=setTimeout(() => {if(!busy)wrap(refreshOrchestration)();},2000);
}

function wrap(fn) {
  return async (event) => {
    if (busy) return;
    const button = event?.currentTarget;
    busy = true;
    if (button) button.disabled = true;
    try {
      await fn();
    } catch (e) {
      alert(e.message || String(e));
    } finally {
      busy = false;
      if (button && button.isConnected && button.id !== 'btn-confirm-import') button.disabled = false;
      if (button?.id === 'btn-confirm-import' && latestPreview) button.disabled = false;
    }
  };
}

$("btn-load-projects").addEventListener("click", wrap(loadProjects));
$("btn-refresh-project").addEventListener("click", wrap(refreshProjectContext));
$("project-select").addEventListener("change", () => {
  $("project-id-manual").value = $("project-select").value;
});
$("cr-select").addEventListener("change", () => {
  $("cr-id-manual").value = $("cr-select").value;
});
$("btn-upload-preview").addEventListener("click", wrap(uploadAndPreview));
$("btn-preview-existing").addEventListener("click", wrap(() => previewDocumentVersion($("doc-version-id-manual").value.trim())));
$("btn-start-orch").addEventListener("click", wrap(() => runOrchestration("start")));
$("btn-recalc-orch").addEventListener("click", wrap(() => runOrchestration("recalculate")));
$("btn-refresh-orch").addEventListener("click", wrap(refreshOrchestration));

checkStatus();
