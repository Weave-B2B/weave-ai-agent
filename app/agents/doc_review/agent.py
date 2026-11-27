"""문서 검토 에이전트 (#91): LLM이 매 턴 다음 행동을 고르고, 코드가 울타리와 빠짐없음을 지킨다.

    관찰(문서 항목 지도·버전 차이·지금까지의 도구 결과·남은 항목) → LLM 행동 선택 → 코드가 도구 실행 → 반복
    finish를 고르면 남은 항목이 없을 때만 끝난다. 단계 한도에 닿으면 본 것만으로 마무리하고 남은 항목은 "미확인".

LLM이 정하는 것: 무엇을 무엇과 비교할지, 같은 항목인지, 어긋나는지, 의미·확신도.
코드가 강제하는 것: 주어진 문서·항목만 열람, 항목 키 검증, 모든 항목 확인(빠짐없음), 추출 실패 문서 미확인 표시,
같은 문서 버전은 규칙 도구(version_diff)를 먼저 돌려 관찰에 싣기, 단계 상한.
도구 7개: open_document(관찰에 포함) · find_same_items · read_items · version_diff(관찰에 포함) · compare_values ·
prior_judgments · record_finding/mark_checked(기록).
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.agents.base import AgentFailure
from app.agents.doc_review.prompts import AGENT_SYSTEM, FINAL_SYSTEM
from app.tools.doc_review import (compare_values, find_same_items, item_key, open_document, prior_judgment_refs, read_items,
                                  version_diff)

AGENT_VERSION = "doc-review-agent-0.1.0"
FindingType = Literal["CHANGE", "SCOPE_MISMATCH", "MISSING", "VALUE_MISMATCH", "EFFECT_APPROVAL", "PRIOR_JUDGMENT_IMPACT"]
Action = Literal["find_same_items", "read_items", "compare_values", "prior_judgments", "record_finding", "mark_checked", "finish"]


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FindingDraft(_Out):
    type: FindingType
    items: list[str] = Field(description="근거 항목 키 (문서ID@v버전#항목ID). 관련 문서마다 하나 이상")
    title: str = Field(description="한 줄 제목")
    explanation: str = Field(description="무엇이 어떻게 어긋나거나 바뀌었는지, 업무 의미 1~3문장")
    confidence: Literal["high", "medium", "low"]
    change: Optional[Literal["added", "removed", "modified", "reverted"]] = Field(None, description="CHANGE일 때 변경 종류")


class Step(_Out):
    """LLM이 고르는 다음 행동 하나. action에 맞는 인자만 채운다."""
    thought: str = Field(description="이 행동을 고른 이유 한두 문장")
    action: Action
    query: Optional[str] = Field(None, description="find_same_items: 찾을 내용(조항 코드·핵심 단어)")
    keys: list[str] = Field([], description="read_items·mark_checked: 항목 키 목록")
    before_key: Optional[str] = Field(None, description="compare_values: 비교할 앞 항목 키")
    after_key: Optional[str] = Field(None, description="compare_values: 비교할 뒤 항목 키")
    document_id: Optional[str] = Field(None, description="prior_judgments: 문서 ID")
    codes: list[str] = Field([], description="prior_judgments: 조항 코드")
    finding: Optional[FindingDraft] = Field(None, description="record_finding: 기록할 후보")


class FinalFindings(_Out):
    findings: list[FindingDraft] = []


class DocReviewInput(BaseModel):
    """documents: 항목 지도 [{document_id, document_type, version_no, extraction_status, items:[{item_id, code, heading, text, page}]}]."""
    as_of: Optional[str] = None
    documents: list[dict]
    prior_judgments: list[dict] = []


class Finding(BaseModel):
    type: str
    docs: list[str]
    codes: list[str]
    items: list[str]
    title: str
    explanation: str
    confidence: str
    change: Optional[str] = None
    source: Literal["agent", "system"] = "agent"


class DocReviewResult(BaseModel):
    agent_version: str = AGENT_VERSION
    findings: list[Finding]
    unchecked: list[dict]
    coverage: dict
    version_diffs: list[dict] = []
    trace: list[dict] = []
    usage: dict = {}


class DocReviewAgent:
    max_steps = 40                       # 비용·지연 울타리 (항목이 많으면 mark_checked로 묶어 표시)
    max_pushbacks = 2                    # finish 거절(남은 항목 알림) 횟수

    def __init__(self, llm):
        self.llm = llm
        self._linked: dict[str, set[str]] = {}
        self._read: dict[str, dict] = {}         # 이번 실행에서 읽은 원문 — 관찰의 <read_items>에 한 번만 싣는다

    # ---- 실행 ----
    def run(self, inp: DocReviewInput) -> DocReviewResult:
        self._read = {}
        docs = inp.documents
        ready = [d for d in docs if d.get("extraction_status", "READY") != "FAILED" and d.get("items")]
        failed = [d for d in docs if d not in ready]
        all_keys = {item_key(d, i): (d, i) for d in ready for i in d["items"]}
        checked: set[str] = set()
        findings: list[Finding] = []
        history: list[dict] = []
        usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "models": set()}

        diffs = self._version_diffs(ready)
        # 같은 문서 버전들의 같은 항목은 한 묶음 — 하나를 확인·기록하면 다른 버전도 확인한 것으로 본다
        linked: dict[str, set[str]] = {}
        for diff, versions in diffs:
            for track in diff["items"]:
                names = set(track["codes"]) | {track["key"]}
                group = {item_key(d, i) for d in versions for i in d["items"] if (i.get("code") or i["item_id"]) in names}
                for k in group:
                    linked[k] = group
                # 규칙이 "변경 없음"으로 본 항목은 의미 판단 대상이 아니다
                if track["summary"]["change"] == "same":
                    checked.update(group)
        self._linked = linked
        # 같은 번호 재사용(code_reused) 항목: 에이전트가 수정 하나로 기록해도 삭제·추가로 나눈다 (정책 5)
        self._reused: dict[str, tuple[str, str]] = {}
        for diff, versions in diffs:
            reused = [t for t in diff["items"] if t["summary"].get("code_reused")]
            old = next((t for t in reused if t["summary"]["change"] == "removed"), None)
            new = next((t for t in reused if t["summary"]["change"] == "added"), None)
            if old and new:
                first, last = versions[0], versions[-1]
                ko = next((item_key(first, i) for i in first["items"] if (i.get("code") or i["item_id"]) == old["code"]), None)
                kn = next((item_key(last, i) for i in last["items"] if (i.get("code") or i["item_id"]) == new["code"]), None)
                if ko and kn:
                    self._reused[ko] = self._reused[kn] = (ko, kn)
        overview = self._overview(inp, ready, failed, [d for d, _ in diffs])

        pushbacks = 0
        for n in range(self.max_steps):
            remaining = [k for k in all_keys if k not in checked]
            step = self._ask(AGENT_SYSTEM, self._observe(overview, history, findings, remaining, self.max_steps - n), Step,
                             "choose_next_action", usage)
            if step.action == "finish":
                if remaining and pushbacks < self.max_pushbacks:
                    pushbacks += 1
                    history.append({"action": "finish", "result": {"rejected": "아직 확인하지 않은 항목이 있다", "remaining": remaining[:40]}})
                    continue
                history.append({"action": "finish"})
                break
            result = self._run_tool(step, inp, ready, all_keys, checked, findings)
            history.append({"thought": step.thought, "action": step.action,
                            "args": step.model_dump(exclude={"thought", "action", "finding"}, exclude_defaults=True),
                            "result": result})
        else:
            extra = self._ask(FINAL_SYSTEM, self._observe(overview, history, findings, [k for k in all_keys if k not in checked], 0),
                              FinalFindings, "submit_findings", usage)
            for draft in extra.findings:
                self._record(draft, all_keys, checked, findings)

        unchecked = [{"document_id": d["document_id"], "version_no": d.get("version_no"), "reason": "추출 실패 또는 항목 없음"} for d in failed]
        unchecked += [{"key": k, "document_id": all_keys[k][0]["document_id"], "code": all_keys[k][1].get("code"), "reason": "검토 단계 안에 확인하지 못함"}
                      for k in all_keys if k not in checked]
        for d in failed:
            findings.append(Finding(type="UNCHECKED", docs=[d["document_id"]], codes=[], items=[], title=f"{d['document_id']} 미확인",
                                    explanation="문서 내용을 추출하지 못해 검토하지 못했습니다. 이 문서에 대해서는 '문제 없음'이 아닙니다.",
                                    confidence="high", source="system"))
        usage["models"] = sorted(usage["models"])
        return DocReviewResult(findings=findings, unchecked=unchecked,
                               coverage={"items": len(all_keys), "checked": len(checked & set(all_keys)), "failed_documents": len(failed)},
                               version_diffs=[d for d, _ in diffs], trace=history, usage=usage)

    # ---- 관찰 ----
    @staticmethod
    def _version_diffs(ready: list[dict]) -> list[tuple[dict, list[dict]]]:
        groups: dict[str, list[dict]] = defaultdict(list)
        for d in ready:
            groups[d["document_id"]].append(d)
        return [(version_diff(vs[:5]), vs[:5]) for vs in groups.values() if len(vs) > 1]

    @staticmethod
    def _overview(inp: DocReviewInput, ready: list[dict], failed: list[dict], diffs: list[dict]) -> str:
        docs = []
        for d in ready:
            opened = open_document(d)
            docs.append({"document_id": d["document_id"], "type": d.get("document_type"), "version_no": d.get("version_no"),
                         "items": [{"key": item_key(d, i), "code": i.get("code"), "title": o["title"]}
                                   for i, o in zip(d["items"], opened["items"])]})
        brief_diffs = [{"document_id": x["document_id"], "versions": x["versions"],
                        "changes": [{"code": i["code"], "codes": i["codes"], "title": i["title"], **i["summary"],
                                     "history": [{k: v for k, v in h.items() if k in ("from", "to", "status", "value_changes", "scope_words_changed", "diff", "code_change")}
                                                 for h in i["history"]]}
                                    for i in x["items"] if i["summary"]["change"] != "same"]} for x in diffs]
        parts = [f"<as_of>{inp.as_of or '미지정'}</as_of>", "{documents}"]
        if brief_diffs:
            parts.append("<version_diff>\n" + json.dumps(brief_diffs, ensure_ascii=False) + "\n</version_diff>")
        if failed:
            parts.append("<failed_documents>" + ", ".join(d["document_id"] for d in failed) + " — 추출 실패. 시스템이 미확인으로 표시한다</failed_documents>")
        if inp.prior_judgments:
            parts.append(f"<prior_judgments_available>기존 AI 판단 {len(inp.prior_judgments)}건 — prior_judgments 도구로 문서·코드별 조회</prior_judgments_available>")
        return "\n".join(parts), docs

    @staticmethod
    def _documents(docs: list[dict], remaining: set[str]) -> str:
        """비용 울타리: 남은 항목은 키·코드·제목, 확인한 항목은 키만 보여 준다."""
        out = []
        for d in docs:
            out.append({"document_id": d["document_id"], "type": d["type"], "version_no": d["version_no"],
                        "remaining": [i for i in d["items"] if i["key"] in remaining],
                        "checked_keys": [i["key"] for i in d["items"] if i["key"] not in remaining]})
        return "<documents>\n" + json.dumps(out, ensure_ascii=False) + "\n</documents>"

    def _observe(self, overview: tuple, history: list[dict], findings: list[Finding], remaining: list[str], steps_left: int) -> str:
        text, docs = overview
        overview = text.replace("{documents}", self._documents(docs, set(remaining)), 1)
        # 비용 울타리: 행동 기록은 최근 10개(읽은 원문은 기록이 아니라 <read_items>에 따로 있어 잊지 않는다)
        recent = history[-10:]
        read = [{"key": k, **v} for k, v in self._read.items()]
        return "\n".join([overview,
                          "<read_items>\n" + json.dumps(read, ensure_ascii=False) + "\n</read_items>",
                          "<findings>\n" + json.dumps([f.model_dump(include={"type", "items", "title"}) for f in findings], ensure_ascii=False) + "\n</findings>",
                          "<history>\n" + json.dumps(recent, ensure_ascii=False)[:24000] + "\n</history>",
                          f"<remaining count=\"{len(remaining)}\">" + json.dumps(remaining[:60], ensure_ascii=False) + "</remaining>",
                          f"<steps_left>{steps_left}</steps_left>"])

    # ---- 도구 ----
    def _run_tool(self, step: Step, inp: DocReviewInput, ready: list[dict], all_keys: dict, checked: set, findings: list) -> dict:
        if step.action == "find_same_items":
            if not step.query:
                return {"error": "query 필요"}
            return {"results": find_same_items(step.query, ready)}
        if step.action == "read_items":
            if not step.keys:
                return {"error": "keys 필요"}
            new = [k for k in step.keys[:6] if k not in self._read]
            got = read_items(new, ready) if new else []
            for r in got:
                if "error" not in r:
                    self._read[r["key"]] = {k: r[k] for k in ("code", "heading", "page", "text")}
            # 원문은 <read_items>에 한 번만 싣고, 기록에는 키만 남긴다
            return {"read": [r["key"] for r in got if "error" not in r],
                    **({"already_read": [k for k in step.keys[:6] if k not in new]} if len(new) < len(step.keys[:6]) else {}),
                    **({"errors": [r for r in got if "error" in r]} if any("error" in r for r in got) else {})}
        if step.action == "compare_values":
            if step.before_key not in all_keys or step.after_key not in all_keys:
                return {"error": "before_key·after_key는 문서 항목 키여야 함"}
            return {"changes": compare_values(all_keys[step.before_key][1]["text"], all_keys[step.after_key][1]["text"])}
        if step.action == "prior_judgments":
            if not step.document_id:
                return {"error": "document_id 필요"}
            return {"judgments": prior_judgment_refs(inp.prior_judgments, step.document_id, step.codes)}
        if step.action == "mark_checked":
            bad = [k for k in step.keys if k not in all_keys]
            for k in step.keys:
                if k in all_keys:
                    checked.update(self._linked.get(k, {k}))
            return {"checked": len(step.keys) - len(bad), **({"unknown_keys": bad} if bad else {})}
        if step.action == "record_finding":
            if step.finding is None:
                return {"error": "finding 필요"}
            return self._record(step.finding, all_keys, checked, findings)
        return {"error": f"알 수 없는 행동 {step.action}"}

    def _record(self, draft: FindingDraft, all_keys: dict, checked: set, findings: list) -> dict:
        pair = next((self._reused[k] for k in draft.items if k in self._reused), None)
        if draft.type == "CHANGE" and pair and draft.change not in ("removed", "added"):
            for key, change in zip(pair, ("removed", "added")):
                self._record(draft.model_copy(update={"items": [key], "change": change}), all_keys, checked, findings)
            return {"recorded": len(findings), "note": "같은 번호 재사용 — 삭제·추가로 나눠 기록함"}
        keys = [k for k in draft.items if k in all_keys]
        if not keys:
            return {"error": "items에 문서 항목 키가 하나도 없음 — 지어낸 키는 쓸 수 없다"}
        docs = list(dict.fromkeys(all_keys[k][0]["document_id"] for k in keys))
        codes = list(dict.fromkeys(all_keys[k][1].get("code") or all_keys[k][1]["item_id"] for k in keys))
        dup = next((f for f in findings if f.type == draft.type and set(f.items) == set(keys)), None)
        if dup:
            return {"note": "이미 같은 후보가 있음"}
        findings.append(Finding(type=draft.type, docs=docs, codes=codes, items=keys, title=draft.title, explanation=draft.explanation,
                                confidence=draft.confidence, change=draft.change))
        for k in keys:
            checked.update(self._linked.get(k, {k}))
        return {"recorded": len(findings), "unknown_keys": [k for k in draft.items if k not in all_keys]}

    def _ask(self, system: str, user: str, schema, tool_name: str, usage: dict):
        try:
            r = self.llm.complete_structured(system=system, user=user, schema=schema, tool_name=tool_name)
        except AgentFailure as e:
            raise AgentFailure(e.error.model_copy(update={"stage": "doc_review"})) from None
        usage["calls"] += 1
        u = getattr(r, "usage", None)
        if u is not None:
            usage["input_tokens"] += getattr(u, "input_tokens", 0) or 0
            usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0
        usage["models"].add(r.label)
        return r.parsed
