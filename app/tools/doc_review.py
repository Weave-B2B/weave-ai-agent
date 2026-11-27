"""문서 검토 에이전트 규칙 도구 (#90) — LLM 없이 결과가 정해지는 도구. 에이전트(#91)와 Agent2가 함께 쓴다.

도구
- item_map / open_document : 새 파서 청크(heading·text·page) → 항목 지도(조항 코드·제목·쪽). 원문은 빼고 지도만 준다
- version_diff             : 같은 문서 2개 이상(최대 5개) 버전을 이웃끼리 비교해 항목별 변경 이력을 만든다
                             (수정·추가·삭제·되돌림·번호만 바뀜·사소한 변경·짝 불확실, 금액·날짜·기간·수량 변화)
                             짝은 원문 유사도로 짓고 같은 코드는 가산점만 준다(번호가 밀리거나 다시 매겨도 찾는다)
- extract_values / compare_values : 금액·날짜·기간·수량·비율을 뽑아 규칙으로 비교한다

판단(의미가 바뀌었는가, 문서끼리 어긋나는가)은 LLM이 한다. 이 도구는 "어디가 어떻게 바뀌었는지"를 정확히 준다.
정책: 2026-11-27 정책 5·6 (조항 코드 우선, 없으면 유사도, 애매하면 짝 불확실).
"""
from __future__ import annotations

import difflib
import re
from typing import Iterable

MAX_VERSIONS = 5
# 조항 코드: [A-F12] / TB-F10 / C-03 / Q-06 / CO-02 / REQ-001 / 제3조 (문서번호 CTR-A-001 속 A-001은 제외)
_CODE = re.compile(r"\[([A-Z]{1,5}-[A-Z]{0,3}\d{1,3}[A-Z]?)\]|(?<![A-Za-z0-9-])([A-Z]{1,5}-[A-Z]{0,3}\d{1,3}[A-Z]?)(?![A-Za-z0-9-])|(제\s*\d+\s*조)")
# 범위·의무를 뒤집는 말 — 이 말이 바뀌면 사소한 변경이 아니다
_SCOPE_WORDS = ("포함", "제외", "IN", "OUT", "불가", "가능", "필수", "선택", "의무", "금지", "허용", "승인", "효력", "무상", "유상")
_MINOR_RATIO = 0.9
_PAIR_RATIO = 0.6
# 같은 코드는 내용이 꽤 바뀌어도(짧은 조항의 포함→제외 등) 같은 항목으로 본다. 이보다 낮으면 같은 번호를 다른 조항에 다시 쓴 것으로 본다
_SAME_CODE_RATIO = 0.35


# ───────── 항목 지도 ─────────

def item_code(heading: str, text: str = "") -> str | None:
    """제목 마지막 경로 → 원문 앞부분 순으로 조항 코드를 찾는다."""
    last = heading.split(">")[-1]
    for source in (last, text[:80]):
        m = _CODE.search(source)
        if m:
            return re.sub(r"\s+", "", next(g for g in m.groups() if g))
    return None


def item_map(chunks: Iterable[dict]) -> list[dict]:
    """청크(section_id·heading·text·page) → 항목(item_id·code·heading·text·page). 순서 유지."""
    items = []
    for c in chunks:
        heading = c.get("heading") or ""
        text = c.get("text") or ""
        items.append({"item_id": c.get("item_id") or c.get("section_id"), "code": c.get("code") or item_code(heading, text),
                      "heading": heading, "text": text, "page": c.get("page")})
    return items


def open_document(doc: dict) -> dict:
    """문서 정보 + 항목 지도(원문 제외). 원문은 항목 원문 보기 도구로 꺼낸다."""
    items = doc.get("items") or []
    return {
        "document_id": doc.get("document_id"), "document_type": doc.get("document_type"), "version_no": doc.get("version_no"),
        "extraction_status": doc.get("extraction_status", "READY" if items else "FAILED"),
        "effective_date": doc.get("effective_date"), "baseline_relation": doc.get("baseline_relation"), "judgment_role": doc.get("judgment_role"),
        "items": [{"item_id": i["item_id"], "code": i.get("code"), "title": _title(i["heading"]), "page": i.get("page")} for i in items],
    }


