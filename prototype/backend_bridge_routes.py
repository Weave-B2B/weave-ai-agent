"""로컬 프로토타입 실연동 검증용 얇은 백엔드 전달 계층(PROTOTYPE_LAB=1 전용).

- 내부 서비스 토큰(X-Internal-Token)을 브라우저에 노출하거나 ADMIN 권한을 우회하지 않는다.
- 브라우저가 전달한 정상 사용자 자격(Authorization Bearer 또는 local 프로필 X-Dev-* 헤더)을
  그대로 BACKEND_BASE_URL의 공식 사용자 API(/api/v1/...)로 전달한다.
- BACKEND_BASE_URL이 없거나 실제 UUID가 없으면 중단하며 로컬 SQLite나 합성 ID로 폴백하지 않는다.
"""
from __future__ import annotations

import hashlib
import os
from urllib.parse import urlsplit
from uuid import UUID
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import ROOT, settings

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_XLSX_BYTES = 10_485_760

FORWARD_HEADERS = (
    "authorization",
    "x-company-id",
    "x-dev-company-id",
    "x-dev-member-id",
    "x-dev-member-role",
    "x-dev-user-id",
)


def require_local_bridge(request: Request):
    if os.environ.get("PROTOTYPE_LAB") != "1":
        raise HTTPException(404, "로컬 프로토타입 모드(PROTOTYPE_LAB=1)에서만 사용할 수 있습니다.")
    if request.client and request.client.host not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(403, "로컬 접속만 허용합니다.")
    origin = request.headers.get("origin")
    if origin and urlsplit(origin).netloc != request.url.netloc:
        raise HTTPException(403, "다른 출처의 요청은 허용하지 않습니다.")


router = APIRouter(
    prefix="/prototype-lab/backend",
    tags=["Local Backend Bridge"],
    dependencies=[Depends(require_local_bridge)],
)


def _backend_url() -> str:
    base = (settings.backend_base_url or "").strip().rstrip("/")
    if not base:
        raise HTTPException(
            503,
            "실연동 검증에는 BACKEND_BASE_URL 설정이 필요합니다. 로컬 SQLite나 합성 데이터로 자동 전환하지 않습니다.",
        )
    return base


def _user_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key in FORWARD_HEADERS:
        val = request.headers.get(key)
        if val and val.strip():
            headers[key] = val.strip()
    # Do not mix partial browser credentials with the configured loopback account.
    if not headers:
        headers = _configured_user_headers()
    if "authorization" not in headers and not (
        "x-dev-company-id" in headers and "x-dev-member-id" in headers and "x-dev-member-role" in headers
    ):
        raise HTTPException(
            401,
            "백엔드 사용자 인증 헤더(Authorization Bearer 또는 local 프로필 X-Dev-Company-Id/Member-Id/Member-Role)가 필요합니다.",
        )
    return headers


def _configured_user_headers() -> dict[str, str]:
    company = settings.prototype_backend_company_id.strip()
    member = settings.prototype_backend_member_id.strip()
    role = settings.prototype_backend_member_role.strip()
    if not (company and member and role):
        return {}
    _validate_uuid(company, "PROTOTYPE_BACKEND_COMPANY_ID")
    _validate_uuid(member, "PROTOTYPE_BACKEND_MEMBER_ID")
    if role not in {"ADMIN", "PM", "MEMBER"}:
        raise HTTPException(503, "로컬 테스트 계정 역할 설정을 확인해 주세요.")
    return {"x-dev-company-id": company, "x-dev-member-id": member, "x-dev-member-role": role}


def _validate_uuid(value: str, label: str) -> str:
    try:
        return str(UUID(value))
    except ValueError:
        raise HTTPException(400, f"{label}는 실제 UUID여야 합니다: {value}") from None


def _proxy(request: Request, method: str, path: str, *, json_body=None, params=None):
    base = _backend_url()
    headers = _user_headers(request)
    try:
        timeout = 180.0 if method == "POST" and "/orchestrations/" in path else 30.0
        with httpx.Client(base_url=base, timeout=max(timeout, float(settings.backend_timeout_sec))) as client:
            resp = client.request(method, path, headers=headers, json=json_body, params=params)
    except httpx.TransportError as e:
        raise HTTPException(502, f"실제 백엔드({base})에 연결할 수 없습니다: {type(e).__name__}") from None
    try:
        body = resp.json()
    except ValueError:
        body = {"message": resp.text[:500]}
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, body)
    return body


