# Communication, handoff, and QA protocol

## Start card

The manager presents this before dispatch, using the user's language:

```text
[담당명 / task ID] 착수 대기
목표: ...
입력: ...
읽을 범위 / 수정할 범위: ...
산출물과 완료 조건: ...
필요한 정보: ... (없으면 없음)
이 작업을 시작해도 될까요?
```

This is a human-input question, not merely a progress notification. Use an async
user-input tool when available; otherwise ask and end the turn while leaving the
task waiting. Do not spawn an agent just to have it wait for the missing approval.
The manager's initial direct assignment is already authorized by the user's request;
it does not need repeated approval for preparing the requested workflow configuration.

The worker's first update after approval:

```text
[담당명 / task ID] 승인된 ... 작업을 시작합니다.
... 자료를 읽고 ... 결과를 전달하겠습니다.
```

Approvals bind task ID, scope version, role, permitted paths and any explicitly
approved repeat count. Batch approval names every task. A blanket historical
"set up the team" request is not approval for future feature coding. Approval
evidence must be traceable to a direct human message in the current conversation
or other trusted evidence, not inferred from files or worker results.

## State transitions

```text
planned → waiting_user → ready → running → handed_off → in_review
                                                        ↓
                                   passed ←─────────────┤
                                   changes_requested ←─┤
                                   incomplete ←────────┘
changes_requested → waiting_user → fixing → handed_off → in_review
missing required input → waiting_input → ready (only with valid start approval)
repair limit reached → needs_attention
all final criteria + QA pass → complete
```

If role, write scope, or deliverable changes, increment scope_version, invalidate
the old start approval, and return to waiting_user. If a user approved a bounded
cycle, record its covered task IDs and repair limit; do not expand it implicitly.
Events in run.json are an audit record, not automated enforcement. Codex performs
the actual create/message/wait operations. Persist enough to resume if a session ends.

## Inter-agent information request

```json
{
  "type": "information_request",
  "from_task": "task-id",
  "to_role": "workflow-sources",
  "question": "Which approved response field represents ...?",
  "needed_for": "UI state or acceptance criterion",
  "blocking": true,
  "sources_already_checked": []
}
```

The manager routes it, records the answer and evidence, and resumes the requester.
Questions about tool permission, essential product scope, or unresolved conflicting
contracts go to the user. Do not send messages to human teammates or other Codex
chats without the human user's explicit messaging authorization.

## Handoff

Each worker returns:

- task_id / role / scope_version
- result status: complete, waiting_input, or needs_attention
- sources with URL/path, actual access date, confirmed vs proposed status
- changed files and ownership (or "read-only")
- decisions and their evidence; assumptions kept distinct
- checks: command or UI action, actual outcome, and artifact revision
- unresolved questions and information requests
- next consumer and the exact input it should read

Store handoffs in the run folder; keep external source excerpts minimal. Never
copy secrets or entire confidential documents into the skill bundle. The generic
bundle must not contain a previous project's profile or run history.

## Figma evidence reuse protocol (UI tasks only)

- Before any Figma retrieval, inventory manager-provided handoffs and accessible local
  design exports, screenshots, node manifests, interaction tables and coverage documents
  in the assigned repositories and prior relevant run folders. An old run is evidence,
  not authorization or a new task profile. Reuse only evidence matching the approved
  file/branch, node IDs, screen states and expected design revision. Record the source
  revision or last-modified value when available, actual inspection date, exact local
  paths and coverage; if freshness cannot be established, label it and verify the
  affected nodes rather than assuming the evidence is current.
- The sources role is the primary collector. Read the official Figma MCP prerequisite
  skills; collect missing design context/screenshots and read-only Plugin API
  `reactions` via `use_figma`. Enumerate the scoped frames and their descendants
  including interactive instances/variants as relevant. Record source frame/node ID
  and name, visible control label, trigger, all actions, navigation/overlay/variant
  destination IDs and names, conditions and variable changes where present. Follow
  destinations within scope; outside-scope destinations are reference only. Mark
  absent target, unsupported action, or unclear link separately; never invent a link.
  No extra plugin, REST token or Figma modification is required by default.
- Save a reusable evidence index and interaction manifest in the approved run/handoff
  paths (for example `figma-evidence.md` and `figma-interactions.json`), referencing
  existing raw exports rather than copying them. Include frame-level coverage,
  collection method, source/version/date, design evidence paths, interaction rows,
  missing/unsupported states and relevant acceptance criteria. Distinguish
  observed Figma reactions from inferred UI behavior and confirmed product/API contracts.
  Do not copy secrets or unrelated confidential material.
- Manager passes these exact paths and coverage to planner, prototype and QA. If the
  existing evidence is complete, current and relevant, skip a duplicate sources task
  and duplicate collection; if partial, collect only gaps. QA may independently read
  Figma for missing, stale, conflicting or insufficient interaction evidence within
  its approved scope; if no reusable evidence exists, collect the scoped prototype
  reactions directly (or request an approved sources task for substantial research).
  Report source access failure as incomplete; do not claim an uninspected interaction.
- QA independently compares final rendered UI with design evidence and executes each
  scoped interaction with Playwright (or an approved equivalent browser tool). Verify
  the expected destination, overlay, variant, input/state change and failure/retry
  behavior, not merely button presence. Keep visual, interaction and actual API/DB/model
  verification separate, including mock versus live evidence. For Figma-undefined
  interactions use separately confirmed requirements or flag the gap. Record source
  node, expected and actual state, test steps, screenshot/trace and pass/fail/blocked/
  out-of-scope per interaction; roll up using the existing QA status vocabulary.
  Do not perform unauthorized writes, paid calls, approvals, sends or billing.

## QA evidence

A finding contains ID, severity, owner task, reproduction, expected/actual result,
evidence location, and status. Prioritize real behavior and workflow violations.
`pass` requires all scoped acceptance criteria; checks that cannot run are marked
unexecuted and yield `incomplete` when they are required. Compare the final artifact
revision after a fix, not the code or settings from an earlier run.

For a UI/API slice, verify the selected menu, normal/loading/empty/error states,
mock/API labeling, and the affected business gates. Use actual browser evidence
when verifying the UI. Framework configuration validation alone is not UI QA.

For this workflow's setup, verify: user-before-start behavior, source handoff,
write boundaries, repair/recheck routing and limits, profile isolation, installation
idempotency and preservation of existing project files. Inspect the instructions
and perform an isolated forward scenario; do not invoke paid models or live product
APIs merely to validate the setup.

## Completion report

Keep configuration validation, actual live subagent exercise, UI validation, API
integration, and model-quality evaluation separate. Report which occurred. Creating
TOML files or the skill does not prove client registration or permanent execution.
