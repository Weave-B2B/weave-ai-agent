CREATE TABLE "job_role" (
	"job_role_id"	UUID		NOT NULL,
	"company_id"	UUID		NOT NULL,
	"job_role_code"	VARCHAR(50)		NOT NULL,
	"job_role_name"	VARCHAR(100)		NOT NULL
);

CREATE TABLE "internal_approval" (
	"internal_approval_id"	UUID		NOT NULL,
	"proposal_version_id"	UUID		NOT NULL,
	"company_member_id"	UUID		NOT NULL,
	"requester_member_id"	UUID		NOT NULL,
	"internal_approval_status"	VARCHAR(30)		NOT NULL,
	"approval_reason"	TEXT		NULL,
	"approved_content_hash"	CHAR(64)		NULL,
	"requested_at"	TIMESTAMPTZ		NOT NULL,
	"approval_decided_at"	TIMESTAMPTZ		NULL
);

CREATE TABLE "invoice_item" (
	"invoice_item_id"	UUID		NOT NULL,
	"billed_amount"	NUMERIC(18, 2)		NOT NULL,
	"invoice_draft_id"	UUID		NOT NULL,
	"billing_candidate_id"	UUID		NOT NULL
);

CREATE TABLE "request_item" (
	"request_item_id"	UUID		NOT NULL,
	"review_version_id"	UUID		NOT NULL,
	"request_source_id"	UUID		NULL,
	"stable_item_key"	UUID		NOT NULL,
	"item_type"	VARCHAR(30)		NOT NULL,
	"content"	TEXT		NOT NULL,
	"source_start"	INTEGER		NULL,
	"source_end"	INTEGER		NULL,
	"item_origin"	VARCHAR(30)		NOT NULL,
	"item_edit_reason"	TEXT		NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL,
	"created_by"	UUID		NULL
);

COMMENT ON COLUMN "request_item"."stable_item_key" IS 'UNIQUE(review_version_id, stable_item_key) — 버전 비교 오류 방지';

COMMENT ON COLUMN "request_item"."source_start" IS 'CHECK(source_start·source_end 둘 다 NULL이거나 request_source_id IS NOT NULL AND source_start >= 0 AND source_end > source_start) — 원문 위치 오입력 방지';

COMMENT ON COLUMN "request_item"."created_by" IS 'FK(company_member.company_member_id) — AI 분해 항목은 NULL';

CREATE TABLE "customer_response" (
	"customer_response_id"	UUID		NOT NULL,
	"delivery_record_id"	UUID		NOT NULL,
	"response_status"	VARCHAR(30)		NOT NULL,
	"response_original_text"	TEXT		NULL,
	"responder_name"	VARCHAR(100)		NOT NULL,
	"responder_position"	VARCHAR(100)		NULL,
	"response_confirmed_at"	TIMESTAMPTZ		NOT NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL,
	"created_by"	UUID		NOT NULL
);

COMMENT ON COLUMN "customer_response"."created_by" IS 'FK(company_member.company_member_id)';

CREATE TABLE "change_agreement" (
	"change_agreement_id"	UUID		NOT NULL,
	"customer_response_id"	UUID		NOT NULL,
	"project_id"	UUID		NOT NULL,
	"document_version_id"	UUID		NULL,
	"baseline_id"	UUID		NULL,
	"agreement_commercial_decision"	VARCHAR(30)		NOT NULL,
	"agreed_amount"	NUMERIC(18, 2)		NOT NULL,
	"agreement_currency"	VARCHAR(3)		NOT NULL,
	"completion_required"	BOOLEAN		NOT NULL,
	"acceptance_required"	BOOLEAN		NOT NULL,
	"finalized_at"	TIMESTAMPTZ		NOT NULL,
	"created_by"	UUID		NOT NULL
);

COMMENT ON COLUMN "change_agreement"."created_by" IS 'FK(company_member.company_member_id)';

CREATE TABLE "review_version" (
	"review_version_id"	UUID		NOT NULL,
	"change_request_id"	UUID		NOT NULL,
	"project_id"	UUID		NOT NULL,
	"baseline_id"	UUID		NOT NULL,
	"review_version_no"	INTEGER		NOT NULL,
	"review_status"	VARCHAR(30)		NOT NULL,
	"review_commercial_decision"	VARCHAR(30)		NULL,
	"commercial_reason"	TEXT		NULL,
	"schedule_status"	VARCHAR(30)		NOT NULL,
	"additional_days"	NUMERIC(10, 2)		NULL,
	"schedule_assumptions"	TEXT		NULL,
	"completion_required"	BOOLEAN		NOT NULL,
	"acceptance_required"	BOOLEAN		NOT NULL,
	"review_content_hash"	CHAR(64)		NULL,
	"frozen_at"	TIMESTAMPTZ		NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL,
	"created_by"	UUID		NOT NULL
);

COMMENT ON COLUMN "review_version"."review_version_no" IS 'UNIQUE(change_request_id, review_version_no) — 이전 버전은 번호 -1로 조회';

COMMENT ON COLUMN "review_version"."created_by" IS 'FK(company_member.company_member_id)';

CREATE TABLE "acceptance_record" (
	"acceptance_record_id"	UUID		NOT NULL,
	"execution_id"	UUID		NOT NULL,
	"acceptance_status"	VARCHAR(30)		NOT NULL,
	"acceptance_note"	TEXT		NULL,
	"acceptance_confirmed_at"	TIMESTAMPTZ		NULL,
	"created_by"	UUID		NOT NULL
);

COMMENT ON COLUMN "acceptance_record"."created_by" IS 'FK(company_member.company_member_id)';

CREATE TABLE "project_member" (
	"project_member_id"	UUID		NOT NULL,
	"project_id"	UUID		NOT NULL,
	"company_member_id"	UUID		NOT NULL,
	"project_role"	VARCHAR(30)		NOT NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL,
	"removed_at"	TIMESTAMPTZ		NULL,
	"removed_by"	UUID		NULL
);

COMMENT ON COLUMN "project_member"."removed_at" IS '프로젝트 멤버 제거 일시 (G-10)';

COMMENT ON COLUMN "project_member"."removed_by" IS 'FK(company_member.company_member_id) — 프로젝트 멤버 제거자 (G-10)';

