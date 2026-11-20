"""합성 시연용 고정 응답 재생. 계산·반올림 공식이나 실제 Backend 장애 대체 경로가 없다.

응답은 Backend Java 계산기로 작성한다. 미등록 입력은 예시를 추정하지 않고 명시적으로 거절한다.
실제 UUID 단가표는 이 클라이언트에서 사용할 수 없다.
"""
import copy
import json
from functools import lru_cache

from app.core.config import ROOT
from app.kb import loader
from app.tools.backend_calc import BackendCalcError
from app.tools.calculation_snapshot import fingerprint
from app.tools.errors import ToolInputError

FIXTURES = ROOT / "data" / "fixtures" / "backend_calculations.json"


@lru_cache
def _fixtures():
    return json.loads(FIXTURES.read_text(encoding="utf-8"))["responses"]


def fixture_key(request, calendar):
    request = copy.deepcopy(request)
    # 화면의 작업 ID가 달라도 동일 입력의 예시를 재사용한다. 금액에 영향을 주는 값은 모두 키에 포함한다.
    request["lines"] = [{**line, "key": f"L{n + 1}"} for n, line in enumerate(request["lines"])]
    return fingerprint({"request": request, "calendar": calendar})


def replay_response(request, calendar, entries):
    entry = entries.get(fixture_key(request, calendar))
    if entry is None:
        raise BackendCalcError("이 입력에 해당하는 고정 예시가 없습니다. 실제 백엔드 계산을 사용해주세요.")
    if "error" in entry:
        raise ToolInputError(entry["error"])
    result = copy.deepcopy(entry)
    if result.get("pricing"):
        for part in (result["pricing"], result["schedule"]):
            for line, original in zip(part["lines"], request["lines"], strict=True):
                line["key"] = original["key"]
    return result


class FixtureCalcClient:
    def __init__(self, rate_card_id):
        from app.kb.backend_rates import is_backend_rate_id
        if is_backend_rate_id(rate_card_id):
            raise BackendCalcError("실제 단가표는 예시 계산 응답으로 처리할 수 없습니다.")
        self.rate_card_id = rate_card_id

    def _request(self, body):
        cards = loader.rate_cards()
        if self.rate_card_id not in cards:
            raise ToolInputError(f"unknown rate_card_id {self.rate_card_id}")
        rows = body.get("lines")
        if not isinstance(rows, list) or any(not isinstance(x, dict) or x.get("role") is None or x.get("md") is None
                                             or isinstance(x.get("md"), bool) for x in rows):
            raise ToolInputError("lines require role and numeric md")
        card = cards[self.rate_card_id]
        try:
            policy = loader.calc_policy()
        except FileNotFoundError as error:
            raise BackendCalcError("예시 계산 정책이 없습니다. 실제 백엔드 계산을 사용해주세요.") from error
        return {"rate_card": {"id": self.rate_card_id, "hours_per_md": card.get("hours_per_md"), "rates": card["rates"]},
                "lines": [{"key": x.get("key") or f"L{n + 1}", "role": x["role"], "md": x["md"]} for n, x in enumerate(rows)],
                "adjustments": body.get("adjustments") or {"expenses_state": "NOT_ENTERED"},
                "policy": {key: policy[key] for key in ("status", "hours_per_md", "vat", "contingency") if key in policy},
                "previous": body.get("previous")}

    def _lookup(self, request, calendar):
        return replay_response(request, calendar, _fixtures())

    def calculate(self, body):
        request, calendar = self._request(body), loader.calc_policy().get("schedule")
        result = self._lookup(request, calendar)
        if result.get("pricing"):
            inputs = {"request": request, "project_id": body.get("project_id"), "as_of": body.get("as_of"),
                      "timezone": "Asia/Seoul", "calendar": calendar}
            identity = fingerprint(inputs)
            result["snapshot"] = {"inputs": inputs, "input_hash": identity, "snapshot_id": identity,
                "policy_hash": fingerprint(request["policy"]), "calculated_at": "2026-11-18T00:00:00Z",
                "calculated_by": "fixture-replay"}
            result["schedule"]["as_of"] = body.get("as_of")
        return result

    def labor_totals(self, body):
        request = self._request({**body, "adjustments": {"expenses_state": "CONFIRMED_NONE"}, "previous": None})
        result = self._lookup(request, loader.calc_policy().get("schedule"))
        if result.get("status") == "NEEDS_INPUT":
            return {"status": "NEEDS_INPUT", "missing": result["missing"], "totals": None}
        return result["totals"]

    def policy_hash(self):
        return fingerprint(self._request({"lines": []})["policy"])

    def validate(self, body):
        p = body["pricing"]
        inputs = p.get("inputs") or {}
        if body.get("rate_card_id") != self.rate_card_id or inputs.get("project_id") != body.get("project_id"):
            raise ToolInputError("예시 계산의 프로젝트·단가표가 다릅니다.")
        request = inputs["request"]
        # 금액 계산 없이, Backend가 만든 고정 정답과 비교한다.
        result = self._lookup(request, inputs.get("calendar"))
        if not result.get("pricing") or any(p.get(k) != v for k, v in result["pricing"].items()):
            raise ToolInputError("계산 결과와 저장된 예시가 일치하지 않습니다.")
        expected = self._request({"lines": request["lines"], "adjustments": request.get("adjustments"),
                                  "previous": request.get("previous")})
        if fingerprint(request["rate_card"]) != fingerprint(expected["rate_card"]):
            raise ToolInputError("단가표가 변경되었습니다.")
        if body.get("current", True) and p["policy_hash"] != self.policy_hash():
            raise ToolInputError("계산 정책이 변경되었습니다. 재계산해주세요.")
        if (body.get("require_confirmed") or "cost:NOT_ENTERED" in p.get("provisional_reasons", [])) and p.get("provisional", True):
            raise ToolInputError("계산 정책과 경비를 확정한 후 승인해주세요.")
        return {"valid": True, "snapshot_id": p["snapshot_id"]}