def _title(heading: str) -> str:
    return heading.split(">")[-1].strip().strip("[]").strip()


# ───────── 수치·날짜 ─────────

_VALUE_PATTERNS = [
    ("date", re.compile(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})")),
    ("amount", re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)\s*(만\s*)?원")),
    ("percent", re.compile(r"(\d+(?:\.\d+)?)\s*%")),
    ("duration", re.compile(r"(\d+)\s*(영업일|개월|주|일)(?![자시])")),
    ("count", re.compile(r"(\d+)\s*(개|건|종|명|회|MD)")),
]


def extract_values(text: str) -> list[dict]:
    """금액(원)·날짜·비율·기간·수량을 뽑는다. value는 비교 가능한 정규화 값."""
    out, taken = [], []
    for kind, pattern in _VALUE_PATTERNS:
        for m in pattern.finditer(text):
            if any(a <= m.start() < b for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            if kind == "date":
                value = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            elif kind == "amount":
                value = int(m.group(1).replace(",", "")) * (10_000 if m.group(2) else 1)
            elif kind == "percent":
                value = float(m.group(1))
            else:
                value = f"{int(m.group(1))}{m.group(2)}"
            out.append({"kind": kind, "value": value, "raw": m.group(0), "context": text[max(0, m.start() - 20):m.end() + 10].replace("\n", " ")})
    return out


def compare_values(before: str, after: str) -> list[dict]:
    """두 원문의 수치·날짜 변화. 종류별로 사라진 값·새 값을 짝지어 돌려준다."""
    a, b = extract_values(before), extract_values(after)
    changes = []
    for kind in ("amount", "date", "duration", "count", "percent"):
        old = [v for v in a if v["kind"] == kind]
        new = [v for v in b if v["kind"] == kind]
        old_vals, new_vals = [v["value"] for v in old], [v["value"] for v in new]
        gone = [v for v in old if old_vals.count(v["value"]) > new_vals.count(v["value"])]
        came = [v for v in new if new_vals.count(v["value"]) > old_vals.count(v["value"])]
        for i in range(max(len(gone), len(came))):
            changes.append({"kind": kind, "before": gone[i]["raw"] if i < len(gone) else None,
                            "after": came[i]["raw"] if i < len(came) else None,
                            "context": (came[i] if i < len(came) else gone[i])["context"]})
    return changes


# ───────── 버전 차이 ─────────

def _norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text.lower())


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b), autojunk=False).ratio()


def _scope_words(text: str) -> set[str]:
    return {w for w in _SCOPE_WORDS if (re.search(rf"\b{w}\b", text) if w.isascii() else w in text)}


def _strip_codes(text: str) -> str:
    """조항 코드를 지운 원문 — 번호만 바뀐 항목을 알아보기 위해."""
    return _norm(_CODE.sub("", text))