@router.get("/status")
def bridge_status():
    base = (settings.backend_base_url or "").strip().rstrip("/")
    if not base:
        return {"backend_connected": False, "backend_base_url": None, "storage": "disconnected"}
    reachable = False
    try:
        with httpx.Client(base_url=base, timeout=3.0) as client:
            r = client.get("/actuator/health")
            reachable = r.status_code == 200 and r.json().get("status") == "UP"
    except (httpx.TransportError, ValueError, AttributeError):
        reachable = False
    return {
        "backend_connected": reachable,
        "backend_base_url": base,
        "storage": "backend-postgresql" if reachable else "backend-unreachable",
    }


@router.get("/context")
def workspace_context():
    configured = _configured_user_headers()
    return {
        **bridge_status(),
        "account_configured": bool(configured),
        "actor_label": settings.prototype_backend_actor_label if configured else None,
    }


@router.get("/workbooks/{kind}")
def download_workbook(kind: Literal["template", "example"]):
    path = ROOT / "data" / "templates" / f"impact-workbook-{kind}.xlsx"
    if not path.is_file():
        raise HTTPException(404, "엑셀 양식 파일이 없습니다.")
    return FileResponse(path, media_type=XLSX_MIME, filename=path.name)


@router.get("/projects")
def list_projects(request: Request, page: int = 0, size: int = 50):
    return _proxy(request, "GET", "/api/v1/projects", params={"page": page, "size": size})


@router.get("/projects/{project_id}/rate-cards/current")
def current_rate_card(project_id: str, request: Request):
    pid = _validate_uuid(project_id, "project_id")
    return _proxy(request, "GET", f"/api/v1/projects/{pid}/rate-cards/current")


@router.get("/projects/{project_id}/change-requests")
def list_change_requests(project_id: str, request: Request, page: int = 0, size: int = 50):
    pid = _validate_uuid(project_id, "project_id")
    return _proxy(request, "GET", f"/api/v1/projects/{pid}/change-requests", params={"page": page, "size": size})

@router.post("/projects/{project_id}/change-requests")
def create_change_request(project_id: str, body: dict, request: Request):
    pid = _validate_uuid(project_id, "project_id")
    return _proxy(request, "POST", f"/api/v1/projects/{pid}/change-requests", json_body=body)


@router.post("/projects/{project_id}/workbook-upload")
async def upload_impact_workbook(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
    document_title: str = Form("일정·공수 엑셀 자료"),
    approve_for_schedule: bool = Form(False),
):
    """백엔드 공식 파일 업로드(upload-url → S3 PUT → complete) + 문서 등록(IMPACT_WORKBOOK)을 순차 수행한다."""
    pid = _validate_uuid(project_id, "project_id")
    base = _backend_url()
    headers = _user_headers(request)
    data = await file.read(MAX_XLSX_BYTES + 1)
    if not data:
        raise HTTPException(400, "업로드할 XLSX 파일이 비어 있습니다.")
    if len(data) > MAX_XLSX_BYTES:
        raise HTTPException(400, "XLSX 파일은 10MB 이하여야 합니다.")
    filename = file.filename or "impact-workbook.xlsx"
    sha256 = hashlib.sha256(data).hexdigest()

    try:
        with httpx.Client(base_url=base, timeout=30.0) as client:
            # 1) Presigned Upload URL 발급
            r1 = client.post(
                f"/api/v1/projects/{pid}/files/upload-url",
                headers=headers,
                json={
                    "originalFilename": filename,
                    "mimeType": XLSX_MIME,
                    "sizeBytes": len(data),
                    "sha256": sha256,
                    "filePurpose": "IMPACT_WORKBOOK",
                },
            )
            if r1.status_code >= 400:
                raise HTTPException(r1.status_code, r1.json() if r1.content else r1.text)
            upload_info = r1.json()
            file_object_id = upload_info["fileObjectId"]
            upload_url = upload_info["uploadUrl"]

            # 2) S3 Presigned PUT
            put_resp = httpx.put(upload_url, content=data, headers={"Content-Type": XLSX_MIME}, timeout=30.0)
            if put_resp.status_code >= 400:
                raise HTTPException(502, f"저장소(S3) 업로드 실패: HTTP {put_resp.status_code}")

            # 3) 업로드 완료 확인
            r3 = client.post(f"/api/v1/projects/{pid}/files/{file_object_id}/complete", headers=headers)
            if r3.status_code >= 400:
                raise HTTPException(r3.status_code, r3.json() if r3.content else r3.text)

            # 4) IMPACT_WORKBOOK 문서·버전 등록
            r4 = client.post(
                f"/api/v1/projects/{pid}/documents",
                headers=headers,
                json={
                    "documentTitle": document_title.strip() or filename,
                    "documentType": "IMPACT_WORKBOOK",
                    "fileObjectId": file_object_id,
                    "documentApprovalStatus": "APPROVED" if approve_for_schedule else "DRAFT",
                },
            )
            if r4.status_code >= 400:
                raise HTTPException(r4.status_code, r4.json() if r4.content else r4.text)
            doc_resp = r4.json()
            return {
                "fileObjectId": file_object_id,
                "sha256": sha256,
                "sizeBytes": len(data),
                "document": doc_resp,
            }
    except httpx.TransportError as e:
        raise HTTPException(502, f"백엔드/저장소 통신 실패: {type(e).__name__}") from None


class PreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documentVersionId: str


class ConfirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documentVersionId: str
    sourceSha256: str
    normalizedInputHash: str
    expectedCurrentRateCardVersionId: str | None = None
    operationId: str
    confirmZeroRates: bool = False


class StartOrchestrationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rateCardId: str | None = None
    autoExecute: bool = True


class RecalculateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rateCardId: str | None = None
    reason: str | None = None
    autoExecute: bool = True


class OperationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedStateRevision: int = Field(ge=0)
    operationId: str = Field(min_length=1, max_length=255)


class AnswerBody(OperationBody):
    questionId: str = Field(min_length=1)
    answer: str = Field(min_length=1, max_length=4000)
    disposition: Literal["provided", "unknown"] = "provided"


class ReviewBody(OperationBody):
    decision: Literal["approve", "hold", "reject"]


@router.post("/projects/{project_id}/rate-card-imports/preview")
def preview_rate_card(project_id: str, body: PreviewBody, request: Request):
    pid = _validate_uuid(project_id, "project_id")
    _validate_uuid(body.documentVersionId, "documentVersionId")
    return _proxy(
        request,
        "POST",
        f"/api/v1/projects/{pid}/rate-card-imports/preview",
        json_body=body.model_dump(),
    )


@router.post("/projects/{project_id}/rate-card-imports/confirm")
def confirm_rate_card(project_id: str, body: ConfirmBody, request: Request):
    pid = _validate_uuid(project_id, "project_id")
    _validate_uuid(body.documentVersionId, "documentVersionId")
    return _proxy(
        request,
        "POST",
        f"/api/v1/projects/{pid}/rate-card-imports/confirm",
        json_body=body.model_dump(),
    )


@router.post("/projects/{project_id}/change-requests/{cr_id}/orchestrations/start")
def start_orchestration(project_id: str, cr_id: str, body: StartOrchestrationBody, request: Request):
    pid = _validate_uuid(project_id, "project_id")
    cid = _validate_uuid(cr_id, "change_request_id")
    return _proxy(
        request,
        "POST",
        f"/api/v1/projects/{pid}/change-requests/{cid}/orchestrations/start",
        json_body=body.model_dump(),
    )


@router.post("/projects/{project_id}/change-requests/{cr_id}/orchestrations/recalculate")
def recalculate_orchestration(project_id: str, cr_id: str, body: RecalculateBody, request: Request):
    pid = _validate_uuid(project_id, "project_id")
    cid = _validate_uuid(cr_id, "change_request_id")
    return _proxy(
        request,
        "POST",
        f"/api/v1/projects/{pid}/change-requests/{cid}/orchestrations/recalculate",
        json_body=body.model_dump(),
    )


def _orchestration_path(project_id, cr_id, action):
    pid = _validate_uuid(project_id, "project_id")
    cid = _validate_uuid(cr_id, "change_request_id")
    return f"/api/v1/projects/{pid}/change-requests/{cid}/orchestrations/{action}"


@router.get("/projects/{project_id}/change-requests/{cr_id}/orchestrations/status")
def orchestration_status(project_id: str, cr_id: str, request: Request):
    return _proxy(request, "GET", _orchestration_path(project_id, cr_id, "status"))


@router.post("/projects/{project_id}/change-requests/{cr_id}/orchestrations/answers")
def answer_question(project_id: str, cr_id: str, body: AnswerBody, request: Request):
    return _proxy(request, "POST", _orchestration_path(project_id, cr_id, "answers"), json_body=body.model_dump())


@router.post("/projects/{project_id}/change-requests/{cr_id}/orchestrations/final-review")
def final_review(project_id: str, cr_id: str, body: ReviewBody, request: Request):
    return _proxy(request, "POST", _orchestration_path(project_id, cr_id, "final-review"), json_body=body.model_dump())


@router.post("/projects/{project_id}/change-requests/{cr_id}/orchestrations/resume")
def resume_orchestration(project_id: str, cr_id: str, body: OperationBody, request: Request):
    return _proxy(request, "POST", _orchestration_path(project_id, cr_id, "resume"), json_body=body.model_dump())
