"""Run the packaged CPU mechanism example in a fresh directory."""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("Output already exists; choose a new directory.")
    try:
        import numpy  # noqa: F401
    except ImportError:
        parser.error("Install requirements-example.txt before running this example.")
    shutil.copytree(ROOT / "assets" / "scaled-attention", output)

    def lab(*arguments):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "lab.py"), "--project", str(output), *arguments],
            capture_output=True, encoding="utf-8", errors="replace",
            env={**__import__("os").environ, "PYTHONUTF8": "1"},
        )
        if proc.returncode:
            raise RuntimeError(proc.stderr or proc.stdout)
        return json.loads(proc.stdout)

    def run(mode, label, parent=None, baseline=False):
        (output / "config.json").write_text(json.dumps({"mode": mode}) + "\n", encoding="utf-8")
        args = ["run", "--label", label, "--hypothesis", label]
        if parent:
            args += ["--parent", parent]
        if baseline:
            args += ["--baseline"]
        return lab(*args)["id"]

    lab("init")
    baseline = run("unscaled", "Unscaled baseline", baseline=True)
    control = run("dimension", "Over-scaled control: metric may improve but equation should fail", parent=baseline)
    candidate = run("sqrt_dimension", "Paper square-root scaling", parent=baseline)
    control_result = lab("compare", "--baseline", baseline, "--candidate", control)
    assert control_result["selection_status"] == "score_improved_conditions_failed", control_result
    frozen_base = lab("run", "--phase", "confirmation", "--source-run", baseline,
                      "--label", "Frozen baseline", "--hypothesis", "Baseline on held-out seeds")["id"]
    frozen_candidate = lab("run", "--phase", "confirmation", "--source-run", candidate,
                           "--label", "Frozen candidate", "--hypothesis", "Candidate on held-out seeds")["id"]
    confirmed = lab("compare", "--baseline", frozen_base, "--candidate", frozen_candidate)
    assert confirmed["selection_status"] == "score_improved_conditions_met", confirmed
    lab("verify")
    lab("report", "--run", frozen_candidate, "--baseline", frozen_base)
    print(json.dumps({"control": control_result["selection_status"],
                      "confirmation": confirmed["selection_status"],
                      "trials": 5, "seed_runs": 25,
                      "report": str(output / ".research" / "report" / "index.html")}, indent=2))

if __name__ == "__main__":
    main()