def _pair(old: list[dict], new: list[dict]) -> tuple[list[tuple[int, int, bool]], list[int], list[int]]:
    """(old_i, new_j, uncertain) 짝, 짝 없는 old, 짝 없는 new.

    번호가 밀리거나(제3조→제4조) 코드를 다시 매겨도(A-F09→A-F17) 찾도록 **원문 유사도로 짝을 짓고, 같은 코드는 가산점만** 준다.
    ① 코드를 지운 원문이 같으면 바로 짝 ② 남은 항목은 (원문 유사도 + 같은 코드 0.1)이 높은 순으로 짝(0.6 이상)
    같은 코드는 원문 유사도 0.35, 다른 코드는 0.6 이상일 때만 짝(같은 번호를 다른 조항에 다시 쓴 경우를 거른다). 0.8 미만은 불확실.
    """
    pairs, used_old, used_new = [], set(), set()
    stripped_new: dict[str, list[int]] = {}
    for j, item in enumerate(new):
        stripped_new.setdefault(_strip_codes(item["text"]), []).append(j)
    for i, item in enumerate(old):
        key = _strip_codes(item["text"])
        js = [j for j in stripped_new.get(key, []) if j not in used_new] if key else []
        if js:
            j = next((j for j in js if new[j].get("code") == item.get("code")), js[0])
            pairs.append((i, j, False))
            used_old.add(i)
            used_new.add(j)
    candidates = []
    for i, o in enumerate(old):
        if i in used_old:
            continue
        for j, n in enumerate(new):
            if j in used_new:
                continue
            same_code = bool(o.get("code")) and o.get("code") == n.get("code")
            floor = _SAME_CODE_RATIO if same_code else _PAIR_RATIO
            sm = difflib.SequenceMatcher(None, _norm(o["text"]), _norm(n["text"]), autojunk=False)
            if sm.real_quick_ratio() < floor or sm.quick_ratio() < floor:
                continue
            body = sm.ratio()
            if same_code and body < _PAIR_RATIO and _ratio(_CODE.sub("", _title(o["heading"])), _CODE.sub("", _title(n["heading"]))) < 0.5:
                continue  # 같은 번호지만 제목도 다르다 → 다른 조항에 번호를 다시 쓴 것
            if not o.get("code") and not n.get("code"):
                body = max(body, _ratio(_title(o["heading"]), _title(n["heading"])) * 0.9)
            bonus = 0.1 if same_code else 0.0
            if body >= floor:
                candidates.append((body + bonus, body, i, j))
    for _, body, i, j in sorted(candidates, reverse=True):
        if i in used_old or j in used_new:
            continue
        pairs.append((i, j, body < 0.8))
        used_old.add(i)
        used_new.add(j)
    return pairs, [i for i in range(len(old)) if i not in used_old], [j for j in range(len(new)) if j not in used_new]


def _classify(before: str, after: str) -> dict:
    if before == after:
        return {"status": "same"}
    if _norm(before) != _norm(after) and _strip_codes(before) == _strip_codes(after):
        return {"status": "renumbered"}
    values = compare_values(before, after)
    ratio = _ratio(before, after)
    scope = _scope_words(before) ^ _scope_words(after)
    if _norm(before) == _norm(after) or (ratio >= _MINOR_RATIO and not values and not scope):
        return {"status": "minor", "similarity": round(ratio, 3)}
    return {"status": "modified", "similarity": round(ratio, 3), "value_changes": values, "scope_words_changed": sorted(scope),
            "diff": _diff(before, after)}


def _diff(before: str, after: str, limit: int = 6) -> list[dict]:
    """바뀐 구절(문자 단위). 화면 강조와 LLM 요약 입력용."""
    sm = difflib.SequenceMatcher(None, before, after, autojunk=False)
    out = []
    for tag, a1, a2, b1, b2 in sm.get_opcodes():
        if tag != "equal":
            out.append({"op": tag, "before": before[a1:a2], "after": after[b1:b2]})
        if len(out) >= limit:
            break
    return out


