"""Local-only test endpoints: upload a PDF, index it, and create a new baseline version.

Real upload, approval and baseline management belong to the backend (CN). This router feeds the same
index_document pipeline that the SQS worker will call, and keeps every result in .prototype-kb/.
"""
import base64
import binascii
import json
import shutil
from datetime import date
from pathlib import Path
from threading import RLock
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.indexing.messages import IndexRequest
from app.indexing.objects import DirStore
from app.indexing.pipeline import index_document
from app.kb import loader
from app.kb.loader import baselines, clear_caches, documents, sections, superseded_by
from app.kb.vector_store import PgVectorStore
from app.storage.erd import to_uuid
from app.tools import doc_tools

router = APIRouter(prefix="/kb", tags=["local prototype"])
lock = RLock()
SOURCE_BUCKET, RESULT_BUCKET = "lab-source", "lab-result"
MAX_PDF_BYTES = 10 * 1024 * 1024
DOC_TYPES = {"contract": 2, "sow": 3, "functional_spec": 4, "quote": 5, "change_order": 1}


class UploadDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=200)
    doc_type: str
    content_base64: str = Field(min_length=1)


class CreateBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_ids: list[str] = Field(min_length=1)


def _root() -> Path:
    return loader.lab_kb_path().parent


def _load() -> dict:
    p = loader.lab_kb_path()
    data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    return {"documents": [], "sections": [], "pages": {}, "baselines": {}, **data}


def _save(data: dict) -> None:
    p = loader.lab_kb_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    clear_caches()


def _project(project_id: str) -> None:
    if not any(d["project_id"] == project_id for d in documents().values()):
        raise HTTPException(404, f"알 수 없는 프로젝트: {project_id}")


def _vectors():
    vb = doc_tools.vector_backend()
    if vb is None or not isinstance(vb[1], PgVectorStore):
        raise HTTPException(409, "vector-db 연결이 필요합니다. .env에 VECTOR_STORE=pgvector, VECTOR_DB_URL, 임베딩 키를 설정해 주세요.")
    return vb


def _active(project_id: str) -> str | None:
    ids = [k for k, b in baselines().items() if b["project_id"] == project_id and not superseded_by(k)]
    return max(ids, key=lambda k: baselines()[k]["as_of"]) if ids else None


@router.get("/projects")
def projects(request: Request):
    from prototype.lab import local_only
    local_only(request)
    lab = {d["doc_id"] for d in _load()["documents"]}
    counts = {}
    for d, _ in sections():
        counts[d] = counts.get(d, 0) + 1
    out = {}
    for doc in documents().values():
        out.setdefault(doc["project_id"], {"project_id": doc["project_id"], "documents": [], "baselines": []})["documents"].append({
            "doc_id": doc["doc_id"], "title": doc.get("title") or doc["doc_id"], "doc_type": doc["doc_type"],
            "version": doc.get("version"), "section_count": counts.get(doc["doc_id"], 0), "source": "lab" if doc["doc_id"] in lab else "dataset"})
    for k, b in sorted(baselines().items(), key=lambda kv: kv[1]["as_of"]):
        if b["project_id"] in out:
            latest = superseded_by(k)
            out[b["project_id"]]["baselines"].append({"baseline_id": k, "as_of": b["as_of"], "document_ids": b["document_ids"],
                "supersedes": b.get("supersedes"), "status": "SUPERSEDED" if latest else "ACTIVE", "superseded_by": latest,
                "source": "lab" if k in _load()["baselines"] else "dataset"})
    for p in out.values():
        used = {d for b in p["baselines"] for d in b["document_ids"]}
        for doc in p["documents"]:
            doc["in_baseline"] = doc["doc_id"] in used
        p["active_baseline_id"] = _active(p["project_id"])
    return {"projects": sorted(out.values(), key=lambda p: p["project_id"]), "doc_types": list(DOC_TYPES)}