CREATE TABLE "execution" (
	"execution_id"	UUID		NOT NULL,
	"execution_status"	VARCHAR(30)		NOT NULL,
	"company_member_id"	UUID		NOT NULL,
	"authorized_at"	TIMESTAMPTZ		NOT NULL,
	"completed_date"	DATE		NULL,
	"actual_schedule_impact"	NUMERIC(10, 2)		NULL,
	"agreement_item_id"	UUID		NOT NULL
);

COMMENT ON COLUMN "execution"."agreement_item_id" IS '수행한 합의 항목 — 합의는 agreement_item.change_agreement_id로 조회';

CREATE TABLE "app_user" (
	"user_id"	UUID		NOT NULL,
	"auth_subject"	VARCHAR(255)		NOT NULL,
	"user_name"	VARCHAR(100)		NULL,
	"email"	VARCHAR(320)		NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL
);

CREATE TABLE "actual_effort" (
	"actual_effort_id"	UUID		NOT NULL,
	"execution_id"	UUID		NOT NULL,
	"job_role_id"	UUID		NOT NULL,
	"actual_md"	NUMERIC(10, 2)		NOT NULL,
	"actual_internal_rate"	NUMERIC(18, 2)		NULL,
	"effort_note"	TEXT		NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL,
	"created_by"	UUID		NOT NULL
);

COMMENT ON COLUMN "actual_effort"."created_by" IS 'FK(company_member.company_member_id)';

CREATE TABLE "response_evidence" (
	"response_evidence_id"	UUID		NOT NULL,
	"customer_response_id"	UUID		NOT NULL,
	"file_object_id"	UUID		NOT NULL
);

CREATE TABLE "billing_condition" (
	"billing_condition_id"	UUID		NOT NULL,
	"change_agreement_id"	UUID		NOT NULL,
	"condition_text"	TEXT		NOT NULL,
	"condition_status"	VARCHAR(30)		NOT NULL,
	"file_object_id"	UUID		NULL,
	"company_member_id"	UUID		NULL,
	"checked_at"	TIMESTAMPTZ		NULL
);

CREATE TABLE "role_rate" (
	"role_rate_id"	UUID		NOT NULL,
	"rate_card_version_id"	UUID		NOT NULL,
	"job_role_id"	UUID		NOT NULL,
	"internal_rate"	NUMERIC(18, 2)		NOT NULL,
	"selling_rate"	NUMERIC(18, 2)		NOT NULL
);

CREATE TABLE "baseline" (
	"baseline_id"	UUID		NOT NULL,
	"project_id"	UUID		NOT NULL,
	"baseline_version_no"	INTEGER		NOT NULL,
	"baseline_effective_date"	DATE		NULL,
	"baseline_status"	VARCHAR(30)		NOT NULL,
	"change_reason"	TEXT		NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL,
	"created_by"	UUID		NOT NULL
);

COMMENT ON COLUMN "baseline"."baseline_version_no" IS 'UNIQUE(project_id, baseline_version_no) — 이전 기준선은 번호 -1로 조회';

COMMENT ON COLUMN "baseline"."created_by" IS 'FK(company_member.company_member_id)';

CREATE TABLE "acceptance_evidence" (
	"acceptance_evidence_id"	UUID		NOT NULL,
	"acceptance_record_id"	UUID		NOT NULL,
	"file_object_id"	UUID		NOT NULL
);

CREATE TABLE "billing_evidence" (
	"billing_evidence_id"	UUID		NOT NULL,
	"billing_record_id"	UUID		NOT NULL,
	"file_object_id"	UUID		NOT NULL
);

CREATE TABLE "billing_record" (
	"billing_record_id"	UUID		NOT NULL,
	"invoice_draft_id"	UUID		NOT NULL,
	"project_id"	UUID		NOT NULL,
	"external_invoice_number"	VARCHAR(100)		NULL,
	"billed_subtotal"	NUMERIC(18, 2)		NOT NULL,
	"billed_tax_amount"	NUMERIC(18, 2)		NOT NULL,
	"billed_total_amount"	NUMERIC(18, 2)		NOT NULL,
	"issued_date"	DATE		NOT NULL,
	"billing_due_date"	DATE		NULL,
	"correction_note"	TEXT		NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL,
	"created_by"	UUID		NOT NULL
);

COMMENT ON COLUMN "billing_record"."created_by" IS 'FK(company_member.company_member_id)';

CREATE TABLE "request_attachment" (
	"request_attachment_id"	UUID		NOT NULL,
	"request_source_id"	UUID		NOT NULL,
	"file_object_id"	UUID		NOT NULL
);

COMMENT ON COLUMN "request_attachment"."file_object_id" IS 'UNIQUE(request_source_id, file_object_id) — 같은 원문에 같은 파일 중복 연결 방지';

CREATE TABLE "ai_analysis" (
	"ai_analysis_id"	UUID		NOT NULL,
	"review_version_id"	UUID		NOT NULL,
	"analysis_type"	VARCHAR(30)		NOT NULL,
	"model_id"	VARCHAR(255)		NULL,
	"prompt_version"	VARCHAR(100)		NULL,
	"input_hash"	CHAR(64)		NULL,
	"analysis_status"	VARCHAR(30)		NOT NULL,
	"result"	JSONB		NULL,
	"error_code"	VARCHAR(100)		NULL,
	"retry_count"	INTEGER		NOT NULL,
	"analysis_created_at"	TIMESTAMPTZ		NOT NULL,
	"completed_at"	TIMESTAMPTZ		NULL,
	"requested_by"	UUID		NULL,
	"retryable"	BOOLEAN		NULL
);

COMMENT ON COLUMN "ai_analysis"."requested_by" IS 'FK(company_member.company_member_id) — 분석 실행자 (M-40). 시스템 자동 실행이면 NULL';

COMMENT ON COLUMN "ai_analysis"."retryable" IS '재시도 가능 여부 (C-MQ-03, G-12). 실패 시에만 값 존재';

CREATE TABLE "document" (
	"document_id"	UUID		NOT NULL,
	"project_id"	UUID		NOT NULL,
	"document_title"	VARCHAR(500)		NOT NULL,
	"document_type"	VARCHAR(30)		NOT NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL,
	"created_by"	UUID		NOT NULL
);

COMMENT ON COLUMN "document"."created_by" IS 'FK(company_member.company_member_id)';

CREATE TABLE "judgment_evidence" (
	"judgment_evidence_id"	UUID		NOT NULL,
	"pm_judgment_id"	UUID		NOT NULL,
	"item_evidence_id"	UUID		NOT NULL
);

