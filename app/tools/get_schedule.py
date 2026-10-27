from app.kb.loader import schedules
from app.tools.errors import ToolInputError
from app.tools.trace import traced


@traced("get_schedule")
def get_schedule(project_id: str, as_of: str | None = None) -> dict:
    """as_of 이전에 효력이 생긴 변경합의서만 종료일에 반영한다. baseline_as_of를 넘길 것."""
    p = schedules().get(project_id)
    if p is None:
        raise ToolInputError(f"unknown project_id {project_id}")
    end, src, applied = p["contract_end"], p["contract_end_ref"], []
    for co in p.get("change_orders", []):
        if as_of is None or co["effective_date"] <= as_of:
            end, src = co.get("contract_end", end), co["ref"]
            applied.append(co["co_id"])
    return {"project_id": project_id, "as_of": as_of, "contract_start": p["contract_start"],
            "contract_end": end, "contract_end_ref": src, "milestones": p["milestones"],
            "applied_change_orders": applied}
