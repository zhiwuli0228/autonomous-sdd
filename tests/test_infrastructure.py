from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autonomous_sdd.config import (
    build_effective_runtime,
    freeze_effective_runtime,
    verify_frozen_runtime,
)
from autonomous_sdd.errors import ConfigurationError, PathSafetyError, WorkspaceError
from autonomous_sdd.locking import FileLock, repository_lock_key
from autonomous_sdd.paths import resolve_beneath
from autonomous_sdd.repository import Repository
from autonomous_sdd.services import create_runtime_services
from autonomous_sdd.workspace import RunWorkspace, create_run_context


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


class InfrastructureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="autonomous-sdd-infra-"))
        self.project = self.temp / "project"
        self.project.mkdir()
        git(self.project, "init")
        git(self.project, "config", "user.name", "Infrastructure Test")
        git(self.project, "config", "user.email", "infra@example.invalid")
        (self.project / "tracked.txt").write_text("initial\n", encoding="utf-8")
        git(self.project, "add", "--all")
        git(self.project, "commit", "-m", "initial")
        self.run_root = self.temp / "runs"

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.temp, ignore_errors=True)

    def test_run_root_inside_project_is_rejected(self) -> None:
        with self.assertRaises(PathSafetyError):
            create_run_context(self.project, self.project / ".runs")

    def test_workspace_paths_reject_escape_and_absolute_paths(self) -> None:
        with self.assertRaises(PathSafetyError):
            resolve_beneath(self.run_root, "../outside")
        with self.assertRaises(PathSafetyError):
            resolve_beneath(self.run_root, self.temp / "absolute")

    def test_workspace_initializes_immutable_metadata_outside_project(self) -> None:
        context = create_run_context(
            self.project,
            self.run_root,
            "20260619T120000Z-1234abcd",
        )
        workspace = RunWorkspace(context)
        workspace.initialize("0.4.0-dev")
        metadata = workspace.load_metadata()
        self.assertEqual(context.run_id, metadata["run_id"])
        self.assertEqual(str(self.project.resolve()), metadata["project_root"])
        self.assertTrue(workspace.metadata_path.is_file())
        self.assertFalse((self.project / ".sdd").exists())
        with self.assertRaises(WorkspaceError):
            workspace.initialize("0.4.0-dev")

    def test_workspace_state_journal_and_evidence_are_isolated(self) -> None:
        context = create_run_context(
            self.project,
            self.run_root,
            "20260619T120001Z-1234abcd",
        )
        workspace = RunWorkspace(context)
        workspace.initialize("0.4.0-dev")
        workspace.save_state({"sequence": 0, "status": "created"})
        self.assertEqual(1, workspace.append_event({"event": "created"}))
        self.assertEqual(2, workspace.append_event({"event": "inspected"}))
        evidence = workspace.write_evidence("baseline", "git-status", "clean\n")
        events = [
            json.loads(line)
            for line in workspace.journal_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual([1, 2], [event["sequence"] for event in events])
        self.assertEqual("run", evidence.namespace)
        self.assertEqual("evidence/baseline/git-status.log", evidence.path)
        self.assertTrue((workspace.run_dir / evidence.path).is_file())
        self.assertEqual([], git(self.project, "status", "--porcelain").splitlines())

    def test_workspace_initialization_captures_dirty_input_in_isolated_work_project(self) -> None:
        tracked = self.project / "tracked.txt"
        tracked.write_text("dirty tracked\n", encoding="utf-8")
        untracked = self.project / "new input.txt"
        untracked.write_text("dirty untracked\n", encoding="utf-8")
        services = create_runtime_services(
            self.project,
            run_root=self.run_root,
            run_id="20260619T120010Z-1234abcd",
        )
        snapshot = services.workspace.initialize("0.4.0-dev")
        work_repo = services.work_repository()
        self.assertEqual("dirty tracked\n", (services.workspace.work_project_root / "tracked.txt").read_text(encoding="utf-8"))
        self.assertEqual("dirty untracked\n", (services.workspace.work_project_root / "new input.txt").read_text(encoding="utf-8"))
        self.assertEqual("dirty tracked\n", tracked.read_text(encoding="utf-8"))
        self.assertEqual("dirty untracked\n", untracked.read_text(encoding="utf-8"))
        self.assertTrue(work_repo.status().clean)
        self.assertEqual(snapshot.baseline_commit, work_repo.head())
        self.assertEqual(snapshot.run_branch, work_repo.branch())
        metadata = services.workspace.load_metadata()
        self.assertEqual(str(self.project.resolve()), metadata["project_root"])
        self.assertEqual(str(services.workspace.work_project_root.resolve()), metadata["work_project_root"])
        self.assertEqual(snapshot.baseline_commit, metadata["baseline_commit"])
        self.assertEqual(snapshot.run_branch, metadata["run_branch"])

    def test_workspace_initialization_preserves_clean_input_and_branches_work_copy(self) -> None:
        services = create_runtime_services(
            self.project,
            run_root=self.run_root,
            run_id="20260619T120011Z-1234abcd",
        )
        snapshot = services.workspace.initialize("0.4.0-dev")
        work_repo = services.work_repository()
        self.assertTrue(work_repo.status().clean)
        self.assertEqual(snapshot.run_branch, work_repo.branch())
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), snapshot.source_head)
        self.assertEqual([], git(self.project, "status", "--porcelain").splitlines())

    def test_repository_reports_staged_unstaged_and_untracked_changes(self) -> None:
        repository = Repository(self.project)
        tracked = self.project / "tracked.txt"
        tracked.write_text("staged\n", encoding="utf-8")
        git(self.project, "add", "tracked.txt")
        tracked.write_text("unstaged\n", encoding="utf-8")
        (self.project / "new file.txt").write_text("new\n", encoding="utf-8")
        status = repository.status()
        self.assertEqual(("tracked.txt",), status.staged)
        self.assertEqual(("tracked.txt",), status.unstaged)
        self.assertEqual(("new file.txt",), status.untracked)
        self.assertFalse(status.clean)

    def test_repository_identity_and_relative_paths(self) -> None:
        repository = Repository(self.project)
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), repository.head())
        self.assertIsNotNone(repository.branch())
        self.assertEqual("tracked.txt", repository.relative_path(self.project / "tracked.txt"))
        self.assertTrue(repository.git_common_dir().exists())

    def test_repository_lock_key_uses_canonical_git_identity(self) -> None:
        repository = Repository(self.project)
        common = repository.git_common_dir()
        self.assertEqual(repository_lock_key(common), repository_lock_key(common / "."))

    def test_same_run_cannot_be_locked_twice(self) -> None:
        services = create_runtime_services(
            self.project,
            run_root=self.run_root,
            run_id="20260619T120002Z-1234abcd",
        )
        services.workspace.initialize("0.4.0-dev")
        with services.locks():
            with self.assertRaises(WorkspaceError):
                with services.locks():
                    pass

    def test_two_runs_cannot_lock_the_same_repository(self) -> None:
        first = create_runtime_services(
            self.project,
            run_root=self.run_root,
            run_id="20260619T120003Z-1234abcd",
        )
        second = create_runtime_services(
            self.project,
            run_root=self.run_root,
            run_id="20260619T120004Z-1234abcd",
        )
        first.workspace.initialize("0.4.0-dev")
        second.workspace.initialize("0.4.0-dev")
        with first.locks():
            with self.assertRaises(WorkspaceError):
                with second.locks():
                    pass

    def test_different_repositories_can_be_locked_together(self) -> None:
        second_project = self.temp / "second-project"
        second_project.mkdir()
        git(second_project, "init")
        git(second_project, "config", "user.name", "Infrastructure Test")
        git(second_project, "config", "user.email", "infra@example.invalid")
        (second_project / "tracked.txt").write_text("initial\n", encoding="utf-8")
        git(second_project, "add", "--all")
        git(second_project, "commit", "-m", "initial")
        first = create_runtime_services(
            self.project,
            run_root=self.run_root,
            run_id="20260619T120005Z-1234abcd",
        )
        second = create_runtime_services(
            second_project,
            run_root=self.run_root,
            run_id="20260619T120006Z-1234abcd",
        )
        first.workspace.initialize("0.4.0-dev")
        second.workspace.initialize("0.4.0-dev")
        with first.locks(), second.locks():
            self.assertTrue(True)

    def test_run_lock_is_released_when_repository_lock_fails(self) -> None:
        services = create_runtime_services(
            self.project,
            run_root=self.run_root,
            run_id="20260619T120007Z-1234abcd",
        )
        services.workspace.initialize("0.4.0-dev")
        lock_set = services.locks()
        with mock.patch.object(lock_set.repository_lock, "acquire", side_effect=WorkspaceError("busy")):
            with self.assertRaises(WorkspaceError):
                with lock_set:
                    pass
        with FileLock(lock_set.run_lock.path, {"pid": os.getpid(), "run_id": "replacement", "token": "new"}):
            self.assertTrue(lock_set.run_lock.path.exists())

    def test_config_rejects_unknown_and_mistyped_fields(self) -> None:
        with self.assertRaises(ConfigurationError):
            build_effective_runtime({"budget": {"max_agent_invocations": 5}})
        with self.assertRaises(ConfigurationError):
            build_effective_runtime({"budget": {"maximum_agent_invocations": True}})
        with self.assertRaises(ConfigurationError):
            build_effective_runtime({"timeouts": {"agent_seconds": 1}})

    def test_policy_cannot_relax_agent_safety_invariants(self) -> None:
        unsafe_overrides = [
            {"changes": {"allow_binary_files": True}},
            {"changes": {"allow_dependency_changes": True}},
            {"changes": {"allow_public_api_changes": True}},
            {"agent": {"allow_network": True}},
            {"agent": {"allow_commits": True}},
            {"agent": {"allow_state_changes": True}},
            {"agent": {"allow_policy_changes": True}},
            {"agent": {"require_exact_result_schema": False}},
            {"agent": {"restore_on_invalid_result": False}},
            {"agent": {"restore_on_scope_violation": False}},
        ]
        for override in unsafe_overrides:
            with self.subTest(override=override):
                with self.assertRaises(ConfigurationError):
                    build_effective_runtime(policy_override=override)

    def test_overrides_cannot_increase_default_budgets_or_change_limits(self) -> None:
        with self.assertRaises(ConfigurationError):
            build_effective_runtime({"budget": {"maximum_agent_invocations": 31}})
        with self.assertRaises(ConfigurationError):
            build_effective_runtime({"timeouts": {"agent_seconds": 1201}})
        with self.assertRaises(ConfigurationError):
            build_effective_runtime(policy_override={"changes": {"maximum_changed_files": 51}})
        with self.assertRaises(ConfigurationError):
            build_effective_runtime({"git": {"auto_commit": False}})

    def test_policy_allows_stricter_change_limits(self) -> None:
        runtime = build_effective_runtime(
            config_override={"budget": {"maximum_agent_invocations": 10}},
            policy_override={
                "changes": {
                    "maximum_changed_files": 5,
                    "maximum_added_lines": 500,
                }
            },
        )
        self.assertEqual(10, runtime.config["budget"]["maximum_agent_invocations"])
        self.assertEqual(5, runtime.policy["changes"]["maximum_changed_files"])
        self.assertEqual(500, runtime.policy["changes"]["maximum_added_lines"])

    def test_frozen_runtime_detects_tampering(self) -> None:
        context = create_run_context(
            self.project,
            self.run_root,
            "20260619T120008Z-1234abcd",
        )
        workspace = RunWorkspace(context)
        workspace.initialize("0.4.0-dev")
        runtime = build_effective_runtime()
        hashes = freeze_effective_runtime(workspace.run_dir, runtime)
        verified = verify_frozen_runtime(workspace.run_dir, hashes)
        self.assertEqual(runtime.config, verified.config)
        policy_path = workspace.run_dir / "effective-policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["changes"]["maximum_changed_files"] = 49
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        with self.assertRaises(WorkspaceError):
            verify_frozen_runtime(workspace.run_dir, hashes)

    def test_runtime_services_start_with_validated_defaults(self) -> None:
        services = create_runtime_services(
            self.project,
            run_root=self.run_root,
            run_id="20260619T120009Z-1234abcd",
            config={"executor": {"name": "fixture"}},
        )
        self.assertEqual("fixture", services.config["executor"]["name"])
        self.assertFalse(services.policy["agent"]["allow_commits"])
        services.workspace.initialize("0.4.0-dev")
        hashes = services.freeze_runtime()
        self.assertEqual({"config_sha256", "policy_sha256"}, set(hashes))

    @unittest.skipIf(os.name == "nt", "POSIX symlink semantics")
    def test_symlink_escape_is_rejected(self) -> None:
        self.run_root.mkdir()
        outside = self.temp / "outside"
        outside.mkdir()
        (self.run_root / "link").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(PathSafetyError):
            resolve_beneath(self.run_root, "link/file.json")


if __name__ == "__main__":
    unittest.main()
