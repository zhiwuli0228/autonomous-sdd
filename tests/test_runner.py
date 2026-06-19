from __future__ import annotations

import json
import importlib.util
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "sdd.py"
SPEC = importlib.util.spec_from_file_location("autonomous_sdd_runner", RUNNER)
assert SPEC and SPEC.loader
SDD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SDD)


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

    def test_one_command_fixture_closes_full_lifecycle(self) -> None:
        result = run(
            "--project",
            str(self.project),
            "compete",
            "--task",
            "Implement a deterministic bounded behavior without protected API changes",
            "--change-id",
            "complete-rehearsal",
            "--executor",
            "fixture",
            "--max-steps",
            "30",
        )
        self.assertIn("RESULT=CLOSED", result.stdout)
        state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual("closed", state["status"])
        self.assertEqual({}, state["retries"])
        self.assertTrue((self.project / ".sdd" / "delivery-report.md").exists())
        archives = list((self.project / "openspec" / "changes" / "archive").glob("*-complete-rehearsal"))
        self.assertEqual(1, len(archives))
        self.assertTrue((self.project / "openspec" / "specs" / "competition-sample" / "spec.md").exists())

    def test_compete_bootstraps_plain_repository(self) -> None:
        plain = self.temp / "plain"
        plain.mkdir()
        subprocess.run(["git", "init"], cwd=plain, check=True, stdout=subprocess.PIPE)
        subprocess.run(["git", "config", "user.name", "SDD Test"], cwd=plain, check=True)
        subprocess.run(["git", "config", "user.email", "sdd@example.invalid"], cwd=plain, check=True)
        (plain / "src").mkdir()
        (plain / "src" / "existing.txt").write_text("existing project\n", encoding="utf-8")
        subprocess.run(["git", "add", "--all"], cwd=plain, check=True)
        subprocess.run(["git", "commit", "-m", "initial project"], cwd=plain, check=True, stdout=subprocess.PIPE)
        result = run(
            "--project",
            str(plain),
            "compete",
            "--task",
            "Deliver a complete unattended rehearsal from a plain repository",
            "--change-id",
            "plain-repository-rehearsal",
            "--executor",
            "fixture",
        )
        self.assertIn("RESULT=CLOSED", result.stdout)
        self.assertTrue((plain / "sdd.cmd").exists())
        self.assertTrue((plain / ".sdd" / "delivery-report.md").exists())

    def test_one_command_failure_exhausts_budget_and_reports_blocked(self) -> None:
        config_path = self.project / ".sdd" / "config.yaml"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["fixture_fail_stage"] = "proposal"
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        subprocess.run(["git", "add", "--all"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-m", "test: inject proposal failure"], cwd=self.project, check=True)
        result = run(
            "--project",
            str(self.project),
            "compete",
            "--task",
            "Exercise safe unattended failure handling and final blocked reporting",
            "--change-id",
            "blocked-rehearsal",
            "--executor",
            "fixture",
            "--max-steps",
            "20",
            expected=2,
        )
        self.assertIn("Injected deterministic failure at stage proposal", result.stdout)
        state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual("blocked", state["status"])
        report = (self.project / ".sdd" / "delivery-report.md").read_text(encoding="utf-8")
        self.assertIn("Outcome: `BLOCKED`", report)

    def test_default_api_paths_are_frozen_by_baseline(self) -> None:
        api = self.project / "src" / "main" / "java" / "sample" / "api" / "StableApi.java"
        api.parent.mkdir(parents=True)
        api.write_text("public interface StableApi {}\n", encoding="utf-8")
        subprocess.run(["git", "add", "--all"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-m", "feat: add stable api"], cwd=self.project, check=True)
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "api-guard",
            "Verify protected competition API files cannot be modified",
        )
        api.write_text("public interface StableApi { void changed(); }\n", encoding="utf-8")
        result = run("--project", str(self.project), "recover", expected=2)
        self.assertIn("changed:src/main/java/sample/api/StableApi.java", result.stdout)

    def test_task_gate_rejects_nested_checkbox_explosion(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "task-shape",
            "Verify bounded task decomposition before unattended implementation",
        )
        state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        state["stage"] = "tasks"
        (self.project / ".sdd" / "runtime" / "state.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
        tasks = self.project / "openspec" / "changes" / "task-shape" / "tasks.md"
        tasks.write_text(
            "# Tasks\n\n- [ ] 1.1 Coherent task\n- [ ] nested micro task\n",
            encoding="utf-8",
        )
        errors = SDD.validate_stage_artifact(self.project, state)
        self.assertTrue(any("3-20" in error for error in errors))
        self.assertTrue(any("unnumbered" in error for error in errors))

    def test_runner_marks_exact_apply_task_after_gate(self) -> None:
        run(
            "--project",
            str(self.project),
            "compete",
            "--task",
            "Exercise runner-owned exact apply task completion",
            "--change-id",
            "task-ownership",
            "--executor",
            "fixture",
            "--max-steps",
            "30",
        )
        archive = next((self.project / "openspec" / "changes" / "archive").glob("*-task-ownership"))
        tasks = (archive / "tasks.md").read_text(encoding="utf-8")
        self.assertNotIn("- [ ]", tasks)
        self.assertIn("- [x] 1.1", tasks)
        self.assertIn("- [x] 1.2", tasks)

    def test_timeout_terminates_descendant_process(self) -> None:
        marker = self.temp / "descendant-survived.txt"
        child = (
            "import time; from pathlib import Path; "
            f"time.sleep(1.5); Path({str(marker)!r}).write_text('survived')"
        )
        parent = (
            "import subprocess,sys,time; "
            f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(30)"
        )
        with self.assertRaises(subprocess.TimeoutExpired):
            SDD.run_command([sys.executable, "-c", parent], self.project, timeout=1)
        time.sleep(2)
        self.assertFalse(marker.exists(), "descendant process survived command timeout")

    def test_migrate_legacy_nested_tasks_to_bounded_tasks(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "legacy-tasks",
            "Recover a legacy task file without losing implementation details",
        )
        state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        state["stage"] = "apply"
        (self.project / ".sdd" / "runtime" / "state.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
        tasks = self.project / "openspec" / "changes" / "legacy-tasks" / "tasks.md"
        tasks.write_text(
            "# Tasks\n\n"
            "## 1. First capability\n\n### 1.1 Detail\n- [ ] implement first\n- [ ] test first\n\n"
            "## 2. Second capability\n\n- [ ] implement second\n\n"
            "## 3. Verification\n\n- [ ] verify all\n",
            encoding="utf-8",
        )
        run("--project", str(self.project), "migrate-tasks")
        migrated = tasks.read_text(encoding="utf-8")
        self.assertEqual(3, migrated.count("- [ ] "))
        self.assertIn("- [ ] 1.1 First capability", migrated)
        self.assertIn("  - implement first", migrated)
        self.assertNotIn("- [ ] implement first", migrated)

    def test_strict_result_contract_rejects_summary_style_test_object(self) -> None:
        state = {"stage": "apply"}
        result = {
            "status": "completed",
            "summary": "implemented",
            "files_read": [],
            "files_changed": [],
            "commands_run": ["mvn test"],
            "tests": {"passed": 16},
            "deviations": [],
            "blocking_reason": None,
            "task_id": "1.1",
            "requirement_evidence": [],
            "residual_risks": [],
        }
        errors = SDD.validate_agent_result(self.project, state, result)
        self.assertTrue(any("commands_run" in error for error in errors))
        self.assertTrue(any("tests" in error for error in errors))
        self.assertTrue(any("requirement evidence" in error for error in errors))

    def test_maven_focused_test_is_derived_from_changed_test_files(self) -> None:
        project_policy = self.project / ".sdd" / "policy" / "project.yaml"
        policy = json.loads(project_policy.read_text(encoding="utf-8"))
        policy["detected_project_kind"] = "java-maven"
        project_policy.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
        (self.project / "mvnw.cmd").write_text("@echo off\r\n", encoding="utf-8")
        commands, errors = SDD.focused_test_commands(
            self.project,
            [
                "src/main/java/sample/Feature.java",
                "src/test/java/sample/FeatureTest.java",
                "src/test/java/sample/FeatureEdgeTest.java",
            ],
        )
        self.assertEqual([], errors)
        self.assertEqual(
            [[".\\mvnw.cmd", "-Dtest=FeatureEdgeTest,FeatureTest", "test"]],
            commands,
        )

    def test_apply_requires_changed_test_file_for_independent_proof(self) -> None:
        project_policy = self.project / ".sdd" / "policy" / "project.yaml"
        policy = json.loads(project_policy.read_text(encoding="utf-8"))
        policy["detected_project_kind"] = "java-maven"
        project_policy.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
        commands, errors = SDD.focused_test_commands(
            self.project,
            ["src/main/java/sample/Feature.java"],
        )
        self.assertEqual([], commands)
        self.assertTrue(any("changed no test file" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