@router.post("/projects/{project_id}/documents")
def upload(project_id: str, body: UploadDocument, request: Request):
    from prototype.lab import local_only
    local_only(request)
    _project(project_id)
    if body.doc_type not in DOC_TYPES:
        raise HTTPException(422, f"문서 종류를 확인해 주세요: {body.doc_type}")
    try:
        pdf = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "파일 내용을 읽지 못했습니다.") from None
    if len(pdf) > MAX_PDF_BYTES:
        raise HTTPException(413, "10MB 이하 PDF만 올릴 수 있습니다.")
    emb, store = _vectors()
    dv = uuid4()
    key = f"{dv}.pdf"
    objects = DirStore(_root() / "objects")
    objects.put(SOURCE_BUCKET, key, pdf, "application/pdf")
    req = IndexRequest(request_id=uuid4(), project_id=to_uuid("project", project_id), document_version_id=dv,
                       object_key=key, mime_type="application/pdf", bucket=SOURCE_BUCKET)
    result = index_document(req, objects, emb, store, SOURCE_BUCKET, RESULT_BUCKET)
    out = {"result": result.to_json(), "document": None}
    if result.status != "READY":
        return out
    manifest = json.loads(objects.get(result.manifest.bucket, result.manifest.key))
    pages = objects.get(manifest["textSnapshot"]["bucket"], manifest["textSnapshot"]["key"]).decode("utf-8").split("\f")
    doc_id, today = str(dv), date.today().isoformat()
    doc = {"doc_id": doc_id, "project_id": project_id, "doc_type": body.doc_type, "version": "lab",
           "effective_date": today, "approval_state": "approved", "priority_rank": DOC_TYPES[body.doc_type],
           "source_file": f"lab:{Path(body.filename).name}", "pages": len(pages), "section_count": manifest["chunkCount"],
           "title": Path(body.filename).stem}
    secs = [{"doc_id": doc_id, "section_id": c["sectionId"], "project_id": project_id, "doc_type": body.doc_type,
             "doc_version": "lab", "approval_state": "approved", "effective_date": today, "priority_rank": doc["priority_rank"],
             "heading": c["heading"], "text": c["text"], "page": c["pageStart"], "line_start": c["lineStart"],
             "page_end": c["pageEnd"], "line_end": c["lineEnd"], "feature_id": c["featureId"], "scope_flag": c["scopeFlag"],
             "ref": f"{doc_id}:{c['sectionId']}", "locator": None, "extraction_version": manifest["extractionVersion"],
             "chunk_id": c["chunkId"], "start_offset": c["startOffset"], "end_offset": c["endOffset"]}
            for c in manifest["chunks"]]
    with lock:
        data = _load()
        data["documents"].append(doc)
        data["sections"] += secs
        data["pages"][doc_id] = pages
        _save(data)
    out["document"] = {**doc, "sections": [{"section_id": s["section_id"], "heading": s["heading"], "page": s["page"]} for s in secs]}
    return out


@router.delete("/documents/{doc_id}")
def remove(doc_id: str, request: Request):
    """기준선에 넣기 전의 테스트 문서만 목록에서 뺀다. vector-db의 벡터는 남긴다 (같은 ID면 다시 쓰임)."""
    from prototype.lab import local_only
    local_only(request)
    with lock:
        data = _load()
        if not any(d["doc_id"] == doc_id for d in data["documents"]):
            raise HTTPException(404, "테스트로 추가한 문서만 뺄 수 있습니다.")
        if any(doc_id in b["document_ids"] for b in baselines().values()):
            raise HTTPException(409, "기준선에 포함된 문서는 뺄 수 없습니다. 테스트 데이터 초기화를 사용해 주세요.")
        data["documents"] = [d for d in data["documents"] if d["doc_id"] != doc_id]
        data["sections"] = [s for s in data["sections"] if s["doc_id"] != doc_id]
        data["pages"].pop(doc_id, None)
        _save(data)
    return {"removed": doc_id}


@router.post("/projects/{project_id}/baselines")
def create_baseline(project_id: str, body: CreateBaseline, request: Request):
    """현재 기준선을 대체하는 새 버전을 만든다. 이전 기준선은 SUPERSEDED가 된다 (백엔드 createBaselineFromAgreement 대응)."""
    from prototype.lab import local_only
    local_only(request)
    _project(project_id)
    docs = list(dict.fromkeys(body.document_ids))
    bad = [d for d in docs if documents().get(d, {}).get("project_id") != project_id]
    if bad:
        raise HTTPException(422, f"이 프로젝트의 문서가 아닙니다: {', '.join(bad)}")
    with lock:
        current = _active(project_id)
        n = max((int(k.rsplit("BL-", 1)[1]) for k, b in baselines().items()
                 if b["project_id"] == project_id and k.rsplit("BL-", 1)[-1].isdigit()), default=0) + 1
        new_id = f"{project_id}:BL-{n}"
        prev = baselines().get(current, {})
        baseline = {"project_id": project_id, "as_of": date.today().isoformat(), "document_ids": docs,
                    "priority": prev.get("priority", ["CO", "CTR", "SOW", "FSP", "QUO"]), "supersedes": current}
        data = _load()
        data["baselines"][new_id] = baseline
        _save(data)
    return {"baseline_id": new_id, **baseline}


@router.post("/reset")
def reset(request: Request):
    """테스트 문서·기준선을 지운다. 원본 data/kb와 vector-db 벡터는 그대로다."""
    from prototype.lab import local_only
    local_only(request)
    with lock:
        if _root().is_dir():
            shutil.rmtree(_root())
        clear_caches()
    return {"reset": True}
