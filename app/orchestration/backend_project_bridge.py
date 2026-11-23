"""Developer bridge between local Backend PostgreSQL (weave-postgres) and the AI prototype UI.

Activated only when the prototype is opened with ?projectId=<Backend Project UUID>.
Standalone prototype sessions (without ?projectId) remain completely separate.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import urllib.error
import urllib.request
import uuid
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.kb import backend_kb, loader

DOC_TYPE_MAP = {
    "CHANGE_ORDER": "change_order",
    "CONTRACT": "contract",
    "SOW": "sow",
    "FUNCTIONAL_SPEC": "functional_spec",
    "QUOTATION": "quote",
    "APPROVAL_EVIDENCE": "approval_evidence",
    "OTHER": "other",
}

DEFAULT_ROLES = [
    ("PM", "PM·기획", 450000.00, 550000.00),
    ("UX", "기획·디자인", 350000.00, 450000.00),
    ("FE", "프론트엔드 개발", 400000.00, 500000.00),
    ("BE", "백엔드 개발", 400000.00, 500000.00),
    ("QA", "QA·테스트", 300000.00, 400000.00),
]


def backend_db_url() -> str:
    return os.environ.get("BACKEND_DB_URL") or "postgresql://weave:weave@localhost:5432/weave"


def backend_http_base() -> str:
    return (settings.backend_base_url or os.environ.get("BACKEND_BASE_URL") or "http://localhost:8080").rstrip("/")


def backend_internal_token() -> str:
    return settings.backend_internal_token or os.environ.get("BACKEND_INTERNAL_TOKEN") or "local-dev-internal-token"


def is_backend_project_id(project_id: str | None) -> bool:
    return bool(project_id and backend_kb.is_backend_id(project_id))


def ensure_backend_schedule(project_id: str) -> dict[str, Any] | None:
    """Register and return a schedule entry for a Backend project UUID so get_schedule works without error."""
    if not is_backend_project_id(project_id):
        return None
    scheds = loader.schedules()
    if project_id in scheds:
        return scheds[project_id]
    try:
        import psycopg
        with psycopg.connect(backend_db_url(), autocommit=True) as conn:
            proj = conn.execute(
                "SELECT project_name, start_date, end_date FROM project WHERE project_id = %s",
                (project_id,),
            ).fetchone()
            if not proj:
                return None
            docs = conn.execute(
                """
                SELECT d.document_title, d.document_type, dv.document_version_id, dv.document_effective_date
                FROM document d
                JOIN document_version dv ON d.document_id = dv.document_id
                WHERE d.project_id = %s AND dv.document_approval_status = 'APPROVED'
                ORDER BY dv.document_version_no DESC
                """,
                (project_id,),
            ).fetchall()
        titles = " ".join(str(r[0]) for r in docs)
        template_key = "P-COM-101" if ("CTR-A" in titles or "모노마켓" in str(proj[0])) else (
            "P-COM-102" if "CTR-B" in titles else ("P-COM-103" if "CTR-C" in titles else "P-COM-101")
        )
        base = copy.deepcopy(scheds.get(template_key) or {})
        base["name"] = proj[0]
        if proj[1]:
            base["contract_start"] = str(proj[1])
        if proj[2]:
            base["contract_end"] = str(proj[2])
        ctr_doc = next((str(r[2]) for r in docs if r[1] == "CONTRACT"), None)
        co_doc = next((str(r[2]) for r in docs if r[1] == "CHANGE_ORDER"), None)
        if ctr_doc:
            base["contract_end_ref"] = f"{ctr_doc}:C-03"
        if co_doc and base.get("change_orders"):
            for co in base["change_orders"]:
                co["co_id"] = co_doc
                co["ref"] = f"{co_doc}:CO-02"
        scheds[project_id] = base
        return base
    except Exception:
        return None


def _ensure_rate_card(conn, project_id: str, company_id: str, actor_member_id: str) -> str:
    row = conn.execute(
        """
        SELECT rate_card_version_id
        FROM rate_card_version
        WHERE project_id = %s
        ORDER BY rate_card_version_no DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if row:
        return str(row[0])

    role_ids: dict[str, str] = {}
    for code, name, _, _ in DEFAULT_ROLES:
        existing = conn.execute(
            "SELECT job_role_id FROM job_role WHERE company_id = %s AND job_role_code = %s LIMIT 1",
            (company_id, code),
        ).fetchone()
        if existing:
            role_ids[code] = str(existing[0])
        else:
            jid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"weave-role:{company_id}:{code}"))
            conn.execute(
                "INSERT INTO job_role (job_role_id, company_id, job_role_code, job_role_name) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (jid, company_id, code, name),
            )
            role_ids[code] = jid

    rc_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"weave-rate-card:{project_id}:v1"))
    conn.execute(
        """
        INSERT INTO rate_card_version (
            rate_card_version_id, project_id, rate_card_version_no,
            rate_currency, rate_effective_date, hours_per_md, created_at, created_by
        )
        VALUES (%s, %s, 1, 'KRW', '2026-09-01', 8.00, now(), %s)
        ON CONFLICT DO NOTHING
        """,
        (rc_id, project_id, actor_member_id),
    )
    for code, _, internal_rate, selling_rate in DEFAULT_ROLES:
        rr_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"weave-role-rate:{rc_id}:{code}"))
        conn.execute(
            """
            INSERT INTO role_rate (role_rate_id, rate_card_version_id, job_role_id, internal_rate, selling_rate)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (rr_id, rc_id, role_ids[code], internal_rate, selling_rate),
        )
    return rc_id


def sync_change_request_to_backend(
    project_id: str,
    case_id: str,
    title: str,
    message_text: str,
    requester: str = "직접 입력",
    channel: str = "OTHER",
) -> str | None:
    """Ensure a change_request + request_source row exists in weave-postgres for this case_id."""
    if not is_backend_project_id(project_id):
        return None
    import psycopg

    channel_code = (channel or "OTHER").upper()
    if channel_code not in ("EMAIL", "MEETING", "PHONE", "MESSENGER", "OTHER"):
        channel_code = "EMAIL" if "mail" in channel_code.lower() else "OTHER"

    try:
        with psycopg.connect(backend_db_url(), autocommit=True) as conn:
            proj = conn.execute(
                """
                SELECT p.company_id, COALESCE(
                    (SELECT pm.company_member_id FROM project_member pm WHERE pm.project_id = p.project_id LIMIT 1),
                    (SELECT cm.company_member_id FROM company_member cm WHERE cm.company_id = p.company_id LIMIT 1)
                )
                FROM project p
                WHERE p.project_id = %s
                """,
                (project_id,),
            ).fetchone()
            if not proj or not proj[1]:
                return None
            actor_id = str(proj[1])

            existing = conn.execute(
                "SELECT change_request_id, change_request_title FROM change_request WHERE project_id = %s AND (cr_number = %s OR change_request_id::text = %s) LIMIT 1",
                (project_id, case_id, case_id),
            ).fetchone()
            short_title = ((message_text.splitlines()[0] if message_text else title) or "신규 변경 요청").strip()[:40] or "신규 변경 요청"
            if existing:
                cr_uuid = str(existing[0])
                if existing[1] in ("요청분석", "신규 변경 요청", "") and short_title not in ("요청분석", "신규 변경 요청"):
                    conn.execute(
                        "UPDATE change_request SET change_request_title = %s WHERE change_request_id = %s",
                        (short_title, cr_uuid),
                    )
                return cr_uuid

            try:
                cr_uuid = str(uuid.UUID(case_id))
            except ValueError:
                cr_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"weave-cr:{project_id}:{case_id}"))

            conn.execute(
                """
                INSERT INTO change_request (
                    change_request_id, project_id, cr_number, change_request_title,
                    progress_status, priority, needs_reanalysis, lock_version,
                    company_member_id, created_at, created_by
                )
                VALUES (%s, %s, %s, %s, 'RECEIVED', 'NORMAL', false, 0, %s, now(), %s)
                ON CONFLICT DO NOTHING
                """,
                (cr_uuid, project_id, case_id, short_title, actor_id, actor_id),
            )
            src_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"weave-cr-src:{cr_uuid}:1"))
            text_hash = hashlib.sha256((message_text or "").encode("utf-8")).hexdigest()
            conn.execute(
                """
                INSERT INTO request_source (
                    request_source_id, change_request_id, request_channel,
                    request_original_text, requester, received_at,
                    source_content_hash, created_at, created_by
                )
                VALUES (%s, %s, %s, %s, %s, now(), %s, now(), %s)
                ON CONFLICT DO NOTHING
                """,
                (src_uuid, cr_uuid, channel_code, message_text or short_title, requester or "직접 입력", text_hash, actor_id),
            )
            return cr_uuid
    except Exception:
        return None


def _call_backend_complete(ai_analysis_id: str, payload: dict[str, Any]) -> bool:
    """Invoke Spring Boot's POST /internal/v1/ai-analyses/{id}/complete so Backend handlers run."""
    url = f"{backend_http_base()}/internal/v1/ai-analyses/{ai_analysis_id}/complete"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Internal-Token": backend_internal_token(),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _ensure_review_version(conn, project_id: str, cr_id: str, actor_id: str) -> tuple[str | None, str | None]:
    """Ensure a DRAFT review_version exists for cr_id and return (review_version_id, baseline_id)."""
    row = conn.execute(
        """
        SELECT cr.review_version_id, rv.baseline_id
        FROM change_request cr
        LEFT JOIN review_version rv ON cr.review_version_id = rv.review_version_id
        WHERE cr.change_request_id = %s
        """,
        (cr_id,),
    ).fetchone()
    if row and row[0]:
        return str(row[0]), str(row[1])

    bl = conn.execute(
        """
        SELECT baseline_id FROM baseline
        WHERE project_id = %s
        ORDER BY (CASE WHEN baseline_status = 'ACTIVE' THEN 0 ELSE 1 END), baseline_version_no DESC
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    if not bl:
        return None, None
    baseline_id = str(bl[0])
    rv_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"weave-rv:{cr_id}:1"))
    conn.execute(
        """
        INSERT INTO review_version (
            review_version_id, change_request_id, project_id, baseline_id,
            review_version_no, review_status, schedule_status,
            acceptance_required, completion_required, item_revision, question_revision,
            created_at, created_by
        )
        VALUES (%s, %s, %s, %s, 1, 'DRAFT', 'NOT_EVALUATED', false, false, 0, 0, now(), %s)
        ON CONFLICT (change_request_id, review_version_no) DO NOTHING
        """,
        (rv_id, cr_id, project_id, baseline_id, actor_id),
    )
    actual = conn.execute(
        "SELECT review_version_id, baseline_id FROM review_version WHERE change_request_id = %s ORDER BY review_version_no DESC LIMIT 1",
        (cr_id,),
    ).fetchone()
    if not actual:
        return None, None
    rv_id = str(actual[0])
    conn.execute(
        "UPDATE change_request SET review_version_id = %s WHERE change_request_id = %s AND review_version_id IS NULL",
        (rv_id, cr_id),
    )
    return rv_id, str(actual[1])


def _insert_queued_analysis(conn, review_version_id: str, analysis_type: str, input_obj: Any, actor_id: str) -> str:
    aid = str(uuid.uuid4())
    snap = json.dumps(input_obj if input_obj is not None else {}, ensure_ascii=False)
    ihash = hashlib.sha256(snap.encode("utf-8")).hexdigest()
    conn.execute(
        """
        INSERT INTO ai_analysis (
            ai_analysis_id, review_version_id, analysis_type, analysis_status,
            input_snapshot, input_hash, requested_by, analysis_created_at, retry_count, retryable
        )
        VALUES (%s, %s, %s, 'QUEUED', %s::jsonb, %s, %s, now(), 0, false)
        """,
        (aid, review_version_id, analysis_type, snap, ihash, actor_id),
    )
    return aid


def _build_item_id_map(conn, review_version_id: str, a1_items: list[dict[str, Any]]) -> dict[str, str]:
    """Map Agent 1 item_ids (e.g. CR-UI-...-1, R1, item-1) to weave-postgres request_item_id UUID strings."""
    db_items = conn.execute(
        """
        SELECT request_item_id, item_type, content, source_start, source_end
        FROM request_item
        WHERE review_version_id = %s AND retired_at IS NULL
        ORDER BY created_at ASC, request_item_id ASC
        """,
        (review_version_id,),
    ).fetchall()
    mapping: dict[str, str] = {}
    if not db_items:
        return mapping

    for idx, r in enumerate(db_items):
        rid = str(r[0])
        mapping[rid] = rid
        mapping[f"R{idx + 1}"] = rid
        mapping[f"item-{idx + 1}"] = rid

    used_db_ids: set[str] = set()
    for idx, it in enumerate(a1_items):
        raw_id = str(it.get("item_id") or "")
        text = (it.get("text") or "").strip()
        itype = (it.get("item_type") or "function").strip().lower()
        matched_rid: str | None = None
        if raw_id in mapping:
            matched_rid = mapping[raw_id]
        if not matched_rid:
            for r in db_items:
                rid = str(r[0])
                if rid not in used_db_ids and (r[2] or "").strip() == text and (r[1] or "").strip().lower() == itype:
                    matched_rid = rid
                    break
        if not matched_rid:
            for r in db_items:
                rid = str(r[0])
                if rid not in used_db_ids and (r[2] or "").strip() == text:
                    matched_rid = rid
                    break
        if not matched_rid:
            for r in db_items:
                rid = str(r[0])
                if rid not in used_db_ids and (r[1] or "").strip().lower() == itype:
                    matched_rid = rid
                    break
        if not matched_rid and idx < len(db_items):
            matched_rid = str(db_items[idx][0])
        if matched_rid:
            used_db_ids.add(matched_rid)
            if raw_id:
                mapping[raw_id] = matched_rid
            disp = it.get("display_no")
            if disp:
                mapping[str(disp)] = matched_rid
    return mapping


def sync_local_run_to_backend(project_id: str, case_id: str, store: Any = None) -> bool:
    """Sync Agent 1 (DECOMPOSITION), Agent 2 (SCOPE), and Agent 3 (IMPACT) results from LocalRunStore into weave-postgres."""
    if not is_backend_project_id(project_id):
        return False
    import psycopg
    from app.orchestration.local_run_store import LocalRunStore

    local_store = store if isinstance(store, LocalRunStore) else LocalRunStore(settings.prototype_run_db)
    try:
        run = local_store.get_coordinated_run(case_id)
        inp = local_store.get_inputs(case_id).agent1_input
    except Exception:
        return False

    if inp.context.project_id != project_id:
        return False

    def _extract_stage_payload(stage: str) -> dict[str, Any] | None:
        st = run.state.agents.get(stage)
        if st and st.current_result_id:
            try:
                res = local_store.get_result(case_id, st.current_result_id)
                if res and res.result_payload:
                    return copy.deepcopy(res.result_payload)
            except Exception:
                pass
        return None

    a1 = _extract_stage_payload("agent1")
    a2 = _extract_stage_payload("agent2")
    a3 = _extract_stage_payload("agent3")
    if not a1:
        return False

    cr_id = sync_change_request_to_backend(
        project_id,
        case_id,
        inp.subject or "",
        inp.message_text,
        inp.sender_name or "직접 입력",
        inp.channel or "OTHER",
    )
    if not cr_id:
        return False

    try:
        with psycopg.connect(backend_db_url(), autocommit=True) as conn:
            proj = conn.execute(
                """
                SELECT COALESCE(
                    (SELECT pm.company_member_id FROM project_member pm WHERE pm.project_id = p.project_id LIMIT 1),
                    (SELECT cm.company_member_id FROM company_member cm WHERE cm.company_id = p.company_id LIMIT 1)
                )
                FROM project p WHERE p.project_id = %s
                """,
                (project_id,),
            ).fetchone()
            if not proj or not proj[0]:
                return False
            actor_id = str(proj[0])

            rv_id, _ = _ensure_review_version(conn, project_id, cr_id, actor_id)
            if not rv_id:
                return False

            # 1) Sync DECOMPOSITION (Agent 1)
            has_items = conn.execute(
                "SELECT count(*) FROM request_item WHERE review_version_id = %s AND retired_at IS NULL",
                (rv_id,),
            ).fetchone()[0] > 0
            has_decomp = conn.execute(
                "SELECT count(*) FROM ai_analysis WHERE review_version_id = %s AND analysis_type = 'DECOMPOSITION' AND analysis_status = 'COMPLETED'",
                (rv_id,),
            ).fetchone()[0] > 0

            if not has_items or not has_decomp:
                aid = _insert_queued_analysis(conn, rv_id, "DECOMPOSITION", inp.model_dump(mode="json"), actor_id)
                if not _call_backend_complete(aid, a1):
                    # Direct SQL fallback if Backend HTTP is unreachable
                    src_row = conn.execute(
                        "SELECT request_source_id FROM request_source WHERE change_request_id = %s ORDER BY created_at DESC LIMIT 1",
                        (cr_id,),
                    ).fetchone()
                    src_id = str(src_row[0]) if src_row else None
                    if not has_items:
                        for idx, it in enumerate(a1.get("items") or []):
                            itype = (it.get("item_type") or "function").strip().lower()
                            content = (it.get("text") or "").strip()
                            if not content:
                                continue
                            spans = it.get("spans") or []
                            s_start = spans[0].get("start") if spans else None
                            s_end = spans[0].get("end") if spans else None
                            ri_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"weave-ri:{rv_id}:{idx + 1}"))
                            conn.execute(
                                """
                                INSERT INTO request_item (
                                    request_item_id, review_version_id, request_source_id,
                                    stable_item_key, item_type, content, item_origin,
                                    source_start, source_end, predecessor_item_ids, change_kind,
                                    created_at, created_by
                                )
                                VALUES (%s, %s, %s, %s, %s, %s, 'AI', %s, %s, '[]'::jsonb, %s, now(), %s)
                                ON CONFLICT DO NOTHING
                                """,
                                (ri_id, rv_id, src_id, ri_id, itype, content, s_start, s_end, it.get("change_kind") if itype == "function" else None, actor_id),
                            )
                    conn.execute(
                        "UPDATE ai_analysis SET analysis_status = 'COMPLETED', result = %s::jsonb, completed_at = now() WHERE ai_analysis_id = %s",
                        (json.dumps(a1, ensure_ascii=False), aid),
                    )

            item_map = _build_item_id_map(conn, rv_id, a1.get("items") or [])
            if not item_map:
                return True

            # 2) Sync SCOPE (Agent 2)
            if a2 and a2.get("assessments"):
                a2_res_id = (a2.get("meta") or {}).get("result_id")
                existing_scope = conn.execute(
                    """
                    SELECT ai_analysis_id, result::jsonb->'meta'->>'result_id'
                    FROM ai_analysis
                    WHERE review_version_id = %s AND analysis_type = 'SCOPE' AND analysis_status = 'COMPLETED'
                    ORDER BY analysis_created_at DESC LIMIT 1
                    """,
                    (rv_id,),
                ).fetchone()
                if not existing_scope or (a2_res_id and existing_scope[1] != a2_res_id and not existing_scope[0]):
                    a2_be = copy.deepcopy(a2)
                    meta = a2_be.setdefault("meta", {})
                    if not str(meta.get("schema_version") or "").startswith("0.3."):
                        meta["schema_version"] = "0.3.0"
                    req_items_input = []
                    for assess in a2_be.get("assessments") or []:
                        old_iid = str(assess.get("item_id") or "")
                        new_iid = item_map.get(old_iid, old_iid)
                        assess["item_id"] = new_iid
                        req_items_input.append({"item_id": new_iid})
                    for q in a2_be.get("questions") or []:
                        if isinstance(q.get("item_ids"), list):
                            q["item_ids"] = [item_map.get(str(x), str(x)) for x in q["item_ids"] if str(x) in item_map]
                        if q.get("item_id"):
                            q["item_id"] = item_map.get(str(q["item_id"]), str(q["item_id"]))

                    if not existing_scope:
                        scope_input = {"items": req_items_input}
                        aid = _insert_queued_analysis(conn, rv_id, "SCOPE", scope_input, actor_id)
                        if not _call_backend_complete(aid, a2_be):
                            conn.execute(
                                "UPDATE ai_analysis SET analysis_status = 'COMPLETED', result = %s::jsonb, completed_at = now() WHERE ai_analysis_id = %s",
                                (json.dumps(a2_be, ensure_ascii=False), aid),
                            )

            # 3) Sync IMPACT (Agent 3)
            if a3 and a3.get("status") in ("IMPACT_DRAFT_READY", "NOT_REQUIRED") and a3.get("estimates") is not None:
                a3_res_id = (a3.get("meta") or {}).get("result_id")
                existing_impact = conn.execute(
                    """
                    SELECT ai_analysis_id, result::jsonb->'meta'->>'result_id'
                    FROM ai_analysis
                    WHERE review_version_id = %s AND analysis_type = 'IMPACT' AND analysis_status = 'COMPLETED'
                    ORDER BY analysis_created_at DESC LIMIT 1
                    """,
                    (rv_id,),
                ).fetchone()
                if not existing_impact or (a3_res_id and existing_impact[1] != a3_res_id):
                    a3_be = copy.deepcopy(a3)
                    for est in a3_be.get("estimates") or []:
                        if isinstance(est.get("item_ids"), list):
                            est["item_ids"] = [item_map.get(str(x), str(x)) for x in est["item_ids"]]
                        for line in est.get("lines") or []:
                            if isinstance(line.get("item_ids"), list):
                                line["item_ids"] = [item_map.get(str(x), str(x)) for x in line["item_ids"]]
                    for q in a3_be.get("questions") or []:
                        if isinstance(q.get("item_ids"), list):
                            q["item_ids"] = [item_map.get(str(x), str(x)) for x in q["item_ids"]]
                        if q.get("item_id"):
                            q["item_id"] = item_map.get(str(q["item_id"]), str(q["item_id"]))
                    for n in a3_be.get("needs") or []:
                        if isinstance(n.get("item_ids"), list):
                            n["item_ids"] = [item_map.get(str(x), str(x)) for x in n["item_ids"]]
                        if n.get("item_id"):
                            n["item_id"] = item_map.get(str(n["item_id"]), str(n["item_id"]))

                    aid = _insert_queued_analysis(conn, rv_id, "IMPACT", {}, actor_id)
                    if not _call_backend_complete(aid, a3_be):
                        conn.execute(
                            "UPDATE ai_analysis SET analysis_status = 'COMPLETED', result = %s::jsonb, completed_at = now() WHERE ai_analysis_id = %s",
                            (json.dumps(a3_be, ensure_ascii=False), aid),
                        )
        return True
    except Exception:
        return False


def _synthesize_documents_for_backend_a3(
    project_name: str,
    customer_name: str,
    message_text: str,
    a1: dict[str, Any],
    a2: dict[str, Any] | None,
    a3: dict[str, Any],
    proposal_text: str | None = None,
) -> dict[str, Any]:
    """Ensure Agent 3 results originating from Frontend have email & agreement drafts for the Developer Prototype UI."""
    docs = copy.deepcopy(a3.get("documents") or {})
    if docs.get("email") and docs.get("agreement"):
        return docs

    func_items = [i.get("text") for i in (a1.get("items") or []) if i.get("item_type") == "function" and i.get("text")]
    title_summary = func_items[0] if func_items else (message_text.splitlines()[0][:50] if message_text else "변경 요청")
    pricing = a3.get("pricing") or {}
    est_totals = [e.get("totals") or {} for e in (a3.get("estimates") or []) if e.get("totals")]
    total_md = pricing.get("total_md") if pricing.get("total_md") is not None else (sum(t.get("md", 0) for t in est_totals) if est_totals else None)
    supply_amount = pricing.get("supply_amount") if pricing.get("supply_amount") is not None else (sum(t.get("price", 0) for t in est_totals) if est_totals else None)
    total_with_vat = pricing.get("total_with_vat")
    plan = (a3.get("plan_detail") or {}).get("plan") or {}
    est_end = plan.get("estimated_end_date")
    impact_days = plan.get("impact_business_days")

    md_str = f"{total_md} MD" if total_md is not None else "산정 확인 필요"
    price_str = f"{int(supply_amount):,}원 (VAT 별도" + (f" / 총액 {int(total_with_vat):,}원)" if total_with_vat else ")") if supply_amount is not None else "별도 협의"
    sched_str = f"예상 완료일 {est_end} (일정 영향 +{impact_days}영업일)" if est_end else "세부 일정 협의 필요"

    lines_summary = "\n".join(
        f"- [{l.get('role')}] {l.get('task')}: {l.get('md')} MD"
        for est in (a3.get("estimates") or [])
        for l in (est.get("lines") or [])
    )

    if not docs.get("email"):
        docs["email"] = {
            "subject": f"[{project_name}] '{title_summary[:36]}' 변경 요청 검토 및 견적 안내",
            "body": (
                f"안녕하세요, {customer_name or '고객사'} 담당자님.\n\n"
                f"요청해 주신 변경 사항({title_summary})에 대하여 기준 문서(계약서·과업지시서·기능명세서) 및 현재 공정 일정을 바탕으로 범위와 영향을 검토한 결과를 안내드립니다.\n\n"
                f"1. 범위 검토 결과\n"
                f"- 판정: 기준선 범위 밖 신규 추가 과업 (변경합의 대상)\n"
                f"- 요청 원문: {message_text}\n\n"
                f"2. 예상 공수 및 견적 금액\n"
                f"- 총 추가 공수: {md_str}\n"
                f"- 공급가액: {price_str}\n"
                f"- 일정 영향: {sched_str}\n\n"
                f"3. 세부 작업 내역\n"
                f"{lines_summary or '- 세부 산정 내역 참조'}\n\n"
                f"검토 후 회신 주시면 변경합의 절차를 진행하겠습니다.\n감사합니다."
            ),
            "model": (a3.get("meta") or {}).get("model") or "backend-synced",
        }
    if not docs.get("agreement"):
        docs["agreement"] = {
            "title": f"[{project_name}] 변경합의서 초안",
            "body": proposal_text or (
                f"변경합의서 (초안)\n\n"
                f"프로젝트명: {project_name}\n"
                f"요청 항목: {title_summary}\n\n"
                f"1. 변경 내용 및 세부 작업\n{lines_summary or '- 세부 산정 내역 참조'}\n\n"
                f"2. 추가 공수 및 계약 금액 변경\n"
                f"- 추가 공수: {md_str}\n"
                f"- 추가 금액: {price_str}\n\n"
                f"3. 일정 영향\n"
                f"- {sched_str}\n"
            ),
            "model": (a3.get("meta") or {}).get("model") or "backend-synced",
        }
    return docs


def _materialize_backend_cr_to_local_store(
    local_store: Any,
    case_key: str,
    project_id: str,
    project_name: str,
    customer_name: str,
    active_baseline_id: str,
    active_baseline_as_of: str,
    rate_card_id: str,
    cr_title: str,
    orig_text: str,
    requester: str,
    channel: str,
    received_at: str,
    db_items: list[tuple[Any, ...]],
    decomp_json: dict[str, Any] | None,
    scope_json: dict[str, Any] | None,
    impact_json: dict[str, Any] | None,
    proposal_text: str | None,
) -> bool:
    """Seed or update LocalRunStore from weave-postgres ai_analysis rows so Frontend-analyzed CRs show as completed in Developer Prototype."""
    if not db_items and not decomp_json:
        return False

    ctx = {
        "project_id": project_id,
        "case_id": case_key,
        "request_revision": 1,
        "baseline_id": active_baseline_id,
        "baseline_as_of": active_baseline_as_of,
        "analysis_id": f"UI-{case_key}",
    }

    # Build Agent 1 output aligned with request_item UUIDs and R1, R2, R3 display order
    a1 = copy.deepcopy(decomp_json) if decomp_json else {
        "context": ctx,
        "status": "READY",
        "items": [],
        "questions": [],
        "meta": {"result_id": f"be-a1-{case_key}", "schema_version": "0.3.0", "model": "backend-db", "agent_version": "backend-sync"},
    }
    a1["context"] = ctx
    a1.setdefault("meta", {})["result_id"] = (a1.get("meta") or {}).get("result_id") or f"be-a1-{case_key}"

    raw_a1_items = a1.get("items") or []
    aligned_items: list[dict[str, Any]] = []
    old_to_new_id: dict[str, str] = {}
    used_raw: set[int] = set()

    for idx, r in enumerate(db_items):
        ri_id = str(r[0])
        ri_type = (r[1] or "function").strip().lower()
        ri_content = (r[2] or "").strip()
        s_start = r[3]
        s_end = r[4]
        c_kind = r[5]

        matched_raw: dict[str, Any] | None = None
        for r_idx, cand in enumerate(raw_a1_items):
            if r_idx in used_raw:
                continue
            if (cand.get("text") or "").strip() == ri_content:
                matched_raw = copy.deepcopy(cand)
                used_raw.add(r_idx)
                break
        if not matched_raw:
            for r_idx, cand in enumerate(raw_a1_items):
                if r_idx in used_raw:
                    continue
                if (cand.get("item_type") or "").strip().lower() == ri_type:
                    matched_raw = copy.deepcopy(cand)
                    used_raw.add(r_idx)
                    break

        if matched_raw and matched_raw.get("item_id"):
            old_to_new_id[str(matched_raw["item_id"])] = ri_id

        spans = (matched_raw.get("spans") if matched_raw else None) or (
            [{"start": s_start, "end": s_end, "text": orig_text[s_start:s_end]}]
            if isinstance(s_start, int) and isinstance(s_end, int) and 0 <= s_start < s_end <= len(orig_text)
            else []
        )
        intent_default = "change_request" if ri_type == "function" else ("estimate_request" if ri_type == "question" else "constraint")
        aligned_items.append({
            "item_id": ri_id,
            "case_id": case_key,
            "display_no": f"R{idx + 1}",
            "item_type": ri_type,
            "change_kind": c_kind or (matched_raw.get("change_kind") if matched_raw else None),
            "intent": (matched_raw.get("intent") if matched_raw else None) or intent_default,
            "text": ri_content,
            "inferred": bool(matched_raw.get("inferred")) if matched_raw else False,
            "source": "ai",
            "spans": spans,
            "feature_candidates": (matched_raw.get("feature_candidates") if matched_raw else None) or [],
            "conditional_followup": matched_raw.get("conditional_followup") if matched_raw else None,
        })

    if aligned_items:
        a1["items"] = aligned_items

    a2 = copy.deepcopy(scope_json) if scope_json else None
    if a2:
        a2["context"] = ctx
        a2.setdefault("meta", {})["result_id"] = (a2.get("meta") or {}).get("result_id") or f"be-a2-{case_key}"
        for assess in a2.get("assessments") or []:
            iid = str(assess.get("item_id") or "")
            if iid in old_to_new_id:
                assess["item_id"] = old_to_new_id[iid]

    a3 = copy.deepcopy(impact_json) if impact_json else None
    if a3:
        a3["context"] = ctx
        a3.setdefault("meta", {})["result_id"] = (a3.get("meta") or {}).get("result_id") or f"be-a3-{case_key}"
        for est in a3.get("estimates") or []:
            if isinstance(est.get("item_ids"), list):
                est["item_ids"] = [old_to_new_id.get(str(x), str(x)) for x in est["item_ids"]]
            for line in est.get("lines") or []:
                if isinstance(line.get("item_ids"), list):
                    line["item_ids"] = [old_to_new_id.get(str(x), str(x)) for x in line["item_ids"]]
        a3["documents"] = _synthesize_documents_for_backend_a3(
            project_name, customer_name, orig_text, a1, a2, a3, proposal_text
        )

    # Check if LocalRunStore already has a run with equal or more progress
    from app.orchestration.fake_run_coordination import FakeCoordinatedRunStateClient
    from app.orchestration.run_coordination import ReviewBundle
    from app.orchestration.run_state import AgentState, CommittedResult, PendingReview, RunInputs
    from schema.schemas import Agent1Input

    existing_body = None
    with closing(sqlite3.connect(local_store.path)) as sdb:
        row = sdb.execute("SELECT body FROM local_run WHERE id = ?", (case_key,)).fetchone()
        if row:
            existing_body = json.loads(row[0])

    if existing_body:
        ex_agents = existing_body.get("agents") or {}
        ex_has_a3 = bool((ex_agents.get("agent3") or {}).get("current_result_id")) and (ex_agents.get("agent3") or {}).get("status") == "COMPLETED"
        if ex_has_a3:
            return True

    a1_input = Agent1Input.model_validate({
        "context": ctx,
        "received_at": received_at,
        "sender_role": "CLIENT",
        "sender_name": requester or "고객사 담당자",
        "channel": channel.lower() if channel.lower() in ("email", "messenger", "meeting", "doc", "manual", "other") else "email",
        "channel_src": "BACKEND",
        "subject": cr_title,
        "message_text": orig_text,
        "answers": [],
    })

    client = FakeCoordinatedRunStateClient()
    client.create_run(case_key, RunInputs(agent1_input=a1_input, rate_card_id=rate_card_id))
    run = client._runs[case_key]

    now_dt = datetime.now(timezone.utc)
    a1_rid = a1["meta"]["result_id"]
    run.results[a1_rid] = CommittedResult(
        run_id=case_key, agent_type="agent1", result_id=a1_rid, result_revision=1,
        execution_status="COMPLETED", upstream_result_ids=[], result_payload=a1,
        created_at=now_dt, is_current=True,
    )
    run.agents["agent1"] = AgentState(status="COMPLETED", current_result_id=a1_rid, result_revision=1, stale=False)
    run.result_counts["agent1"] = 1
    run.revision = 2
    run.status = "RUNNING"

    progress_events: list[dict[str, Any]] = [
        {"seq": 1, "kind": "step", "agent": "Agent 1", "title": "요청 항목을 구조화했습니다", "body": f"{len(a1.get('items') or [])}개 항목을 확인했습니다.", "tab": "original"},
    ]

    if a2:
        a2_rid = a2["meta"]["result_id"]
        run.results[a2_rid] = CommittedResult(
            run_id=case_key, agent_type="agent2", result_id=a2_rid, result_revision=1,
            execution_status="COMPLETED", upstream_result_ids=[a1_rid], result_payload=a2,
            created_at=now_dt, is_current=True,
        )
        run.agents["agent2"] = AgentState(status="COMPLETED", current_result_id=a2_rid, result_revision=1, stale=False)
        run.result_counts["agent2"] = 1
        run.revision = 4
        progress_events.append({"seq": 2, "kind": "step", "agent": "Agent 2", "title": "범위와 근거를 검토했습니다", "body": f"{len(a2.get('assessments') or [])}개 항목의 범위 검토 결과가 있습니다.", "tab": "scope"})

    if a2 and a3:
        a3_rid = a3["meta"]["result_id"]
        run.results[a3_rid] = CommittedResult(
            run_id=case_key, agent_type="agent3", result_id=a3_rid, result_revision=1,
            execution_status="COMPLETED", upstream_result_ids=[a1_rid, a2["meta"]["result_id"]], result_payload=a3,
            created_at=now_dt, is_current=True,
        )
        run.agents["agent3"] = AgentState(status="COMPLETED", current_result_id=a3_rid, result_revision=1, stale=False)
        run.result_counts["agent3"] = 1
        run.revision = 6
        run.status = "WAITING_PM_REVIEW"
        from app.agents.agent3.workflow import _hash
        doc_fp = _hash(a3.get("documents") or {})
        run.review_bundle = ReviewBundle(
            result_ids={"agent1": a1_rid, "agent2": a2["meta"]["result_id"], "agent3": a3_rid},
            document_fingerprint=doc_fp,
        )
        run.pending = PendingReview(
            agent_type="agent3",
            target_result_id=a3_rid,
            reason="Final result and customer document review",
            kind="FINAL_REVIEW",
        )
        progress_events.append({"seq": 3, "kind": "step", "agent": "Agent 3", "title": "영향 산정 결과를 준비했습니다", "body": "공수·비용과 일정 영향 산정 결과를 확인했습니다.", "tab": "impact"})
        progress_events.append({"seq": 4, "kind": "step", "agent": "Agent 3", "title": "문서 초안을 작성했습니다", "body": "고객에게 전달할 초안을 검토할 수 있습니다.", "tab": "draft"})

    with closing(sqlite3.connect(local_store.path, timeout=10)) as sdb, sdb:
        sdb.execute(
            "INSERT INTO local_run (id, provider, body) VALUES (?, 'gpt', ?) ON CONFLICT(id) DO UPDATE SET body = excluded.body",
            (case_key, local_store._dump(run)),
        )
    local_store.save_progress(case_key, progress_events)
    return True


def load_backend_project_context(project_id: str) -> dict[str, Any]:
    """Load real project, baselines, documents, impact workbooks, rate card, and change requests from weave-postgres."""
    import psycopg
    from app.orchestration.local_run_store import LocalRunStore

    local_store = LocalRunStore(settings.prototype_run_db)

    # First, push any local runs belonging to this project into weave-postgres so both stores are up-to-date
    if local_store.path.is_file():
        with closing(sqlite3.connect(local_store.path)) as sdb:
            for (run_id, body_str) in sdb.execute("SELECT id, body FROM local_run").fetchall():
                try:
                    body = json.loads(body_str)
                    inp = (body.get("inputs") or {}).get("agent1_input") or {}
                    if (inp.get("context") or {}).get("project_id") == project_id:
                        sync_local_run_to_backend(project_id, run_id, local_store)
                except Exception:
                    continue

    with psycopg.connect(backend_db_url(), autocommit=True) as conn:
        proj = conn.execute(
            """
            SELECT p.project_id, p.project_name, p.customer_name, p.company_id, c.company_name,
                   p.start_date, p.end_date,
                   COALESCE(
                       (SELECT cm.company_member_id FROM project_member pm JOIN company_member cm ON pm.company_member_id = cm.company_member_id WHERE pm.project_id = p.project_id LIMIT 1),
                       (SELECT cm.company_member_id FROM company_member cm WHERE cm.company_id = p.company_id LIMIT 1)
                   ) AS pm_member_id,
                   COALESCE(
                       (SELECT cm.member_name FROM project_member pm JOIN company_member cm ON pm.company_member_id = cm.company_member_id WHERE pm.project_id = p.project_id LIMIT 1),
                       (SELECT cm.member_name FROM company_member cm WHERE cm.company_id = p.company_id LIMIT 1),
                       'PM'
                   ) AS pm_name
            FROM project p
            JOIN company c ON p.company_id = c.company_id
            WHERE p.project_id = %s
            """,
            (project_id,),
        ).fetchone()
        if not proj:
            raise KeyError(f"백엔드에서 프로젝트를 찾을 수 없습니다: {project_id}")

        pid = str(proj[0])
        project_name = proj[1]
        customer_name = proj[2]
        company_id = str(proj[3])
        company_name = proj[4]
        start_date = str(proj[5]) if proj[5] else None
        end_date = str(proj[6]) if proj[6] else None
        pm_member_id = str(proj[7])
        pm_name = proj[8]

        rate_card_id = _ensure_rate_card(conn, pid, company_id, pm_member_id)

        # Baselines
        bl_rows = conn.execute(
            """
            SELECT b.baseline_id, b.baseline_version_no, b.baseline_status, b.baseline_effective_date, b.created_at
            FROM baseline b
            WHERE b.project_id = %s
            ORDER BY b.baseline_version_no ASC
            """,
            (pid,),
        ).fetchall()

        baselines_list: list[dict[str, Any]] = []
        active_baseline_id: str | None = None
        active_baseline_as_of: str | None = None
        all_baseline_doc_ids: set[str] = set()
        active_doc_ids: set[str] = set()

        for idx, b in enumerate(bl_rows):
            bid = str(b[0])
            vno = b[1]
            bstatus = b[2]
            eff_date = str(b[3]) if b[3] else (str(b[4])[:10] if b[4] else datetime.now(timezone.utc).strftime("%Y-%m-%d"))
            bdocs = conn.execute(
                """
                SELECT bd.document_version_id, bd.judgment_role
                FROM baseline_document bd
                WHERE bd.baseline_id = %s
                ORDER BY bd.baseline_document_id
                """,
                (bid,),
            ).fetchall()
            doc_ids = [str(r[0]) for r in bdocs]
            all_baseline_doc_ids.update(doc_ids)
            supersedes = str(bl_rows[idx - 1][0]) if idx > 0 else None
            superseded_by = str(bl_rows[idx + 1][0]) if idx + 1 < len(bl_rows) else None
            if bstatus == "ACTIVE":
                active_baseline_id = bid
                active_baseline_as_of = eff_date
                active_doc_ids = set(doc_ids)
            baselines_list.append({
                "baseline_id": bid,
                "version_no": vno,
                "label": f"#{vno} ({bid[:8]})",
                "as_of": eff_date,
                "status": "ACTIVE" if bstatus == "ACTIVE" else "SUPERSEDED",
                "supersedes": supersedes,
                "superseded_by": superseded_by,
                "document_ids": doc_ids,
                "source": "backend",
            })

        if not active_baseline_id and baselines_list:
            last_bl = baselines_list[-1]
            active_baseline_id = last_bl["baseline_id"]
            active_baseline_as_of = last_bl["as_of"]
            active_doc_ids = set(last_bl["document_ids"])

        # Documents & Impact Workbooks
        doc_rows = conn.execute(
            """
            SELECT d.document_id, d.document_title, d.document_code, d.document_type,
                   dv.document_version_id, dv.document_version_no, dv.document_approval_status,
                   dv.document_effective_date, dv.extraction_status, dv.section_count,
                   (SELECT count(*) FROM document_chunk dc WHERE dc.document_version_id = dv.document_version_id) AS chunks,
                   d.created_at
            FROM document d
            JOIN document_version dv ON d.document_id = dv.document_id
            WHERE d.project_id = %s
            ORDER BY d.document_code ASC, dv.document_version_no DESC
            """,
            (pid,),
        ).fetchall()

        contract_docs: list[dict[str, Any]] = []
        impact_workbooks: list[dict[str, Any]] = []
        for r in doc_rows:
            doc_id = str(r[0])
            title = r[1]
            code = r[2]
            dtype = r[3]
            dvid = str(r[4])
            vno = r[5]
            approval = r[6]
            eff = str(r[7]) if r[7] else None
            ext_status = r[8]
            sec_count = r[9] if r[9] is not None else int(r[10] or 0)
            created_at = str(r[11])[:10] if r[11] else ""

            if dtype == "IMPACT_WORKBOOK":
                impact_workbooks.append({
                    "document_id": doc_id,
                    "doc_id": dvid,
                    "title": title,
                    "document_code": code,
                    "version_no": vno,
                    "approval_status": approval,
                    "effective_date": eff,
                    "extraction_status": ext_status,
                    "created_at": created_at,
                })
            else:
                contract_docs.append({
                    "document_id": doc_id,
                    "doc_id": dvid,
                    "title": f"{title} ({code} · V{vno})",
                    "raw_title": title,
                    "document_code": code,
                    "version_no": vno,
                    "doc_type": DOC_TYPE_MAP.get(dtype, "other"),
                    "backend_doc_type": dtype,
                    "section_count": sec_count,
                    "in_baseline": dvid in all_baseline_doc_ids,
                    "in_active_baseline": dvid in active_doc_ids,
                    "source": "backend",
                    "approval_status": approval,
                    "effective_date": eff,
                    "extraction_status": ext_status,
                    "created_at": created_at,
                })

        # Change requests + review_version + ai_analysis from weave-postgres
        cr_rows = conn.execute(
            """
            SELECT cr.change_request_id, cr.cr_number, cr.change_request_title, cr.progress_status, cr.created_at,
                   rs.request_original_text, rs.requester, rs.request_channel, rs.received_at,
                   cr.review_version_id
            FROM change_request cr
            LEFT JOIN LATERAL (
                SELECT request_original_text, requester, request_channel, received_at
                FROM request_source
                WHERE change_request_id = cr.change_request_id
                ORDER BY created_at DESC
                LIMIT 1
            ) rs ON true
            WHERE cr.project_id = %s
            ORDER BY cr.created_at DESC
            """,
            (pid,),
        ).fetchall()

        cr_analysis_data: dict[str, dict[str, Any]] = {}
        for r in cr_rows:
            cr_id = str(r[0])
            rv_id = str(r[9]) if r[9] else None
            if not rv_id:
                continue
            db_items = conn.execute(
                """
                SELECT request_item_id, item_type, content, source_start, source_end, change_kind
                FROM request_item
                WHERE review_version_id = %s AND retired_at IS NULL
                ORDER BY created_at ASC, request_item_id ASC
                """,
                (rv_id,),
            ).fetchall()
            analyses = conn.execute(
                """
                SELECT DISTINCT ON (analysis_type) analysis_type, result
                FROM ai_analysis
                WHERE review_version_id = %s AND analysis_status = 'COMPLETED' AND result IS NOT NULL
                ORDER BY analysis_type, analysis_created_at DESC
                """,
                (rv_id,),
            ).fetchall()
            by_type: dict[str, dict[str, Any]] = {}
            for atype, res_str in analyses:
                try:
                    by_type[atype] = json.loads(res_str) if isinstance(res_str, str) else res_str
                except Exception:
                    continue
            prop_row = conn.execute(
                "SELECT proposal_text FROM proposal_version WHERE review_version_id = %s ORDER BY proposal_version_no DESC LIMIT 1",
                (rv_id,),
            ).fetchone()
            cr_analysis_data[cr_id] = {
                "db_items": db_items,
                "decomp": by_type.get("DECOMPOSITION"),
                "scope": by_type.get("SCOPE"),
                "impact": by_type.get("IMPACT"),
                "proposal_text": prop_row[0] if prop_row else None,
            }

    # Refresh remote KB and rate card in loader cache so Agent 2 & Agent 3 can immediately use them
    if active_baseline_id:
        try:
            loader.refresh_remote(active_baseline_id)
        except Exception:
            pass
    if rate_card_id:
        try:
            loader.rate_cards().get(rate_card_id)
        except Exception:
            pass

    # Materialize any Backend-analyzed CRs into LocalRunStore so Developer Prototype shows their full results
    for r in cr_rows:
        cr_id = str(r[0])
        cr_number = r[1]
        cr_title = r[2]
        created_at = r[4].isoformat() if hasattr(r[4], "isoformat") else str(r[4])
        orig_text = r[5] or cr_title
        requester = r[6] or "고객사 담당자"
        channel = r[7] or "EMAIL"
        received_at = r[8].isoformat() if hasattr(r[8], "isoformat") else (str(r[8]) if r[8] else created_at)
        adata = cr_analysis_data.get(cr_id)
        if adata and (adata["db_items"] or adata["decomp"]):
            _materialize_backend_cr_to_local_store(
                local_store,
                cr_number,
                pid,
                project_name,
                customer_name,
                active_baseline_id or "",
                active_baseline_as_of or "",
                rate_card_id or "",
                cr_title,
                orig_text,
                requester,
                channel,
                received_at,
                adata["db_items"],
                adata["decomp"],
                adata["scope"],
                adata["impact"],
                adata["proposal_text"],
            )

    # Check LocalRunStore for all runs belonging to this project
    project_runs: dict[str, dict[str, Any]] = {}
    if local_store.path.is_file():
        with closing(sqlite3.connect(local_store.path)) as sdb:
            for run_id, provider, body_str in sdb.execute("SELECT id, provider, body FROM local_run").fetchall():
                try:
                    body = json.loads(body_str)
                    inp = (body.get("inputs") or {}).get("agent1_input") or {}
                    ctx = inp.get("context") or {}
                    if ctx.get("project_id") == pid:
                        project_runs[run_id] = {
                            "case_id": run_id,
                            "provider": provider,
                            "status": body.get("status"),
                            "message_text": inp.get("message_text") or "",
                            "subject": inp.get("subject") or "",
                            "received_at": inp.get("received_at") or "",
                            "sender_name": inp.get("sender_name") or "직접 입력",
                            "channel": inp.get("channel") or "OTHER",
                        }
                except Exception:
                    continue

    change_requests: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()

    for r in cr_rows:
        cr_id = str(r[0])
        cr_number = r[1]
        cr_title = r[2]
        p_status = r[3]
        created_at = r[4].isoformat() if hasattr(r[4], "isoformat") else str(r[4])
        orig_text = r[5] or cr_title
        requester = r[6] or "고객사 담당자"
        channel = r[7] or "EMAIL"
        received_at = r[8].isoformat() if hasattr(r[8], "isoformat") else (str(r[8]) if r[8] else created_at)

        matched_run_id = cr_number if cr_number in project_runs else (cr_id if cr_id in project_runs else None)
        case_key = matched_run_id or cr_number
        seen_case_ids.add(case_key)
        seen_case_ids.add(cr_id)
        change_requests.append({
            "id": case_key,
            "change_request_id": cr_id,
            "cr_number": cr_number,
            "title": cr_title,
            "message_text": orig_text,
            "requester": requester,
            "channel": channel,
            "received_at": received_at,
            "date": received_at[5:10] if len(received_at) >= 10 else created_at[5:10],
            "progress_status": p_status,
            "has_run": matched_run_id is not None,
        })

    return {
        "project_id": pid,
        "name": project_name,
        "customer_name": customer_name,
        "company_id": company_id,
        "company_name": company_name,
        "start_date": start_date,
        "end_date": end_date,
        "pm_member_id": pm_member_id,
        "pm_name": pm_name,
        "rate_card_id": rate_card_id,
        "active_baseline_id": active_baseline_id,
        "active_baseline_as_of": active_baseline_as_of,
        "baselines": baselines_list,
        "documents": contract_docs,
        "impact_workbooks": impact_workbooks,
        "change_requests": change_requests,
    }