def version_diff(versions: list[dict]) -> dict:
    """같은 문서의 버전들(version_no 오름차순으로 정렬해 비교) → 항목별 이력.

    반환: {document_id, versions:[번호...], items:[{key, code, title, history:[{from,to,status,...}], summary}], unchecked:[...]}
    summary.change: added / removed / modified / reverted / renumbered(번호만 바뀜) / minor / same.
    summary.code_changed(번호가 바뀐 항목), code_reused(같은 번호를 다른 조항에 다시 씀), pairing_uncertain(짝 불확실).  추출 실패 버전은 비교하지 않고 unchecked로 알린다.
    """
    if len(versions) < 2:
        raise ValueError("버전을 2개 이상 주세요.")
    if len(versions) > MAX_VERSIONS:
        raise ValueError(f"버전은 한 번에 최대 {MAX_VERSIONS}개까지 비교합니다.")
    ids = {v.get("document_id") for v in versions}
    if len(ids) != 1:
        raise ValueError("같은 문서의 버전만 비교합니다.")
    ordered = sorted(versions, key=lambda v: v.get("version_no") or 0)
    failed = [v.get("version_no") for v in ordered if v.get("extraction_status") == "FAILED" or not v.get("items")]
    usable = [v for v in ordered if v.get("version_no") not in failed]
    result = {"document_id": ordered[0].get("document_id"), "versions": [v.get("version_no") for v in ordered],
              "unchecked": [{"version_no": n, "reason": "추출 실패 또는 항목 없음"} for n in failed], "items": []}
    if len(usable) < 2:
        return result

    # 항목 키: 첫 버전 항목부터 이웃 짝을 따라 이어 붙인다. 중간에 생긴 항목은 새 키.
    tracks: list[dict] = []  # {key, code, title, texts: {version_no: text}}
    current: dict[int, dict] = {}
    for idx, item in enumerate(usable[0]["items"]):
        track = {"key": item.get("code") or item["item_id"], "codes": {usable[0]["version_no"]: item.get("code")},
                 "title": _title(item["heading"]), "texts": {usable[0]["version_no"]: item["text"]}, "uncertain": False}
        tracks.append(track)
        current[idx] = track
    for prev, nxt in zip(usable, usable[1:]):
        pairs, _, added = _pair(prev["items"], nxt["items"])
        following: dict[int, dict] = {}
        for i, j, uncertain in pairs:
            track = current.get(i)
            if track is None:
                continue
            track["texts"][nxt["version_no"]] = nxt["items"][j]["text"]
            track["codes"][nxt["version_no"]] = nxt["items"][j].get("code")
            track["title"] = _title(nxt["items"][j]["heading"])
            track["uncertain"] = track["uncertain"] or uncertain
            following[j] = track
        for j in added:
            item = nxt["items"][j]
            track = {"key": item.get("code") or item["item_id"], "codes": {nxt["version_no"]: item.get("code")},
                     "title": _title(item["heading"]), "texts": {nxt["version_no"]: item["text"]}, "uncertain": False}
            tracks.append(track)
            following[j] = track
        current = following

    numbers = [v["version_no"] for v in usable]
    for track in tracks:
        history = []
        for a, b in zip(numbers, numbers[1:]):
            ta, tb = track["texts"].get(a), track["texts"].get(b)
            if ta is None and tb is None:
                continue
            if ta is None:
                history.append({"from": a, "to": b, "status": "added"})
            elif tb is None:
                history.append({"from": a, "to": b, "status": "removed"})
            else:
                entry = {"from": a, "to": b, **_classify(ta, tb)}
                ca, cb = track["codes"].get(a), track["codes"].get(b)
                if ca != cb:
                    entry["code_change"] = {"from": ca, "to": cb}
                history.append(entry)
        statuses = [h["status"] for h in history]
        first, last = track["texts"].get(numbers[0]), track["texts"].get(numbers[-1])
        if all(s == "same" for s in statuses):
            change = "same"
        elif first is None and last is not None:
            change = "added"
        elif first is not None and last is None:
            change = "removed"
        elif first is not None and last is not None and _norm(first) == _norm(last) and any(s in ("modified", "added", "removed") for s in statuses):
            change = "reverted"
        elif all(s in ("same", "renumbered") for s in statuses):
            change = "renumbered"
        elif all(s in ("same", "minor", "renumbered") for s in statuses):
            change = "minor"
        else:
            change = "modified"
        value_change = any(h.get("value_changes") for h in history)
        codes = [c for _, c in sorted(track["codes"].items())]
        last_code = next((c for c in reversed(codes) if c), None)
        result["items"].append({"key": track["key"], "code": last_code, "codes": [c for c in dict.fromkeys(codes) if c],
                                "title": track["title"], "history": history,
                                "summary": {"change": change, "value_change": value_change, "pairing_uncertain": track["uncertain"],
                                            "code_changed": len({c for c in codes if c}) > 1}})
    # 같은 번호를 다른 조항에 다시 쓴 경우: 삭제된 항목과 새 항목의 코드가 같다
    gone = {i["code"] for i in result["items"] if i["summary"]["change"] == "removed" and i["code"]}
    for item in result["items"]:
        if item["summary"]["change"] == "added" and item["code"] in gone:
            item["summary"]["code_reused"] = True
        if item["summary"]["change"] == "removed" and item["code"] in {i["code"] for i in result["items"] if i["summary"]["change"] == "added"}:
            item["summary"]["code_reused"] = True
    return result
