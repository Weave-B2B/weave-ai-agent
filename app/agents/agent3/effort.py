"""Synthetic, narrowly scoped effort rules for local MVP testing."""
import json

from app.core.config import ROOT

PATH = ROOT / "data/kb/effort_rules.json"


def matches_rule(rule, inp, matched_targets):
    if rule.get("kind") == "general_test":
        return bool(matched_targets)
    matches = inp.context.case_id == rule.get("case_id") and (
        matched_targets == rule.get("targets") if "targets" in rule
        else bool(matched_targets) and all(text == rule.get("target_text") for text in matched_targets.values()))
    return matches and ("constraints" not in rule or [item.text for item in inp.constraints] == rule["constraints"])


def available_rule(rule, inp):
    if rule.get("kind") == "general_test":
        return True
    targets = {item.item_id: item.text for item in inp.targets()}
    matched = rule.get("targets")
    if matched is not None:
        return bool(matched) and all(targets.get(key) == value for key, value in matched.items()) and matches_rule(rule, inp, matched)
    return any(matches_rule(rule, inp, {key: text}) for key, text in targets.items())


def load_effort_rules() -> dict:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    for rule in data["rules"].values():
        rule["case_id"] = data["case_id"]
        rule["target_text"] = data["target_text"]
    examples = json.loads((PATH.parent / "effort_example_rules.json").read_text(encoding="utf-8"))
    if set(data["rules"]) & set(examples["rules"]):
        raise ValueError("duplicate synthetic effort rule IDs")
    data["rules"].update(examples["rules"])
    general = json.loads((PATH.parent / "effort_general_rules.json").read_text(encoding="utf-8"))
    if set(data["rules"]) & set(general["rules"]):
        raise ValueError("duplicate synthetic effort rule IDs")
    data["rules"].update(general["rules"])
    data["version"] += "+" + examples["version"] + "+" + general["version"]
    return data
