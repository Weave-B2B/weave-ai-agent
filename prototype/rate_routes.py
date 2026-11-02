"""Local-only rate card inspection for the prototype, including internal rates."""

from urllib.parse import urlsplit
from datetime import datetime, timezone
import json
import os
from uuid import uuid4
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.agents.agent3.effort import load_effort_rules
from app.agents.agent3.workflow import _hash
from app.kb.loader import rate_cards
from app.tools.errors import ToolInputError

from app.core.config import settings

router = APIRouter(prefix="/prototype-data", tags=["local prototype"])
RateValue = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]


class RateEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cost: RateValue | None
    price: RateValue | None


class SaveRateCard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_fingerprint: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)
    rates: dict[str, RateEntry]


def _local_request(request: Request, *, writing: bool = False):


    if not request.client or request.client.host not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(403, "로컬 테스트에서만 확인할 수 있습니다.")
    try:
        host = urlsplit("http://" + request.headers.get("host", "")).hostname
        origin = request.headers.get("origin")
        origin_host = urlsplit(origin).hostname if origin else None
    except ValueError:
        raise HTTPException(403, "로컬 화면에서만 확인할 수 있습니다.") from None
    if host not in ("127.0.0.1", "localhost", "::1", "testserver") or (origin and origin_host not in ("127.0.0.1", "localhost", "::1")):
        raise HTTPException(403, "로컬 화면에서만 확인할 수 있습니다.")
    if writing:
        if os.environ.get("PROTOTYPE_LAB") != "1":
            raise HTTPException(403, "단가표 수정은 로컬 테스트 모드에서만 가능합니다.")
        if not origin or urlsplit(origin).netloc != request.headers.get("host", "") or urlsplit(origin).scheme != "http":
            raise HTTPException(403, "현재 로컬 화면에서만 저장할 수 있습니다.")


@router.get("/rate-cards/{card_id}")
def get_rate_card(card_id: str, request: Request, response: Response):
    _local_request(request)
    try:
        cards = rate_cards()
        if card_id not in cards:
            raise HTTPException(404, "선택한 단가표를 찾을 수 없습니다.")
        card = cards[card_id]
        fingerprint = _hash({"rate_card": card, "effort_rules": load_effort_rules()})
    except (OSError, ToolInputError, ValueError):
        raise HTTPException(503, "단가표 데이터를 읽을 수 없습니다.") from None
    response.headers["Cache-Control"] = "no-store"
    return {"id": card_id, "synthetic": card.get("synthetic"), "unit": card["unit"],
            "vat_included": card["vat_included"], "rates": card["rates"], "fingerprint": fingerprint}


@router.post("/rate-cards/{card_id}/versions")
def save_rate_card(card_id: str, body: SaveRateCard, request: Request, response: Response):
    _local_request(request, writing=True)
    if not body.reason.strip() or set(body.rates) != {"PM", "UX", "FE", "BE", "QA"}:
        raise HTTPException(422, "수정 사유와 모든 직군의 단가 항목을 확인해 주세요.")
    try:
        cards = rate_cards()
        if card_id not in cards:
            raise HTTPException(404, "선택한 단가표를 찾을 수 없습니다.")
        original = cards[card_id]
        fingerprint = _hash({"rate_card": original, "effort_rules": load_effort_rules()})
        if fingerprint != body.expected_fingerprint:
            raise HTTPException(409, "단가표 또는 공수 기준이 변경되었습니다. 다시 열어 확인해 주세요.")
        version_id = "RC-LOCAL-" + uuid4().hex
        card = {**original, "rates": {role: rate.model_dump() for role, rate in body.rates.items()},
                "source_rate_card_id": card_id, "edit_reason": body.reason.strip(),
                "created_at": datetime.now(timezone.utc).isoformat()}
        directory = settings.data_dir.parent / ".prototype-rate-cards"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = directory / (version_id + ".json")
        temporary = directory / (version_id + ".tmp")
        try:
            with open(temporary, "x", encoding="utf-8", opener=lambda path, flags: os.open(path, flags, 0o600)) as file:
                json.dump({"rate_cards": {version_id: card}}, file, ensure_ascii=False, allow_nan=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        response.headers["Cache-Control"] = "no-store"
        return {"id": version_id, "source_id": card_id}
    except (OSError, ToolInputError, ValueError):
        raise HTTPException(503, "수정한 단가표를 저장할 수 없습니다.") from None