COMMENT ON COLUMN "judgment_evidence"."item_evidence_id" IS 'UNIQUE(pm_judgment_id, item_evidence_id) — 같은 판단에 같은 근거 중복 연결 방지';

CREATE TABLE "file_object" (
	"file_object_id"	UUID		NOT NULL,
	"company_id"	UUID		NOT NULL,
	"project_id"	UUID		NOT NULL,
	"object_key"	TEXT		NOT NULL,
	"original_filename"	VARCHAR(500)		NOT NULL,
	"mime_type"	VARCHAR(100)		NOT NULL,
	"size_bytes"	BIGINT		NOT NULL,
	"sha256"	CHAR(64)		NOT NULL,
	"upload_status"	VARCHAR(30)		NOT NULL,
	"file_purpose"	VARCHAR(30)		NOT NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL,
	"created_by"	UUID		NOT NULL
);

COMMENT ON COLUMN "file_object"."created_by" IS 'FK(company_member.company_member_id)';

CREATE TABLE "document_version" (
	"document_version_id"	UUID		NOT NULL,
	"document_id"	UUID		NOT NULL,
	"file_object_id"	UUID		NOT NULL,
	"document_version_no"	INTEGER		NOT NULL,
	"document_approval_status"	VARCHAR(30)		NOT NULL,
	"document_effective_date"	DATE		NULL,
	"extraction_status"	VARCHAR(30)		NOT NULL,
	"extraction_error"	TEXT		NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL,
	"created_by"	UUID		NOT NULL
);

COMMENT ON COLUMN "document_version"."created_by" IS 'FK(company_member.company_member_id)';

CREATE TABLE "estimate_line" (
	"estimate_line_id"	UUID		NOT NULL,
	"request_item_id"	UUID		NOT NULL,
	"role_rate_id"	UUID		NULL,
	"job_role_id"	UUID		NOT NULL,
	"task"	TEXT		NOT NULL,
	"estimated_md"	NUMERIC(10, 2)		NOT NULL,
	"internal_rate_snapshot"	NUMERIC(18, 2)		NOT NULL,
	"selling_rate_snapshot"	NUMERIC(18, 2)		NOT NULL,
	"assumptions"	TEXT		NULL,
	"estimate_status"	VARCHAR(30)		NOT NULL,
	"estimate_edit_reason"	TEXT		NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL,
	"created_by"	UUID		NULL,
	"confirmed_at"	TIMESTAMPTZ		NULL,
	"confirmed_by"	UUID		NULL
);

COMMENT ON COLUMN "estimate_line"."created_by" IS 'FK(company_member.company_member_id) — AI 초안이면 NULL';

COMMENT ON COLUMN "estimate_line"."confirmed_by" IS 'FK(company_member.company_member_id)';

CREATE TABLE "project" (
	"project_id"	UUID		NOT NULL,
	"company_id"	UUID		NOT NULL,
	"customer_name"	VARCHAR(255)		NOT NULL,
	"project_name"	VARCHAR(255)		NOT NULL,
	"contract_type"	VARCHAR(30)		NOT NULL,
	"start_date"	DATE		NULL,
	"end_date"	DATE		NULL,
	"project_currency"	VARCHAR(3)		NOT NULL,
	"project_status"	VARCHAR(30)		NOT NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL,
	"created_by"	UUID		NOT NULL
);

COMMENT ON COLUMN "project"."created_by" IS 'FK(company_member.company_member_id)';

CREATE TABLE "request_source" (
	"request_source_id"	UUID		NOT NULL,
	"change_request_id"	UUID		NOT NULL,
	"request_original_text"	TEXT		NOT NULL,
	"requester"	VARCHAR(500)		NULL,
	"request_recipient"	VARCHAR(500)		NULL,
	"request_channel"	VARCHAR(30)		NOT NULL,
	"received_at"	TIMESTAMPTZ		NOT NULL,
	"source_content_hash"	CHAR(64)		NOT NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL,
	"created_by"	UUID		NOT NULL
);

COMMENT ON COLUMN "request_source"."created_by" IS 'FK(company_member.company_member_id)';

CREATE TABLE "company" (
	"company_id"	UUID		NOT NULL,
	"company_name"	VARCHAR(255)		NOT NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL
);

CREATE TABLE "pm_judgment" (
	"pm_judgment_id"	UUID		NOT NULL,
	"request_item_id"	UUID		NOT NULL,
	"company_member_id"	UUID		NOT NULL,
	"scope_status"	VARCHAR(30)		NOT NULL,
	"judgment_reason"	TEXT		NULL,
	"judged_at"	TIMESTAMPTZ		NOT NULL
);

COMMENT ON COLUMN "pm_judgment"."scope_status" IS 'ENUM(IN_SCOPE, OUT_OF_SCOPE, UNCLEAR) — 기존 범위 내 / 범위 밖 가능성 / 판단 보류';

CREATE TABLE "baseline_document" (
	"baseline_document_id"	UUID		NOT NULL,
	"baseline_id"	UUID		NOT NULL,
	"document_version_id"	UUID		NOT NULL
);

CREATE TABLE "delivery_record" (
	"delivery_record_id"	UUID		NOT NULL,
	"internal_approval_id"	UUID		NOT NULL,
	"delivery_recipient"	VARCHAR(500)		NOT NULL,
	"delivery_channel"	VARCHAR(30)		NOT NULL,
	"sent_at"	TIMESTAMPTZ		NOT NULL,
	"delivery_note"	TEXT		NULL,
	"created_by"	UUID		NOT NULL
);

COMMENT ON COLUMN "delivery_record"."created_by" IS 'FK(company_member.company_member_id)';

CREATE TABLE "document_chunk" (
	"document_chunk_id"	UUID		NOT NULL,
	"document_version_id"	UUID		NOT NULL,
	"chunk_text"	TEXT		NOT NULL,
	"page_number"	INTEGER		NULL,
	"paragraph_ref"	VARCHAR(255)		NULL,
	"start_offset"	INTEGER		NULL,
	"end_offset"	INTEGER		NULL,
	"chunk_origin"	VARCHAR(30)		NOT NULL
);

