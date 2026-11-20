"""Calculation identity shared by tool sessions, persisted Runs and final review."""
from decimal import Decimal
import hashlib
import json

from app.tools.errors import ToolInputError


def fingerprint(value):
    def normalize(v):
        if isinstance(v, dict): return {k: normalize(x) for k, x in v.items()}
        if isinstance(v, list): return [normalize(x) for x in v]
        if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
            n = Decimal(str(v))
            return '0' if n == 0 else format(n.normalize(), 'f')
        return v
    return hashlib.sha256(json.dumps(normalize(value), ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def policy_hash(rate_card_id):
    from app.tools.backend_calc import calculation_client
    return calculation_client(rate_card_id).policy_hash()


def validate_pricing(pricing, rate_card_id, *, require_confirmed=False, current=True):
    if pricing is None:
        raise ToolInputError('계산식 v1 금액이 없습니다. 계산 상태를 확인한 뒤 재계산해주세요.')
    p = pricing.model_dump() if hasattr(pricing, 'model_dump') else pricing
    inputs = p.get('inputs') or {}
    if not inputs.get('request') or p.get('input_hash') != fingerprint(inputs):
        raise ToolInputError('계산 입력 스냅샷이 없거나 변경되었습니다. 재계산해주세요.')
    if p.get('snapshot_id') != p['input_hash'] or p.get('formula_version') != 'calc-v1':
        raise ToolInputError('계산 스냅샷 버전이 일치하지 않습니다.')
    request = inputs['request']
    if (request.get('rate_card') or {}).get('id') != rate_card_id:
        raise ToolInputError('계산 단가표와 산정 단가표가 다릅니다.')
    if p.get('policy_hash') != fingerprint(request.get('policy') or {}):
        raise ToolInputError('계산 정책 스냅샷이 일치하지 않습니다.')
    from app.kb.backend_rates import is_backend_rate_id
    from app.tools.backend_calc import calculation_client
    calculation_client(rate_card_id).validate({"pricing": p, "rate_card_id": rate_card_id,
        "project_id": inputs.get("project_id"), "current": current,
        "require_confirmed": require_confirmed or is_backend_rate_id(rate_card_id)})


def validate_plan(plan_detail, rate_card_id):
    """저장된 일정 계산 결과가 입력 스냅샷으로 재현되는지 Backend에 확인한다. 계획이 없으면 검사하지 않는다."""
    plan = (plan_detail or {}).get("plan") if isinstance(plan_detail, dict) else None
    if not plan:
        return
    snapshot = plan.get("snapshot") or {}
    if not snapshot.get("inputs") or snapshot.get("input_hash") != snapshot.get("snapshot_id"):
        raise ToolInputError('일정 계산 스냅샷이 없거나 변경되었습니다. 재산정해주세요.')
    from app.tools.backend_calc import calculation_client
    client = calculation_client(rate_card_id)
    if not hasattr(client, "validate_schedule"):
        raise ToolInputError('일정 계산 결과는 Backend 공식 계산기에서만 검증할 수 있습니다.')
    client.validate_schedule(plan)


def validate_output_pricing(output, *, require_confirmed=False, current=True):
    if current:
        from app.tools.impact_workbook import assert_workbook_current
        assert_workbook_current(output)
    if not output.estimates:
        return
    rate = output.source.rate_card_id if getattr(output, 'source', None) else output.estimates[0].rate_card_id
    validate_pricing(output.pricing, rate, require_confirmed=require_confirmed, current=current)
    p = output.pricing.model_dump()
    expected = sorted((x['role'], Decimal(str(x['md']))) for x in p['inputs']['request']['lines'])
    actual = sorted((x.role, Decimal(str(x.md))) for e in output.estimates for x in e.lines)
    if expected != actual or p['inputs'].get('project_id') != output.context.project_id:
        raise ToolInputError('산정 항목과 계산 입력이 다릅니다. 재계산해주세요.')
    validate_plan(getattr(output, 'plan_detail', None), rate)
