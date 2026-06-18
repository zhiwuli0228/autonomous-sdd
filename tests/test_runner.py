from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "sdd.py"


def run(*args: str, cwd: Path | None = None, expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, str(RUNNER), *args],
        cwd=cwd or ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != expected:
        raise AssertionError(f"exit={result.returncode}\n{result.stdout}")
    return result


class RunnerSmokeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="autonomous-sdd-test-"))
        self.project = self.temp / "project"
        run("init", str(self.project))
        subprocess.run(["git", "config", "user.name", "SDD Test"], cwd=self.project, check=True)
        subprocess.run(["git", "config", "user.email", "sdd@example.invalid"], cwd=self.project, check=True)
        subprocess.run(["git", "add", "--all"], cwd=self.project, check=True)
        subprocess.run(
            ["git", "commit", "-m", "chore: initialize test project"],
            cwd=self.project,
            check=True,
            stdout=subprocess.PIPE,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def test_init_baseline_start_and_packet(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "sample-change",
            "Implement a bounded sample behavior for runner verification",
        )
        output = run("--project", str(self.project), "run-once", "--dry-run").stdout
        packet = json.loads(output)
        self.assertEqual("brainstorm", packet["stage"])
        self.assertEqual("sample-change", packet["change_id"])
        state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual("opencode-default", state["model_selection"])

    def test_policy_tampering_breaks_recovery(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "policy-guard",
            "Verify immutable policy detection in the autonomous runner",
        )
        policy = self.project / ".sdd" / "policy" / "project.yaml"
        policy.write_text(policy.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        result = run("--project", str(self.project), "recover", expected=2)
        self.assertIn("changed:.sdd/policy/project.yaml", result.stdout)


if __name__ == "__main__":
    unittest.main()