CREATE TABLE "change_request" (
	"change_request_id"	UUID		NOT NULL,
	"project_id"	UUID		NOT NULL,
	"company_member_id"	UUID		NULL,
	"review_version_id"	UUID		NULL,
	"cr_number"	VARCHAR(100)		NOT NULL,
	"change_request_title"	VARCHAR(500)		NOT NULL,
	"priority"	VARCHAR(30)		NOT NULL,
	"progress_status"	VARCHAR(30)		NOT NULL,
	"needs_reanalysis"	BOOLEAN		NOT NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL,
	"created_by"	UUID		NOT NULL
);

COMMENT ON COLUMN "change_request"."cr_number" IS 'UNIQUE(project_id, cr_number) — 같은 프로젝트에서 CR 번호 중복 발급 방지';

COMMENT ON COLUMN "change_request"."created_by" IS 'FK(company_member.company_member_id)';

CREATE TABLE "proposal_version" (
	"proposal_version_id"	UUID		NOT NULL,
	"review_version_id"	UUID		NOT NULL,
	"proposal_version_no"	INTEGER		NOT NULL,
	"proposal_text"	TEXT		NOT NULL,
	"proposed_amount"	NUMERIC(18, 2)		NOT NULL,
	"proposal_currency"	VARCHAR(3)		NOT NULL,
	"proposal_content_hash"	CHAR(64)		NULL,
	"frozen_at"	TIMESTAMPTZ		NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL,
	"created_by"	UUID		NOT NULL
);

COMMENT ON COLUMN "proposal_version"."created_by" IS 'FK(company_member.company_member_id)';

CREATE TABLE "billing_candidate" (
	"billing_candidate_id"	UUID		NOT NULL,
	"project_id"	UUID		NOT NULL,
	"billing_ratio"	NUMERIC(5, 2)		NOT NULL,
	"eligibility_status"	VARCHAR(30)		NOT NULL,
	"checked_at"	TIMESTAMPTZ		NULL,
	"agreement_item_id"	UUID		NOT NULL
);

COMMENT ON COLUMN "billing_candidate"."agreement_item_id" IS '청구 대상 합의 항목 — 합의는 agreement_item.change_agreement_id로 조회';

CREATE TABLE "company_member" (
	"company_member_id"	UUID		NOT NULL,
	"company_id"	UUID		NOT NULL,
	"user_id"	UUID		NULL,
	"member_name"	VARCHAR(100)		NOT NULL,
	"member_email"	VARCHAR(320)		NOT NULL,
	"member_status"	VARCHAR(30)		NOT NULL,
	"can_approve"	BOOLEAN		NOT NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL,
	"created_by"	UUID		NULL,
	"member_role"	VARCHAR(30)		NOT NULL
);

COMMENT ON COLUMN "company_member"."created_by" IS 'FK(company_member.company_member_id) — 최초 가입 구성원은 NULL';

COMMENT ON COLUMN "company_member"."member_role" IS '회사 내 역할 (G-18, G-20). 역할이 project_member.project_role을 뜻하면 삭제';

CREATE TABLE "question_answer" (
	"question_answer_id"	UUID		NOT NULL,
	"review_version_id"	UUID		NOT NULL,
	"request_item_id"	UUID		NULL,
	"question"	TEXT		NOT NULL,
	"answer"	TEXT		NULL,
	"question_status"	VARCHAR(30)		NOT NULL,
	"answered_at"	TIMESTAMPTZ		NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL
);

COMMENT ON COLUMN "question_answer"."question_status" IS 'CHECK(OPEN이면 answer·answered_at NULL, ANSWERED이면 둘 다 NOT NULL) — 답변 내용과 질문 상태 불일치 방지';

CREATE TABLE "invoice_draft" (
	"invoice_draft_id"	UUID		NOT NULL,
	"project_id"	UUID		NOT NULL,
	"description"	TEXT		NULL,
	"draft_subtotal"	NUMERIC(18, 2)		NOT NULL,
	"draft_tax_amount"	NUMERIC(18, 2)		NOT NULL,
	"draft_total_amount"	NUMERIC(18, 2)		NOT NULL,
	"billing_date"	DATE		NULL,
	"draft_due_date"	DATE		NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL,
	"created_by"	UUID		NOT NULL
);

COMMENT ON COLUMN "invoice_draft"."created_by" IS 'FK(company_member.company_member_id)';

CREATE TABLE "item_evidence" (
	"item_evidence_id"	UUID		NOT NULL,
	"request_item_id"	UUID		NOT NULL,
	"document_chunk_id"	UUID		NOT NULL,
	"ai_analysis_id"	UUID		NULL,
	"quote"	TEXT		NULL,
	"evidence_origin"	VARCHAR(30)		NOT NULL,
	"conflict_type"	VARCHAR(30)		NULL,
	"location_verified"	BOOLEAN		NOT NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL,
	"created_by"	UUID		NULL,
	"excluded_at"	TIMESTAMPTZ		NULL,
	"excluded_by"	UUID		NULL,
	"exclusion_reason"	TEXT		NULL
);

COMMENT ON COLUMN "item_evidence"."ai_analysis_id" IS 'UNIQUE(request_item_id, document_chunk_id, ai_analysis_id) — SQS 중복 수신 시 중복 저장 방지';

COMMENT ON COLUMN "item_evidence"."evidence_origin" IS 'ENUM(AI, MANUAL) — AI 분석 근거 / PM 수동 추가 근거';

COMMENT ON COLUMN "item_evidence"."conflict_type" IS 'ENUM(VERSION_CONFLICT, UNCONFIRMED_COMMITMENT) — 문서 버전 간 상충 / 후속 약속 미확인. 충돌 없으면 NULL';

COMMENT ON COLUMN "item_evidence"."created_by" IS 'FK(company_member.company_member_id) — 수동 근거 추가자 (AI 근거는 NULL)';

COMMENT ON COLUMN "item_evidence"."excluded_at" IS '근거 제외 일시 (C-08)';

COMMENT ON COLUMN "item_evidence"."excluded_by" IS 'FK(company_member.company_member_id) — 근거 제외자 (C-08)';

COMMENT ON COLUMN "item_evidence"."exclusion_reason" IS '근거 제외 사유 (C-08)';

CREATE TABLE "rate_card_version" (
	"rate_card_version_id"	UUID		NOT NULL,
	"project_id"	UUID		NOT NULL,
	"rate_card_version_no"	INTEGER		NOT NULL,
	"hours_per_md"	NUMERIC(10, 2)		NOT NULL,
	"rate_currency"	VARCHAR(3)		NOT NULL,
	"rate_effective_date"	DATE		NOT NULL,
	"created_at"	TIMESTAMPTZ		NOT NULL,
	"created_by"	UUID		NOT NULL
);

