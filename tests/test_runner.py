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

    def test_initialized_project_contains_self_hosted_agent_runtime(self) -> None:
        self.assertTrue((self.project / ".opencode" / "agents" / "autonomous-sdd.md").is_file())
        self.assertTrue((self.project / ".opencode" / "agents" / "sdd-stage.md").is_file())
        self.assertFalse((self.project / ".opencode" / "skills" / "autonomous-sdd" / "SKILL.md").exists())
        self.assertFalse((self.project / ".sdd" / "skills" / "cpp-unitool-header" / "SKILL.md").exists())
        copied_runner = self.project / ".sdd" / "bin" / "sdd.py"
        result = subprocess.run(
            [sys.executable, str(copied_runner), "--version"],
            cwd=self.project,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.assertEqual(0, result.returncode, result.stdout)
        self.assertIn(SDD.VERSION, result.stdout)

    def test_force_init_removes_legacy_bundled_skills(self) -> None:
        legacy_host = self.project / ".opencode" / "skills" / "autonomous-sdd"
        legacy_domain = self.project / ".sdd" / "skills" / "cpp-unitool-header"
        legacy_host.mkdir(parents=True, exist_ok=True)
        legacy_domain.mkdir(parents=True, exist_ok=True)
        (legacy_host / "SKILL.md").write_text("legacy host skill\n", encoding="utf-8")
        (legacy_domain / "SKILL.md").write_text("legacy domain skill\n", encoding="utf-8")
        SDD.init_project(argparse.Namespace(project=str(self.project), force=True))
        self.assertFalse(legacy_host.exists())
        self.assertFalse(legacy_domain.exists())

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
        self.assertEqual(2, packet["packet_contract_version"])
        self.assertEqual("brainstorm", packet["stage"])
        self.assertEqual("sample-change", packet["change_id"])
        self.assertIn("context_summary", packet)
        self.assertIn("required_artifacts", packet)
        self.assertIn("metadata", packet)
        self.assertEqual("generic-hosted", packet["metadata"]["scenario_profile"])
        self.assertEqual("generic-hosted", packet["scenario_profile"])
        self.assertIn("scenario_constraints", packet)
        self.assertIn("required_outcomes", packet)
        self.assertIn("scenario_tooling_constraints", packet)
        self.assertIn("frozen_goal", packet)
        self.assertIn("competition_constraints", packet)
        self.assertIn("required_acceptance_invariants", packet)
        self.assertIn("tooling_integration_constraints", packet)
        state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual("opencode-default", state["model_selection"])
        self.assertEqual("inline", state["objective_source"])
        self.assertEqual("inline", state["scenario_objective_source"])
        self.assertEqual("generic-hosted", state["scenario_profile"])
        self.assertEqual(state["scenario_constraints"], state["competition_constraints"])
        self.assertEqual(state["required_outcomes"], state["required_acceptance_invariants"])
        objective_bundle = json.loads(
            (self.project / ".sdd" / "runtime" / "scenario-objective.json").read_text(encoding="utf-8")
        )
        self.assertEqual("inline", objective_bundle["source"])
        legacy_objective_bundle = json.loads(
            (self.project / ".sdd" / "runtime" / "competition-objective.json").read_text(encoding="utf-8")
        )
        self.assertEqual(objective_bundle, legacy_objective_bundle)

    def test_resolve_competition_objective_defaults_to_frozen_cpp_goal(self) -> None:
        bundle = SDD.resolve_competition_objective(None, self.project)
        self.assertEqual("default", bundle["source"])
        self.assertTrue(bundle["branch_default_used"])
        self.assertEqual(SDD.DEFAULT_COMPETITION_GOAL, bundle["effective_objective"])
        self.assertIn("Unpack must still work correctly after customization.", bundle["competition_constraints"])
        self.assertIn("skill_delivery_required", bundle["required_acceptance_invariants"])

    def test_start_selects_generic_hosted_profile(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "generic-change",
            "Implement a bounded generic behavior with focused verification evidence",
            "--profile",
            "generic-hosted",
        )
        state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual("generic-hosted", state["scenario_profile"])
        self.assertIn("Preserve existing public behavior unless the task explicitly changes it.", state["scenario_constraints"])
        packet = json.loads(run("--project", str(self.project), "run-once", "--dry-run").stdout)
        self.assertEqual("generic-hosted", packet["scenario_profile"])
        self.assertEqual("generic-hosted", packet["metadata"]["scenario_profile"])

    def test_compete_selects_generic_hosted_default_objective(self) -> None:
        run("--project", str(self.project), "baseline")
        with (
            mock.patch.object(SDD, "run_loop") as run_loop,
            mock.patch.object(SDD, "finalize_hosted_run", return_value={"status": "closed"}),
        ):
            SDD.compete(
                argparse.Namespace(
                    project=str(self.project),
                    task=None,
                    change_id="generic-default",
                    executor="fixture",
                    max_steps=1,
                    scenario_profile="generic-hosted",
                )
            )
        active_root = Path(run_loop.call_args.args[0].project)
        state = json.loads((active_root / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual("generic-hosted", state["scenario_profile"])
        self.assertEqual(SDD.get_profile("generic-hosted").default_objective, state["objective"])

    def test_compete_allows_missing_task_and_uses_default_objective(self) -> None:
        run("--project", str(self.project), "baseline")
        with (
            mock.patch.object(SDD, "run_loop") as run_loop,
            mock.patch.object(SDD, "finalize_hosted_run", return_value={"status": "closed"}) as finalize,
        ):
            SDD.compete(
                argparse.Namespace(
                    project=str(self.project),
                    task=None,
                    change_id="default-goal",
                    executor="fixture",
                    max_steps=1,
                )
            )
        run_loop.assert_called_once()
        finalize.assert_called_once()
        active_root = Path(run_loop.call_args.args[0].project)
        state = json.loads((active_root / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(SDD.DEFAULT_SCENARIO_GOAL, state["objective"])
        self.assertEqual("default", state["objective_source"])
        self.assertEqual("default", state["scenario_objective_source"])
        self.assertEqual("generic-hosted", state["scenario_profile"])

    def test_apply_packet_includes_current_task_contract(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "packet-contract",
            "Implement a bounded sample behavior for runner verification",
        )
        change_dir = self.project / "openspec" / "changes" / "packet-contract"
        (change_dir / "tasks.md").write_text(
            "# Tasks\n\n## 1. Implementation\n\n"
            "- [ ] 1.1 Implement parameter-driven variable-length custom header payload support with focused tests\n",
            encoding="utf-8",
        )
        (change_dir / "plan.md").write_text(
            "# Execution Plan\n\n"
            "## Execution Strategy\n\nScoped per task.\n\n"
            "## Tasks\n\n"
            "### Task 1.1\n\n"
            "- Theme: custom header payload and variable-length header handling\n"
            "- Verification: run focused custom-header pack checks and variable-length payload regression\n"
            "- Evidence: implementation diff, focused test output, and header-related validation logs\n"
            "- Implementation Targets: src/pack, src/header\n"
            "- Test Targets: tests/header, tests/pack\n\n"
            "## Verification\n\ncovered\n\n"
            "## Checkpoint Strategy\n\ncheckpoint\n",
            encoding="utf-8",
        )
        state_path = self.project / ".sdd" / "runtime" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["stage"] = "apply"
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        packet = json.loads(run("--project", str(self.project), "run-once", "--dry-run").stdout)
        self.assertEqual(2, packet["packet_contract_version"])
        self.assertEqual("1.1", packet["task_id"])
        self.assertTrue(any(item["name"] == "coding-skill" for item in packet["skill_requirements"]))
        coding_requirement = next(item for item in packet["skill_requirements"] if item["capability"] == "coding")
        self.assertEqual(["coding-skill"], coding_requirement["candidates"])
        self.assertIn("current_task_contract", packet)
        self.assertEqual(
            "src/pack, src/header",
            packet["current_task_contract"]["implementation_targets"],
        )
        self.assertEqual(
            "tests/header, tests/pack",
            packet["current_task_contract"]["test_targets"],
        )
        self.assertNotIn("openspec/changes/packet-contract/tasks.md", packet["allowed_paths"])

    def test_project_skill_routing_overrides_profile_candidates(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "project-skill-routing",
            "Implement a bounded behavior using project-provided coding standards",
            "--profile",
            "generic-hosted",
        )
        config_path = self.project / ".sdd" / "config.yaml"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["skill_routing"]["capabilities"]["coding"] = ["project-coder", "company-coder"]
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        state_path = self.project / ".sdd" / "runtime" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["stage"] = "apply"
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        change_dir = self.project / "openspec" / "changes" / "project-skill-routing"
        (change_dir / "tasks.md").write_text(
            "# Tasks\n\n## 1. Implementation\n\n- [ ] 1.1 Apply the bounded behavior\n",
            encoding="utf-8",
        )
        packet = SDD.build_packet(self.project, state)
        coding = next(item for item in packet["skill_requirements"] if item["capability"] == "coding")
        self.assertEqual(["project-coder", "company-coder"], coding["candidates"])
        self.assertEqual("project-coder", coding["name"])

    def test_apply_packet_synthesizes_contract_from_tasks_when_plan_contract_is_missing(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "packet-synthesized-contract",
            "Recover apply scope from tasks when plan contracts drift",
        )
        change_dir = self.project / "openspec" / "changes" / "packet-synthesized-contract"
        (change_dir / "tasks.md").write_text(
            "# Tasks\n\n## 1. Implementation\n\n"
            "- [ ] 1.7 Deliver the tool skill at `skills/unitool/SKILL.md` with THX handling and header inspection\n"
            "  - Write skill content with THX-related handling guidance.\n"
            "  - Include header inspection workflows (using `packtool info` to inspect archive headers).\n",
            encoding="utf-8",
        )
        (change_dir / "plan.md").write_text(
            "# Execution Plan\n\n"
            "## Execution Strategy\n\nScoped per task.\n\n"
            "## Tasks\n\n"
            "### Task 1.1\n\n"
            "- Theme: custom header payload\n"
            "- Verification: run tests\n"
            "- Evidence: logs\n"
            "- Implementation Targets: src/archive.h\n"
            "- Test Targets: tests/test_packager.cpp\n\n"
            "## Verification\n\ncovered\n\n"
            "## Checkpoint Strategy\n\ncheckpoint\n",
            encoding="utf-8",
        )
        state_path = self.project / ".sdd" / "runtime" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["stage"] = "apply"
        state["task"] = "1.7"
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        packet = json.loads(run("--project", str(self.project), "run-once", "--dry-run").stdout)
        self.assertEqual("1.7", packet["task_id"])
        self.assertIn("required_artifacts", packet)
        self.assertEqual("skills/unitool/SKILL.md", packet["current_task_contract"]["implementation_targets"])
        self.assertEqual("None (documentation-only change)", packet["current_task_contract"]["test_targets"])
        self.assertEqual("synthesized", packet["current_task_contract"]["_source"])
        self.assertIn("THX-related handling guidance.", packet["task_details"])
        self.assertIn("skills/unitool/SKILL.md", packet["allowed_paths"])
        self.assertIn("openspec/changes/packet-synthesized-contract/tasks.md", packet["required_reads"])

    def test_apply_packet_allows_contract_targets_outside_default_source_globs(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "packet-allowed-targets",
            "Deliver a documentation skill update through apply packet targets",
        )
        change_dir = self.project / "openspec" / "changes" / "packet-allowed-targets"
        (change_dir / "tasks.md").write_text(
            "# Tasks\n\n## 1. Implementation\n\n"
            "- [ ] 1.1 Update skill documentation for header inspection\n",
            encoding="utf-8",
        )
        (change_dir / "plan.md").write_text(
            "# Execution Plan\n\n"
            "## Execution Strategy\n\nScoped per task.\n\n"
            "## Tasks\n\n"
            "### Task 1.1\n\n"
            "- Theme: skill delivery, THX handling, header inspection\n"
            "- Verification: review the exact skill file content and confirm THX/header inspection guidance is present\n"
            "- Evidence: updated skill file path plus content review proof for THX handling and header inspection sections\n"
            "- Implementation Targets: skills/unitool/SKILL.md\n"
            "- Test Targets: None (documentation-only change)\n\n"
            "## Verification\n\ncovered\n\n"
            "## Checkpoint Strategy\n\ncheckpoint\n",
            encoding="utf-8",
        )
        state_path = self.project / ".sdd" / "runtime" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["stage"] = "apply"
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        packet = json.loads(run("--project", str(self.project), "run-once", "--dry-run").stdout)
        self.assertIn("skills/unitool/skill.md", [path.lower() for path in packet["allowed_paths"]])

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
            mock.patch.object(SDD, "finalize_hosted_run", return_value={"status": "closed"}) as finalize,
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

        final = SDD.finalize_hosted_run(root, self.project)

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

        final = SDD.finalize_hosted_run(root, self.project)

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
        self.assertEqual("hosted_sdd_result", payload["kind"])
        self.assertEqual("competition_result", payload["legacy_kind"])
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
        self.assertTrue((self.project / "openspec" / "specs" / "custom-header-payload" / "spec.md").exists())

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
        self.assertEqual("hosted_sdd_result", payload["kind"])
        self.assertEqual("competition_result", payload["legacy_kind"])
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
        self.assertEqual("hosted_sdd_result", payload["kind"])
        self.assertEqual("competition_result", payload["legacy_kind"])
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
        self.assertEqual("hosted_sdd_result", payload["kind"])
        self.assertEqual("competition_result", payload["legacy_kind"])
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

    def test_one_command_failure_exhausts_budget_and_reports_terminal_result(self) -> None:
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
        )
        self.assertIn("Injected deterministic failure at stage proposal", result.stdout)
        payload = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual("hosted_sdd_result", payload["kind"])
        self.assertEqual("competition_result", payload["legacy_kind"])
        self.assertEqual("closed", payload["status"])
        self.assertEqual("closed_partial", payload["outcome"])
        self.assertEqual("completed_partial", payload["decision"])
        self.assertEqual("none", payload["recommended_action"])
        self.assertEqual(0, payload["exit_code"])
        state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual("closed", state["status"])
        self.assertEqual("closed_partial", state["terminal_outcome"])
        self.assertIn("forced_closeout", state)
        report = (self.project / ".sdd" / "delivery-report.md").read_text(encoding="utf-8")
        self.assertIn("Outcome: `CLOSED_PARTIAL`", report)
        receipt = json.loads((self.project / ".sdd" / "delivery-receipt.json").read_text(encoding="utf-8"))
        self.assertEqual("closed_partial", receipt["outcome"])
        self.assertTrue(receipt["score_signals"]["best_effort_result_available"])

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

    def test_current_task_remains_pinned_when_tasks_md_is_edited_by_agent(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "pinned-apply-task",
            "Keep apply task identity stable even if tasks.md is edited unexpectedly",
        )
        change_dir = self.project / "openspec" / "changes" / "pinned-apply-task"
        (change_dir / "tasks.md").write_text(
            "# Tasks\n\n## 1. Implementation\n\n"
            "- [x] 1.1 First task\n"
            "- [ ] 1.2 Second task\n"
            "- [ ] 1.3 Third task\n",
            encoding="utf-8",
        )
        state_path = self.project / ".sdd" / "runtime" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["stage"] = "apply"
        state["task"] = "1.2"
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        (change_dir / "tasks.md").write_text(
            "# Tasks\n\n## 1. Implementation\n\n"
            "- [x] 1.1 First task\n"
            "- [x] 1.2 Second task\n"
            "- [ ] 1.3 Third task\n",
            encoding="utf-8",
        )
        pinned = SDD.current_task(self.project, json.loads(state_path.read_text(encoding="utf-8")))
        self.assertIsNotNone(pinned)
        self.assertEqual("1.2", pinned["id"])
        SDD.complete_task(self.project, json.loads(state_path.read_text(encoding="utf-8")), "1.2")
        errors = SDD.validate_scope(self.project, "apply")
        self.assertTrue(any("openspec/changes/pinned-apply-task/tasks.md" in error for error in errors))

    def test_apply_stage_required_reads_excludes_tasks_md(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "apply-required-reads",
            "Keep apply required reads focused on the current task contract and implementation context",
        )
        state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        state["stage"] = "apply"
        (self.project / ".sdd" / "runtime" / "state.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8"
        )
        change_dir = self.project / "openspec" / "changes" / "apply-required-reads"
        (change_dir / "design.md").write_text("# Design\n", encoding="utf-8")
        (change_dir / "plan.md").write_text("# Plan\n", encoding="utf-8")
        (change_dir / "tasks.md").write_text("# Tasks\n", encoding="utf-8")
        (change_dir / "brainstorm.md").write_text("# Brainstorm\n", encoding="utf-8")
        reads = SDD.stage_required_reads(self.project, state)
        self.assertIn("openspec/changes/apply-required-reads/design.md", reads)
        self.assertIn("openspec/changes/apply-required-reads/plan.md", reads)
        self.assertNotIn("openspec/changes/apply-required-reads/tasks.md", reads)
        self.assertNotIn("openspec/changes/apply-required-reads/brainstorm.md", reads)

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

    def test_invoke_agent_timeout_becomes_sdd_error_with_evidence(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "agent-timeout",
            "Ensure unattended agent timeout is recorded and converted into a recoverable runner error",
        )
        config_path = self.project / ".sdd" / "config.yaml"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["timeouts"]["stage_agent_seconds"] = {"specs": 17}
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        state["stage"] = "specs"
        timeout = subprocess.TimeoutExpired(["opencode", "run"], 17, output="partial output")
        with mock.patch.object(SDD, "run_command", side_effect=timeout):
            with self.assertRaises(SDD.SddError) as exc:
                SDD.invoke_agent(self.project, state, False)
        self.assertIn("timed out after", str(exc.exception))
        evidence = sorted((self.project / ".sdd" / "evidence").glob("*agent-timeout.log"))
        self.assertEqual(1, len(evidence))
        content = evidence[0].read_text(encoding="utf-8")
        self.assertIn("partial output", content)
        self.assertIn("[TIMEOUT] agent stage exceeded 17 seconds", content)
        journal = (self.project / ".sdd" / "runtime" / "execution-journal.jsonl").read_text(encoding="utf-8")
        self.assertIn('"event": "agent_timed_out"', journal)
        self.assertIn('"stage": "specs"', journal)

    def test_invoke_agent_selects_bounded_stage_agent(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "agent-selection",
            "Ensure every hosted stage uses the bounded OpenCode stage agent",
        )
        state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        completed = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
        with mock.patch.object(SDD, "run_command", return_value=completed) as execute:
            SDD.invoke_agent(self.project, state, False)
        command = execute.call_args.args[0]
        self.assertEqual("opencode", command[0])
        self.assertIn("--agent", command)
        self.assertEqual(SDD.STAGE_AGENT_NAME, command[command.index("--agent") + 1])

    def test_run_loop_force_closes_after_repeated_failure_signature_budget(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "repeated-signature",
            "Stop an unattended run after the same failure repeats beyond budget",
        )
        config_path = self.project / ".sdd" / "config.yaml"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["budget"]["maximum_repeated_failure_signatures"] = 2
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        with (
            mock.patch.object(SDD, "validate_execution_preflight"),
            mock.patch.object(SDD, "execute_stage", side_effect=SDD.SddError("OpenCode invocation failed; see timeout.log")),
            mock.patch.object(SDD, "git_changed", return_value=[]),
        ):
            SDD.run_loop(argparse.Namespace(project=str(self.project), max_steps=5))
        state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        self.assertEqual("closed", state["status"])
        self.assertEqual("closed_fail", state["terminal_outcome"])
        self.assertIn("repeated failure signature exceeded budget", state["blocking_reason"])
        self.assertEqual(3, state["failure_signatures"]["brainstorm:agent_exit_nonzero"])
        receipt = json.loads((self.project / ".sdd" / "delivery-receipt.json").read_text(encoding="utf-8"))
        self.assertEqual("closed_fail", receipt["outcome"])
        self.assertTrue(receipt["score_signals"]["forced_closeout_used"])

    def test_gate_and_advance_force_closes_partial_after_gate_retry_budget(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "forced-partial",
            "Ensure gate retry exhaustion still produces a terminal partial result",
        )
        state_path = self.project / ".sdd" / "runtime" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["stage"] = "verify"
        state["status"] = "repair_required"
        state["pending_action"] = "gate"
        maximum = json.loads((self.project / ".sdd" / "config.yaml").read_text(encoding="utf-8"))["budget"][
            "maximum_stage_retries"
        ]
        state["retries"]["verify"] = maximum
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        with mock.patch.object(SDD, "execute_gates", return_value=(["Missing required artifact: verify.md"], [])):
            SDD.gate_and_advance(self.project)
        final_state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual("closed", final_state["status"])
        self.assertEqual("closed_partial", final_state["terminal_outcome"])
        self.assertEqual("closed", final_state["stage"])
        self.assertEqual("gate_retry_budget_exhausted", final_state["forced_closeout"]["trigger"])
        self.assertIn("Missing required artifact: verify.md", final_state["forced_closeout"]["gate_errors"])

    def test_stage_retry_budget_is_configurable(self) -> None:
        run("--project", str(self.project), "baseline")
        config_path = self.project / ".sdd" / "config.yaml"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(1, config["budget"]["maximum_stage_retries"])
        config["budget"]["maximum_stage_retries"] = 3
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        self.assertEqual(3, SDD.stage_retry_budget(self.project))

    def test_gate_and_advance_allows_soft_plan_findings_to_continue(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "soft-plan-findings",
            "Continue past generic plan wording while recording findings",
        )
        state_path = self.project / ".sdd" / "runtime" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["stage"] = "plan"
        state["status"] = "running"
        state["pending_action"] = "gate"
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        with (
            mock.patch.object(
                SDD,
                "execute_gates",
                return_value=(
                    [
                        "plan.md task 1.5 implementation_targets is too generic",
                        "plan.md task 1.5 evidence is too generic",
                    ],
                    [],
                ),
            ),
            mock.patch.object(SDD, "next_stage_after", return_value="apply"),
            mock.patch.object(SDD, "write_handoff", return_value=self.project / ".sdd" / "runtime" / "handoffs" / "x.json"),
            mock.patch.object(SDD, "checkpoint", return_value="abc123"),
        ):
            SDD.gate_and_advance(self.project)
        final_state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual("apply", final_state["stage"])
        self.assertEqual("running", final_state["status"])
        self.assertEqual(2, len(final_state["open_findings"]))
        self.assertTrue(all(item["severity"] == "soft" for item in final_state["open_findings"]))

    def test_gate_and_advance_requeues_stage_execution_after_hard_gate_failure(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "gate-requeue",
            "Retry the current stage by executing it again after a hard gate failure",
        )
        state_path = self.project / ".sdd" / "runtime" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["stage"] = "apply"
        state["status"] = "running"
        state["task"] = "1.1"
        state["pending_action"] = "gate"
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        with mock.patch.object(
            SDD,
            "execute_gates",
            return_value=(["Agent result is missing"], []),
        ):
            with self.assertRaises(SDD.SddError):
                SDD.gate_and_advance(self.project)
        final_state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual("repair_required", final_state["status"])
        self.assertEqual("execute_stage", final_state["pending_action"])
        self.assertEqual(1, final_state["retries"]["apply"])

    def test_emit_final_report_includes_open_and_resolved_findings(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "receipt-findings",
            "Surface deferred and resolved findings in the final receipt",
        )
        state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        state["status"] = "closed"
        state["stage"] = "closed"
        state["terminal_outcome"] = "closed_partial"
        state["open_findings"] = [{"stage": "plan", "severity": "soft", "message": "generic target", "status": "open"}]
        state["resolved_findings"] = [
            {"stage": "plan", "severity": "soft", "message": "generic evidence", "status": "reviewed_in_verify"}
        ]
        SDD.save_state(self.project, state)
        SDD.emit_final_report(self.project, state)
        receipt = json.loads((self.project / ".sdd" / "delivery-receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(1, len(receipt["open_findings"]))
        self.assertEqual(1, len(receipt["resolved_findings"]))

    def test_validate_agent_result_tolerates_known_metadata_fields(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "metadata-compat",
            "Accept compatible agent metadata fields without blocking unattended execution",
        )
        state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        result = {
            "schema_version": 1,
            "run_id": state["run_id"],
            "change_id": state["change_id"],
            "stage": state["stage"],
            "status": "completed",
            "summary": "completed",
            "files_read": [],
            "files_changed": [],
            "commands_run": [],
            "tests": [],
            "deviations": [],
            "blocking_reason": None,
            "task_id": None,
            "requirement_evidence": [],
            "residual_risks": [],
        }
        self.assertEqual([], SDD.validate_agent_result(self.project, state, result))
        result["role"] = "tasks"
        self.assertEqual([], SDD.validate_agent_result(self.project, state, result))
        result["created_at"] = "2026-06-27T00:00:00Z"
        self.assertEqual([], SDD.validate_agent_result(self.project, state, result))
        result["unexpected"] = True
        errors = SDD.validate_agent_result(self.project, state, result)
        self.assertTrue(any("unsupported fields: unexpected" in error for error in errors))

    def test_prompt_for_non_apply_forbids_build_and_test_commands(self) -> None:
        packet = {
            "stage": "brainstorm",
            "required_output": "openspec/changes/example/brainstorm.md",
        }
        prompt = SDD.prompt_for(packet)
        self.assertIn("do not run build, compile, test, cmake, ctest, packaging, or cleanup commands", prompt)
        self.assertIn("Current stage: brainstorm.", prompt)

    def test_prompt_for_apply_forbids_posix_shell_idioms_on_windows(self) -> None:
        packet = {
            "stage": "apply",
            "required_output": "complete exactly task 1.2",
        }
        prompt = SDD.prompt_for(packet)
        self.assertIn("do not use POSIX shell idioms such as `mkdir -p`", prompt)
        self.assertIn("reuse existing build directories when present", prompt)
        self.assertIn("Treat packet.allowed_paths and the task contract implementation_targets/test_targets as the hard scope boundary", prompt)
        self.assertIn("leave them unchanged and record the gap in residual_risks instead", prompt)
        self.assertIn("Do not read, glob, diff, or inspect source_root/work_root paths from state.json", prompt)
        self.assertIn("If any command, tool call, or directory access is denied, immediately write .sdd/runtime/agent-result.json with status blocked", prompt)
        self.assertIn("If the contract was synthesized, use task_details as additional binding scope and acceptance context", prompt)

    def test_prompt_for_verify_requires_workspace_local_outputs(self) -> None:
        packet = {
            "stage": "verify",
            "required_output": "openspec/changes/example/verify.md",
        }
        prompt = SDD.prompt_for(packet)
        self.assertIn("do not use external temp roots such as /tmp, C:\\tmp, %TEMP%", prompt)
        self.assertIn("you must write the required verify artifact and .sdd/runtime/agent-result.json", prompt)
        self.assertIn("return status blocked with exact evidence", prompt)

    def test_validate_residual_risks_accepts_strings_and_structured_objects(self) -> None:
        self.assertEqual([], SDD.validate_residual_risks(["risk a", "risk b"]))
        self.assertEqual(
            [],
            SDD.validate_residual_risks(
                [
                    {"risk": "definition ambiguity", "mitigation": "document interpretation"},
                    {"risk": "tooling convention unknown"},
                ]
            ),
        )
        errors = SDD.validate_residual_risks([{"risk": ""}])
        self.assertTrue(any("risk must be a non-empty string" in error for error in errors))

    def test_validate_agent_result_accepts_stage_specific_requirement_statuses(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "stage-status-compat",
            "Accept stage-specific requirement evidence statuses before apply",
        )
        state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        result = {
            "status": "completed",
            "summary": "ok",
            "files_read": [],
            "files_changed": [],
            "commands_run": [],
            "tests": [],
            "deviations": [],
            "blocking_reason": None,
            "task_id": None,
            "requirement_evidence": [
                {
                    "requirement": "Support custom header payload content through a parameter",
                    "implementation_files": [],
                    "test_files": [],
                    "status": "addressed_in_brainstorm",
                }
            ],
            "residual_risks": [],
        }
        self.assertEqual([], SDD.validate_agent_result(self.project, state, result))
        result["requirement_evidence"][0]["status"] = "addressed_in_plan"
        self.assertEqual([], SDD.validate_agent_result(self.project, state, result))
        result["requirement_evidence"][0]["status"] = "analyzed"
        self.assertEqual([], SDD.validate_agent_result(self.project, state, result))
        state["stage"] = "apply"
        errors = SDD.validate_agent_result(self.project, state, result)
        self.assertTrue(any("requirement_evidence has an invalid structure" in error for error in errors))

    def test_validate_agent_result_tolerates_missing_deviations_field(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "missing-deviations",
            "Treat omitted deviations as an empty list for unattended compatibility",
        )
        state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        result = {
            "status": "completed",
            "summary": "ok",
            "files_read": [],
            "files_changed": [],
            "commands_run": [],
            "tests": [],
            "blocking_reason": None,
            "task_id": None,
            "requirement_evidence": [],
            "residual_risks": [],
        }
        self.assertEqual([], SDD.validate_agent_result(self.project, state, result))

    def test_validate_agent_result_tolerates_missing_blocking_reason_field(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "missing-blocking-reason",
            "Accept omitted blocking_reason as an implicit null for compatible agents",
        )
        state = json.loads((self.project / ".sdd" / "runtime" / "state.json").read_text(encoding="utf-8"))
        result = {
            "status": "completed",
            "summary": "ok",
            "files_read": [],
            "files_changed": [],
            "commands_run": [],
            "tests": [],
            "deviations": [],
            "task_id": None,
            "requirement_evidence": [],
            "residual_risks": [],
        }
        self.assertEqual([], SDD.validate_agent_result(self.project, state, result))
        result["deviations"] = "none"
        errors = SDD.validate_agent_result(self.project, state, result)
        self.assertTrue(any("deviations must be an array of strings" in error for error in errors))

    def test_validate_agent_result_for_apply_normalizes_future_requirement_evidence(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "apply-evidence-compat",
            "Keep apply compatible with future planned evidence items while enforcing current-task evidence",
        )
        change_dir = self.project / "openspec" / "changes" / "apply-evidence-compat"
        (change_dir / "tasks.md").write_text(
            "# Tasks\n\n## 1. Implementation\n\n"
            "- [ ] 1.1 Add custom header parameter\n",
            encoding="utf-8",
        )
        (change_dir / "plan.md").write_text(
            "# Execution Plan\n\n"
            "## Execution Strategy\n\nScoped per task.\n\n"
            "## Tasks\n\n"
            "### Task 1.1\n\n"
            "- Theme: custom header payload, variable-length header payload\n"
            "- Verification: compile and run existing tests\n"
            "- Evidence: src/archive.h signature diff and existing test output\n"
            "- Implementation Targets: src/archive.h\n"
            "- Test Targets: tests/test_packager.cpp\n\n"
            "## Verification\n\ncovered\n\n"
            "## Checkpoint Strategy\n\ncheckpoint\n",
            encoding="utf-8",
        )
        state_path = self.project / ".sdd" / "runtime" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["stage"] = "apply"
        state["task"] = "1.1"
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        (self.project / "src").mkdir(exist_ok=True)
        (self.project / "src" / "archive.h").write_text("// changed\n", encoding="utf-8")
        (self.project / "tests").mkdir(exist_ok=True)
        (self.project / "tests" / "test_packager.cpp").write_text("// test\n", encoding="utf-8")
        result = {
            "status": "completed",
            "summary": "ok",
            "files_read": [],
            "files_changed": ["src/archive.h"],
            "commands_run": [],
            "tests": [],
            "deviations": [],
            "blocking_reason": None,
            "task_id": "1.1",
            "requirement_evidence": [
                {
                    "requirement": "variable_length_header_payload",
                    "implementation_files": ["src/archive.h"],
                    "test_files": ["tests/test_packager.cpp"],
                    "status": "satisfied",
                    "detail": "implemented in current task",
                },
                {
                    "requirement": "skill_delivery_required",
                    "implementation_files": ["skills/unitool/SKILL.md"],
                    "test_files": [],
                    "status": "planned",
                    "detail": "later task",
                },
            ],
            "residual_risks": [],
        }
        self.assertEqual([], SDD.validate_agent_result(self.project, state, result))

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

    def test_verify_stage_requires_competition_requirement_coverage(self) -> None:
        change_id = "coverage-check"
        handoff_dir = self.project / ".sdd" / "changes" / change_id / "handoffs"
        handoff_dir.mkdir(parents=True, exist_ok=True)
        (handoff_dir / "001-apply-to-apply.json").write_text(
            json.dumps(
                {
                    "requirement_evidence": [
                        {
                            "requirement": "Support parameter-driven custom header payload",
                            "implementation_files": ["src/a.cpp"],
                            "test_files": ["tests/a.txt"],
                            "status": "satisfied",
                        },
                        {
                            "requirement": "Preserve unpack correctness",
                            "implementation_files": ["src/b.cpp"],
                            "test_files": ["tests/b.txt"],
                            "status": "satisfied",
                        },
                    ]
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        errors = SDD.validate_competition_requirement_coverage(
            self.project,
            {"stage": "verify", "change_id": change_id},
            {"requirement_evidence": []},
        )
        self.assertTrue(any("compatibility" in error for error in errors))
        self.assertTrue(any("skill_delivery" in error for error in errors))

    def test_verify_artifact_requires_competition_keywords(self) -> None:
        change_id = "verify-keywords"
        change_dir = self.project / "openspec" / "changes" / change_id
        change_dir.mkdir(parents=True, exist_ok=True)
        (change_dir / "verify.md").write_text(
            "# Verification Report\n\n"
            "## Structural Validation\n\nPASS\n\n"
            "## Requirement Traceability\n\nPartial\n\n"
            "## Protected API and Scope\n\nPASS\n\n"
            "## Dependency Integrity\n\nPASS\n\n"
            "## Quality Gates\n\nPASS\n\n"
            "## Findings\n\nNone\n\n"
            "## Decision\n\nPASS\n\n"
            "## Machine State\n\n- Git commit:\n- Worktree:\n- Next action:\n",
            encoding="utf-8",
        )
        errors = SDD.validate_stage_artifact(
            self.project,
            {"stage": "verify", "change_id": change_id},
        )
        self.assertTrue(any("custom_header_payload" in error for error in errors))
        self.assertTrue(any("skill_delivery" in error for error in errors))

    def test_apply_task_requires_matching_requirement_theme(self) -> None:
        task = {
            "id": "1.3",
            "title": "Deliver the tool skill for THX-related handling and header inspection, and verify end-to-end behavior",
        }
        errors = SDD.validate_apply_task_requirement_alignment(
            self.project,
            {"stage": "apply", "scenario_profile": "competition-cpp-header-payload"},
            task,
            [
                {
                    "requirement": "Preserve unpack correctness for customized packages",
                    "implementation_files": ["src/a.cpp"],
                    "test_files": ["tests/a.txt"],
                    "status": "satisfied",
                }
            ],
        )
        self.assertTrue(any("skill_delivery" in error for error in errors))

    def test_apply_task_accepts_matching_requirement_theme(self) -> None:
        task = {
            "id": "1.3",
            "title": "Deliver the tool skill for THX-related handling and header inspection, and verify end-to-end behavior",
        }
        errors = SDD.validate_apply_task_requirement_alignment(
            self.project,
            {"stage": "apply", "scenario_profile": "competition-cpp-header-payload"},
            task,
            [
                {
                    "requirement": "Deliver the tool skill for THX handling and header inspection",
                    "implementation_files": ["src/a.cpp"],
                    "test_files": ["tests/a.txt"],
                    "status": "satisfied",
                }
            ],
        )
        self.assertEqual([], errors)

    def test_apply_task_documentation_only_uses_contract_theme_instead_of_task_text(self) -> None:
        task = {
            "id": "1.3",
            "title": "Deliver the tool skill for THX-related handling and header inspection, and verify end-to-end behavior",
            "details": "Add example commands for pack with custom header, unpack, and info inspection",
        }
        skill = self.project / "skills" / "unitool"
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text("THX header inspection with custom header examples\n", encoding="utf-8")
        errors = SDD.validate_apply_task_requirement_alignment(
            self.project,
            {"stage": "apply", "scenario_profile": "competition-cpp-header-payload"},
            task,
            [
                {
                    "requirement": "skill_delivery_required",
                    "implementation_files": ["skills/unitool/SKILL.md"],
                    "test_files": [],
                    "status": "satisfied",
                }
            ],
            {"theme": "skill delivery, THX handling, header inspection", "test_targets": "None (documentation-only change)"},
        )
        self.assertEqual([], errors)

    def test_task_expected_themes_depend_on_title_not_legacy_task_number(self) -> None:
        self.assertEqual(
            ["custom_header_payload"],
            SDD.task_expected_themes(
                {
                    "id": "1.2",
                    "title": "Add file-based custom header roundtrip regression coverage",
                }
            ),
        )
        self.assertEqual(
            ["skill_delivery"],
            SDD.task_expected_themes(
                {
                    "id": "1.4",
                    "title": "Deliver the tool skill for THX-related handling and header inspection",
                }
            ),
        )
        self.assertEqual(
            ["custom_header_payload", "skill_delivery"],
            SDD.task_expected_themes(
                {
                    "id": "1.2",
                    "title": "Add integration test for info --json visibility of custom header",
                    "details": "- File: `tests/integration/test_integration.cpp`",
                }
            ),
        )

    def test_task_declared_targets_extracts_inline_paths_from_title(self) -> None:
        self.assertEqual(
            ["skills/unitool/skill.md"],
            SDD.task_declared_targets(
                {
                    "id": "1.5",
                    "title": "Deliver the tool skill for THX/header inspection at skills/unitool/SKILL.md",
                    "details": "",
                }
            ),
        )

    def test_apply_task_accepts_invariant_requirement_names(self) -> None:
        task = {
            "id": "1.1",
            "title": "Implement parameter-driven variable-length custom header payload support with focused tests",
        }
        errors = SDD.validate_apply_task_requirement_alignment(
            self.project,
            {"stage": "apply", "scenario_profile": "competition-cpp-header-payload"},
            task,
            [
                {
                    "requirement": "variable_length_header_payload",
                    "implementation_files": ["src/a.cpp"],
                    "test_files": ["tests/a.txt"],
                    "status": "satisfied",
                }
            ],
        )
        self.assertEqual([], errors)

    def test_apply_task_accepts_theme_from_realized_test_file_content(self) -> None:
        task = {
            "id": "1.4",
            "title": "Add tests for custom header roundtrip, variable-length headers, backward compatibility, and info inspection in `tests/test_packager.cpp`",
        }
        target = self.project / "tests" / "test_packager.cpp"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            "TEST(CustomHeader, Roundtrip) { unpack_file(archive, out); }\n"
            "TEST(CustomHeader, DefaultCompatibility) { pack_file(input, archive); }\n",
            encoding="utf-8",
        )
        errors = SDD.validate_apply_task_requirement_alignment(
            self.project,
            {"stage": "apply"},
            task,
            [
                {
                    "requirement": "Custom header roundtrip test",
                    "implementation_files": ["tests/test_packager.cpp"],
                    "test_files": ["tests/test_packager.cpp"],
                    "status": "satisfied",
                },
                {
                    "requirement": "Backward-compatible default test",
                    "implementation_files": ["tests/test_packager.cpp"],
                    "test_files": ["tests/test_packager.cpp"],
                    "status": "satisfied",
                },
            ],
        )
        self.assertEqual([], errors)

    def test_apply_task_accepts_backward_compatible_requirement_label(self) -> None:
        task = {
            "id": "1.4",
            "title": "Add tests for custom header roundtrip, variable-length headers, backward compatibility, and info inspection in `tests/test_packager.cpp`",
        }
        errors = SDD.validate_apply_task_requirement_alignment(
            self.project,
            {"stage": "apply"},
            task,
            [
                {
                    "requirement": "Backward-compatible default test",
                    "implementation_files": ["tests/test_packager.cpp"],
                    "test_files": ["tests/test_packager.cpp"],
                    "status": "satisfied",
                },
                {
                    "requirement": "Custom header roundtrip test",
                    "implementation_files": ["tests/test_packager.cpp"],
                    "test_files": ["tests/test_packager.cpp"],
                    "status": "satisfied",
                },
                {
                    "requirement": "Variable-length header test",
                    "implementation_files": ["tests/test_packager.cpp"],
                    "test_files": ["tests/test_packager.cpp"],
                    "status": "satisfied",
                },
                {
                    "requirement": "Info inspection test",
                    "implementation_files": ["tests/test_packager.cpp"],
                    "test_files": ["tests/test_packager.cpp"],
                    "status": "satisfied",
                },
            ],
        )
        self.assertEqual([], errors)

    def test_validate_scope_ignores_apply_build_outputs(self) -> None:
        run("--project", str(self.project), "baseline")
        (self.project / ".sdd" / "runtime" / "state.json").write_text(
            json.dumps(
                {
                    "run_id": "run-apply-build-outputs",
                    "change_id": "apply-build-outputs",
                    "objective": "Ignore apply build outputs",
                    "stage": "apply",
                    "status": "running",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        build_dir = self.project / "build"
        build_dir.mkdir(exist_ok=True)
        (build_dir / "CMakeCache.txt").write_text("cache", encoding="utf-8")
        errors = SDD.validate_scope(self.project, "apply")
        self.assertEqual([], errors)

    def test_validate_scope_ignores_verify_build_outputs(self) -> None:
        competition = json.loads((self.project / ".sdd" / "policy" / "competition.yaml").read_text(encoding="utf-8"))
        competition["modification"]["allowed"] = ["src/**", "tests/**", "openspec/**", ".sdd/**"]
        (self.project / ".sdd" / "policy" / "competition.yaml").write_text(
            json.dumps(competition, indent=2) + "\n",
            encoding="utf-8",
        )
        build_dir = self.project / "build"
        build_dir.mkdir(exist_ok=True)
        (build_dir / "CMakeCache.txt").write_text("cache", encoding="utf-8")
        errors = SDD.validate_scope(self.project, "verify")
        self.assertEqual([], errors)

    def test_validate_execution_preflight_ignores_apply_build_outputs(self) -> None:
        build_temp = self.project / "build" / "Testing" / "Temporary"
        build_temp.mkdir(parents=True, exist_ok=True)
        (build_temp / "LastTest.log").write_text("log", encoding="utf-8")
        (build_temp / "CTestCostData.txt").write_text("cost", encoding="utf-8")
        SDD.validate_execution_preflight(
            self.project,
            {
                "status": "running",
                "stage": "apply",
                "change_id": "sample-change",
            },
        )

    def test_validate_scope_ignores_apply_focused_build_outputs(self) -> None:
        focused_build = self.project / ".sdd" / "tmp" / "focused-build" / "Testing" / "Temporary"
        focused_build.mkdir(parents=True, exist_ok=True)
        (focused_build / "LastTest.log").write_text("log", encoding="utf-8")
        (focused_build / "CTestCostData.txt").write_text("cost", encoding="utf-8")
        errors = SDD.validate_scope(self.project, "apply")
        self.assertEqual([], errors)

    def test_validate_scope_allows_apply_contract_targets_outside_default_source_globs(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "apply-scope-contract",
            "Allow documentation-only skill targets during apply",
        )
        change_dir = self.project / "openspec" / "changes" / "apply-scope-contract"
        (change_dir / "tasks.md").write_text(
            "# Tasks\n\n## 1. Implementation\n\n"
            "- [ ] 1.1 Update skill documentation with THX/header inspection guidance\n",
            encoding="utf-8",
        )
        (change_dir / "plan.md").write_text(
            "# Execution Plan\n\n"
            "## Execution Strategy\n\nScoped per task.\n\n"
            "## Tasks\n\n"
            "### Task 1.1\n\n"
            "- Theme: skill delivery, THX handling, header inspection\n"
            "- Verification: review the exact skill file content and confirm THX/header inspection guidance is present\n"
            "- Evidence: updated skill file path plus content review proof for THX handling and header inspection sections\n"
            "- Implementation Targets: skills/unitool/SKILL.md\n"
            "- Test Targets: None (documentation-only change)\n\n"
            "## Verification\n\ncovered\n\n"
            "## Checkpoint Strategy\n\ncheckpoint\n",
            encoding="utf-8",
        )
        state_path = self.project / ".sdd" / "runtime" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["stage"] = "apply"
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        skill = self.project / "skills" / "unitool"
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text("updated skill doc", encoding="utf-8")
        errors = SDD.validate_scope(self.project, "apply")
        self.assertFalse(any("skills/unitool/SKILL.md" in error for error in errors))

    def test_focused_test_commands_allows_documentation_only_apply_task(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "doc-only-apply",
            "Deliver documentation-only skill guidance",
        )
        change_dir = self.project / "openspec" / "changes" / "doc-only-apply"
        (change_dir / "tasks.md").write_text(
            "# Tasks\n\n## 1. Implementation\n\n"
            "- [ ] 1.1 Update skill documentation with unpack guidance\n",
            encoding="utf-8",
        )
        (change_dir / "plan.md").write_text(
            "# Execution Plan\n\n"
            "## Execution Strategy\n\nScoped per task.\n\n"
            "## Tasks\n\n"
            "### Task 1.1\n\n"
            "- Theme: skill delivery, THX handling, header inspection\n"
            "- Verification: review the exact skill file content and confirm THX/header inspection guidance is present\n"
            "- Evidence: updated skill file path plus content review proof for THX handling and header inspection sections\n"
            "- Implementation Targets: skills/unitool/SKILL.md\n"
            "- Test Targets: None (documentation-only change)\n\n"
            "## Verification\n\ncovered\n\n"
            "## Checkpoint Strategy\n\ncheckpoint\n",
            encoding="utf-8",
        )
        state_path = self.project / ".sdd" / "runtime" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["stage"] = "apply"
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        commands, errors = SDD.focused_test_commands(self.project, ["skills/unitool/SKILL.md"])
        self.assertEqual([], commands)
        self.assertEqual([], errors)

    def test_focused_test_commands_allows_source_only_apply_task_without_test_file_change(self) -> None:
        run("--project", str(self.project), "baseline")
        run(
            "--project",
            str(self.project),
            "start",
            "source-only-apply",
            "Allow narrow source-only apply work to prove behavior with existing tests",
        )
        change_dir = self.project / "openspec" / "changes" / "source-only-apply"
        (change_dir / "tasks.md").write_text(
            "# Tasks\n\n## 1. Implementation\n\n"
            "- [ ] 1.1 Update archive declaration\n",
            encoding="utf-8",
        )
        (change_dir / "plan.md").write_text(
            "# Execution Plan\n\n"
            "## Execution Strategy\n\nScoped per task.\n\n"
            "## Tasks\n\n"
            "### Task 1.1\n\n"
            "- Theme: custom header payload, variable-length header payload\n"
            "- Verification: compile the project and run existing tests to confirm backward compatibility\n"
            "- Evidence: src/archive.h signature diff plus existing test output\n"
            "- Implementation Targets: src/archive.h\n"
            "- Test Targets: tests/test_packager.cpp\n\n"
            "## Verification\n\ncovered\n\n"
            "## Checkpoint Strategy\n\ncheckpoint\n",
            encoding="utf-8",
        )
        state_path = self.project / ".sdd" / "runtime" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["stage"] = "apply"
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        commands, errors = SDD.focused_test_commands(self.project, ["src/archive.h"])
        self.assertEqual([], commands)
        self.assertEqual([], errors)

    def test_focused_test_commands_generic_cpp_falls_back_to_cmake_build_dir(self) -> None:
        project_policy = json.loads((self.project / ".sdd" / "policy" / "project.yaml").read_text(encoding="utf-8"))
        project_policy["detected_project_kind"] = "generic"
        (self.project / ".sdd" / "policy" / "project.yaml").write_text(
            json.dumps(project_policy, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.project / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\nproject(sample)\n", encoding="utf-8")
        test_dir = self.project / "tests"
        test_dir.mkdir(exist_ok=True)
        (test_dir / "test_format.cpp").write_text("// test\n", encoding="utf-8")
        commands, errors = SDD.focused_test_commands(self.project, ["tests/test_format.cpp"])
        self.assertEqual([], errors)
        self.assertEqual(
            [
                ["cmake", "-S", ".", "-B", ".sdd/tmp/focused-build", "-DCMAKE_BUILD_TYPE=Release"],
                ["cmake", "--build", ".sdd/tmp/focused-build", "--config", "Release"],
                ["ctest", "--test-dir", ".sdd/tmp/focused-build", "-C", "Release", "--output-on-failure"],
            ],
            commands,
        )

    def test_focused_test_commands_windows_ignores_build_sh_and_uses_cmake(self) -> None:
        project_policy = json.loads((self.project / ".sdd" / "policy" / "project.yaml").read_text(encoding="utf-8"))
        project_policy["detected_project_kind"] = "generic"
        (self.project / ".sdd" / "policy" / "project.yaml").write_text(
            json.dumps(project_policy, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.project / "build.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        (self.project / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\nproject(sample)\n", encoding="utf-8")
        test_dir = self.project / "tests"
        test_dir.mkdir(exist_ok=True)
        (test_dir / "test_format.cpp").write_text("// test\n", encoding="utf-8")
        with mock.patch.object(SDD.os, "name", "nt"):
            commands, errors = SDD.focused_test_commands(self.project, ["tests/test_format.cpp"])
        self.assertEqual([], errors)
        self.assertEqual(
            [
                ["cmake", "-S", ".", "-B", ".sdd/tmp/focused-build", "-DCMAKE_BUILD_TYPE=Release"],
                ["cmake", "--build", ".sdd/tmp/focused-build", "--config", "Release"],
                ["ctest", "--test-dir", ".sdd/tmp/focused-build", "-C", "Release", "--output-on-failure"],
            ],
            commands,
        )

    def test_focused_test_commands_python_ignores_non_python_tests(self) -> None:
        project_policy = json.loads((self.project / ".sdd" / "policy" / "project.yaml").read_text(encoding="utf-8"))
        project_policy["detected_project_kind"] = "python"
        (self.project / ".sdd" / "policy" / "project.yaml").write_text(
            json.dumps(project_policy, indent=2) + "\n",
            encoding="utf-8",
        )
        commands, errors = SDD.focused_test_commands(self.project, ["tests/unit/test_format.cpp"])
        self.assertEqual([], commands)
        self.assertEqual([], errors)

    def test_detect_project_treats_runner_repo_shape_as_python(self) -> None:
        package_dir = self.project / "autonomous_sdd"
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / "__init__.py").write_text("", encoding="utf-8")
        tests_dir = self.project / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "test_runner.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        detected = SDD.detect_project(self.project)
        self.assertEqual("python", detected["kind"])
        self.assertEqual([sys.executable, "-m", "pytest"], detected["full_test"])
        self.assertEqual([sys.executable, "-m", "compileall", "-q", "autonomous_sdd", "tests"], detected["quick_check"])

    def test_target_list_ignores_contract_explanatory_text(self) -> None:
        self.assertEqual(
            ["scripts/sdd.py", "tests/test_cpp_detection.py"],
            SDD.target_list(
                "`scripts/sdd.py` (add `cpp-cmake` branch after line 1083, before generic fallback at line 1084), "
                "`tests/test_cpp_detection.py` (new file: tests for CMakeLists.txt present, absent, and Rust-wins-over-C++ priority)"
            ),
        )

    def test_substantive_changed_paths_keeps_skill_delivery_under_sdd(self) -> None:
        run("--project", str(self.project), "baseline")
        skill = self.project / ".sdd" / "skills" / "cpp-unitool-header"
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text("skill delivery\n", encoding="utf-8")
        self.assertEqual(
            [".sdd/skills/cpp-unitool-header/SKILL.md"],
            SDD.substantive_changed_paths(self.project),
        )

    def test_apply_result_allows_requirement_without_per_task_impl_and_test_files(self) -> None:
        result = {
            "status": "completed",
            "summary": "Applied task changes and preserved broader competition constraints.",
            "files_read": [],
            "files_changed": ["src/main.cpp"],
            "commands_run": [],
            "tests": [],
            "deviations": [],
            "blocking_reason": None,
            "task_id": "1.1",
            "requirement_evidence": [
                {
                    "requirement": "unchanged_build_entrypoint",
                    "implementation_files": [],
                    "test_files": [],
                    "status": "satisfied",
                }
            ],
            "residual_risks": [],
        }
        errors = SDD.validate_agent_result(self.project, {"stage": "apply"}, result)
        self.assertEqual([], errors)

    def test_validate_agent_result_accepts_string_deviations(self) -> None:
        result = {
            "status": "completed",
            "summary": "Applied task changes.",
            "files_read": [],
            "files_changed": [],
            "commands_run": [],
            "tests": [],
            "deviations": "Compiler unavailable; verification deferred.",
            "blocking_reason": None,
            "requirement_evidence": [],
            "residual_risks": [],
        }
        errors = SDD.validate_agent_result(self.project, {"stage": "review"}, result)
        self.assertFalse(any("deviations" in error for error in errors))

    def test_pre_apply_requirement_evidence_accepts_partially_satisfied_alias(self) -> None:
        result = {
            "status": "completed",
            "summary": "Brainstorm complete.",
            "files_read": [],
            "files_changed": [],
            "commands_run": [],
            "tests": [],
            "deviations": [],
            "blocking_reason": None,
            "task_id": None,
            "requirement_evidence": [
                {
                    "requirement": "variable_length_header_payload",
                    "implementation_files": ["src/a.cpp"],
                    "test_files": ["tests/a.txt"],
                    "status": "partially_satisfied",
                }
            ],
            "residual_risks": [],
        }
        errors = SDD.validate_agent_result(self.project, {"stage": "brainstorm"}, result)
        self.assertEqual([], errors)

    def test_specs_requirement_evidence_accepts_specified_status(self) -> None:
        result = {
            "status": "completed",
            "summary": "Specs complete.",
            "files_read": [],
            "files_changed": [],
            "commands_run": [],
            "tests": [],
            "deviations": [],
            "blocking_reason": None,
            "task_id": None,
            "requirement_evidence": [
                {
                    "requirement": "Skill documents THX binary layout",
                    "implementation_files": [],
                    "test_files": [],
                    "status": "specified",
                }
            ],
            "residual_risks": [],
        }
        errors = SDD.validate_agent_result(self.project, {"stage": "specs"}, result)
        self.assertEqual([], errors)

    def test_design_requirement_evidence_accepts_designed_status(self) -> None:
        result = {
            "status": "completed",
            "summary": "Design complete.",
            "files_read": [],
            "files_changed": [],
            "commands_run": [],
            "tests": [],
            "deviations": [],
            "blocking_reason": None,
            "task_id": None,
            "requirement_evidence": [
                {
                    "requirement": "Support custom header payload content through a parameter",
                    "implementation_files": ["src/archive.h"],
                    "test_files": ["tests/test_packager.cpp"],
                    "status": "designed",
                }
            ],
            "residual_risks": [],
        }
        errors = SDD.validate_agent_result(self.project, {"stage": "design"}, result)
        self.assertEqual([], errors)

    def test_brainstorm_requirement_evidence_accepts_complete_suffix_alias(self) -> None:
        result = {
            "status": "completed",
            "summary": "Brainstorm complete.",
            "files_read": [],
            "files_changed": [],
            "commands_run": [],
            "tests": [],
            "deviations": [],
            "blocking_reason": None,
            "task_id": None,
            "requirement_evidence": [
                {
                    "requirement": "Support custom header payload content through a parameter",
                    "implementation_files": [],
                    "test_files": [],
                    "status": "design_complete",
                }
            ],
            "residual_risks": [],
        }
        errors = SDD.validate_agent_result(self.project, {"stage": "brainstorm"}, result)
        self.assertEqual([], errors)

    def test_proposal_requirement_evidence_accepts_optional_note(self) -> None:
        result = {
            "status": "completed",
            "summary": "Proposal complete.",
            "files_read": [],
            "files_changed": [],
            "commands_run": [],
            "tests": [],
            "deviations": [],
            "blocking_reason": None,
            "task_id": None,
            "requirement_evidence": [
                {
                    "requirement": "variable_length_header_payload",
                    "implementation_files": [],
                    "test_files": [],
                    "status": "satisfied",
                    "note": "Proposal maps this invariant to a header_content parameter and serialized header size.",
                }
            ],
            "residual_risks": [],
        }
        errors = SDD.validate_agent_result(self.project, {"stage": "proposal"}, result)
        self.assertEqual([], errors)

    def test_plan_contracts_require_all_tasks_and_required_fields(self) -> None:
        change_id = "plan-contracts"
        change_dir = self.project / "openspec" / "changes" / change_id
        change_dir.mkdir(parents=True, exist_ok=True)
        (change_dir / "tasks.md").write_text(
            "# Tasks\n\n## 1. Implementation\n\n"
            "- [ ] 1.1 Implement parameter-driven variable-length custom header payload support with focused tests\n"
            "- [ ] 1.2 Preserve unpack correctness, original CLI compatibility, and unchanged build entrypoint with regression tests\n"
            "- [ ] 1.3 Deliver the tool skill for THX-related handling and header inspection, and verify end-to-end behavior\n",
            encoding="utf-8",
        )
        (change_dir / "plan.md").write_text(
            "# Execution Plan\n\n"
            "## Execution Strategy\n\nScoped per task.\n\n"
            "## Tasks\n\n"
            "### Task 1.1\n\n"
            "- Theme: custom header payload and variable-length header handling\n"
            "- Verification: focused custom-header pack checks\n"
            "- Evidence: diff and focused test logs\n\n"
            "- Implementation Targets: src/pack, src/header\n"
            "- Test Targets: tests/header, tests/pack\n\n"
            "### Task 1.2\n\n"
            "- Theme: unpack correctness, legacy cli compatibility, build entrypoint stability\n"
            "- Verification: unpack and compatibility regression checks\n"
            "- Evidence: unpack logs and compatibility logs\n\n"
            "- Implementation Targets: src/unpack, src/cli\n"
            "- Test Targets: tests/unpack, tests/compatibility\n\n"
            "### Task 1.3\n\n"
            "- Theme: skill delivery, thx handling, header inspection\n"
            "- Verification: skill invocation and end-to-end checks\n"
            "- Evidence: skill files and invocation logs\n"
            "- Implementation Targets: skill/cpp-unitool-header, src/inspection\n"
            "- Test Targets: tests/skill, tests/inspection\n\n"
            "## Verification\n\ncovered\n\n"
            "## Checkpoint Strategy\n\ncheckpoint\n",
            encoding="utf-8",
        )
        errors = SDD.validate_plan_contracts(
            self.project,
            {"stage": "plan", "change_id": change_id},
        )
        self.assertEqual([], errors)

    def test_plan_contracts_reject_missing_task_mapping_and_fields(self) -> None:
        change_id = "plan-contracts-fail"
        change_dir = self.project / "openspec" / "changes" / change_id
        change_dir.mkdir(parents=True, exist_ok=True)
        (change_dir / "tasks.md").write_text(
            "# Tasks\n\n## 1. Implementation\n\n"
            "- [ ] 1.1 Implement parameter-driven variable-length custom header payload support with focused tests\n"
            "- [ ] 1.2 Preserve unpack correctness, original CLI compatibility, and unchanged build entrypoint with regression tests\n",
            encoding="utf-8",
        )
        (change_dir / "plan.md").write_text(
            "# Execution Plan\n\n"
            "## Execution Strategy\n\nScoped per task.\n\n"
            "## Tasks\n\n"
            "### Task 1.1\n\n"
            "- Theme: custom header payload\n"
            "- Verification: focused checks\n\n"
            "- Implementation Targets: src/pack\n"
            "- Test Targets: tests/header\n\n"
            "## Verification\n\ncovered\n\n"
            "## Checkpoint Strategy\n\ncheckpoint\n",
            encoding="utf-8",
        )
        errors = SDD.validate_plan_contracts(
            self.project,
            {"stage": "plan", "change_id": change_id},
        )
        self.assertTrue(any("1.1 missing evidence" in error for error in errors))
        self.assertTrue(any("missing contract block for task 1.2" in error for error in errors))

    def test_plan_contracts_reject_generic_verification_and_evidence(self) -> None:
        change_id = "plan-generic"
        change_dir = self.project / "openspec" / "changes" / change_id
        change_dir.mkdir(parents=True, exist_ok=True)
        (change_dir / "tasks.md").write_text(
            "# Tasks\n\n## 1. Implementation\n\n"
            "- [ ] 1.1 Implement parameter-driven variable-length custom header payload support with focused tests\n",
            encoding="utf-8",
        )
        (change_dir / "plan.md").write_text(
            "# Execution Plan\n\n"
            "## Execution Strategy\n\nScoped per task.\n\n"
            "## Tasks\n\n"
            "### Task 1.1\n\n"
            "- Theme: custom header payload and variable-length header handling\n"
            "- Verification: run tests\n"
            "- Evidence: collect logs\n"
            "- Implementation Targets: src/pack, src/header\n"
            "- Test Targets: tests/header, tests/pack\n\n"
            "## Verification\n\ncovered\n\n"
            "## Checkpoint Strategy\n\ncheckpoint\n",
            encoding="utf-8",
        )
        errors = SDD.validate_plan_contracts(
            self.project,
            {"stage": "plan", "change_id": change_id},
        )
        self.assertTrue(any("verification is too generic" in error for error in errors))
        self.assertTrue(any("evidence is too generic" in error for error in errors))

    def test_plan_contract_specificity_helpers_accept_concrete_lines(self) -> None:
        self.assertTrue(
            SDD.verification_line_is_specific(
                "run customized unpack regression and original CLI compatibility checks"
            )
        )
        self.assertTrue(
            SDD.evidence_line_is_specific(
                "unpack test logs, compatibility output, and build-entry stability proof"
            )
        )
        self.assertTrue(
            SDD.verification_line_is_specific(
                "Run `test_format` binary; all existing + new tests pass; exit code 0"
            )
        )
        self.assertTrue(
            SDD.evidence_line_is_specific(
                "New test functions `test_write_read_hwx_custom_header_empty` in `tests/unit/test_format.cpp`"
            )
        )
        self.assertTrue(
            SDD.target_line_is_specific(
                "`tests/unit/test_format.cpp` — add 4 static test functions using existing assert pattern"
            )
        )
        self.assertTrue(SDD.target_line_is_specific("None (documentation-only change)"))

    def test_plan_commitment_coverage_rejects_unrealized_theme(self) -> None:
        change_id = "plan-coverage-fail"
        change_dir = self.project / "openspec" / "changes" / change_id
        handoff_dir = self.project / ".sdd" / "changes" / change_id / "handoffs"
        change_dir.mkdir(parents=True, exist_ok=True)
        handoff_dir.mkdir(parents=True, exist_ok=True)
        (change_dir / "plan.md").write_text(
            "# Execution Plan\n\n"
            "## Execution Strategy\n\nScoped per task.\n\n"
            "## Tasks\n\n"
            "### Task 1.3\n\n"
            "- Theme: skill delivery, thx handling, header inspection\n"
            "- Verification: run skill invocation and header inspection verification\n"
            "- Evidence: skill files and header inspection output\n"
            "- Implementation Targets: skill/cpp-unitool-header, src/inspection\n"
            "- Test Targets: tests/skill, tests/inspection\n\n"
            "## Verification\n\ncovered\n\n"
            "## Checkpoint Strategy\n\ncheckpoint\n",
            encoding="utf-8",
        )
        (handoff_dir / "001-apply-to-apply.json").write_text(
            json.dumps(
                {
                    "requirement_evidence": [
                        {
                            "requirement": "Preserve unpack correctness for customized packages",
                            "implementation_files": ["src/unpack/a.cpp"],
                            "test_files": ["tests/unpack/a.txt"],
                            "status": "satisfied",
                        }
                    ]
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        errors = SDD.validate_plan_commitment_coverage(
            self.project,
            {"stage": "verify", "change_id": change_id},
            {"requirement_evidence": []},
        )
        self.assertTrue(any("verification has no realized evidence for theme: skill_delivery" in error for error in errors))

    def test_plan_commitment_coverage_accepts_realized_theme(self) -> None:
        change_id = "plan-coverage-pass"
        change_dir = self.project / "openspec" / "changes" / change_id
        handoff_dir = self.project / ".sdd" / "changes" / change_id / "handoffs"
        change_dir.mkdir(parents=True, exist_ok=True)
        handoff_dir.mkdir(parents=True, exist_ok=True)
        (change_dir / "plan.md").write_text(
            "# Execution Plan\n\n"
            "## Execution Strategy\n\nScoped per task.\n\n"
            "## Tasks\n\n"
            "### Task 1.3\n\n"
            "- Theme: skill delivery, thx handling, header inspection\n"
            "- Verification: run skill invocation and header inspection verification\n"
            "- Evidence: skill files and header inspection output\n"
            "- Implementation Targets: skill/cpp-unitool-header, src/inspection\n"
            "- Test Targets: tests/skill, tests/inspection\n\n"
            "## Verification\n\ncovered\n\n"
            "## Checkpoint Strategy\n\ncheckpoint\n",
            encoding="utf-8",
        )
        (handoff_dir / "001-apply-to-apply.json").write_text(
            json.dumps(
                {
                    "requirement_evidence": [
                        {
                            "requirement": "Deliver the tool skill for THX handling and header inspection",
                            "implementation_files": ["skill/cpp-unitool-header/SKILL.md"],
                            "test_files": ["tests/skill/header_skill_test.txt"],
                            "status": "satisfied",
                        }
                    ]
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        errors = SDD.validate_plan_commitment_coverage(
            self.project,
            {"stage": "verify", "change_id": change_id},
            {"requirement_evidence": []},
        )
        self.assertEqual([], errors)

    def test_plan_contracts_reject_generic_targets(self) -> None:
        change_id = "plan-targets-generic"
        change_dir = self.project / "openspec" / "changes" / change_id
        change_dir.mkdir(parents=True, exist_ok=True)
        (change_dir / "tasks.md").write_text(
            "# Tasks\n\n## 1. Implementation\n\n"
            "- [ ] 1.1 Implement parameter-driven variable-length custom header payload support with focused tests\n",
            encoding="utf-8",
        )
        (change_dir / "plan.md").write_text(
            "# Execution Plan\n\n"
            "## Execution Strategy\n\nScoped per task.\n\n"
            "## Tasks\n\n"
            "### Task 1.1\n\n"
            "- Theme: custom header payload and variable-length header handling\n"
            "- Verification: run focused custom-header pack checks\n"
            "- Evidence: header-related validation logs and diff output\n"
            "- Implementation Targets: src\n"
            "- Test Targets: tests\n\n"
            "## Verification\n\ncovered\n\n"
            "## Checkpoint Strategy\n\ncheckpoint\n",
            encoding="utf-8",
        )
        errors = SDD.validate_plan_contracts(
            self.project,
            {"stage": "plan", "change_id": change_id},
        )
        self.assertTrue(any("implementation_targets is too generic" in error for error in errors))
        self.assertTrue(any("test_targets is too generic" in error for error in errors))

    def test_plan_contracts_accept_realistic_concrete_plan_from_live_run_shape(self) -> None:
        change_id = "plan-live-shape"
        change_dir = self.project / "openspec" / "changes" / change_id
        change_dir.mkdir(parents=True, exist_ok=True)
        (change_dir / "tasks.md").write_text(
            "# Tasks\n\n## 1. Implementation\n\n"
            "- [ ] 1.1 Add unit tests for variable-length custom_header payloads through binary write/read roundtrip\n"
            "- [ ] 1.2 Add integration tests for unpack correctness, backward compatibility, and info output with custom header\n"
            "- [ ] 1.3 Update skill with THX header inspection guidance\n",
            encoding="utf-8",
        )
        (change_dir / "plan.md").write_text(
            "# Execution Plan\n\n"
            "## Execution Strategy\n\nScoped per task.\n\n"
            "## Tasks\n\n"
            "### Task 1.1\n\n"
            "- Theme: Add unit tests for variable-length custom_header payloads through binary write/read roundtrip\n"
            "- Verification: Run `test_format` binary; all existing + new tests pass; exit code 0\n"
            "- Evidence: New test functions `test_write_read_hwx_custom_header_empty` in `tests/unit/test_format.cpp`\n"
            "- Implementation Targets: `tests/unit/test_format.cpp` — add 4 static test functions\n"
            "- Test Targets: `tests/unit/test_format.cpp` — each function exercises `write_hwx`/`read_hwx` roundtrip\n\n"
            "### Task 1.2\n\n"
            "- Theme: Add integration tests for unpack correctness, backward compatibility, and info output with custom header\n"
            "- Verification: Run `test_integration` binary; all existing + new tests pass; exit code 0\n"
            "- Evidence: New test functions `test_unpack_with_custom_header` in `tests/integration/test_integration.cpp`\n"
            "- Implementation Targets: `tests/integration/test_integration.cpp` — add 3 static test functions\n"
            "- Test Targets: `tests/integration/test_integration.cpp` — verify pack/unpack and info output with `custom_header`\n\n"
            "### Task 1.3\n\n"
            "- Theme: Update skill with THX header inspection guidance\n"
            "- Verification: `skills/unitool/SKILL.md` content review; THX inspection and custom_header guidance present\n"
            "- Evidence: Updated `skills/unitool/SKILL.md` with THX/header inspection subsections\n"
            "- Implementation Targets: `skills/unitool/SKILL.md` — add THX guidance and examples\n"
            "- Test Targets: None (documentation-only change)\n\n"
            "## Verification\n\ncovered\n\n"
            "## Checkpoint Strategy\n\ncheckpoint\n",
            encoding="utf-8",
        )
        errors = SDD.validate_plan_contracts(
            self.project,
            {"stage": "plan", "change_id": change_id},
        )
        self.assertEqual([], errors)

    def test_plan_contracts_accept_aggregated_contracts_for_related_tasks(self) -> None:
        change_id = "plan-aggregated"
        change_dir = self.project / "openspec" / "changes" / change_id
        change_dir.mkdir(parents=True, exist_ok=True)
        (change_dir / "tasks.md").write_text(
            "# Tasks\n\n## 1. Implementation\n\n"
            "- [ ] 1.1 Add unit tests for variable-length custom header payload at boundaries\n"
            "  - File: `tests/unit/test_format.cpp`\n\n"
            "- [ ] 1.2 Add integration test for info --json visibility of custom header\n"
            "  - File: `tests/integration/test_integration.cpp`\n\n"
            "- [ ] 1.3 Add CLI integration test for info --json on packed archive\n"
            "  - File: `tests/integration/test_cli.cpp`\n\n"
            "- [ ] 1.4 Enhance skill documentation with THX header inspection workflow\n"
            "  - File: `skills/unitool/SKILL.md`\n\n"
            "- [ ] 1.5 Verify all existing tests pass unchanged\n",
            encoding="utf-8",
        )
        (change_dir / "plan.md").write_text(
            "# Execution Plan\n\n"
            "## Execution Strategy\n\nScoped per task.\n\n"
            "## Tasks\n\n"
            "### Task 1.1\n\n"
            "- Theme: custom header payload, variable-length header payload\n"
            "- Verification: run the specific custom-header regression binary or command and confirm the targeted custom_header roundtrip behavior passes with exit code 0\n"
            "- Evidence: focused test output plus the exact changed test file paths that prove custom_header roundtrip and variable-length payload coverage\n"
            "- Implementation Targets: tests/unit/test_format.cpp\n"
            "- Test Targets: tests/unit/test_format.cpp\n\n"
            "### Task 1.2\n\n"
            "- Theme: unpack correctness, legacy CLI compatibility, unchanged build entrypoint\n"
            "- Verification: run the exact unpack and CLI regression targets and confirm customized unpack, original CLI behavior, and build entry stability all pass\n"
            "- Evidence: unpack regression output, CLI regression output, and file-level proof tied to the changed integration test paths\n"
            "- Implementation Targets: tests/integration/test_integration.cpp, tests/integration/test_cli.cpp\n"
            "- Test Targets: tests/integration/test_integration.cpp, tests/integration/test_cli.cpp\n\n"
            "### Task 1.3\n\n"
            "- Theme: skill delivery, THX handling, header inspection\n"
            "- Verification: review the exact skill file content and confirm THX/header inspection guidance, custom_header behavior, and usage examples are present\n"
            "- Evidence: updated skill file path plus content review proof for THX handling and header inspection sections\n"
            "- Implementation Targets: skills/unitool/SKILL.md\n"
            "- Test Targets: None (documentation-only change)\n\n"
            "## Verification\n\ncovered\n\n"
            "## Checkpoint Strategy\n\ncheckpoint\n",
            encoding="utf-8",
        )
        errors = SDD.validate_plan_contracts(
            self.project,
            {"stage": "plan", "change_id": change_id},
        )
        self.assertEqual([], errors)

    def test_plan_commitment_coverage_rejects_unmatched_declared_targets(self) -> None:
        change_id = "plan-target-match-fail"
        change_dir = self.project / "openspec" / "changes" / change_id
        handoff_dir = self.project / ".sdd" / "changes" / change_id / "handoffs"
        change_dir.mkdir(parents=True, exist_ok=True)
        handoff_dir.mkdir(parents=True, exist_ok=True)
        (change_dir / "plan.md").write_text(
            "# Execution Plan\n\n"
            "## Execution Strategy\n\nScoped per task.\n\n"
            "## Tasks\n\n"
            "### Task 1.1\n\n"
            "- Theme: custom header payload and variable-length header handling\n"
            "- Verification: run focused custom-header pack checks and variable-length payload regression\n"
            "- Evidence: implementation diff, focused test output, and header-related validation logs\n"
            "- Implementation Targets: src/pack, src/header\n"
            "- Test Targets: tests/header, tests/pack\n\n"
            "## Verification\n\ncovered\n\n"
            "## Checkpoint Strategy\n\ncheckpoint\n",
            encoding="utf-8",
        )
        (handoff_dir / "001-apply-to-apply.json").write_text(
            json.dumps(
                {
                    "requirement_evidence": [
                        {
                            "requirement": "Support parameter-driven custom header payload with variable-length header content",
                            "implementation_files": ["src/cli/a.cpp"],
                            "test_files": ["tests/cli/a.txt"],
                            "status": "satisfied",
                        }
                    ]
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        errors = SDD.validate_plan_commitment_coverage(
            self.project,
            {"stage": "verify", "change_id": change_id},
            {"requirement_evidence": []},
        )
        self.assertTrue(any("implementation_targets have no realized file match" in error for error in errors))
        self.assertTrue(any("test_targets have no realized file match" in error for error in errors))

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
