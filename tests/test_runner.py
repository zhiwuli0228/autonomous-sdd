from __future__ import annotations

import argparse
import json
import importlib.util
import errno
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


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

    def test_status_and_recover_include_workspace_overview(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "workspace-overview",
            "Verify runtime reporting distinguishes source and work trees",
        )
        status = json.loads(run("--project", str(self.project), "status").stdout)
        self.assertEqual(str(self.project.resolve()), status["workspace"]["project_root"])
        self.assertEqual(
            str(self.project.resolve()),
            status["workspace"]["work_root"],
        )
        self.assertEqual(
            str(self.project.resolve()),
            status["workspace"]["work_project_root"],
        )
        self.assertIn("state", status)
        self.assertEqual("running", status["state"]["status"])
        recover_output = run("--project", str(self.project), "recover", expected=3).stdout
        result = json.loads(recover_output[recover_output.index("{") :])
        self.assertEqual("FAIL", result["status"])
        self.assertEqual(str(self.project.resolve()), result["workspace"]["project_root"])

    def test_resume_uses_active_run_locator(self) -> None:
        run_root = self.temp / "runs"
        work_root = run_root / "20260619T120012Z-1234abcd" / "work" / "project"
        work_root.mkdir(parents=True, exist_ok=True)
        (work_root / ".sdd" / "runtime").mkdir(parents=True, exist_ok=True)
        (self.project / ".sdd" / "runtime").mkdir(parents=True, exist_ok=True)
        (self.project / ".sdd" / "runtime" / "active-run.json").write_text(
            json.dumps(
                {
                    "run_id": "20260619T120012Z-1234abcd",
                    "run_root": str(run_root / "20260619T120012Z-1234abcd"),
                    "work_project_root": str(work_root),
                    "source_root": str(self.project),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (work_root / ".sdd" / "runtime" / "state.json").write_text(
            json.dumps({"status": "running", "stage": "brainstorm", "pending_action": "gate"}, indent=2)
            + "\n",
            encoding="utf-8",
        )
        with mock.patch.object(SDD, "gate_and_advance") as gate:
            SDD.resume(argparse.Namespace(project=str(self.project), dry_run=False))
        gate.assert_called_once_with(work_root)

    def test_run_loop_continues_from_pending_gate(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "pending-gate",
            "Continue an interrupted run from the persisted gate boundary",
        )
        state_path = self.project / ".sdd" / "runtime" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["pending_action"] = "gate"
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        with (
            mock.patch.object(SDD, "validate_execution_preflight"),
            mock.patch.object(SDD, "execute_stage") as execute_stage,
            mock.patch.object(SDD, "gate_and_advance") as gate_and_advance,
        ):
            with self.assertRaises(SDD.SddError):
                SDD.run_loop(argparse.Namespace(project=str(self.project), max_steps=1))
        execute_stage.assert_not_called()
        gate_and_advance.assert_called_once_with(self.project)

    def test_compete_reuses_active_run_instead_of_creating_new_workspace(self) -> None:
        run_root = self.temp / "runs"
        work_root = run_root / "20260619T120012Z-1234abcd" / "work" / "project"
        (work_root / ".sdd" / "runtime").mkdir(parents=True, exist_ok=True)
        (self.project / ".sdd" / "runtime").mkdir(parents=True, exist_ok=True)
        (self.project / ".sdd" / "runtime" / "active-run.json").write_text(
            json.dumps(
                {
                    "run_id": "20260619T120012Z-1234abcd",
                    "run_root": str(run_root / "20260619T120012Z-1234abcd"),
                    "work_project_root": str(work_root),
                    "source_root": str(self.project),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (work_root / ".sdd" / "runtime" / "state.json").write_text(
            json.dumps(
                {
                    "run_id": "active-run",
                    "change_id": "resume-me",
                    "objective": "Resume the active unattended workflow instead of starting over",
                    "status": "running",
                    "stage": "proposal",
                    "pending_action": "gate",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        work_config = json.loads((self.project / ".sdd" / "config.yaml").read_text(encoding="utf-8"))
        work_config["executor"] = "fixture"
        (work_root / ".sdd").mkdir(parents=True, exist_ok=True)
        (work_root / ".sdd" / "config.yaml").write_text(json.dumps(work_config, indent=2) + "\n", encoding="utf-8")
        with (
            mock.patch.object(SDD, "run_loop") as run_loop,
            mock.patch.object(SDD, "finalize_competition_run", return_value={"status": "closed"}) as finalize,
            mock.patch.object(SDD, "create_runtime_services") as create_services,
        ):
            SDD.compete(
                argparse.Namespace(
                    project=str(self.project),
                    task="Resume the active unattended workflow instead of starting over",
                    change_id="resume-me",
                    executor="fixture",
                    max_steps=30,
                )
            )
        run_loop.assert_called_once()
        self.assertEqual(str(work_root), run_loop.call_args.args[0].project)
        finalize.assert_called_once_with(work_root, self.project)
        create_services.assert_not_called()

    def test_finalize_blocks_when_source_workspace_drifted_during_isolated_run(self) -> None:
        run("--project", str(self.project), "baseline")
        services = SDD.create_runtime_services(self.project)
        with services.locks():
            snapshot = services.workspace.initialize(SDD.VERSION)
        root = services.workspace.work_project_root
        SDD.start(
            argparse.Namespace(
                project=str(root),
                change_id="drifted-finalize",
                objective="Protect source workspace changes that happen after isolated run start",
                source_root=str(self.project),
                work_root=str(snapshot.work_root),
                source_head=snapshot.source_head,
                baseline_commit=snapshot.baseline_commit,
                run_branch=snapshot.run_branch,
                source_status=snapshot.source_status.to_dict(),
            )
        )
        SDD.write_active_run_locator(
            self.project,
            {
                "run_id": snapshot.work_root.parent.name,
                "run_root": str(snapshot.work_root.parent),
                "work_project_root": str(root),
                "source_root": str(self.project),
            },
        )
        delivered = root / "src" / "main" / "java" / "sample" / "Delivered.java"
        delivered.parent.mkdir(parents=True, exist_ok=True)
        delivered.write_text("package sample;\n\npublic class Delivered {}\n", encoding="utf-8")
        external = self.project / "src" / "main" / "java" / "sample" / "ExternalEdit.java"
        external.parent.mkdir(parents=True, exist_ok=True)
        external.write_text("package sample;\n\npublic class ExternalEdit {}\n", encoding="utf-8")
        state = SDD.load_state(root)
        state["status"] = "closed"
        state["stage"] = "closed"
        state["last_verified_commit"] = SDD.git_head(root)
        SDD.save_state(root, state)

        final = SDD.finalize_competition_run(root, self.project)

        self.assertEqual("blocked", final["status"])
        self.assertIn("Source workspace changed since run started", final["blocking_reason"])
        self.assertTrue((self.project / ".sdd" / "runtime" / "active-run.json").exists())
        self.assertTrue(delivered.exists())
        self.assertFalse((self.project / "src" / "main" / "java" / "sample" / "Delivered.java").exists())
        self.assertFalse((self.project / ".sdd" / "delivery-report.md").exists())

    def test_finalize_materializes_and_commits_source_workspace(self) -> None:
        run("--project", str(self.project), "baseline")
        source_head_before = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.project,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        services = SDD.create_runtime_services(self.project)
        with services.locks():
            snapshot = services.workspace.initialize(SDD.VERSION)
        root = services.workspace.work_project_root
        SDD.start(
            argparse.Namespace(
                project=str(root),
                change_id="materialize-cleanly",
                objective="Materialize isolated run results back into the source repository",
                source_root=str(self.project),
                work_root=str(snapshot.work_root),
                source_head=snapshot.source_head,
                baseline_commit=snapshot.baseline_commit,
                run_branch=snapshot.run_branch,
                source_status=snapshot.source_status.to_dict(),
            )
        )
        SDD.write_active_run_locator(
            self.project,
            {
                "run_id": snapshot.work_root.parent.name,
                "run_root": str(snapshot.work_root.parent),
                "work_project_root": str(root),
                "source_root": str(self.project),
            },
        )
        delivered = root / "src" / "main" / "java" / "sample" / "Delivered.java"
        delivered.parent.mkdir(parents=True, exist_ok=True)
        delivered.write_text("package sample;\n\npublic class Delivered {}\n", encoding="utf-8")
        state = SDD.load_state(root)
        state["status"] = "closed"
        state["stage"] = "closed"
        state["last_verified_commit"] = SDD.git_head(root)
        SDD.save_state(root, state)

        final = SDD.finalize_competition_run(root, self.project)

        self.assertEqual("closed", final["status"])
        self.assertFalse((self.project / ".sdd" / "runtime" / "active-run.json").exists())
        self.assertTrue((self.project / "src" / "main" / "java" / "sample" / "Delivered.java").exists())
        self.assertEqual([], subprocess.run(
            ["git", "status", "--short"],
            cwd=self.project,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.splitlines())
        source_head_after = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.project,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        self.assertNotEqual(source_head_before, source_head_after)
        self.assertEqual(source_head_after, final["delivery_commit"])
        mirrored_state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(source_head_after, mirrored_state["delivery_commit"])

    def test_status_prefers_active_run_locator_over_local_state(self) -> None:
        run_root = self.temp / "runs"
        work_root = run_root / "20260619T120012Z-1234abcd" / "work" / "project"
        (work_root / ".sdd" / "runtime").mkdir(parents=True, exist_ok=True)
        (self.project / ".sdd" / "runtime").mkdir(parents=True, exist_ok=True)
        (self.project / ".sdd" / "runtime" / "state.json").write_text(
            json.dumps({"status": "closed", "stage": "closed", "source_root": str(self.project)}, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.project / ".sdd" / "runtime" / "active-run.json").write_text(
            json.dumps(
                {
                    "run_id": "20260619T120012Z-1234abcd",
                    "run_root": str(run_root / "20260619T120012Z-1234abcd"),
                    "work_project_root": str(work_root),
                    "source_root": str(self.project),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (work_root / ".sdd" / "runtime" / "state.json").write_text(
            json.dumps(
                {
                    "status": "running",
                    "stage": "proposal",
                    "source_root": str(self.project),
                    "work_root": str(run_root / "20260619T120012Z-1234abcd" / "work"),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        status = json.loads(run("--project", str(self.project), "status").stdout)
        self.assertEqual("running", status["state"]["status"])
        self.assertEqual(str(work_root.resolve()), status["workspace"]["work_project_root"])

    def test_gate_uses_active_run_locator_over_local_state(self) -> None:
        run_root = self.temp / "runs"
        work_root = run_root / "20260619T120012Z-1234abcd" / "work" / "project"
        (work_root / ".sdd" / "runtime").mkdir(parents=True, exist_ok=True)
        (self.project / ".sdd" / "runtime").mkdir(parents=True, exist_ok=True)
        (self.project / ".sdd" / "runtime" / "state.json").write_text(
            json.dumps({"status": "closed", "stage": "closed"}, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.project / ".sdd" / "runtime" / "active-run.json").write_text(
            json.dumps(
                {
                    "run_id": "20260619T120012Z-1234abcd",
                    "run_root": str(run_root / "20260619T120012Z-1234abcd"),
                    "work_project_root": str(work_root),
                    "source_root": str(self.project),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (work_root / ".sdd" / "runtime" / "state.json").write_text(
            json.dumps({"status": "running", "stage": "proposal", "pending_action": "gate"}, indent=2) + "\n",
            encoding="utf-8",
        )
        with mock.patch.object(SDD, "gate_and_advance") as gate:
            SDD.gate(argparse.Namespace(project=str(self.project)))
        gate.assert_called_once_with(work_root)

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
        result = run("--project", str(self.project), "recover", expected=3)
        self.assertIn("changed:.sdd/policy/project.yaml", result.stdout)
        payload = json.loads(result.stdout[result.stdout.index("{") :])
        self.assertEqual("manual_repair", payload["recommended_action"])
        self.assertEqual("manual_repair_required", payload["decision"])
        self.assertEqual(3, payload["exit_code"])
        self.assertEqual([], payload["recommended_command"])
        self.assertEqual("manual", payload["recovery_plan"][0]["kind"])
        self.assertEqual([], payload["recovery_plan"][0]["command"])

    def test_recover_reports_resume_ready_for_clean_running_state(self) -> None:
        (self.project / ".sdd" / "runtime").mkdir(parents=True, exist_ok=True)
        (self.project / ".sdd" / "baseline").mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": 1,
            "control_hashes": {},
            "protected_files": {},
            "dependency_files": {},
        }
        (self.project / ".sdd" / "baseline" / "manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        git_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.project,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        (self.project / ".sdd" / "runtime" / "state.json").write_text(
            json.dumps(
                {
                    "run_id": "resume-ready",
                    "stage": "proposal",
                    "status": "running",
                    "pending_action": "gate",
                    "next_action": "execute_stage",
                    "last_verified_commit": git_head,
                    "source_root": str(self.project),
                    "work_root": str(self.project),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        payload = json.loads(run("--project", str(self.project), "recover").stdout)
        self.assertEqual("PASS", payload["status"])
        self.assertEqual("resume_ready", payload["decision"])
        self.assertEqual("resume", payload["recommended_action"])
        self.assertTrue(payload["resume_supported"])
        self.assertEqual(0, payload["exit_code"])
        self.assertEqual("--project", payload["recommended_command"][2])
        self.assertEqual(str(self.project), payload["recommended_command"][3])
        self.assertEqual("resume", payload["recommended_command"][-1])
        self.assertEqual("execute", payload["recovery_plan"][0]["kind"])
        self.assertEqual(payload["recommended_command"], payload["recovery_plan"][0]["command"])

    def test_resume_process_advances_interrupted_run(self) -> None:
        config_path = self.project / ".sdd" / "config.yaml"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["executor"] = "fixture"
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        subprocess.run(["git", "add", "--all"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-m", "test: use fixture executor for resume process"], cwd=self.project, check=True)
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "resume-process",
            "Verify the resume process advances an interrupted unattended run",
        )
        subprocess.run(["git", "add", "--all"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-m", "test: checkpoint resumable run"], cwd=self.project, check=True)
        run("--project", str(self.project), "run-once")
        recovery = json.loads(run("--project", str(self.project), "recover").stdout)
        self.assertEqual("resume_ready", recovery["decision"])
        self.assertEqual("resume", recovery["recommended_action"])
        run("--project", str(self.project), "resume")
        state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual("specs", state["stage"])
        self.assertEqual("running", state["status"])

    def test_autorecover_process_advances_interrupted_run(self) -> None:
        config_path = self.project / ".sdd" / "config.yaml"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["executor"] = "fixture"
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        subprocess.run(["git", "add", "--all"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-m", "test: use fixture executor for autorecover process"], cwd=self.project, check=True)
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "autorecover-process",
            "Verify the autorecover process advances an interrupted unattended run",
        )
        subprocess.run(["git", "add", "--all"], cwd=self.project, check=True)
        subprocess.run(["git", "commit", "-m", "test: checkpoint autorecover run"], cwd=self.project, check=True)
        run("--project", str(self.project), "run-once")
        run("--project", str(self.project), "autorecover")
        state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual("specs", state["stage"])
        self.assertEqual("running", state["status"])

    def test_dead_owner_lock_with_dirty_workspace_can_recover_and_finish(self) -> None:
        config_path = self.project / ".sdd" / "config.yaml"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["executor"] = "fixture"
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        subprocess.run(["git", "add", "--all"], cwd=self.project, check=True)
        subprocess.run(
            ["git", "commit", "-m", "test: fixture executor for dirty recovery"],
            cwd=self.project,
            check=True,
        )
        run("--project", str(self.project), "baseline")
        services = SDD.create_runtime_services(self.project)
        with services.locks():
            snapshot = services.workspace.initialize(SDD.VERSION)
        root = services.workspace.work_project_root
        SDD.start(
            argparse.Namespace(
                project=str(root),
                change_id="hard-interrupt-recovery",
                objective="Verify an interrupted unattended run can recover and still finish",
                source_root=str(self.project),
                work_root=str(snapshot.work_root),
                source_head=snapshot.source_head,
                baseline_commit=snapshot.baseline_commit,
                run_branch=snapshot.run_branch,
                source_status=snapshot.source_status.to_dict(),
            )
        )
        SDD.write_active_run_locator(
            self.project,
            {
                "run_id": snapshot.work_root.parent.name,
                "run_root": str(snapshot.work_root.parent),
                "work_project_root": str(root),
                "source_root": str(self.project),
            },
        )
        lock_path = root / ".sdd" / "runtime" / "execution.lock"
        lock_path.write_text(json.dumps({"pid": 999999, "created_at": "2026-06-20T00:00:00Z"}), encoding="utf-8")
        recover = json.loads(run("--project", str(self.project), "recover").stdout)
        self.assertIn(recover["decision"], {"restore_ready", "resume_ready"})
        self.assertIn(recover["recommended_action"], {"restore_and_resume", "resume"})
        self.assertTrue(recover["resume_supported"])
        run("--project", str(self.project), "autorecover")
        result = run(
            "--project",
            str(self.project),
            "compete",
            "--task",
            "Verify an interrupted unattended run can recover and still finish",
            "--change-id",
            "hard-interrupt-recovery",
            "--executor",
            "fixture",
            "--max-steps",
            "30",
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual("closed", payload["status"])
        self.assertEqual(0, payload["exit_code"])
        self.assertFalse((self.project / ".sdd" / "runtime" / "active-run.json").exists())
        self.assertEqual([], subprocess.run(
            ["git", "status", "--short"],
            cwd=self.project,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.splitlines())

    def test_rehearse_recovery_command_closes_and_reports_summary(self) -> None:
        summary_path = self.project / ".sdd" / "runtime" / "rehearsal-summary.json"
        artifacts_dir = self.project / ".sdd" / "runtime" / "rehearsal-artifacts"
        result = run(
            "--project",
            str(self.project),
            "rehearse-recovery",
            "--task",
            "Verify the dedicated recovery rehearsal command can recover and finish",
            "--change-id",
            "rehearsal-recovery-command",
            "--max-steps",
            "30",
            "--json-out",
            str(summary_path),
            "--artifacts-dir",
            str(artifacts_dir),
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual("recovery_rehearsal_result", payload["kind"])
        self.assertEqual("closed", payload["final_status"])
        self.assertIn(payload["initial_recovery_decision"], {"restore_ready", "resume_ready"})
        self.assertEqual(str(summary_path.resolve()), payload["json_out"])
        self.assertEqual(str(artifacts_dir.resolve()), payload["artifacts_dir"])
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(payload, summary)
        self.assertTrue((artifacts_dir / "initial-recovery.json").exists())
        self.assertTrue((artifacts_dir / "final-state.json").exists())
        self.assertTrue((artifacts_dir / "rehearsal-result.json").exists())
        self.assertTrue((self.project / ".sdd" / "delivery-report.md").exists())
        self.assertEqual([], subprocess.run(
            ["git", "status", "--short"],
            cwd=self.project,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.splitlines())

    def test_recover_reports_no_action_for_closed_run(self) -> None:
        (self.project / ".sdd" / "runtime").mkdir(parents=True, exist_ok=True)
        (self.project / ".sdd" / "baseline").mkdir(parents=True, exist_ok=True)
        (self.project / ".sdd" / "baseline" / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "control_hashes": {},
                    "protected_files": {},
                    "dependency_files": {},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (self.project / ".sdd" / "runtime" / "state.json").write_text(
            json.dumps(
                {
                    "run_id": "closed-run",
                    "stage": "closed",
                    "status": "closed",
                    "next_action": "emit_final_report",
                    "source_root": str(self.project),
                    "work_root": str(self.project),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        payload = json.loads(run("--project", str(self.project), "recover").stdout)
        self.assertEqual("PASS", payload["status"])
        self.assertEqual("closed", payload["decision"])
        self.assertEqual("none", payload["recommended_action"])
        self.assertFalse(payload["resume_supported"])
        self.assertEqual(0, payload["exit_code"])
        self.assertEqual([], payload["recommended_command"])
        self.assertEqual("noop", payload["recovery_plan"][0]["kind"])
        self.assertEqual([], payload["recovery_plan"][0]["command"])

    def test_autorecover_executes_resume_when_recovery_is_ready(self) -> None:
        report = {
            "status": "PASS",
            "run_id": "resume-ready",
            "stage": "proposal",
            "git_head": "abc123",
            "workspace": {},
            "errors": [],
            "next_action": "execute_stage",
            "decision": "resume_ready",
            "recommended_action": "resume",
            "resume_supported": True,
            "reason": "Run can continue from pending action: gate",
            "recommended_command": ["python", "sdd.py", "--project", str(self.project), "resume"],
            "recovery_plan": [{"kind": "execute", "description": "resume", "command": ["resume"]}],
            "exit_code": 0,
        }
        (self.project / ".sdd" / "runtime").mkdir(parents=True, exist_ok=True)
        with (
            mock.patch.object(SDD, "recovery_report", return_value=report),
            mock.patch.object(SDD, "resolve_runtime_root", return_value=self.project),
            mock.patch.object(SDD, "resume") as resume,
        ):
            SDD.autorecover(argparse.Namespace(project=str(self.project), dry_run=True))
        resume.assert_called_once()
        self.assertTrue(resume.call_args.args[0].dry_run)

    def test_autorecover_fails_safely_for_manual_repair(self) -> None:
        report = {
            "status": "FAIL",
            "run_id": "needs-repair",
            "stage": "proposal",
            "git_head": "abc123",
            "workspace": {},
            "errors": ["tampered policy"],
            "next_action": "execute_stage",
            "decision": "manual_repair_required",
            "recommended_action": "manual_repair",
            "resume_supported": False,
            "reason": "tampered policy",
            "recommended_command": [],
            "recovery_plan": [{"kind": "manual", "description": "tampered policy", "command": []}],
            "exit_code": 3,
        }
        (self.project / ".sdd" / "runtime").mkdir(parents=True, exist_ok=True)
        with (
            mock.patch.object(SDD, "recovery_report", return_value=report),
            mock.patch.object(SDD, "resolve_runtime_root", return_value=self.project),
            mock.patch.object(SDD, "resume") as resume,
        ):
            with self.assertRaises(SDD.SddError):
                SDD.autorecover(argparse.Namespace(project=str(self.project), dry_run=False))
        resume.assert_not_called()

    def test_recover_process_uses_manual_repair_exit_code(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "policy-guard-exit",
            "Verify machine exit code for manual repair recovery state",
        )
        policy = self.project / ".sdd" / "policy" / "project.yaml"
        policy.write_text(policy.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(RUNNER), "--project", str(self.project), "recover"],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.assertEqual(3, result.returncode)

    def test_autorecover_process_uses_manual_repair_exit_code(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "policy-guard-autorecover-exit",
            "Verify machine exit code for autorecover manual repair state",
        )
        policy = self.project / ".sdd" / "policy" / "project.yaml"
        policy.write_text(policy.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(RUNNER), "--project", str(self.project), "autorecover"],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.assertEqual(3, result.returncode)

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
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual("competition_result", payload["kind"])
        self.assertEqual("closed", payload["status"])
        self.assertEqual("completed", payload["decision"])
        self.assertEqual("none", payload["recommended_action"])
        self.assertEqual(0, payload["exit_code"])
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.project,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        self.assertEqual(head, payload["delivery_commit"])
        self.assertEqual(str(self.project / ".sdd" / "delivery-report.md"), payload["report"])
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
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual("competition_result", payload["kind"])
        self.assertEqual("closed", payload["status"])
        self.assertEqual("completed", payload["decision"])
        self.assertEqual(0, payload["exit_code"])
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=plain,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        self.assertEqual(head, payload["delivery_commit"])
        self.assertEqual(str(plain / ".sdd" / "delivery-report.md"), payload["report"])
        self.assertTrue((plain / "sdd.cmd").exists())
        self.assertTrue((plain / ".sdd" / "delivery-report.md").exists())

    def test_compete_accepts_dirty_workspace_and_materializes_final_state(self) -> None:
        dirty = self.project / "src" / "main" / "java" / "sample" / "DirtyInput.java"
        dirty.parent.mkdir(parents=True, exist_ok=True)
        dirty.write_text("package sample;\n\npublic class DirtyInput {}\n", encoding="utf-8")
        result = run(
            "--project",
            str(self.project),
            "compete",
            "--task",
            "Deliver a complete unattended rehearsal from a dirty repository",
            "--change-id",
            "dirty-rehearsal",
            "--executor",
            "fixture",
            "--max-steps",
            "30",
        )
        self.assertIn("RESULT=CLOSED", result.stdout)
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual("competition_result", payload["kind"])
        self.assertEqual("closed", payload["status"])
        self.assertEqual("completed", payload["decision"])
        self.assertEqual(0, payload["exit_code"])
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.project,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        self.assertEqual(head, payload["delivery_commit"])
        self.assertEqual(str(self.project / ".sdd" / "delivery-report.md"), payload["report"])
        state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual("closed", state["status"])
        self.assertTrue(dirty.exists())
        self.assertTrue((self.project / ".sdd" / "delivery-report.md").exists())
        self.assertEqual([], subprocess.run(
            ["git", "status", "--short"],
            cwd=self.project,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.splitlines())

    def test_compete_replaces_existing_baseline_manifest_without_self_poisoning_controls(self) -> None:
        run("--project", str(self.project), "baseline")
        dirty = self.project / "src" / "main" / "java" / "sample" / "DirtyInput.java"
        dirty.parent.mkdir(parents=True, exist_ok=True)
        dirty.write_text("package sample;\n\npublic class DirtyInput {}\n", encoding="utf-8")
        result = run(
            "--project",
            str(self.project),
            "compete",
            "--task",
            "Deliver a complete unattended rehearsal from a project with an existing baseline manifest",
            "--change-id",
            "existing-baseline-rehearsal",
            "--executor",
            "fixture",
            "--max-steps",
            "30",
        )
        self.assertIn("RESULT=CLOSED", result.stdout)
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual("competition_result", payload["kind"])
        self.assertEqual("closed", payload["status"])
        self.assertEqual("completed", payload["decision"])
        self.assertEqual(0, payload["exit_code"])
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.project,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        self.assertEqual(head, payload["delivery_commit"])

    def test_compete_fixture_can_rerun_on_same_materialized_project(self) -> None:
        first = run(
            "--project",
            str(self.project),
            "compete",
            "--task",
            "Deliver a complete unattended rehearsal that can be repeated safely",
            "--change-id",
            "repeatable-rehearsal-one",
            "--executor",
            "fixture",
            "--max-steps",
            "30",
        )
        self.assertIn("RESULT=CLOSED", first.stdout)
        second = run(
            "--project",
            str(self.project),
            "compete",
            "--task",
            "Deliver a complete unattended rehearsal that can be repeated safely",
            "--change-id",
            "repeatable-rehearsal-two",
            "--executor",
            "fixture",
            "--max-steps",
            "30",
        )
        self.assertIn("RESULT=CLOSED", second.stdout)
        self.assertTrue((self.project / "src" / "autonomous_sdd_rehearsal_repeatable_rehearsal_one_1_1.txt").exists())
        self.assertTrue((self.project / "src" / "autonomous_sdd_rehearsal_repeatable_rehearsal_two_1_1.txt").exists())
        self.assertEqual([], subprocess.run(
            ["git", "status", "--short"],
            cwd=self.project,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.splitlines())

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
            expected=4,
        )
        self.assertIn("Injected deterministic failure at stage proposal", result.stdout)
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual("competition_result", payload["kind"])
        self.assertEqual("blocked", payload["status"])
        self.assertEqual("blocked", payload["decision"])
        self.assertEqual("manual_repair", payload["recommended_action"])
        self.assertEqual(4, payload["exit_code"])
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
        result = run("--project", str(self.project), "recover", expected=3)
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

    def test_recovery_rejects_dirty_running_checkpoint(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "dirty-checkpoint",
            "Reject unknown workspace changes before autonomous execution",
        )
        unknown = self.project / "src" / "unknown-change.txt"
        unknown.parent.mkdir(parents=True, exist_ok=True)
        unknown.write_text("unexpected\n", encoding="utf-8")
        result = run("--project", str(self.project), "recover", expected=3)
        self.assertIn("unexpected uncommitted changes", result.stdout)

    def test_live_pid_file_lock_rejects_second_runner(self) -> None:
        lock = self.project / ".sdd" / "runtime" / "execution.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps({"pid": os.getpid(), "created_at": "test"}), encoding="utf-8")
        with self.assertRaises(SDD.SddError):
            with SDD.pid_file_execution_lock(self.project):
                pass

    def test_windows_process_check_does_not_call_os_kill(self) -> None:
        with (
            mock.patch.object(SDD.os, "name", "nt"),
            mock.patch.object(SDD, "windows_process_is_alive", return_value=True) as win32_query,
            mock.patch.object(SDD.os, "kill") as os_kill,
        ):
            self.assertTrue(SDD.process_is_alive(1234))
        win32_query.assert_called_once_with(1234)
        os_kill.assert_not_called()

    def test_posix_process_check_only_treats_esrch_as_absent(self) -> None:
        with (
            mock.patch.object(SDD.os, "name", "posix"),
            mock.patch.object(SDD.os, "kill", side_effect=ProcessLookupError(errno.ESRCH, "missing")),
        ):
            self.assertFalse(SDD.process_is_alive(1234))
        with (
            mock.patch.object(SDD.os, "name", "posix"),
            mock.patch.object(SDD.os, "kill", side_effect=PermissionError(errno.EPERM, "denied")),
        ):
            self.assertTrue(SDD.process_is_alive(1234))
        with (
            mock.patch.object(SDD.os, "name", "posix"),
            mock.patch.object(SDD.os, "kill", side_effect=OSError(errno.EIO, "unknown")),
        ):
            self.assertTrue(SDD.process_is_alive(1234))

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux flock behavior")
    def test_linux_execution_lock_rejects_second_runner(self) -> None:
        with SDD.linux_execution_lock(self.project):
            with self.assertRaises(SDD.SddError):
                with SDD.linux_execution_lock(self.project):
                    pass

    def test_linux_platform_dispatches_to_flock(self) -> None:
        lock_context = mock.MagicMock()
        with (
            mock.patch.object(SDD.sys, "platform", "linux"),
            mock.patch.object(SDD, "linux_execution_lock", return_value=lock_context) as linux_lock,
        ):
            with SDD.execution_lock(self.project):
                pass
        linux_lock.assert_called_once_with(self.project)
        lock_context.__enter__.assert_called_once_with()
        lock_context.__exit__.assert_called_once()

    def test_stale_pid_file_lock_is_reclaimed(self) -> None:
        lock = self.project / ".sdd" / "runtime" / "execution.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps({"pid": 99999999, "created_at": "test"}), encoding="utf-8")
        with SDD.pid_file_execution_lock(self.project):
            self.assertTrue(lock.exists())
        self.assertFalse(lock.exists())


if __name__ == "__main__":
    unittest.main()