COMMENT ON COLUMN "rate_card_version"."created_by" IS 'FK(company_member.company_member_id)';

CREATE TABLE "request_note" (
	"request_note_id"	UUID		NOT NULL,
	"change_request_id"	UUID		NOT NULL,
	"company_member_id"	UUID		NOT NULL,
	"body"	TEXT		NOT NULL,
	"note_created_at"	TIMESTAMPTZ		NOT NULL
);

CREATE TABLE "agreement_item" (
	"agreement_item_id"	UUID		NOT NULL,
	"agreed_scope"	TEXT		NULL,
	"agreed_amount"	NUMERIC(18, 2)		NOT NULL,
	"charge_type"	VARCHAR(30)		NOT NULL,
	"change_agreement_id"	UUID		NOT NULL,
	"request_item_id"	UUID		NOT NULL
);

COMMENT ON COLUMN "agreement_item"."agreed_scope" IS '합의 범위 (타입 확인 필요)';

COMMENT ON COLUMN "agreement_item"."charge_type" IS '유상·무상 구분';

ALTER TABLE "job_role" ADD CONSTRAINT "PK_JOB_ROLE" PRIMARY KEY (
	"job_role_id"
);

ALTER TABLE "internal_approval" ADD CONSTRAINT "PK_INTERNAL_APPROVAL" PRIMARY KEY (
	"internal_approval_id"
);

ALTER TABLE "invoice_item" ADD CONSTRAINT "PK_INVOICE_ITEM" PRIMARY KEY (
	"invoice_item_id"
);

ALTER TABLE "request_item" ADD CONSTRAINT "PK_REQUEST_ITEM" PRIMARY KEY (
	"request_item_id"
);

ALTER TABLE "customer_response" ADD CONSTRAINT "PK_CUSTOMER_RESPONSE" PRIMARY KEY (
	"customer_response_id"
);

ALTER TABLE "change_agreement" ADD CONSTRAINT "PK_CHANGE_AGREEMENT" PRIMARY KEY (
	"change_agreement_id"
);

ALTER TABLE "review_version" ADD CONSTRAINT "PK_REVIEW_VERSION" PRIMARY KEY (
	"review_version_id"
);

ALTER TABLE "acceptance_record" ADD CONSTRAINT "PK_ACCEPTANCE_RECORD" PRIMARY KEY (
	"acceptance_record_id"
);

ALTER TABLE "project_member" ADD CONSTRAINT "PK_PROJECT_MEMBER" PRIMARY KEY (
	"project_member_id"
);

ALTER TABLE "execution" ADD CONSTRAINT "PK_EXECUTION" PRIMARY KEY (
	"execution_id"
);

ALTER TABLE "app_user" ADD CONSTRAINT "PK_APP_USER" PRIMARY KEY (
	"user_id"
);

ALTER TABLE "actual_effort" ADD CONSTRAINT "PK_ACTUAL_EFFORT" PRIMARY KEY (
	"actual_effort_id"
);

ALTER TABLE "response_evidence" ADD CONSTRAINT "PK_RESPONSE_EVIDENCE" PRIMARY KEY (
	"response_evidence_id"
);

ALTER TABLE "billing_condition" ADD CONSTRAINT "PK_BILLING_CONDITION" PRIMARY KEY (
	"billing_condition_id"
);

ALTER TABLE "role_rate" ADD CONSTRAINT "PK_ROLE_RATE" PRIMARY KEY (
	"role_rate_id"
);

ALTER TABLE "baseline" ADD CONSTRAINT "PK_BASELINE" PRIMARY KEY (
	"baseline_id"
);

ALTER TABLE "acceptance_evidence" ADD CONSTRAINT "PK_ACCEPTANCE_EVIDENCE" PRIMARY KEY (
	"acceptance_evidence_id"
);

ALTER TABLE "billing_evidence" ADD CONSTRAINT "PK_BILLING_EVIDENCE" PRIMARY KEY (
	"billing_evidence_id"
);

ALTER TABLE "billing_record" ADD CONSTRAINT "PK_BILLING_RECORD" PRIMARY KEY (
	"billing_record_id"
);

ALTER TABLE "request_attachment" ADD CONSTRAINT "PK_REQUEST_ATTACHMENT" PRIMARY KEY (
	"request_attachment_id"
);

ALTER TABLE "ai_analysis" ADD CONSTRAINT "PK_AI_ANALYSIS" PRIMARY KEY (
	"ai_analysis_id"
);

ALTER TABLE "document" ADD CONSTRAINT "PK_DOCUMENT" PRIMARY KEY (
	"document_id"
);

ALTER TABLE "judgment_evidence" ADD CONSTRAINT "PK_JUDGMENT_EVIDENCE" PRIMARY KEY (
	"judgment_evidence_id"
);

ALTER TABLE "file_object" ADD CONSTRAINT "PK_FILE_OBJECT" PRIMARY KEY (
	"file_object_id"
);

ALTER TABLE "document_version" ADD CONSTRAINT "PK_DOCUMENT_VERSION" PRIMARY KEY (
	"document_version_id"
);

ALTER TABLE "estimate_line" ADD CONSTRAINT "PK_ESTIMATE_LINE" PRIMARY KEY (
	"estimate_line_id"
);

ALTER TABLE "project" ADD CONSTRAINT "PK_PROJECT" PRIMARY KEY (
	"project_id"
);

ALTER TABLE "request_source" ADD CONSTRAINT "PK_REQUEST_SOURCE" PRIMARY KEY (
	"request_source_id"
);

ALTER TABLE "company" ADD CONSTRAINT "PK_COMPANY" PRIMARY KEY (
	"company_id"
);

ALTER TABLE "pm_judgment" ADD CONSTRAINT "PK_PM_JUDGMENT" PRIMARY KEY (
	"pm_judgment_id"
);

ALTER TABLE "baseline_document" ADD CONSTRAINT "PK_BASELINE_DOCUMENT" PRIMARY KEY (
	"baseline_document_id"
);

ALTER TABLE "delivery_record" ADD CONSTRAINT "PK_DELIVERY_RECORD" PRIMARY KEY (
	"delivery_record_id"
);

