"""Explicit fixture authoring only: execute Backend's Java engine, never a Python formula.

WEAVE_RECORD_CALCULATIONS=1 + WEAVE_CALC_CLASSPATH_FILE + JAVA_HOME is required.
Ordinary pytest and the application only replay the resulting JSON file.
"""
import atexit
import copy
import json
import os
from pathlib import Path
import subprocess


def install(monkeypatch):
    from app.tools import fixture_calculation as fixtures
    from app.tools.errors import ToolInputError
    path = fixtures.FIXTURES
    data = json.loads(path.read_text()) if path.exists() else {"producer": "weave-backend PricingCalculator / BusinessDayCalculator", "responses": {}}
    java = str(Path(os.environ["JAVA_HOME"]) / "bin" / "java")
    cp = Path(os.environ["WEAVE_CALC_CLASSPATH_FILE"]).read_text().strip()
    proc = subprocess.Popen([java, "-cp", cp, "com.weave.estimate.calc.CalculationFixtureBridge"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8")

    def save():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        proc.terminate()
    atexit.register(save)

    def lookup(self, request, calendar):
        key = fixtures.fixture_key(request, calendar)
        if key not in data["responses"]:
            canonical = copy.deepcopy(request)
            canonical["lines"] = [{**x, "key": f"L{n+1}"} for n, x in enumerate(canonical["lines"])]
            proc.stdin.write(json.dumps({"request": canonical, "calendar": calendar}) + "\n")
            proc.stdin.flush()
            data["responses"][key] = json.loads(proc.stdout.readline())
        result = copy.deepcopy(data["responses"][key])
        if "error" in result:
            raise ToolInputError(result["error"])
        if result.get("pricing"):
            for part in (result["pricing"], result["schedule"]):
                for line, original in zip(part["lines"], request["lines"], strict=True): line["key"] = original["key"]
        return result

    monkeypatch.setattr(fixtures.FixtureCalcClient, "_lookup", lookup)
