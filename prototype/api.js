export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function detailMessage(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map(item => `${item.loc?.join(".") || "입력"}: ${item.msg || "확인 필요"}`).join(" · ");
  if (detail && typeof detail === "object") return detail.message || detail.code || JSON.stringify(detail);
  return "요청을 처리하지 못했습니다.";
}

export class Agent3Api {
  async request(path, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 65000);
    try {
      const response = await fetch(`/agent3/impacts${path}`, {
        ...options,
        headers: { "Content-Type": "application/json", ...options.headers },
        signal: controller.signal,
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new ApiError(response.status, detailMessage(body?.detail));
      if (!body?.impact || !body.session_id) throw new ApiError(502, "API 응답 형식이 예상한 Agent 3 계약과 다릅니다.");
      return body;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError(0, error.name === "AbortError" ? "응답 시간이 초과됐습니다. 서버 상태를 확인해 주세요." : "AI 서버에 연결할 수 없습니다. 로컬 서버 실행 상태를 확인해 주세요.");
    } finally {
      clearTimeout(timer);
    }
  }
  prepare(input) { return this.request("", { method: "POST", body: JSON.stringify(input) }); }
  get(id) { return this.request(`/${encodeURIComponent(id)}`); }
  edit(id, input) { return this.request(`/${encodeURIComponent(id)}/edit`, { method: "POST", body: JSON.stringify(input) }); }
  review(id, input) { return this.request(`/${encodeURIComponent(id)}/review`, { method: "POST", body: JSON.stringify(input) }); }
}

// State preview is deliberately read-only; it never calculates a replacement quote.
export class MockPreview {
  constructor(snapshot) { this.snapshot = snapshot; }
  async prepare(scenario) {
    await new Promise(resolve => setTimeout(resolve, 300));
    if (scenario.startsWith("error-")) {
      const status = Number(scenario.slice(6));
      throw new ApiError(status, "상태 미리보기에서 재현한 오류입니다. 실제 API를 호출하지 않았습니다.");
    }
    const view = structuredClone(this.snapshot);
    if (scenario === "no-target") {
      view.phase = "NOT_REQUIRED"; view.impact.status = "NOT_REQUIRED"; view.impact.estimates = [];
    } else if (scenario === "upstream") {
      view.phase = "WAITING_UPSTREAM_REVIEW"; view.impact.status = "WAITING_REVIEW"; view.impact.estimates = [];
    } else if (scenario === "missing-rate") {
      view.phase = "WAITING_INPUT"; view.impact.status = "NEEDS_INPUT";
      view.impact.questions = [{ text: "필수 단가 정보가 없습니다. 담당자에게 단가표를 확인해 주세요.", blocking: true, status: "open" }];
      for (const item of view.impact.estimates) item.totals = null;
    } else if (scenario === "failed") {
      view.phase = "FAILED"; view.impact.status = "ERROR"; view.impact.estimates = [];
    }
    return view;
  }
}

// Agent 2 범위 판단: POST /agent2/assessments (Agent2Input → Agent2Output). 결과는 저장하지 않으므로 세션이 없다.
export class Agent2Api {
  async assess(input) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 120000);   // 에이전트가 여러 번 조사하므로 Agent 3보다 길게
    try {
      const response = await fetch("/agent2/assessments", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input), signal: controller.signal,
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new ApiError(response.status, detailMessage(body?.detail));
      if (!Array.isArray(body?.assessments) || !body?.meta) throw new ApiError(502, "API 응답 형식이 예상한 Agent 2 계약과 다릅니다.");
      return body;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError(0, error.name === "AbortError" ? "응답 시간이 초과됐습니다. 서버 상태를 확인해 주세요." : "AI 서버에 연결할 수 없습니다. 로컬 서버 실행 상태를 확인해 주세요.");
    } finally {
      clearTimeout(timer);
    }
  }
}