ALTER TABLE "document_chunk" ADD CONSTRAINT "PK_DOCUMENT_CHUNK" PRIMARY KEY (
	"document_chunk_id"
);

ALTER TABLE "change_request" ADD CONSTRAINT "PK_CHANGE_REQUEST" PRIMARY KEY (
	"change_request_id"
);

ALTER TABLE "proposal_version" ADD CONSTRAINT "PK_PROPOSAL_VERSION" PRIMARY KEY (
	"proposal_version_id"
);

ALTER TABLE "billing_candidate" ADD CONSTRAINT "PK_BILLING_CANDIDATE" PRIMARY KEY (
	"billing_candidate_id"
);

ALTER TABLE "company_member" ADD CONSTRAINT "PK_COMPANY_MEMBER" PRIMARY KEY (
	"company_member_id"
);

ALTER TABLE "question_answer" ADD CONSTRAINT "PK_QUESTION_ANSWER" PRIMARY KEY (
	"question_answer_id"
);

ALTER TABLE "invoice_draft" ADD CONSTRAINT "PK_INVOICE_DRAFT" PRIMARY KEY (
	"invoice_draft_id"
);

ALTER TABLE "item_evidence" ADD CONSTRAINT "PK_ITEM_EVIDENCE" PRIMARY KEY (
	"item_evidence_id"
);

ALTER TABLE "rate_card_version" ADD CONSTRAINT "PK_RATE_CARD_VERSION" PRIMARY KEY (
	"rate_card_version_id"
);

ALTER TABLE "request_note" ADD CONSTRAINT "PK_REQUEST_NOTE" PRIMARY KEY (
	"request_note_id"
);

ALTER TABLE "agreement_item" ADD CONSTRAINT "PK_AGREEMENT_ITEM" PRIMARY KEY (
	"agreement_item_id"
);

ALTER TABLE "job_role" ADD CONSTRAINT "FK_company_TO_job_role_1" FOREIGN KEY (
	"company_id"
)
REFERENCES "company" (
	"company_id"
);

ALTER TABLE "internal_approval" ADD CONSTRAINT "FK_proposal_version_TO_internal_approval_1" FOREIGN KEY (
	"proposal_version_id"
)
REFERENCES "proposal_version" (
	"proposal_version_id"
);

ALTER TABLE "internal_approval" ADD CONSTRAINT "FK_company_member_TO_internal_approval_1" FOREIGN KEY (
	"company_member_id"
)
REFERENCES "company_member" (
	"company_member_id"
);

ALTER TABLE "internal_approval" ADD CONSTRAINT "FK_company_member_TO_internal_approval_2" FOREIGN KEY (
	"requester_member_id"
)
REFERENCES "company_member" (
	"company_member_id"
);

ALTER TABLE "invoice_item" ADD CONSTRAINT "FK_invoice_draft_TO_invoice_item_1" FOREIGN KEY (
	"invoice_draft_id"
)
REFERENCES "invoice_draft" (
	"invoice_draft_id"
);

ALTER TABLE "invoice_item" ADD CONSTRAINT "FK_billing_candidate_TO_invoice_item_1" FOREIGN KEY (
	"billing_candidate_id"
)
REFERENCES "billing_candidate" (
	"billing_candidate_id"
);

ALTER TABLE "request_item" ADD CONSTRAINT "FK_review_version_TO_request_item_1" FOREIGN KEY (
	"review_version_id"
)
REFERENCES "review_version" (
	"review_version_id"
);

ALTER TABLE "request_item" ADD CONSTRAINT "FK_request_source_TO_request_item_1" FOREIGN KEY (
	"request_source_id"
)
REFERENCES "request_source" (
	"request_source_id"
);

ALTER TABLE "customer_response" ADD CONSTRAINT "FK_delivery_record_TO_customer_response_1" FOREIGN KEY (
	"delivery_record_id"
)
REFERENCES "delivery_record" (
	"delivery_record_id"
);

ALTER TABLE "change_agreement" ADD CONSTRAINT "FK_customer_response_TO_change_agreement_1" FOREIGN KEY (
	"customer_response_id"
)
REFERENCES "customer_response" (
	"customer_response_id"
);

ALTER TABLE "change_agreement" ADD CONSTRAINT "FK_project_TO_change_agreement_1" FOREIGN KEY (
	"project_id"
)
REFERENCES "project" (
	"project_id"
);

ALTER TABLE "change_agreement" ADD CONSTRAINT "FK_document_version_TO_change_agreement_1" FOREIGN KEY (
	"document_version_id"
)
REFERENCES "document_version" (
	"document_version_id"
);

ALTER TABLE "change_agreement" ADD CONSTRAINT "FK_baseline_TO_change_agreement_1" FOREIGN KEY (
	"baseline_id"
)
REFERENCES "baseline" (
	"baseline_id"
);

ALTER TABLE "review_version" ADD CONSTRAINT "FK_change_request_TO_review_version_1" FOREIGN KEY (
	"change_request_id"
)
REFERENCES "change_request" (
	"change_request_id"
);

ALTER TABLE "review_version" ADD CONSTRAINT "FK_project_TO_review_version_1" FOREIGN KEY (
	"project_id"
)
REFERENCES "project" (
	"project_id"
);

ALTER TABLE "review_version" ADD CONSTRAINT "FK_baseline_TO_review_version_1" FOREIGN KEY (
	"baseline_id"
)
REFERENCES "baseline" (
	"baseline_id"
);

ALTER TABLE "acceptance_record" ADD CONSTRAINT "FK_execution_TO_acceptance_record_1" FOREIGN KEY (
	"execution_id"
)
REFERENCES "execution" (
	"execution_id"
);

ALTER TABLE "project_member" ADD CONSTRAINT "FK_project_TO_project_member_1" FOREIGN KEY (
	"project_id"
)
REFERENCES "project" (
	"project_id"
);

ALTER TABLE "project_member" ADD CONSTRAINT "FK_company_member_TO_project_member_1" FOREIGN KEY (
	"company_member_id"
)
REFERENCES "company_member" (
	"company_member_id"
);

ALTER TABLE "execution" ADD CONSTRAINT "FK_company_member_TO_execution_1" FOREIGN KEY (
	"company_member_id"
)
REFERENCES "company_member" (
	"company_member_id"
);

ALTER TABLE "execution" ADD CONSTRAINT "FK_agreement_item_TO_execution_1" FOREIGN KEY (
	"agreement_item_id"
)
REFERENCES "agreement_item" (
	"agreement_item_id"
);

