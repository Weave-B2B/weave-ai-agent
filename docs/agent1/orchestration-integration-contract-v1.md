# Agent 1 Orchestration Integration Contract v1

## Intent and item rules

- Item split is the unit of identity: each item has one `intent`; do not use an `intents` list.
- If an item combines confirmation and change work, split it into separate items.
- Preserve the ID of unchanged items when re-splitting. Assign IDs only to new or genuinely split items.
- `change_request`, `confirmation`, `defect`, and `issue_report` are scope-assessment candidates.
- `status_check` and empty item lists do not call Agent 2.
- If a feature or document is named but the intent is ambiguous, classify it as `confirmation` rather than `status_check`.

## Estimate intent distinction

- `estimate_only` means: estimate an already specified item or existing scope, without requesting a change. It must not be auto-converted to `estimate_request`.
- `estimate_request` means: a separate, explicit request for an estimate. It links to one or more target item IDs and is not estimated as duplicate work.
- If meaning is unresolved, preserve an `estimate_request` inquiry or return `NEEDS_INPUT`; do not infer one intent from the other.

## Conditional follow-up

The shared `Item` and internal draft support an optional `conditional_followup` string. It preserves only explicit source wording such as:

```text
포함인지 확인하고, 안 되면 추가해주세요.
```

Agent 2 may assess the confirmation item. When the verdict is verified `out_of_scope` and the item has `conditional_followup`, the host may temporarily convert the confirmation into a change target and estimate it. Final approval is still PM review, and the original item remains in history.

## Confirmation assembly

Confirmation normalization must operate on item evidence (`text` and `quotes`). It must not convert every function item merely because the email contains a confirmation marker. Mixed confirmation/change emails retain separate items and their normal intents.

## Re-call behavior

- `resplit`: reprocess only the requested target item IDs; unchanged item IDs remain unchanged.
- `analyze_answer`: update only affected items or answer-linked questions. Do not clear unrelated open questions.
- Answer analysis does not mark a question resolved only because an answer event arrived. Resolution requires interpreted content and a rerun result.

## Needs and provisional flow

`NEEDS_INPUT` from Agent 1 is not a reason to stop the orchestration when scope-assessment items remain. It represents unresolved input in the common question list and must not prevent Agent 2 from processing provisional items. A blocking PM/client question remains visible; it is not an automatic approval record or resolved state.
