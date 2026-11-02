"""색인 메시지. 필드는 weave-backend의 DocumentIngestRequestedEvent(A-MQ-01)·IngestResultRequest(A-MQ-02)를 따른다.
manifest·embeddingProfileId·documentVersionId는 AI가 결과에 추가로 싣는 값이다 (CN에 추가 요청, VDB 설계 v0.3 §6)."""
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class _Msg(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    def to_json(self) -> dict:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class IndexRequest(_Msg):
    request_id: UUID
    company_id: Optional[UUID] = None
    project_id: UUID
    document_id: Optional[UUID] = None
    document_version_id: UUID
    file_object_id: Optional[UUID] = None
    object_key: str
    mime_type: str = "application/pdf"
    bucket: Optional[str] = None                  # 백엔드는 버킷 하나(weave-dev-files) — 비우면 설정값
    sha256: Optional[str] = Field(None, pattern=r"^[0-9a-f]{64}$")   # FileObject.sha256 (추가 요청). 있으면 검증


class ObjectRef(_Msg):
    bucket: str
    key: str
    sha256: str


FailedStage = Literal["EXTRACT", "CHUNK", "EMBED"]   # 백엔드 ProcessingFailedStage


class IndexResult(_Msg):
    status: Literal["READY", "FAILED"]
    request_id: UUID
    failed_stage: Optional[FailedStage] = None
    error_code: Optional[str] = Field(None, max_length=100)
    error_message: Optional[str] = None
    retryable: Optional[bool] = None
    extraction_version: Optional[str] = Field(None, max_length=100)
    section_count: Optional[int] = Field(None, ge=0)
    # ---- AI 추가 필드 ----
    document_version_id: Optional[UUID] = None
    embedding_profile_id: Optional[str] = None
    manifest: Optional[ObjectRef] = None