ALTER TABLE "actual_effort" ADD CONSTRAINT "FK_execution_TO_actual_effort_1" FOREIGN KEY (
	"execution_id"
)
REFERENCES "execution" (
	"execution_id"
);

ALTER TABLE "actual_effort" ADD CONSTRAINT "FK_job_role_TO_actual_effort_1" FOREIGN KEY (
	"job_role_id"
)
REFERENCES "job_role" (
	"job_role_id"
);

ALTER TABLE "response_evidence" ADD CONSTRAINT "FK_customer_response_TO_response_evidence_1" FOREIGN KEY (
	"customer_response_id"
)
REFERENCES "customer_response" (
	"customer_response_id"
);

ALTER TABLE "response_evidence" ADD CONSTRAINT "FK_file_object_TO_response_evidence_1" FOREIGN KEY (
	"file_object_id"
)
REFERENCES "file_object" (
	"file_object_id"
);

ALTER TABLE "billing_condition" ADD CONSTRAINT "FK_change_agreement_TO_billing_condition_1" FOREIGN KEY (
	"change_agreement_id"
)
REFERENCES "change_agreement" (
	"change_agreement_id"
);

ALTER TABLE "billing_condition" ADD CONSTRAINT "FK_file_object_TO_billing_condition_1" FOREIGN KEY (
	"file_object_id"
)
REFERENCES "file_object" (
	"file_object_id"
);

ALTER TABLE "billing_condition" ADD CONSTRAINT "FK_company_member_TO_billing_condition_1" FOREIGN KEY (
	"company_member_id"
)
REFERENCES "company_member" (
	"company_member_id"
);

ALTER TABLE "role_rate" ADD CONSTRAINT "FK_rate_card_version_TO_role_rate_1" FOREIGN KEY (
	"rate_card_version_id"
)
REFERENCES "rate_card_version" (
	"rate_card_version_id"
);

ALTER TABLE "role_rate" ADD CONSTRAINT "FK_job_role_TO_role_rate_1" FOREIGN KEY (
	"job_role_id"
)
REFERENCES "job_role" (
	"job_role_id"
);

ALTER TABLE "baseline" ADD CONSTRAINT "FK_project_TO_baseline_1" FOREIGN KEY (
	"project_id"
)
REFERENCES "project" (
	"project_id"
);

ALTER TABLE "acceptance_evidence" ADD CONSTRAINT "FK_acceptance_record_TO_acceptance_evidence_1" FOREIGN KEY (
	"acceptance_record_id"
)
REFERENCES "acceptance_record" (
	"acceptance_record_id"
);

ALTER TABLE "acceptance_evidence" ADD CONSTRAINT "FK_file_object_TO_acceptance_evidence_1" FOREIGN KEY (
	"file_object_id"
)
REFERENCES "file_object" (
	"file_object_id"
);

ALTER TABLE "billing_evidence" ADD CONSTRAINT "FK_billing_record_TO_billing_evidence_1" FOREIGN KEY (
	"billing_record_id"
)
REFERENCES "billing_record" (
	"billing_record_id"
);

ALTER TABLE "billing_evidence" ADD CONSTRAINT "FK_file_object_TO_billing_evidence_1" FOREIGN KEY (
	"file_object_id"
)
REFERENCES "file_object" (
	"file_object_id"
);

ALTER TABLE "billing_record" ADD CONSTRAINT "FK_invoice_draft_TO_billing_record_1" FOREIGN KEY (
	"invoice_draft_id"
)
REFERENCES "invoice_draft" (
	"invoice_draft_id"
);

ALTER TABLE "billing_record" ADD CONSTRAINT "FK_project_TO_billing_record_1" FOREIGN KEY (
	"project_id"
)
REFERENCES "project" (
	"project_id"
);

ALTER TABLE "request_attachment" ADD CONSTRAINT "FK_request_source_TO_request_attachment_1" FOREIGN KEY (
	"request_source_id"
)
REFERENCES "request_source" (
	"request_source_id"
);

ALTER TABLE "request_attachment" ADD CONSTRAINT "FK_file_object_TO_request_attachment_1" FOREIGN KEY (
	"file_object_id"
)
REFERENCES "file_object" (
	"file_object_id"
);

ALTER TABLE "ai_analysis" ADD CONSTRAINT "FK_review_version_TO_ai_analysis_1" FOREIGN KEY (
	"review_version_id"
)
REFERENCES "review_version" (
	"review_version_id"
);

ALTER TABLE "document" ADD CONSTRAINT "FK_project_TO_document_1" FOREIGN KEY (
	"project_id"
)
REFERENCES "project" (
	"project_id"
);

ALTER TABLE "judgment_evidence" ADD CONSTRAINT "FK_pm_judgment_TO_judgment_evidence_1" FOREIGN KEY (
	"pm_judgment_id"
)
REFERENCES "pm_judgment" (
	"pm_judgment_id"
);

ALTER TABLE "judgment_evidence" ADD CONSTRAINT "FK_item_evidence_TO_judgment_evidence_1" FOREIGN KEY (
	"item_evidence_id"
)
REFERENCES "item_evidence" (
	"item_evidence_id"
);

ALTER TABLE "file_object" ADD CONSTRAINT "FK_company_TO_file_object_1" FOREIGN KEY (
	"company_id"
)
REFERENCES "company" (
	"company_id"
);

ALTER TABLE "file_object" ADD CONSTRAINT "FK_project_TO_file_object_1" FOREIGN KEY (
	"project_id"
)
REFERENCES "project" (
	"project_id"
);

ALTER TABLE "document_version" ADD CONSTRAINT "FK_document_TO_document_version_1" FOREIGN KEY (
	"document_id"
)
REFERENCES "document" (
	"document_id"
);

ALTER TABLE "document_version" ADD CONSTRAINT "FK_file_object_TO_document_version_1" FOREIGN KEY (
	"file_object_id"
)
REFERENCES "file_object" (
	"file_object_id"
);

ALTER TABLE "estimate_line" ADD CONSTRAINT "FK_request_item_TO_estimate_line_1" FOREIGN KEY (
	"request_item_id"
)
REFERENCES "request_item" (
	"request_item_id"
);

ALTER TABLE "estimate_line" ADD CONSTRAINT "FK_role_rate_TO_estimate_line_1" FOREIGN KEY (
	"role_rate_id"
)
REFERENCES "role_rate" (
	"role_rate_id"
);

