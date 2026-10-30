"""Synthetic, narrowly scoped effort rules for local MVP testing."""
import json

from app.core.config import ROOT

PATH = ROOT / "data/kb/effort_rules.json"


def load_effort_rules() -> dict:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    for rule in data["rules"].values():
        rule["case_id"] = data["case_id"]
        rule["target_text"] = data["target_text"]
    examples = json.loads((PATH.parent / "effort_example_rules.json").read_text(encoding="utf-8"))
    if set(data["rules"]) & set(examples["rules"]):
        raise ValueError("duplicate synthetic effort rule IDs")
    data["rules"].update(examples["rules"])
    data["version"] += "+" + examples["version"]
    return data
