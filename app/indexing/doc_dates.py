"""문서 원문에서 효력일·서명일·기간을 찾는다 (#92, 정책 4: 효력일은 AI가 문서 원문으로 판단).

규칙 기반 추출이다. 확정값이 아니라 후보와 근거 문장을 함께 돌려주고, 판단은 에이전트가 한다.
  effective_date  효력일 / 효력 발생일 / 시행일 / 적용일
  signed_date     서명일 / 계약일 / 체결일
  period_start, period_end  계약기간 / 수행기간 / 사업기간 "A부터(~) B까지"
같은 종류가 여러 번 나오면(예: 변경계약서가 이전 효력일을 인용) 마지막 값을 쓰고 모두 evidence에 남긴다.
"""
import re
from typing import Iterable, Optional

_DATE = r"(\d{4})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})\s*일?"
_KINDS = {
    "effective_date": r"효력\s*(?:발생\s*)?일|효력\s*발생|시행\s*일|적용\s*일",
    "signed_date": r"서명\s*일|계약\s*일|체결\s*일",
}
_PERIOD_LABEL = r"(?:계약|수행|사업|용역|과업)\s*기간"
_GAP = r"[^\n\d]{0,20}?"   # 표시어와 날짜 사이 (예: "은 ", " : ")


def _iso(m: re.Match, start: int = 1) -> Optional[str]:
    y, mo, d = int(m.group(start)), int(m.group(start + 1)), int(m.group(start + 2))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"


def _quote(text: str, m: re.Match) -> str:
    return text[max(0, m.start() - 20): m.end() + 10].replace("\n", " ").strip()


def extract_dates(sections: Iterable[dict]) -> dict:
    """sections: [{"text", "heading"?, "section_id"?}] → 날짜 후보와 근거."""
    out = {"effective_date": None, "signed_date": None, "period_start": None, "period_end": None, "evidence": []}
    for sec in sections:
        text = sec.get("text") or ""
        where = sec.get("section_id") or sec.get("heading")
        for kind, label in _KINDS.items():
            for m in re.finditer(rf"(?:{label}){_GAP}{_DATE}", text):
                date = _iso(m)
                if date:
                    out[kind] = date
                    out["evidence"].append({"kind": kind, "date": date, "section": where, "quote": _quote(text, m)})
        for m in re.finditer(rf"{_PERIOD_LABEL}{_GAP}{_DATE}\s*(?:부터|~|－|-|–)\s*{_DATE}", text):
            start, end = _iso(m, 1), _iso(m, 4)
            if start and end:
                out["period_start"], out["period_end"] = start, end
                out["evidence"].append({"kind": "period", "date": f"{start}~{end}", "section": where,
                                        "quote": _quote(text, m)})
    return out


def effect_status(dates: dict, as_of: Optional[str]) -> str:
    """기준일 대비 효력 상태: effective / not_yet_effective / unknown."""
    eff = dates.get("effective_date") or dates.get("signed_date") or dates.get("period_start")
    if not eff or not as_of:
        return "unknown"
    return "not_yet_effective" if eff > as_of[:10] else "effective"