ALTER TABLE "estimate_line" ADD CONSTRAINT "FK_job_role_TO_estimate_line_1" FOREIGN KEY (
	"job_role_id"
)
REFERENCES "job_role" (
	"job_role_id"
);

ALTER TABLE "project" ADD CONSTRAINT "FK_company_TO_project_1" FOREIGN KEY (
	"company_id"
)
REFERENCES "company" (
	"company_id"
);

ALTER TABLE "request_source" ADD CONSTRAINT "FK_change_request_TO_request_source_1" FOREIGN KEY (
	"change_request_id"
)
REFERENCES "change_request" (
	"change_request_id"
);

ALTER TABLE "pm_judgment" ADD CONSTRAINT "FK_request_item_TO_pm_judgment_1" FOREIGN KEY (
	"request_item_id"
)
REFERENCES "request_item" (
	"request_item_id"
);

ALTER TABLE "pm_judgment" ADD CONSTRAINT "FK_company_member_TO_pm_judgment_1" FOREIGN KEY (
	"company_member_id"
)
REFERENCES "company_member" (
	"company_member_id"
);

ALTER TABLE "baseline_document" ADD CONSTRAINT "FK_baseline_TO_baseline_document_1" FOREIGN KEY (
	"baseline_id"
)
REFERENCES "baseline" (
	"baseline_id"
);

ALTER TABLE "baseline_document" ADD CONSTRAINT "FK_document_version_TO_baseline_document_1" FOREIGN KEY (
	"document_version_id"
)
REFERENCES "document_version" (
	"document_version_id"
);

ALTER TABLE "delivery_record" ADD CONSTRAINT "FK_internal_approval_TO_delivery_record_1" FOREIGN KEY (
	"internal_approval_id"
)
REFERENCES "internal_approval" (
	"internal_approval_id"
);

ALTER TABLE "document_chunk" ADD CONSTRAINT "FK_document_version_TO_document_chunk_1" FOREIGN KEY (
	"document_version_id"
)
REFERENCES "document_version" (
	"document_version_id"
);

ALTER TABLE "change_request" ADD CONSTRAINT "FK_project_TO_change_request_1" FOREIGN KEY (
	"project_id"
)
REFERENCES "project" (
	"project_id"
);

ALTER TABLE "change_request" ADD CONSTRAINT "FK_company_member_TO_change_request_1" FOREIGN KEY (
	"company_member_id"
)
REFERENCES "company_member" (
	"company_member_id"
);

ALTER TABLE "change_request" ADD CONSTRAINT "FK_review_version_TO_change_request_1" FOREIGN KEY (
	"review_version_id"
)
REFERENCES "review_version" (
	"review_version_id"
);

ALTER TABLE "proposal_version" ADD CONSTRAINT "FK_review_version_TO_proposal_version_1" FOREIGN KEY (
	"review_version_id"
)
REFERENCES "review_version" (
	"review_version_id"
);

ALTER TABLE "billing_candidate" ADD CONSTRAINT "FK_project_TO_billing_candidate_1" FOREIGN KEY (
	"project_id"
)
REFERENCES "project" (
	"project_id"
);

ALTER TABLE "billing_candidate" ADD CONSTRAINT "FK_agreement_item_TO_billing_candidate_1" FOREIGN KEY (
	"agreement_item_id"
)
REFERENCES "agreement_item" (
	"agreement_item_id"
);

ALTER TABLE "company_member" ADD CONSTRAINT "FK_company_TO_company_member_1" FOREIGN KEY (
	"company_id"
)
REFERENCES "company" (
	"company_id"
);

ALTER TABLE "company_member" ADD CONSTRAINT "FK_app_user_TO_company_member_1" FOREIGN KEY (
	"user_id"
)
REFERENCES "app_user" (
	"user_id"
);

ALTER TABLE "question_answer" ADD CONSTRAINT "FK_review_version_TO_question_answer_1" FOREIGN KEY (
	"review_version_id"
)
REFERENCES "review_version" (
	"review_version_id"
);

ALTER TABLE "question_answer" ADD CONSTRAINT "FK_request_item_TO_question_answer_1" FOREIGN KEY (
	"request_item_id"
)
REFERENCES "request_item" (
	"request_item_id"
);

ALTER TABLE "invoice_draft" ADD CONSTRAINT "FK_project_TO_invoice_draft_1" FOREIGN KEY (
	"project_id"
)
REFERENCES "project" (
	"project_id"
);

ALTER TABLE "item_evidence" ADD CONSTRAINT "FK_request_item_TO_item_evidence_1" FOREIGN KEY (
	"request_item_id"
)
REFERENCES "request_item" (
	"request_item_id"
);

ALTER TABLE "item_evidence" ADD CONSTRAINT "FK_document_chunk_TO_item_evidence_1" FOREIGN KEY (
	"document_chunk_id"
)
REFERENCES "document_chunk" (
	"document_chunk_id"
);

ALTER TABLE "item_evidence" ADD CONSTRAINT "FK_ai_analysis_TO_item_evidence_1" FOREIGN KEY (
	"ai_analysis_id"
)
REFERENCES "ai_analysis" (
	"ai_analysis_id"
);

ALTER TABLE "rate_card_version" ADD CONSTRAINT "FK_project_TO_rate_card_version_1" FOREIGN KEY (
	"project_id"
)
REFERENCES "project" (
	"project_id"
);

ALTER TABLE "request_note" ADD CONSTRAINT "FK_change_request_TO_request_note_1" FOREIGN KEY (
	"change_request_id"
)
REFERENCES "change_request" (
	"change_request_id"
);

ALTER TABLE "request_note" ADD CONSTRAINT "FK_company_member_TO_request_note_1" FOREIGN KEY (
	"company_member_id"
)
REFERENCES "company_member" (
	"company_member_id"
);

ALTER TABLE "agreement_item" ADD CONSTRAINT "FK_change_agreement_TO_agreement_item_1" FOREIGN KEY (
	"change_agreement_id"
)
REFERENCES "change_agreement" (
	"change_agreement_id"
);

ALTER TABLE "agreement_item" ADD CONSTRAINT "FK_request_item_TO_agreement_item_1" FOREIGN KEY (
	"request_item_id"
)
REFERENCES "request_item" (
	"request_item_id"
);

