#!/usr/bin/env python3
"""Autonomous SDD runner.

The runner intentionally uses only the Python standard library. Files ending in
.yaml are JSON-compatible YAML so they can be parsed deterministically without
installing dependencies on a competition machine.
"""

from __future__ import annotations

import argparse
import datetime as dt
import errno
import fnmatch
import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def _add_repo_root_to_sys_path() -> None:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "autonomous_sdd").is_dir():
            sys.path.insert(0, str(candidate))
            return


_add_repo_root_to_sys_path()

from autonomous_sdd import create_runtime_services
from autonomous_sdd.repository import Repository


VERSION = "0.3.0"
MIN_APPLY_TASKS = 3
MAX_APPLY_TASKS = 20
DEFAULT_COMPETITION_GOAL = (
    "Modify the target C++ packaging project to support custom header payload content provided by a parameter "
    "while preserving unpack correctness, build entry compatibility, original CLI compatibility, skill delivery, "
    "and verification completeness."
)
DEFAULT_COMPETITION_CONSTRAINTS = [
    "Support custom header payload content through a parameter.",
    "The custom header payload content length is variable and must be parsed correctly.",
    "Unpack must still work correctly after customization.",
    "The project build entrypoint must remain unchanged.",
    "The original packaging tool invocation must still work with its original arguments.",
    "Backward compatibility with existing callers must be preserved.",
    "A callable skill for the packaging tool must be delivered.",
    "The skill must support THX-related handling and header inspection.",
    "Reasonable tests must be designed from the codebase structure to validate the change.",
]
DEFAULT_ACCEPTANCE_INVARIANTS = [
    "variable_length_header_payload",
    "successful_unpack_after_customization",
    "unchanged_build_entrypoint",
    "original_cli_compatibility",
    "skill_delivery_required",
    "validation_tests_required",
]
DEFAULT_TOOLING_INTEGRATION_CONSTRAINTS = {
    "formatter_tool_available": False,
    "formatter_invocation_hint": None,
    "formatter_expected_evidence": None,
    "optional_tooling": [
        "future internal formatter/checker may arrive via MCP",
        "future internal formatter/checker may arrive via Skill",
        "future internal formatter/checker may arrive via IDEA plugin integration",
    ],
}
COMPETITION_REQUIREMENT_THEMES = {
    "custom_header_payload": [
        "custom header",
        "header payload",
        "variable-length header",
        "variable length header",
        "variable_length_header_payload",
        "custom_header_payload",
    ],
    "unpack_correctness": [
        "unpack",
        "successful_unpack_after_customization",
        "unpack_correctness",
    ],
    "compatibility": [
        "compatibility",
        "backward compatibility",
        "backward-compatible",
        "source-compatible",
        "existing callers",
        "legacy cli",
        "original cli",
        "build entrypoint",
        "original_cli_compatibility",
        "unchanged_build_entrypoint",
    ],
    "skill_delivery": [
        "skill",
        "header inspection",
        "thx",
        "skill_delivery_required",
        "validation_tests_required",
    ],
}
STAGES = [
    "brainstorm",
    "proposal",
    "specs",
    "design",
    "tasks",
    "plan",
    "apply",
    "review",
    "verify",
    "finalize",
    "archive",
    "retrospective",
    "closed",
]
ARTIFACTS = {
    "brainstorm": "brainstorm.md",
    "proposal": "proposal.md",
    "design": "design.md",
    "tasks": "tasks.md",
    "plan": "plan.md",
    "review": "review.md",
    "verify": "verify.md",
    "finalize": "finalize.md",
}
REQUIRED_SECTIONS = {
    "brainstorm": ["Objective", "Scope", "Alternatives", "Decision"],
    "proposal": ["Why", "What Changes", "Capabilities", "Impact"],
    "design": [
        "Context",
        "Goals",
        "Non-Goals",
        "Existing API Verification",
        "Decisions",
        "Testing Strategy",
    ],
    "plan": ["Execution Strategy", "Tasks", "Verification", "Checkpoint Strategy"],
    "review": ["Scope Compliance", "Specification Compliance", "Clean Code Review", "Findings", "Decision"],
    "verify": ["Structural Validation", "Requirement Traceability", "Quality Gates", "Decision"],
    "finalize": ["Outcome", "Repository State", "Evidence"],
}


class SddError(RuntimeError):
    pass


class SddExit(SddError):
    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SddError(f"Missing required file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SddError(f"Invalid JSON-compatible YAML in {path}: {exc}") from exc


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def run_command(
    args: list[str],
    cwd: Path,
    *,
    timeout: int = 600,
    check: bool = False,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    executable = shutil.which(args[0])
    if executable:
        args = [executable, *args[1:]]
    process = subprocess.Popen(
        args,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        shell=False,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
    )
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        terminate_process_tree(process)
        stdout, _ = process.communicate()
        raise subprocess.TimeoutExpired(args, timeout, output=stdout)
    result = subprocess.CompletedProcess(args, process.returncode, stdout, None)
    if check and result.returncode != 0:
        raise SddError(f"Command failed ({result.returncode}): {' '.join(args)}\n{result.stdout or ''}")
    return result


def terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def project_root(value: str | None) -> Path:
    root = Path(value or ".").resolve()
    if not root.exists():
        raise SddError(f"Project does not exist: {root}")
    return root


def sdd_dir(root: Path) -> Path:
    return root / ".sdd"


def state_path(root: Path) -> Path:
    return sdd_dir(root) / "runtime" / "state.json"


def load_state(root: Path) -> dict[str, Any]:
    return load_json(state_path(root))


def save_state(root: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now()
    atomic_json(state_path(root), state)


def load_config(root: Path) -> dict[str, Any]:
    return load_json(sdd_dir(root) / "config.yaml")


def save_config(root: Path, config: dict[str, Any]) -> None:
    atomic_json(sdd_dir(root) / "config.yaml", config)


def budget_value(root: Path, key: str, default: int) -> int:
    budget = load_config(root).get("budget", {})
    value = budget.get(key, default)
    return value if isinstance(value, int) and value > 0 else default


def agent_timeout_for_state(root: Path, state: dict[str, Any]) -> int:
    config = load_config(root)
    timeouts = config.get("timeouts", {})
    stage_timeouts = timeouts.get("stage_agent_seconds", {})
    stage = state.get("stage", "")
    if isinstance(stage_timeouts, dict):
        value = stage_timeouts.get(stage)
        if isinstance(value, int) and value > 0:
            return value
    fallback = timeouts.get("agent_seconds", 1200)
    return fallback if isinstance(fallback, int) and fallback > 0 else 1200


def normalize_failure_signature(stage: str, message: str) -> str:
    text = (message or "").strip()
    if "timed out after" in text.lower():
        return f"{stage}:agent_timeout"
    if "opencode invocation failed" in text.lower():
        return f"{stage}:agent_exit_nonzero"
    if text.startswith("Gate failed:"):
        lines = [line.strip() for line in text.splitlines()[1:] if line.strip()]
        normalized = " | ".join(lines[:3]) if lines else "gate_failed"
        return f"{stage}:gate:{normalized}"
    first = text.splitlines()[0].strip().lower() if text else "unknown"
    first = re.sub(r"\s+", " ", first)
    return f"{stage}:{first[:160]}"


def record_failure_signature(root: Path, state: dict[str, Any], message: str) -> tuple[str, int]:
    signature = normalize_failure_signature(state["stage"], message)
    signatures = state.setdefault("failure_signatures", {})
    count = int(signatures.get(signature, 0)) + 1
    signatures[signature] = count
    state["last_failure_signature"] = signature
    append_journal(
        root,
        {
            "event": "failure_signature_recorded",
            "stage": state["stage"],
            "signature": signature,
            "count": count,
        },
    )
    return signature, count


def stage_retry_budget(root: Path) -> int:
    return budget_value(root, "maximum_stage_retries", 1)


def clear_failure_signature(root: Path, state: dict[str, Any], stage: str) -> None:
    prefix = f"{stage}:"
    signatures = state.setdefault("failure_signatures", {})
    for key in list(signatures):
        if key.startswith(prefix):
            signatures.pop(key, None)
    if isinstance(state.get("last_failure_signature"), str) and state["last_failure_signature"].startswith(prefix):
        state.pop("last_failure_signature", None)


def process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return windows_process_is_alive(pid)
    try:
        os.kill(pid, 0)
        return True
    except OSError as exc:
        # ESRCH is the only portable indication that the process is absent.
        # EPERM and unexpected failures mean the process may still own a lock.
        return exc.errno != errno.ESRCH


def windows_process_is_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    error_invalid_parameter = 87
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    open_process.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = open_process(process_query_limited_information, False, pid)
    if handle:
        close_handle(handle)
        return True

    # ERROR_INVALID_PARAMETER is the documented result for a nonexistent PID.
    # Access denial and unexpected query failures are treated conservatively:
    # reclaiming a lock owned by a process we cannot inspect is unsafe.
    return ctypes.get_last_error() != error_invalid_parameter


@contextmanager
def linux_execution_lock(root: Path):
    import fcntl

    path = sdd_dir(root) / "runtime" / "execution.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            try:
                owner = load_json(path)
                pid = int(owner.get("pid", -1))
            except (SddError, TypeError, ValueError):
                pid = -1
            raise SddError(f"Another Runner owns the workspace lock: pid={pid}") from exc

        os.ftruncate(descriptor, 0)
        payload = json.dumps({"pid": os.getpid(), "created_at": now()}).encode("utf-8")
        written = 0
        while written < len(payload):
            written += os.write(descriptor, payload[written:])
        os.fsync(descriptor)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@contextmanager
def pid_file_execution_lock(root: Path):
    path = sdd_dir(root) / "runtime" / "execution.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + 5
    while True:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"pid": os.getpid(), "created_at": now()}, stream)
            break
        except FileExistsError:
            try:
                owner = load_json(path)
                pid = int(owner.get("pid", -1))
            except (SddError, TypeError, ValueError):
                pid = -1
            if process_is_alive(pid):
                if time.time() < deadline:
                    time.sleep(0.1)
                    continue
                raise SddError(f"Another Runner owns the workspace lock: pid={pid}")
            path.unlink(missing_ok=True)
    try:
        yield
    finally:
        try:
            owner = load_json(path)
            if int(owner.get("pid", -1)) == os.getpid():
                path.unlink(missing_ok=True)
        except (SddError, TypeError, ValueError):
            pass


@contextmanager
def execution_lock(root: Path):
    lock = linux_execution_lock if sys.platform.startswith("linux") else pid_file_execution_lock
    with lock(root):
        yield


def validate_execution_preflight(root: Path, state: dict[str, Any]) -> None:
    changed = [
        path
        for path in git_changed(root)
        if path != f"openspec/changes/{state['change_id']}/.openspec.yaml"
        and not (state.get("stage") == "apply" and is_ephemeral_build_output(path))
    ]
    if state.get("status") == "running" and changed:
        raise SddError(
            "Running state must start from a clean verified checkpoint; unexpected changes:\n- "
            + "\n- ".join(changed)
        )


def change_dir(root: Path, state: dict[str, Any]) -> Path:
    return root / "openspec" / "changes" / state["change_id"]


def rel(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def matches(path: str, patterns: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns)


def git(root: Path, *args: str, check: bool = True) -> str:
    result = run_command(["git", *args], root, timeout=120, check=check)
    return (result.stdout or "").strip()


def git_head(root: Path) -> str:
    return git(root, "rev-parse", "HEAD")


def git_changed(root: Path) -> list[str]:
    result = run_command(["git", "status", "--porcelain"], root, timeout=120)
    output = result.stdout or ""
    changed: list[str] = []
    for line in output.splitlines():
        if len(line) >= 4:
            path = line[3:].strip().replace("\\", "/")
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            path = path.strip('"')
            target = root / path
            if path.endswith("/") and target.is_dir():
                changed.extend(
                    rel(root, child)
                    for child in target.rglob("*")
                    if child.is_file() and ".git" not in child.parts
                )
            else:
                changed.append(path)
    return sorted(set(changed))


def is_ephemeral_build_output(path: str) -> bool:
    normalized = path.replace("\\", "/").strip("./")
    if normalized.startswith(
        ("build/", "build\\", "out/", "out\\", "dist/", "dist\\", "sdd/tmp/focused-build/")
    ):
        return True
    if normalized.endswith((".obj", ".o", ".a", ".so", ".dll", ".dylib", ".exe", ".class")):
        return True
    return False


def is_substantive_sdd_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.startswith(".sdd/skills/")


def git_path_changed_from_head(root: Path, path: str) -> bool:
    normalized = path.replace("\\", "/")
    if not (root / normalized).is_file():
        return False
    tracked = run_command(["git", "ls-files", "--error-unmatch", normalized], root, timeout=120, check=False)
    if tracked.returncode != 0:
        return True
    diff = run_command(["git", "diff", "--quiet", "HEAD", "--", normalized], root, timeout=120, check=False)
    return diff.returncode != 0


def substantive_changed_paths(root: Path) -> list[str]:
    changed = [
        path
        for path in git_changed(root)
        if not path.startswith("openspec/")
        and (not path.startswith(".sdd/") or is_substantive_sdd_path(path))
        and not is_ephemeral_build_output(path)
    ]
    skills_root = sdd_dir(root) / "skills"
    if skills_root.exists():
        for path in skills_root.rglob("*"):
            if not path.is_file():
                continue
            relative = rel(root, path)
            if is_substantive_sdd_path(relative) and git_path_changed_from_head(root, relative):
                changed.append(relative)
    return sorted(set(changed))


def policy_files(root: Path) -> list[Path]:
    return sorted((sdd_dir(root) / "policy").glob("*.yaml"))


def schema_files(root: Path) -> list[Path]:
    return sorted((root / "openspec" / "schemas").rglob("*.*"))


def protected_control_hashes(root: Path) -> dict[str, str]:
    files = policy_files(root) + schema_files(root)
    files.extend([sdd_dir(root) / "config.yaml", sdd_dir(root) / "AGENT-INSTRUCTIONS.md"])
    return {rel(root, path): sha256(path) for path in files if path.is_file()}


def capture_files(root: Path, patterns: list[str]) -> dict[str, str]:
    captured: dict[str, str] = {}
    for path in root.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            relative = rel(root, path)
            if matches(relative, patterns):
                captured[relative] = sha256(path)
    return dict(sorted(captured.items()))


def verify_hashes(root: Path, expected: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for name, expected_hash in expected.items():
        path = root / name
        if not path.is_file():
            errors.append(f"missing:{name}")
        elif sha256(path) != expected_hash:
            errors.append(f"changed:{name}")
    return errors


def write_evidence(root: Path, name: str, command: list[str], result: subprocess.CompletedProcess[str]) -> str:
    evidence = sdd_dir(root) / "evidence" / f"{name}.log"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(
        f"$ {' '.join(command)}\nexit_code={result.returncode}\n\n{result.stdout or ''}",
        encoding="utf-8",
    )
    return rel(root, evidence)


def init_project(args: argparse.Namespace) -> None:
    root = Path(args.project).resolve()
    root.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parent.parent / "assets" / "project-skeleton"
    if (root / ".sdd").exists() and not args.force:
        raise SddError(f"{root} is already initialized; use --force only before a competition run")
    preserve = {"AGENTS.md", "opencode.json", "openspec/config.yaml", ".gitignore"}
    for source_path in source.rglob("*"):
        relative = source_path.relative_to(source)
        target = root / relative
        if source_path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if relative.as_posix() in preserve and target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target)
    ensure_runtime_ignores(root)
    target_runner = root / ".sdd" / "bin" / "sdd.py"
    target_runner.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__).resolve(), target_runner)
    (root / "sdd.cmd").write_text('@echo off\r\npython "%~dp0.sdd\\bin\\sdd.py" --project "%~dp0." %*\r\n', encoding="utf-8")
    (root / "sdd").write_text(
        '#!/usr/bin/env sh\nexec python3 "$(dirname "$0")/.sdd/bin/sdd.py" --project "$(dirname "$0")" "$@"\n',
        encoding="utf-8",
    )
    if not (root / ".git").exists():
        git(root, "init")
    print(f"Initialized Autonomous SDD {VERSION} in {root}")
    print("Run `sdd compete --task <file-or-text>` for one-command delivery.")


def ensure_runtime_ignores(root: Path) -> None:
    path = root / ".gitignore"
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    required = [".sdd/runtime/", ".sdd/evidence/", ".sdd/baseline/", ".sdd/bin/", ".opencode/storage/"]
    missing = [entry for entry in required if entry not in current.splitlines()]
    if missing:
        separator = "" if not current or current.endswith("\n") else "\n"
        path.write_text(
            current + separator + "\n# Autonomous SDD machine-local state\n" + "\n".join(missing) + "\n",
            encoding="utf-8",
        )


def compete(args: argparse.Namespace) -> None:
    source_root = project_root(args.project)
    objective_bundle = resolve_competition_objective(getattr(args, "task", None), source_root)
    objective = objective_bundle["effective_objective"]
    if not (source_root / ".sdd").exists():
        init_project(argparse.Namespace(project=str(source_root), force=False))
    active_root = active_runtime_root(source_root)
    if active_root is not None:
        assert_compete_request_matches_active_run(
            active_root,
            objective=objective,
            change_id=args.change_id,
            executor=args.executor,
        )
        try:
            run_loop(argparse.Namespace(project=str(active_root), max_steps=args.max_steps))
        except Exception:
            if load_state(active_root).get("status") in {"closed", "blocked"}:
                final = finalize_competition_run(active_root, source_root)
                raise_compete_result(active_root, final)
            raise
        final = finalize_competition_run(active_root, source_root)
        raise_compete_result(active_root, final)
        return
    ensure_git_identity(source_root)
    detected = detect_project(source_root)
    configure_detected_project(source_root, detected)
    config = load_config(source_root)
    config["executor"] = args.executor
    save_config(source_root, config)
    if not git(source_root, "rev-parse", "--verify", "HEAD", check=False):
        commit_all(source_root, "chore: establish competition baseline")
    else:
        commit_all(source_root, "chore: install autonomous competition harness")
    baseline(argparse.Namespace(project=str(source_root)))
    services = create_runtime_services(source_root)
    root = None
    try:
        with services.locks():
            snapshot = services.workspace.initialize(VERSION)
        write_active_run_locator(
            source_root,
            {
                "run_id": snapshot.work_root.parent.name,
                "run_root": str(snapshot.work_root.parent),
                "work_project_root": str(snapshot.work_root),
                "source_root": str(source_root),
            },
        )
        root = services.workspace.work_project_root
        change_id = unique_change_id(root, args.change_id or slugify(objective))
        start(
            argparse.Namespace(
                project=str(root),
                change_id=change_id,
                objective=objective,
                objective_bundle=objective_bundle,
                source_root=str(snapshot.source_root),
                work_root=str(snapshot.work_root),
                source_head=snapshot.source_head,
                baseline_commit=snapshot.baseline_commit,
                run_branch=snapshot.run_branch,
                source_status=snapshot.source_status.to_dict(),
            )
        )
        run_loop(argparse.Namespace(project=str(root), max_steps=args.max_steps))
    except Exception:
        if root is not None and state_path(root).exists():
            state = load_state(root)
            if state.get("status") in {"closed", "blocked"}:
                final = finalize_competition_run(root, source_root)
                raise_compete_result(root, final)
        elif services.workspace.run_dir.exists():
            shutil.rmtree(services.workspace.run_dir, ignore_errors=True)
            clear_active_run_locator(source_root)
        raise
    else:
        if root is not None and state_path(root).exists():
            final = finalize_competition_run(root, source_root)
            raise_compete_result(root, final)


def mirror_worktree(source: Path, target: Path) -> None:
    if source.resolve() == target.resolve():
        return
    remove_missing_targets(source, target)
    for path in source.rglob("*"):
        if ".git" in path.parts:
            continue
        relative = path.relative_to(source)
        destination = target / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            if destination.exists() or destination.is_symlink():
                if destination.is_dir() and not destination.is_symlink():
                    shutil.rmtree(destination)
                else:
                    destination.unlink()
            destination.symlink_to(os.readlink(path), target_is_directory=path.is_dir())
        else:
            shutil.copy2(path, destination)


def write_active_run_locator(source_root: Path, payload: dict[str, Any]) -> None:
    locator = source_root / ".sdd" / "runtime" / "active-run.json"
    atomic_json(locator, payload)


def clear_active_run_locator(source_root: Path) -> None:
    locator = source_root / ".sdd" / "runtime" / "active-run.json"
    locator.unlink(missing_ok=True)


def load_active_run_locator(project: Path) -> dict[str, Any] | None:
    locator = project / ".sdd" / "runtime" / "active-run.json"
    if not locator.exists():
        return None
    return load_json(locator)


def active_runtime_root(project: Path) -> Path | None:
    try:
        root = resolve_runtime_root(project)
    except SddError:
        return None
    state = load_state(root)
    if state.get("status") in {"closed", "blocked"}:
        if load_active_run_locator(project) is not None:
            clear_active_run_locator(project)
        return None
    return root


def assert_compete_request_matches_active_run(
    root: Path,
    *,
    objective: str,
    change_id: str | None,
    executor: str,
) -> dict[str, Any]:
    state = load_state(root)
    if change_id and state.get("change_id") != change_id:
        raise SddError(f"Active run change_id mismatch: requested {change_id}, active {state.get('change_id')}")
    if state.get("objective") != objective:
        raise SddError("Active run objective does not match the requested competition task")
    active_executor = state.get("executor", load_config(root).get("executor", "opencode"))
    if active_executor != executor:
        raise SddError(f"Active run executor mismatch: requested {executor}, active {active_executor}")
    return state


def finalize_competition_run(root: Path, source_root: Path) -> dict[str, Any]:
    state = load_state(root)
    drift = source_workspace_drift(root, state, source_root)
    if drift:
        state["status"] = "blocked"
        state["blocking_reason"] = (
            "Source workspace changed since run started; cannot materialize final state automatically:\n- "
            + "\n- ".join(drift)
        )
        save_state(root, state)
    report = emit_final_report(root, state)
    if git_changed(root):
        final_commit = commit_all(root, f"sdd({state['change_id']}): record final delivery")
        state["delivery_report"] = rel(root, report)
        state["delivery_commit"] = final_commit
        save_state(root, state)
        state = load_state(root)
    if drift:
        return state
    mirror_worktree(root, source_root)
    source_delivery_commit = git_head(source_root)
    if git_changed(source_root):
        ensure_git_identity(source_root)
        source_delivery_commit = commit_all(source_root, f"sdd({state['change_id']}): materialize final delivery")
    state["delivery_commit"] = source_delivery_commit
    state["delivery_report"] = ".sdd/delivery-report.md"
    save_state(root, state)
    if root.resolve() != source_root.resolve():
        save_state(source_root, state)
    clear_active_run_locator(source_root)
    return state


def source_workspace_drift(root: Path, state: dict[str, Any], source_root: Path) -> list[str]:
    if root.resolve() == source_root.resolve():
        return []
    if not source_root.exists():
        return [f"source workspace is missing: {source_root}"]
    repository = Repository(source_root)
    expected_head = state.get("source_head")
    current_head = repository.head()
    expected_status = normalize_repository_status(state.get("source_status"))
    current_status = normalize_repository_status(repository.status().to_dict())
    drift: list[str] = []
    if current_head != expected_head:
        drift.append(f"source HEAD changed from {expected_head} to {current_head}")
    if current_status != expected_status:
        drift.append("source workspace status changed since run start")
    return drift


def normalize_repository_status(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        value = {}
    normalized: dict[str, list[str]] = {}
    for key in ("staged", "unstaged", "untracked", "conflicted"):
        entries = value.get(key, [])
        if not isinstance(entries, list):
            entries = []
        normalized[key] = sorted(str(entry).replace("\\", "/") for entry in entries)
    return normalized


def compete_result_payload(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    if state_path(root).exists():
        merged_state = load_state(root)
        merged_state.update(state)
        state = merged_state
    decision = compete_decision(state)
    report_root = Path(str(state.get("source_root") or root))
    if not (sdd_dir(report_root) / "delivery-report.md").exists():
        report_root = root
    report_path = sdd_dir(report_root) / "delivery-report.md"
    return {
        "kind": "competition_result",
        "status": state["status"],
        "outcome": terminal_outcome(state),
        "run_id": state["run_id"],
        "change_id": state["change_id"],
        "objective": state["objective"],
        "report": str(report_path),
        "delivery_commit": state.get("delivery_commit"),
        "decision": decision["decision"],
        "reason": decision["reason"],
        "recommended_action": decision["recommended_action"],
        "exit_code": compete_exit_code(decision),
    }


def emit_compete_result(root: Path, state: dict[str, Any]) -> None:
    payload = compete_result_payload(root, state)
    print(f"RESULT={payload['status'].upper()}")
    print(f"REPORT={payload['report']}")
    print(json.dumps(payload, ensure_ascii=False))


def compete_decision(state: dict[str, Any]) -> dict[str, str]:
    status = state["status"]
    if status == "closed":
        outcome = terminal_outcome(state)
        if outcome == "closed_partial":
            return {
                "decision": "completed_partial",
                "reason": state.get(
                    "blocking_reason",
                    "Competition run closed with partial evidence after automated recovery exhausted retries.",
                ),
                "recommended_action": "none",
            }
        if outcome == "closed_fail":
            return {
                "decision": "completed_fail",
                "reason": state.get(
                    "blocking_reason",
                    "Competition run closed with failure evidence after automated recovery exhausted retries.",
                ),
                "recommended_action": "none",
            }
        return {
            "decision": "completed",
            "reason": "Competition run completed and final delivery was materialized.",
            "recommended_action": "none",
        }
    if status == "blocked":
        return {
            "decision": "blocked",
            "reason": state.get("blocking_reason", "Competition run stopped in a blocked state."),
            "recommended_action": "manual_repair",
        }
    return {
        "decision": "manual_review",
        "reason": f"Competition run ended in unexpected status: {status}",
        "recommended_action": "manual_review",
    }


def compete_exit_code(decision: dict[str, str]) -> int:
    mapping = {
        "completed": 0,
        "completed_partial": 0,
        "completed_fail": 0,
        "blocked": 4,
        "manual_review": 2,
    }
    return mapping.get(decision["decision"], 2)


def raise_compete_result(root: Path, state: dict[str, Any]) -> None:
    payload = compete_result_payload(root, state)
    print(f"RESULT={payload['status'].upper()}")
    print(f"REPORT={payload['report']}")
    print(json.dumps(payload, ensure_ascii=False))
    if payload["exit_code"] != 0:
        raise SddExit(payload["reason"], payload["exit_code"])


def resolve_resume_root(project: Path) -> Path:
    locator = load_active_run_locator(project)
    if locator is not None:
        work_root = Path(str(locator["work_project_root"]))
        if work_root.exists() and state_path(work_root).exists():
            return work_root
        raise SddError(f"Recorded work copy is missing: {work_root}")
    if state_path(project).exists():
        return project
    raise SddError(f"No resumable run found in {project}")


def resume(args: argparse.Namespace) -> None:
    project = project_root(args.project)
    locator = load_active_run_locator(project)
    root = resolve_resume_root(project)
    with execution_lock(root):
        state = load_state(root)
        if state["status"] in {"closed", "blocked"}:
            print(json.dumps(state, indent=2, ensure_ascii=False))
            if locator is not None:
                clear_active_run_locator(Path(str(locator["source_root"])))
            return
        if state.get("pending_action") == "gate":
            gate_and_advance(root)
            if load_state(root)["status"] in {"closed", "blocked"} and locator is not None:
                clear_active_run_locator(Path(str(locator["source_root"])))
            return
        if state.get("pending_action") != "execute_stage":
            raise SddError(f"Cannot resume run with pending action: {state.get('pending_action')}")
        if state["status"] == "repair_required":
            restore_verified_checkpoint(root, state)
            state["status"] = "running"
            save_state(root, state)
        if state["status"] != "running":
            raise SddError(f"Cannot resume run in status: {state['status']}")
        if not args.dry_run:
            validate_execution_preflight(root, state)
        execute_stage(root, state, args.dry_run)
        if args.dry_run:
            return
        state = load_state(root)
        state["pending_action"] = "gate"
        save_state(root, state)
        gate_and_advance(root)
        if load_state(root)["status"] in {"closed", "blocked"} and locator is not None:
            clear_active_run_locator(Path(str(locator["source_root"])))


def remove_missing_targets(source: Path, target: Path) -> None:
    for path in sorted(target.rglob("*"), reverse=True):
        if ".git" in path.parts:
            continue
        relative = path.relative_to(target)
        if (source / relative).exists() or (source / relative).is_symlink():
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()


def restore_verified_checkpoint(root: Path, state: dict[str, Any]) -> None:
    commit = state.get("last_verified_commit")
    if not commit:
        raise SddError("Cannot restore workspace without last_verified_commit")
    if not git_changed(root):
        return
    git(root, "reset", "--hard", commit)
    git(root, "clean", "-fd")
    directory = change_dir(root, state)
    directory.mkdir(parents=True, exist_ok=True)
    schema_path = directory / ".openspec.yaml"
    if not schema_path.exists():
        schema_path.write_text("schema: autonomous-superspec\n", encoding="utf-8")
    append_journal(root, {"event": "workspace_restored", "stage": state.get("stage"), "commit": commit})


def detect_project(root: Path) -> dict[str, Any]:
    if (root / "mvnw.cmd").exists():
        return {
            "kind": "java-maven",
            "quick_check": [".\\mvnw.cmd", "-DskipTests", "test-compile"],
            "full_test": [".\\mvnw.cmd", "test"],
            "sources": ["src/**"],
        }
    if (root / "mvnw").exists():
        return {
            "kind": "java-maven",
            "quick_check": ["./mvnw", "-DskipTests", "test-compile"],
            "full_test": ["./mvnw", "test"],
            "sources": ["src/**"],
        }
    if (root / "pom.xml").exists():
        return {
            "kind": "java-maven",
            "quick_check": ["mvn", "-DskipTests", "test-compile"],
            "full_test": ["mvn", "test"],
            "sources": ["src/**"],
        }
    if (root / "gradlew.bat").exists():
        return {
            "kind": "java-gradle",
            "quick_check": [".\\gradlew.bat", "testClasses"],
            "full_test": [".\\gradlew.bat", "test"],
            "sources": ["src/**"],
        }
    if (root / "gradlew").exists():
        return {
            "kind": "java-gradle",
            "quick_check": ["./gradlew", "testClasses"],
            "full_test": ["./gradlew", "test"],
            "sources": ["src/**"],
        }
    if (root / "package.json").exists():
        manager = "pnpm" if (root / "pnpm-lock.yaml").exists() else "npm"
        return {
            "kind": "javascript",
            "quick_check": [],
            "full_test": [manager, "test"],
            "sources": ["src/**", "test/**", "tests/**"],
        }
    python_roots = [
        name
        for name in ("src", "autonomous_sdd", "scripts", "tests", "test")
        if (root / name).exists()
    ]
    has_python_files = any(any((root / name).rglob("*.py")) for name in python_roots)
    if (
        (root / "pyproject.toml").exists()
        or (root / "pytest.ini").exists()
        or (root / "setup.py").exists()
        or (root / "setup.cfg").exists()
        or (root / "requirements.txt").exists()
        or has_python_files
    ):
        return {
            "kind": "python",
            "quick_check": [sys.executable, "-m", "compileall", "-q", *python_roots] if python_roots else [],
            "full_test": [sys.executable, "-m", "pytest"],
            "sources": ["src/**", "test/**", "tests/**"],
        }
    if (root / "go.mod").exists():
        return {
            "kind": "go",
            "quick_check": ["go", "test", "-run", "^$", "./..."],
            "full_test": ["go", "test", "./..."],
            "sources": ["**/*.go"],
        }
    if (root / "Cargo.toml").exists():
        return {
            "kind": "rust",
            "quick_check": ["cargo", "check", "--tests"],
            "full_test": ["cargo", "test"],
            "sources": ["src/**", "tests/**"],
        }
    return {
        "kind": "generic",
        "quick_check": [],
        "full_test": [],
        "sources": ["src/**", "test/**", "tests/**"],
    }


def configure_detected_project(root: Path, detected: dict[str, Any]) -> None:
    verification_path = sdd_dir(root) / "policy" / "verification.yaml"
    verification = load_json(verification_path)
    verification["commands"]["project_quick_check"] = detected["quick_check"]
    verification["commands"]["project_full_test"] = detected["full_test"]
    atomic_json(verification_path, verification)
    project_path = sdd_dir(root) / "policy" / "project.yaml"
    project = load_json(project_path)
    project["detected_project_kind"] = detected["kind"]
    project["change_boundaries"]["allowed"] = detected["sources"]
    atomic_json(project_path, project)


def validate_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return [f"Agent result {field} must be an array of strings"]
    return []


def validate_residual_risks(value: Any) -> list[str]:
    if not isinstance(value, list):
        return ["Agent result residual_risks must be an array"]
    for item in value:
        if isinstance(item, str):
            continue
        if isinstance(item, dict):
            allowed = {"risk", "mitigation"}
            if set(item) - allowed:
                return ["Agent result residual_risks objects may only contain risk and mitigation"]
            if not isinstance(item.get("risk"), str) or not item.get("risk", "").strip():
                return ["Agent result residual_risks object risk must be a non-empty string"]
            mitigation = item.get("mitigation")
            if mitigation is not None and (not isinstance(mitigation, str) or not mitigation.strip()):
                return ["Agent result residual_risks object mitigation must be a non-empty string when provided"]
            continue
        return ["Agent result residual_risks must contain only strings or {risk, mitigation} objects"]
    return []


def allowed_requirement_statuses_for_stage(stage: str) -> set[str]:
    base = {"satisfied", "partial", "partially_satisfied", "not_satisfied"}
    if stage in {"brainstorm", "proposal", "specs", "design", "tasks", "plan", "review"}:
        planning_statuses = {
            "addressed_in_brainstorm",
            "addressed_in_proposal",
            "addressed_in_specs",
            "addressed_in_design",
            "addressed_in_tasks",
            "addressed_in_plan",
            "addressed_in_review",
        }
        return base | planning_statuses | {"planned", "deferred", "specified", "designed", "analyzed"}
    return base


def requirement_status_allowed_for_stage(stage: str, status: str) -> bool:
    allowed = allowed_requirement_statuses_for_stage(stage)
    if status in allowed:
        return True
    if stage in {"brainstorm", "proposal", "specs", "design", "tasks", "plan", "review"}:
        return re.fullmatch(r"[a-z]+(?:_[a-z]+)*_complete", status) is not None
    return False


def requirement_evidence_item_valid_for_stage(stage: str, item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    allowed = {"requirement", "implementation_files", "test_files", "status", "note"}
    if set(item) - allowed:
        return False
    note = item.get("note")
    if note is not None and (not isinstance(note, str) or not note.strip()):
        return False
    return (
        isinstance(item.get("requirement"), str)
        and item["requirement"].strip()
        and isinstance(item.get("implementation_files"), list)
        and all(isinstance(path, str) for path in item["implementation_files"])
        and isinstance(item.get("test_files"), list)
        and all(isinstance(path, str) for path in item["test_files"])
        and isinstance(item.get("status"), str)
        and requirement_status_allowed_for_stage(stage, item["status"])
    )


def is_test_path(path: str) -> bool:
    return path.startswith(("src/test/", "test/", "tests/")) or path.endswith(
        (".spec.js", ".test.js", ".spec.ts", ".test.ts", "_test.go")
    )


def normalize_requirement_evidence_for_stage(
    root: Path, state: dict[str, Any], evidence: Any
) -> Any:
    if not isinstance(evidence, list):
        return evidence
    stage = str(state.get("stage", ""))
    task = current_task(root, state) if stage == "apply" else None
    normalized: list[Any] = []
    for item in evidence:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        current = dict(item)
        if "note" not in current and isinstance(current.get("detail"), str) and current.get("detail", "").strip():
            current["note"] = current["detail"].strip()
        allowed = {"requirement", "implementation_files", "test_files", "status", "note"}
        current = {key: value for key, value in current.items() if key in allowed}
        if stage == "apply" and task is not None and current.get("status") != "satisfied":
            requirement = str(current.get("requirement", ""))
            if not evidence_matches_theme(requirement, task_expected_themes(task)):
                continue
        normalized.append(current)
    return normalized


def validate_agent_result(root: Path, state: dict[str, Any], result: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "deviations" not in result or "blocking_reason" not in result:
        result = dict(result)
        if "deviations" not in result:
            result["deviations"] = []
        if "blocking_reason" not in result:
            result["blocking_reason"] = None
    if isinstance(result.get("deviations"), str) and result.get("deviations", "").strip():
        result = dict(result)
        result["deviations"] = [result["deviations"].strip()]
    if "requirement_evidence" in result:
        result = dict(result)
        result["requirement_evidence"] = normalize_requirement_evidence_for_stage(
            root, state, result.get("requirement_evidence")
        )
    required = {
        "status",
        "summary",
        "files_read",
        "files_changed",
        "commands_run",
        "tests",
        "deviations",
        "blocking_reason",
        "task_id",
        "requirement_evidence",
        "residual_risks",
    }
    tolerated_metadata = {
        "schema_version",
        "run_id",
        "change_id",
        "stage",
        "role",
        "created_at",
        "updated_at",
    }
    extra = sorted(set(result) - required - tolerated_metadata)
    missing = sorted(required - set(result))
    if missing:
        errors.append("Agent result missing fields: " + ", ".join(missing))
    if extra:
        errors.append("Agent result has unsupported fields: " + ", ".join(extra))
    if result.get("status") not in {"completed", "failed", "blocked"}:
        errors.append("Agent result status is invalid")
    if not isinstance(result.get("summary"), str) or not result.get("summary", "").strip():
        errors.append("Agent result summary must be a non-empty string")
    for field in ["files_read", "files_changed", "deviations"]:
        errors.extend(validate_string_list(result.get(field), field))
    errors.extend(validate_residual_risks(result.get("residual_risks")))
    commands = result.get("commands_run")
    if not isinstance(commands, list) or not all(
        isinstance(command, list) and command and all(isinstance(part, str) for part in command)
        for command in commands
    ):
        errors.append("Agent result commands_run must be an array of non-empty string arrays")
    tests = result.get("tests")
    if not isinstance(tests, list) or not all(
        isinstance(test, dict)
        and set(test) == {"command", "exit_code"}
        and isinstance(test["command"], list)
        and test["command"]
        and all(isinstance(part, str) for part in test["command"])
        and isinstance(test["exit_code"], int)
        for test in tests
    ):
        errors.append("Agent result tests must contain only {command: string[], exit_code: integer} objects")
    evidence = result.get("requirement_evidence")
    current_stage = str(state["stage"])
    if not isinstance(evidence, list) or not all(
        requirement_evidence_item_valid_for_stage(current_stage, item) for item in evidence
    ):
        errors.append("Agent result requirement_evidence has an invalid structure")
    elif state["stage"] == "apply":
        if not evidence:
            errors.append("Apply result must include requirement evidence")
        for item in evidence:
            if item["status"] != "satisfied":
                errors.append(f"Requirement is not satisfied: {item['requirement']}")
            for path in item["implementation_files"] + item["test_files"]:
                if not (root / path).is_file():
                    errors.append(f"Requirement evidence file does not exist: {path}")
    return errors


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def evidence_matches_theme(requirement: str, markers: list[str]) -> bool:
    normalized = normalize_text(requirement)
    return any(marker in normalized for marker in markers)


def text_matches_theme(text: str, markers: list[str]) -> bool:
    normalized = normalize_text(text)
    return any(marker in normalized for marker in markers)


def realized_files_match_theme(root: Path, paths: list[str], markers: list[str]) -> bool:
    for path in paths:
        if not isinstance(path, str):
            continue
        candidate = root / path
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        if text_matches_theme(text, markers):
            return True
    return False


def task_expected_themes(task: dict[str, Any] | None) -> list[str]:
    if not task:
        return []
    title = normalize_text(str(task.get("title", "")))
    details = normalize_text(str(task.get("details", "")))
    text = f"{title} {details}".strip()
    expected: list[str] = []
    if any(marker in text for marker in ["custom header", "header payload", "variable-length", "variable length"]):
        expected.append("custom_header_payload")
    if "unpack" in text:
        expected.append("unpack_correctness")
    if any(
        marker in text for marker in ["compatibility", "legacy cli", "original cli", "build entrypoint"]
    ):
        expected.append("compatibility")
    if any(marker in text for marker in ["skill", "header inspection", "thx", "info --json", "info json"]):
        expected.append("skill_delivery")
    return list(dict.fromkeys(expected))


def parse_plan_task_contracts(root: Path, state: dict[str, Any]) -> dict[str, dict[str, str]]:
    path = change_dir(root, state) / "plan.md"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^###\s+Task\s+(?P<task_id>\d+\.\d+)\s*$"
        r"(?P<body>.*?)(?=^###\s+Task\s+\d+\.\d+\s*$|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    contracts: dict[str, dict[str, str]] = {}
    for match in pattern.finditer(text):
        body = match.group("body")
        fields: dict[str, str] = {}
        for name in ("Theme", "Verification", "Evidence", "Implementation Targets", "Test Targets"):
            field_match = re.search(
                rf"^\s*-\s*{re.escape(name)}:\s*(?P<value>.+?)\s*$",
                body,
                re.MULTILINE,
            )
            if field_match:
                fields[name.lower().replace(" ", "_")] = field_match.group("value").strip()
        contracts[match.group("task_id")] = fields
    return contracts


def current_task_contract(root: Path, state: dict[str, Any]) -> dict[str, str] | None:
    task = current_task(root, state)
    if task is None:
        return None
    contracts = parse_plan_task_contracts(root, state)
    resolved = resolve_plan_contract_for_task(task, contracts)
    if resolved is not None:
        return resolved
    return synthesized_task_contract(task)


def synthesized_task_contract(task: dict[str, Any]) -> dict[str, str] | None:
    targets = task_declared_targets(task)
    implementation_targets = [path for path in targets if not is_test_path(path)]
    test_targets = [path for path in targets if is_test_path(path)]
    expected = task_expected_themes(task)
    theme = ", ".join(
        {
            "custom_header_payload": "custom header payload",
            "unpack_correctness": "unpack correctness",
            "compatibility": "original CLI compatibility",
            "skill_delivery": "skill delivery",
        }.get(item, item)
        for item in expected
    )
    verification = " ".join(
        line.strip("- ").strip()
        for line in str(task.get("details", "")).splitlines()
        if line.strip().startswith("-")
    ).strip() or str(task.get("title", "")).strip()
    evidence = verification or str(task.get("title", "")).strip()
    if not implementation_targets and not test_targets and not theme:
        return None
    return {
        "theme": theme or str(task.get("title", "")).strip(),
        "verification": verification,
        "evidence": evidence,
        "implementation_targets": ", ".join(implementation_targets) if implementation_targets else "",
        "test_targets": ", ".join(test_targets) if test_targets else "None (documentation-only change)",
        "_task_id": str(task.get("id", "")),
        "_source": "synthesized",
    }


def contract_is_documentation_only(contract: dict[str, Any] | None) -> bool:
    if not isinstance(contract, dict):
        return False
    return normalize_text(str(contract.get("test_targets", ""))).startswith("none")


def verification_line_is_specific(value: str) -> bool:
    text = normalize_text(value)
    action_markers = ["run", "invoke", "execute", "verify", "check", "compare", "review"]
    object_markers = [
        "pack",
        "unpack",
        "header",
        "payload",
        "compatibility",
        "cli",
        "build",
        "skill",
        "thx",
        "inspection",
        "regression",
    ]
    concrete_targets = (
        "/" in value
        or "`" in value
        or "test_" in text
        or ".md" in text
        or "content review" in text
        or "exit code" in text
    )
    return len(text) >= 20 and any(marker in text for marker in action_markers) and (
        any(marker in text for marker in object_markers) or concrete_targets
    )


def evidence_line_is_specific(value: str) -> bool:
    text = normalize_text(value)
    artifact_markers = ["log", "logs", "output", "diff", "file", "files", "report", "proof", "artifact"]
    object_markers = [
        "pack",
        "unpack",
        "header",
        "payload",
        "compatibility",
        "cli",
        "build",
        "skill",
        "thx",
        "inspection",
        "test",
    ]
    concrete_targets = (
        "/" in value
        or "`" in value
        or "function" in text
        or ".md" in text
        or ".cpp" in text
        or "subsection" in text
    )
    return len(text) >= 20 and (
        (any(marker in text for marker in artifact_markers) and any(marker in text for marker in object_markers))
        or concrete_targets
    )


def themes_from_text(value: str) -> list[str]:
    text = normalize_text(value)
    themes: list[str] = []
    for theme, markers in COMPETITION_REQUIREMENT_THEMES.items():
        if any(marker in text for marker in markers):
            themes.append(theme)
    return themes


def contains_word_marker(text: str, marker: str) -> bool:
    return re.search(rf"\b{re.escape(marker)}\b", text) is not None


def target_list(value: str) -> list[str]:
    normalized = value.replace("`", "")
    matches = re.findall(r"([A-Za-z0-9._-]+(?:/[A-Za-z0-9._*:-]+)+)", normalized)
    result: list[str] = []
    seen: set[str] = set()
    for item in matches:
        candidate = item.strip().replace("\\", "/")
        key = candidate.lower()
        if candidate and key not in seen:
            result.append(candidate)
            seen.add(key)
    return result


def target_line_is_specific(value: str) -> bool:
    lowered = normalize_text(value)
    if lowered.startswith("none") and "documentation" in lowered:
        return True
    targets = target_list(value)
    return bool(targets) and all(len(target) >= 3 and "/" in target for target in targets)


def validate_plan_contracts(root: Path, state: dict[str, Any]) -> list[str]:
    if state["stage"] != "plan":
        return []
    tasks = task_entries(root, state)
    if not tasks:
        return ["plan.md cannot be validated before tasks.md defines bounded tasks"]
    contracts = parse_plan_task_contracts(root, state)
    errors: list[str] = []
    task_ids = {task["id"] for task in tasks}
    for task in tasks:
        contract = resolve_plan_contract_for_task(task, contracts)
        if contract is None:
            if is_verification_only_task(task):
                continue
            errors.append(f"plan.md missing contract block for task {task['id']}")
            continue
        for field in ("theme", "verification", "evidence", "implementation_targets", "test_targets"):
            value = contract.get(field, "")
            if not value:
                errors.append(f"plan.md task {task['id']} missing {field}")
        verification = contract.get("verification", "")
        evidence = contract.get("evidence", "")
        implementation_targets = contract.get("implementation_targets", "")
        test_targets = contract.get("test_targets", "")
        if verification and not verification_line_is_specific(verification):
            errors.append(f"plan.md task {task['id']} verification is too generic")
        if evidence and not evidence_line_is_specific(evidence):
            errors.append(f"plan.md task {task['id']} evidence is too generic")
        if implementation_targets and not target_line_is_specific(implementation_targets):
            errors.append(f"plan.md task {task['id']} implementation_targets is too generic")
        if test_targets and not target_line_is_specific(test_targets):
            errors.append(f"plan.md task {task['id']} test_targets is too generic")
        expected = task_expected_themes(task)
        theme_text = normalize_text(contract.get("theme", ""))
        matched_by_targets = contract_matches_task_targets(task, contract)
        for theme in expected:
            markers = COMPETITION_REQUIREMENT_THEMES[theme]
            if not any(marker in theme_text for marker in markers) and not matched_by_targets:
                errors.append(f"plan.md task {task['id']} theme does not cover expected topic: {theme}")
    extra = sorted(set(contracts) - task_ids)
    used_contracts = {value.get("_task_id", "") for value in contracts.values() if isinstance(value, dict)}
    for task_id in extra:
        if task_id not in used_contracts:
            errors.append(f"plan.md contains contract for unknown task {task_id}")
    return errors


def classify_gate_error(stage: str, error: str) -> str:
    text = str(error).lower()
    if stage == "plan" and any(
        marker in text
        for marker in (
            "verification is too generic",
            "evidence is too generic",
            "implementation_targets is too generic",
            "test_targets is too generic",
            "theme does not cover expected topic",
        )
    ):
        return "soft"
    return "hard"


def split_gate_findings(stage: str, errors: list[str]) -> tuple[list[str], list[str]]:
    hard: list[str] = []
    soft: list[str] = []
    for error in errors:
        if classify_gate_error(stage, error) == "soft":
            soft.append(error)
        else:
            hard.append(error)
    return hard, soft


def finding_records(stage: str, findings: list[str], *, deferred_to: str | None = None) -> list[dict[str, Any]]:
    return [
        {
            "stage": stage,
            "severity": classify_gate_error(stage, finding),
            "message": finding,
            "status": "open",
            "deferred_to": deferred_to,
        }
        for finding in findings
    ]


def merge_open_findings(state: dict[str, Any], records: list[dict[str, Any]]) -> None:
    existing = state.setdefault("open_findings", [])
    for record in records:
        if not any(
            isinstance(item, dict)
            and item.get("stage") == record.get("stage")
            and item.get("message") == record.get("message")
            and item.get("status") == "open"
            for item in existing
        ):
            existing.append(record)


def reconcile_open_findings(state: dict[str, Any], completed_stage: str) -> None:
    findings = state.setdefault("open_findings", [])
    resolved = state.setdefault("resolved_findings", [])
    if completed_stage != "verify":
        return
    remaining: list[dict[str, Any]] = []
    for item in findings:
        if not isinstance(item, dict):
            continue
        if item.get("severity") == "soft":
            updated = dict(item)
            updated["status"] = "reviewed_in_verify"
            updated["resolved_at_stage"] = completed_stage
            resolved.append(updated)
            continue
        remaining.append(item)
    state["open_findings"] = remaining


def task_declared_targets(task: dict[str, Any]) -> list[str]:
    details = str(task.get("details", ""))
    title = str(task.get("title", ""))
    targets = re.findall(r"File:\s*`([^`]+)`", details)
    inline_paths = re.findall(r"`([^`]+/[^`]+)`", f"{title}\n{details}")
    plain_paths = re.findall(r"\bat\s+([A-Za-z0-9._/-]+/[A-Za-z0-9._/-]+)", f"{title}\n{details}")
    combined = [*targets, *inline_paths, *plain_paths]
    normalized: list[str] = []
    seen: set[str] = set()
    for target in combined:
        value = target.strip().replace("\\", "/")
        key = value.lower()
        if value and key not in seen:
            normalized.append(value)
            seen.add(key)
    return normalized


def contract_matches_task_targets(task: dict[str, Any], contract: dict[str, str]) -> bool:
    task_targets = task_declared_targets(task)
    if not task_targets:
        return False
    declared = ", ".join(
        value
        for value in [contract.get("implementation_targets", ""), contract.get("test_targets", "")]
        if value
    )
    if not declared:
        return False
    contract_targets = [target.lower() for target in target_list(declared)]
    lowered_task_targets = [target.lower() for target in task_targets]
    return any(
        any(task_target in contract_target or contract_target in task_target for contract_target in contract_targets)
        for task_target in lowered_task_targets
    )


def resolve_plan_contract_for_task(task: dict[str, Any], contracts: dict[str, dict[str, str]]) -> dict[str, str] | None:
    exact = contracts.get(task["id"])
    if exact is not None:
        exact = dict(exact)
        exact["_task_id"] = task["id"]
        return exact
    expected = task_expected_themes(task)
    for contract_id, contract in contracts.items():
        candidate = dict(contract)
        candidate["_task_id"] = contract_id
        if contract_matches_task_targets(task, candidate):
            return candidate
        theme_text = normalize_text(candidate.get("theme", ""))
        if expected and all(
            any(marker in theme_text for marker in COMPETITION_REQUIREMENT_THEMES[theme])
            for theme in expected
        ):
            return candidate
    return None


def is_verification_only_task(task: dict[str, Any]) -> bool:
    title = normalize_text(str(task.get("title", "")))
    verification_markers = ["verify", "validation", "regression"]
    implementation_markers = [
        "implement",
        "add",
        "update",
        "deliver",
        "preserve",
        "support",
        "modify",
        "change",
        "introduce",
        "create",
    ]
    if not any(contains_word_marker(title, marker) for marker in verification_markers):
        return False
    if any(contains_word_marker(title, marker) for marker in implementation_markers):
        return False
    return not task_declared_targets(task)


def matches_declared_targets(paths: list[str], declared: str) -> bool:
    lowered = normalize_text(declared)
    if lowered.startswith("none") and "documentation" in lowered:
        return True
    targets = [target.lower() for target in target_list(declared)]
    if not targets:
        return False
    normalized_paths = [path.replace("\\", "/").lower() for path in paths]
    return any(any(target in path for target in targets) for path in normalized_paths)


def validate_plan_commitment_coverage(root: Path, state: dict[str, Any], current: dict[str, Any]) -> list[str]:
    if state["stage"] not in {"verify", "finalize", "archive", "retrospective", "closed"}:
        return []
    contracts = parse_plan_task_contracts(root, state)
    if not contracts:
        return []
    satisfied = [
        item
        for item in collect_requirement_evidence(root, state, current)
        if isinstance(item, dict) and item.get("status") == "satisfied"
    ]
    errors: list[str] = []
    for task_id, contract in contracts.items():
        fallback_themes = themes_from_text(contract.get("theme", ""))
        for field in ("verification", "evidence"):
            value = contract.get(field, "")
            if not value:
                continue
            themes = themes_from_text(value)
            if not themes:
                themes = fallback_themes
            if not themes:
                errors.append(f"plan.md task {task_id} {field} is not mappable to a competition theme")
                continue
            for theme in themes:
                markers = COMPETITION_REQUIREMENT_THEMES[theme]
                if not any(evidence_matches_theme(str(item.get("requirement", "")), markers) for item in satisfied):
                    errors.append(f"plan.md task {task_id} {field} has no realized evidence for theme: {theme}")
        implementation_targets = contract.get("implementation_targets", "")
        test_targets = contract.get("test_targets", "")
        task_evidence = []
        for item in satisfied:
            requirement = str(item.get("requirement", ""))
            if any(
                evidence_matches_theme(requirement, COMPETITION_REQUIREMENT_THEMES[theme])
                for theme in fallback_themes
            ):
                task_evidence.append(item)
        implementation_files = [
            path
            for item in task_evidence
            for path in item.get("implementation_files", [])
            if isinstance(path, str)
        ]
        test_files = [
            path
            for item in task_evidence
            for path in item.get("test_files", [])
            if isinstance(path, str)
        ]
        if implementation_targets and not matches_declared_targets(implementation_files, implementation_targets):
            errors.append(f"plan.md task {task_id} implementation_targets have no realized file match")
        if test_targets and not matches_declared_targets(test_files, test_targets):
            errors.append(f"plan.md task {task_id} test_targets have no realized file match")
    return errors


def validate_apply_task_requirement_alignment(
    root: Path,
    state: dict[str, Any],
    task: dict[str, Any] | None,
    evidence: list[dict[str, Any]],
    contract: dict[str, Any] | None = None,
) -> list[str]:
    if state["stage"] != "apply" or task is None:
        return []
    expected = (
        themes_from_text(str(contract.get("theme", "")))
        if contract_is_documentation_only(contract)
        else task_expected_themes(task)
    )
    if not expected:
        return []
    satisfied = [item for item in evidence if isinstance(item, dict) and item.get("status") == "satisfied"]
    errors: list[str] = []
    for theme in expected:
        markers = COMPETITION_REQUIREMENT_THEMES[theme]
        if any(evidence_matches_theme(str(item.get("requirement", "")), markers) for item in satisfied):
            continue
        realized_paths = [
            path
            for item in satisfied
            for path in [*item.get("implementation_files", []), *item.get("test_files", [])]
            if isinstance(path, str)
        ]
        if realized_files_match_theme(root, realized_paths, markers):
            continue
        if contract_is_documentation_only(contract):
            implementation_targets = str(contract.get("implementation_targets", ""))
            if realized_files_match_theme(root, target_list(implementation_targets), markers):
                continue
        errors.append(f"Apply task {task['id']} evidence missing expected theme: {theme}")
    return errors


def collect_requirement_evidence(root: Path, state: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    evidence = current.get("requirement_evidence")
    if isinstance(evidence, list):
        collected.extend(item for item in evidence if isinstance(item, dict))
    handoff_dir = sdd_dir(root) / "changes" / state["change_id"] / "handoffs"
    if handoff_dir.exists():
        for path in sorted(handoff_dir.glob("*.json")):
            try:
                payload = load_json(path)
            except SddError:
                continue
            evidence = payload.get("requirement_evidence")
            if isinstance(evidence, list):
                collected.extend(item for item in evidence if isinstance(item, dict))
    return collected


def validate_competition_requirement_coverage(root: Path, state: dict[str, Any], current: dict[str, Any]) -> list[str]:
    if state["stage"] not in {"verify", "finalize", "archive", "retrospective", "closed"}:
        return []
    evidence = collect_requirement_evidence(root, state, current)
    if not evidence:
        return ["No accumulated requirement evidence found for competition acceptance coverage"]
    satisfied = [
        item for item in evidence if isinstance(item, dict) and item.get("status") == "satisfied"
    ]
    errors: list[str] = []
    for theme, markers in COMPETITION_REQUIREMENT_THEMES.items():
        if not any(evidence_matches_theme(str(item.get("requirement", "")), markers) for item in satisfied):
            errors.append(f"Competition requirement coverage missing theme: {theme}")
    return errors


def focused_test_commands(root: Path, changed: list[str]) -> tuple[list[list[str]], list[str]]:
    project = load_json(sdd_dir(root) / "policy" / "project.yaml")
    kind = project.get("detected_project_kind", "generic")
    try:
        state = load_state(root)
    except SddError:
        state = {"stage": None}
    contract = current_task_contract(root, state) if state.get("stage") == "apply" else None
    test_files = sorted(path for path in changed if is_test_path(path))
    if not test_files:
        if contract_is_documentation_only(contract):
            return [], []
        if state.get("stage") == "apply" and contract is not None:
            implementation_targets = target_list(str(contract.get("implementation_targets", "")))
            if implementation_targets and not any(is_test_path(path) for path in implementation_targets):
                return [], []
        return [], ["Apply task changed no test file; Runner cannot prove the new behavior"]
    if kind == "java-maven":
        names = sorted({Path(path).stem for path in test_files if path.endswith(".java")})
        if not names:
            return [], ["No Maven test class could be derived from changed tests"]
        executable = ".\\mvnw.cmd" if (root / "mvnw.cmd").exists() else (
            "./mvnw" if (root / "mvnw").exists() else "mvn"
        )
        return [[executable, f"-Dtest={','.join(names)}", "test"]], []
    if kind == "java-gradle":
        executable = ".\\gradlew.bat" if (root / "gradlew.bat").exists() else "./gradlew"
        command = [executable, "test"]
        for path in test_files:
            if path.endswith(".java"):
                class_name = path.removeprefix("src/test/java/").removesuffix(".java").replace("/", ".")
                command.extend(["--tests", class_name])
        return ([command], []) if len(command) > 2 else ([], ["No Gradle test class could be derived"])
    if kind == "python":
        python_tests = [path for path in test_files if path.endswith(".py")]
        if python_tests:
            return [[sys.executable, "-m", "pytest", *python_tests]], []
        return [], []
    if kind == "go":
        packages = sorted({"./" + str(Path(path).parent).replace("\\", "/") for path in test_files})
        return [["go", "test", *packages]], []
    if kind in {"generic", "cpp-cmake", "cxx-cmake", "c++"}:
        if os.name != "nt" and (root / "build.sh").exists() and shutil.which("bash"):
            command = ["bash", "./build.sh", "--type", "release", "build"]
            return [command], []
        if (root / "CMakeLists.txt").exists():
            return [
                ["cmake", "-S", ".", "-B", ".sdd/tmp/focused-build", "-DCMAKE_BUILD_TYPE=Release"],
                ["cmake", "--build", ".sdd/tmp/focused-build", "--config", "Release"],
                ["ctest", "--test-dir", ".sdd/tmp/focused-build", "-C", "Release", "--output-on-failure"],
            ], []
    return [], [f"Focused test derivation is unsupported for project kind: {kind}"]


def ensure_git_identity(root: Path) -> None:
    if not git(root, "config", "user.name", check=False):
        git(root, "config", "user.name", "Autonomous SDD")
    if not git(root, "config", "user.email", check=False):
        git(root, "config", "user.email", "autonomous-sdd@localhost.invalid")


def commit_all(root: Path, message: str) -> str:
    git(root, "add", "--all")
    if git(root, "status", "--porcelain", check=False):
        git(root, "commit", "-m", message)
    return git_head(root)


def read_task(value: str | None, root: Path) -> str:
    if not value:
        raise SddError("Competition task must be provided or resolved from the default branch objective")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_file():
        text = candidate.read_text(encoding="utf-8").strip()
    else:
        text = value.strip()
    if len(text) < 10:
        raise SddError("Competition task must contain at least 10 characters")
    return text


def resolve_competition_objective(value: str | None, root: Path) -> dict[str, Any]:
    if value:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8").strip()
            source = "file"
            input_path: str | None = str(candidate.resolve())
        else:
            text = value.strip()
            source = "inline"
            input_path = None
        if len(text) < 10:
            raise SddError("Competition task must contain at least 10 characters")
        branch_default_used = False
    else:
        text = DEFAULT_COMPETITION_GOAL
        source = "default"
        input_path = None
        branch_default_used = True
    return {
        "source": source,
        "input_path": input_path,
        "raw_text": text,
        "effective_objective": text,
        "frozen_goal": DEFAULT_COMPETITION_GOAL,
        "competition_constraints": list(DEFAULT_COMPETITION_CONSTRAINTS),
        "required_acceptance_invariants": list(DEFAULT_ACCEPTANCE_INVARIANTS),
        "tooling_integration_constraints": dict(DEFAULT_TOOLING_INTEGRATION_CONSTRAINTS),
        "branch_default_used": branch_default_used,
        "frozen_at": now(),
    }


def slugify(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", value.lower())
    base = "-".join(words[:6]) or "competition-change"
    return base[:60].strip("-")


def unique_change_id(root: Path, preferred: str) -> str:
    candidate = preferred
    active = root / "openspec" / "changes" / candidate
    archived = list((root / "openspec" / "changes" / "archive").glob(f"*-{candidate}"))
    if not active.exists() and not archived:
        return candidate
    return f"{preferred[:45].rstrip('-')}-{dt.datetime.now():%Y%m%d%H%M%S}"


def doctor(args: argparse.Namespace) -> None:
    root = project_root(args.project)
    checks = {
        "python": [sys.executable, "--version"],
        "git": ["git", "--version"],
        "opencode": ["opencode", "--version"],
        "openspec": ["openspec.cmd" if os.name == "nt" else "openspec", "--version"],
    }
    failed = False
    for name, command in checks.items():
        try:
            result = run_command(command, root, timeout=30)
            ok = result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            ok = False
            result = None
        print(f"{name}: {'PASS' if ok else 'FAIL'} {((result.stdout or '').strip()) if result else ''}")
        failed |= not ok
    if failed:
        raise SddError("Doctor checks failed")


def baseline(args: argparse.Namespace) -> None:
    root = project_root(args.project)
    dirty = git_changed(root)
    if dirty:
        raise SddError("Commit project configuration before baseline:\n- " + "\n- ".join(dirty))
    competition = load_json(sdd_dir(root) / "policy" / "competition.yaml")
    api_policy = load_json(sdd_dir(root) / "policy" / "api-contract.yaml")
    protected_patterns = competition["modification"]["forbidden"]
    protected_patterns += api_policy["public_api"]["protected_paths"]
    manifest_path = sdd_dir(root) / "baseline" / "manifest.json"
    manifest_key = rel(root, manifest_path)
    manifest = {
        "schema_version": 1,
        "created_at": now(),
        "runner_version": VERSION,
        "git_head": git_head(root),
        "control_hashes": protected_control_hashes(root),
        "protected_files": capture_files(root, protected_patterns),
        "dependency_files": capture_files(root, competition["dependencies"]["manifest_paths"]),
    }
    manifest["protected_files"].pop(manifest_key, None)
    manifest["dependency_files"].pop(manifest_key, None)
    atomic_json(manifest_path, manifest)
    print(f"Baseline captured at {manifest['git_head']} ({len(manifest['protected_files'])} protected files)")


def start(args: argparse.Namespace) -> None:
    root = project_root(args.project)
    baseline_file = sdd_dir(root) / "baseline" / "manifest.json"
    if not baseline_file.exists():
        raise SddError("Capture the competition baseline before starting")
    dirty = git_changed(root)
    if dirty:
        raise SddError("Start requires a clean competition workspace:\n- " + "\n- ".join(dirty))
    if state_path(root).exists():
        existing = load_state(root)
        if existing.get("status") not in {"closed", "blocked"}:
            raise SddError(f"Active run already exists: {existing['run_id']}")
    change = args.change_id
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", change):
        raise SddError("change-id must be kebab-case")
    directory = root / "openspec" / "changes" / change
    directory.mkdir(parents=True, exist_ok=False)
    (directory / ".openspec.yaml").write_text("schema: autonomous-superspec\n", encoding="utf-8")
    objective_bundle = getattr(args, "objective_bundle", None) or resolve_competition_objective(args.objective, root)
    atomic_json(sdd_dir(root) / "runtime" / "competition-objective.json", objective_bundle)
    state = {
        "schema_version": 1,
        "run_id": f"{dt.datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}",
        "change_id": change,
        "objective": objective_bundle["effective_objective"],
        "objective_source": objective_bundle["source"],
        "objective_input_path": objective_bundle["input_path"],
        "frozen_goal": objective_bundle["frozen_goal"],
        "competition_constraints": objective_bundle["competition_constraints"],
        "required_acceptance_invariants": objective_bundle["required_acceptance_invariants"],
        "tooling_integration_constraints": objective_bundle["tooling_integration_constraints"],
        "executor": getattr(args, "executor", load_config(root).get("executor", "opencode")),
        "source_root": getattr(args, "source_root", str(root)),
        "work_root": getattr(args, "work_root", str(root)),
        "source_head": getattr(args, "source_head", None),
        "baseline_commit": getattr(args, "baseline_commit", None),
        "run_branch": getattr(args, "run_branch", None),
        "source_status": getattr(args, "source_status", None),
        "stage": "brainstorm",
        "status": "running",
        "pending_action": "execute_stage",
        "iteration": 0,
        "task": None,
        "baseline_commit": load_json(baseline_file)["git_head"],
        "last_verified_commit": git_head(root),
        "last_handoff": None,
        "next_action": "execute_stage",
        "model_selection": load_config(root).get("model") or "opencode-default",
        "retries": {},
        "failure_signatures": {},
        "last_failure_signature": None,
        "created_at": now(),
    }
    save_state(root, state)
    append_journal(root, {"event": "run_started", "stage": "brainstorm", "change_id": change})
    print(f"Started {state['run_id']} for {change}")


def append_journal(root: Path, event: dict[str, Any]) -> None:
    path = sdd_dir(root) / "runtime" / "execution-journal.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"at": now(), **event}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


def artifact_for(root: Path, state: dict[str, Any], stage: str) -> Path | None:
    if stage in ARTIFACTS:
        return change_dir(root, state) / ARTIFACTS[stage]
    if stage == "retrospective":
        return sdd_dir(root) / "changes" / state["change_id"] / "retrospective.md"
    return None


def task_entries(root: Path, state: dict[str, Any]) -> list[dict[str, Any]]:
    path = change_dir(root, state) / "tasks.md"
    if not path.exists():
        return []
    entries = []
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^- \[(?P<mark>[ xX])\] (?P<id>\d+\.\d+) (?P<title>.+)$"
        r"(?P<details>(?:\n(?!- \[[ xX]\] \d+\.\d+ ).+)*)",
        re.MULTILINE,
    )
    for match in pattern.finditer(text):
        entries.append(
            {
                "id": match.group("id"),
                "title": match.group("title").strip(),
                "completed": match.group("mark").lower() == "x",
                "details": match.group("details").strip(),
            }
        )
    return entries


def task_by_id(root: Path, state: dict[str, Any], task_id: str | None) -> dict[str, Any] | None:
    if not task_id:
        return None
    for entry in task_entries(root, state):
        if entry["id"] == task_id:
            return entry
    return None


def unchecked_tasks(root: Path, state: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry for entry in task_entries(root, state) if not entry["completed"]]


def current_task(root: Path, state: dict[str, Any]) -> dict[str, Any] | None:
    pinned = task_by_id(root, state, state.get("task"))
    if pinned is not None:
        return pinned
    tasks = unchecked_tasks(root, state)
    return tasks[0] if tasks else None


def complete_task(root: Path, state: dict[str, Any], task_id: str) -> None:
    path = change_dir(root, state) / "tasks.md"
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^- \[ \] {re.escape(task_id)} (?P<title>.+)$", re.MULTILINE)
    updated, count = pattern.subn(rf"- [x] {task_id} \g<title>", text, count=1)
    if count != 1:
        checked = re.search(rf"^- \[x\] {re.escape(task_id)} (?P<title>.+)$", text, re.MULTILINE)
        if checked:
            return
        raise SddError(f"Runner could not complete apply task {task_id}")
    path.write_text(updated, encoding="utf-8")


def migrate_tasks(args: argparse.Namespace) -> None:
    root = project_root(args.project)
    state = load_state(root)
    path = change_dir(root, state) / "tasks.md"
    text = path.read_text(encoding="utf-8")
    if task_entries(root, state) and not re.search(r"^- \[[ xX]\] (?!\d+\.\d+ )", text, re.MULTILINE):
        print("Task file already uses the bounded task format")
        return
    section_pattern = re.compile(r"^## (?P<number>\d+)\. (?P<title>.+)$", re.MULTILINE)
    matches = list(section_pattern.finditer(text))
    if not matches:
        raise SddError("Cannot migrate tasks.md: no numbered level-2 task sections found")
    prefix = text[: matches[0].start()]
    rebuilt = [prefix.rstrip(), ""]
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end() : end].strip()
        body = re.sub(r"^- \[[ xX]\] ", "  - ", body, flags=re.MULTILINE)
        rebuilt.extend(
            [
                match.group(0),
                "",
                f"- [ ] {match.group('number')}.1 {match.group('title').strip()}",
                "",
                body,
                "",
            ]
        )
    migrated = "\n".join(rebuilt).rstrip() + "\n"
    path.write_text(migrated, encoding="utf-8")
    tasks = task_entries(root, state)
    if len(tasks) < MIN_APPLY_TASKS or len(tasks) > MAX_APPLY_TASKS:
        raise SddError(f"Migrated task count must be {MIN_APPLY_TASKS}-{MAX_APPLY_TASKS}; found {len(tasks)}")
    append_journal(root, {"event": "tasks_migrated", "task_count": len(tasks)})
    print(f"Migrated legacy task file to {len(tasks)} bounded tasks")


def required_output(root: Path, state: dict[str, Any]) -> str:
    stage = state["stage"]
    if stage == "specs":
        return f"openspec/changes/{state['change_id']}/specs/<capability>/spec.md"
    if stage == "apply":
        task = current_task(root, state)
        return (
            f"complete exactly task {task['id']}: {task['title']}"
            if task
            else "write apply.md receipt"
        )
    if stage == "archive":
        return f"archive and sync change {state['change_id']}"
    if stage == "closed":
        return "none"
    artifact = artifact_for(root, state, stage)
    return rel(root, artifact) if artifact else stage


def stage_required_reads(root: Path, state: dict[str, Any]) -> list[str]:
    stage = state["stage"]
    change = change_dir(root, state)
    template_root = root / "openspec" / "schemas" / "autonomous-superspec" / "templates"
    current_contract = current_task_contract(root, state) if stage == "apply" else None
    reads = [
        ".sdd/AGENT-INSTRUCTIONS.md",
        ".sdd/runtime/competition-objective.json",
        ".sdd/runtime/state.json",
        ".sdd/runtime/current-handoff.json",
        ".sdd/policy/competition.yaml",
        ".sdd/policy/project.yaml",
        ".sdd/policy/api-contract.yaml",
        ".sdd/policy/coding-standard.yaml",
    ]
    template_name = ARTIFACTS.get(stage)
    if stage == "specs":
        template_name = "spec.md"
    elif stage == "retrospective":
        template_name = "retrospective.md"
    if template_name and (template_root / template_name).exists():
        reads.append(rel(root, template_root / template_name))
    if change.exists():
        stage_reads = [
            "brainstorm.md",
            "proposal.md",
            "design.md",
            "tasks.md",
            "plan.md",
            "apply.md",
            "review.md",
            "verify.md",
        ]
        if stage == "apply":
            stage_reads = [
                "design.md",
                "plan.md",
                "apply.md",
            ]
            if current_contract is not None and current_contract.get("_source") == "synthesized":
                stage_reads.append("tasks.md")
        for name in stage_reads:
            path = change / name
            if path.exists():
                reads.append(rel(root, path))
        for spec in sorted((change / "specs").glob("*/spec.md")):
            reads.append(rel(root, spec))
        reads.append(rel(root, change / ".openspec.yaml"))
    elif stage == "retrospective":
        archives = sorted((root / "openspec" / "changes" / "archive").glob(f"*-{state['change_id']}"))
        if archives:
            for path in archives[-1].rglob("*.md"):
                reads.append(rel(root, path))
    return list(dict.fromkeys(reads))


def build_packet(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    competition = load_json(sdd_dir(root) / "policy" / "competition.yaml")
    project_policy = load_json(sdd_dir(root) / "policy" / "project.yaml")
    api_policy = load_json(sdd_dir(root) / "policy" / "api-contract.yaml")
    stage = state["stage"]
    allowed = list(competition["modification"]["allowed"])
    if stage == "apply":
        contract = current_task_contract(root, state) or {}
        allowed = sorted(set(allowed) & set(project_policy["change_boundaries"]["allowed"])) or project_policy[
            "change_boundaries"
        ]["allowed"]
        for field in ("implementation_targets", "test_targets"):
            value = str(contract.get(field, "")).strip()
            if value.lower().startswith("none"):
                continue
            allowed.extend(target_list(value))
        allowed.extend(
            [
                f"openspec/changes/{state['change_id']}/apply.md",
                ".sdd/runtime/**",
                ".sdd/evidence/**",
            ]
        )
        allowed = list(dict.fromkeys(allowed))
    packet = {
        "schema_version": 1,
        "run_id": state["run_id"],
        "change_id": state["change_id"],
        "stage": stage,
        "role": "implementation" if stage == "apply" else stage,
        "objective": state["objective"],
        "frozen_goal": state.get("frozen_goal", DEFAULT_COMPETITION_GOAL),
        "competition_constraints": state.get("competition_constraints", list(DEFAULT_COMPETITION_CONSTRAINTS)),
        "required_acceptance_invariants": state.get(
            "required_acceptance_invariants", list(DEFAULT_ACCEPTANCE_INVARIANTS)
        ),
        "tooling_integration_constraints": state.get(
            "tooling_integration_constraints", dict(DEFAULT_TOOLING_INTEGRATION_CONSTRAINTS)
        ),
        "required_output": required_output(root, state),
        "task_id": current_task(root, state)["id"] if stage == "apply" and current_task(root, state) else None,
        "task_title": current_task(root, state)["title"] if stage == "apply" and current_task(root, state) else None,
        "task_details": current_task(root, state).get("details", "") if stage == "apply" and current_task(root, state) else "",
        "current_task_contract": current_task_contract(root, state) if stage == "apply" else None,
        "required_reads": stage_required_reads(root, state),
        "allowed_paths": allowed,
        "forbidden_paths": sorted(
            set(competition["modification"]["forbidden"] + api_policy["public_api"]["protected_paths"])
        ),
        "forbidden_actions": [
            "modify policy, baseline, runner, or Superspec schema",
            "add or change dependencies",
            "change protected public API",
            "commit, advance lifecycle state, or archive outside archive stage",
            "perform more than the current stage or one apply task",
            "during non-apply stages, do not run build, compile, test, package, or other commands that modify tracked files or build outputs",
        ],
        "acceptance": {
            "write_result": ".sdd/runtime/agent-result.json",
            "result_statuses": ["completed", "failed", "blocked"],
            "required_sections": REQUIRED_SECTIONS.get(stage, []),
            "tests": load_json(sdd_dir(root) / "policy" / "verification.yaml")["commands"],
            "result_contract": {
                "commands_run": "array of argv arrays; never shell strings",
                "tests": "array of {command: argv array, exit_code: integer}",
                "requirement_evidence": (
                    "array of {requirement, implementation_files, test_files, status}; "
                    "status must be satisfied for completion"
                ),
                "residual_risks": "array of concrete unverified or deferred risks",
            },
        },
    }
    packet_path = sdd_dir(root) / "runtime" / "task-packet.json"
    atomic_json(packet_path, packet)
    return packet


def apply_allowed_paths(root: Path, state: dict[str, Any]) -> list[str]:
    competition = load_json(sdd_dir(root) / "policy" / "competition.yaml")
    project_policy = load_json(sdd_dir(root) / "policy" / "project.yaml")
    allowed = list(competition["modification"]["allowed"])
    allowed = sorted(set(allowed) & set(project_policy["change_boundaries"]["allowed"])) or project_policy[
        "change_boundaries"
    ]["allowed"]
    contract = current_task_contract(root, state) or {}
    for field in ("implementation_targets", "test_targets"):
        value = str(contract.get(field, "")).strip()
        if value.lower().startswith("none"):
            continue
        allowed.extend(target_list(value))
    allowed.extend(
        [
            f"openspec/changes/{state['change_id']}/apply.md",
            ".sdd/runtime/**",
            ".sdd/evidence/**",
        ]
    )
    return list(dict.fromkeys(allowed))


def prompt_for(packet: dict[str, Any]) -> str:
    stage = str(packet["stage"])
    stage_execution_clause = (
        "For non-apply stages, stay read-mostly: write only the required stage artifact and agent-result, "
        "and do not run build, compile, test, cmake, ctest, packaging, or cleanup commands that can modify tracked files, "
        "generated outputs, or the worktree. "
        if stage != "apply"
        else "For apply, execute only the current task contract and keep every command narrowly scoped to that task. "
        "Treat packet.allowed_paths and the task contract implementation_targets/test_targets as the hard scope boundary. "
        "Do not modify files that belong to later tasks or broader requirements outside those targets; if they seem relevant, "
        "leave them unchanged and record the gap in residual_risks instead. "
        "Operate only inside the current repository workspace and packet.allowed_paths. "
        "Do not read, glob, diff, or inspect source_root/work_root paths from state.json or any directory outside the repository; "
        "external-directory access is out of scope and may be denied. "
        "On Windows or PowerShell, do not use POSIX shell idioms such as `mkdir -p`, chained `cd && ...`, or shell-only command strings; "
        "reuse existing build directories when present and invoke tools directly with platform-correct argv arrays. "
    )
    verify_clause = (
        "For verify, keep all temporary files and validation fixtures inside the project workspace or .sdd/tmp; "
        "do not use external temp roots such as /tmp, C:\\tmp, %TEMP%, or other directories outside the repository. "
        "Before any optional exploratory checks, you must write the required verify artifact and .sdd/runtime/agent-result.json; "
        "if a command is denied, a permission is missing, or verification is incomplete, still write both files and return status blocked with exact evidence. "
        if stage == "verify"
        else ""
    )
    plan_clause = (
        "For plan, every task contract must be concrete: Theme must name every competition topic implied by the task title and details, not just the primary headline; "
        "if a task mentions custom headers, unpack behavior, CLI compatibility, build entry stability, skill delivery, THX handling, or header inspection anywhere in the task body, repeat those topics explicitly in Theme; "
        "Verification must name the exact behavior or target to check; "
        "Evidence must name the exact files, logs, or outputs to inspect; Implementation Targets and Test Targets must be specific file paths or narrow path groups; "
        "use 'None (documentation-only change)' only when a task is genuinely documentation-only. "
        if stage == "plan"
        else ""
    )
    return "".join(
        [
            "You are a bounded executor in an unattended competition workflow. ",
            "Read .sdd/runtime/task-packet.json and every required file. ",
            "Use the listed stage template exactly and preserve every required section. ",
            "Treat frozen_goal, competition_constraints, and required_acceptance_invariants as mandatory. ",
            "Perform exactly the declared stage or one apply task. ",
            stage_execution_clause,
            verify_clause,
            plan_clause,
            "When stage is apply, current_task_contract in the packet is the binding execution contract for that task. "
            "If the contract was synthesized, use task_details as additional binding scope and acceptance context. ",
            "If any command, tool call, or directory access is denied, immediately write .sdd/runtime/agent-result.json with status blocked, "
            "the exact blocking_reason, and concrete residual_risks instead of continuing to explore. ",
            "Do not commit or change lifecycle state. Do not modify policy, baseline, runner, schema, ",
            "dependency manifests, protected API, or forbidden paths. ",
            "Write .sdd/runtime/agent-result.json using status, summary, files_read, files_changed, ",
            "commands_run, tests, deviations, blocking_reason, task_id, requirement_evidence, and residual_risks. ",
            "Follow acceptance.result_contract exactly; commands must be argv arrays, not shell strings. ",
            "Optional formatter or checker tooling is supporting evidence only and must not replace core verification. ",
            "For apply, task_id must exactly match the packet and you must not edit task checkboxes; ",
            "the Runner owns task completion state. ",
            "Do not ask questions; if essential intent is ambiguous, return status blocked with the exact reason. ",
            f"Current stage: {packet['stage']}. Required output: {packet['required_output']}.",
        ]
    )


def write_agent_result(
    root: Path,
    summary: str,
    changed: list[str],
    task_id: str | None = None,
    requirement_evidence: list[dict[str, Any]] | None = None,
) -> None:
    atomic_json(
        sdd_dir(root) / "runtime" / "agent-result.json",
        {
            "status": "completed",
            "summary": summary,
            "files_read": [".sdd/runtime/task-packet.json", ".sdd/runtime/state.json"],
            "files_changed": changed,
            "commands_run": [],
            "tests": [],
            "deviations": [],
            "blocking_reason": None,
            "task_id": task_id,
            "requirement_evidence": requirement_evidence
            if requirement_evidence is not None
            else [
                {
                    "requirement": "Deterministic fixture behavior",
                    "implementation_files": changed,
                    "test_files": changed,
                    "status": "satisfied",
                }
            ]
            if task_id
            else [],
            "residual_risks": [],
        },
    )


def fixture_execute(root: Path, state: dict[str, Any]) -> None:
    """Deterministic lifecycle executor used to validate orchestration itself."""
    stage = state["stage"]
    config = load_config(root)
    if config.get("fixture_fail_stage") == stage:
        raise SddError(f"Injected deterministic failure at stage {stage}")
    fixture_stage_delay_seconds = float(config.get("fixture_stage_delay_seconds", 0) or 0)
    if fixture_stage_delay_seconds > 0:
        time.sleep(fixture_stage_delay_seconds)
    directory = change_dir(root, state)
    changed: list[str] = []
    requirement_evidence: list[dict[str, Any]] | None = None
    if stage == "brainstorm":
        path = directory / "brainstorm.md"
        constraints = "\n".join(f"- {item}" for item in state.get("competition_constraints", []))
        path.write_text(
            "# Brainstorm\n\n## Objective\n\n"
            + state["objective"]
            + "\n\n## Current State\n\nProject inspected by the deterministic rehearsal executor."
            "\n\n## Binding Constraints\n\n"
            + constraints
            + "\n\n## Scope\n\nDeliver the C++ packaging customization, compatibility preservation, tool skill, and verification."
            "\n\n## Alternatives\n\n### Option A\n\nExtend the existing header format compatibly.\n\n### Option B\n\nReplace the format contract."
            "\n\n## Decision\n\nUse a backward-compatible header extension with explicit verification of unpack and CLI compatibility."
            "\n\n## Risks\n\nVariable-length header parsing and backward compatibility require focused regression tests."
            "\n\n## Blocking Ambiguities\n\nNone\n",
            encoding="utf-8",
        )
        changed.append(rel(root, path))
    elif stage == "proposal":
        path = directory / "proposal.md"
        path.write_text(
            "# Change Proposal\n\n## Why\n\n"
            + state["objective"]
            + "\n\n## What Changes\n\nAdd parameter-driven custom header payload support, preserve unpack and legacy CLI behavior, and deliver a callable tool skill."
            "\n\n## Capabilities\n\n### New Capabilities\n\n- `custom-header-payload`: write variable-length custom header content into the package output.\n- `header-inspection-skill`: inspect header metadata and support THX-related handling through the delivered skill."
            "\n\n### Modified Capabilities\n\n- Existing pack/unpack flow to parse and preserve customized header payloads.\n\n## Impact\n\nProduction code, tests, and tool skill assets only; no build entrypoint change and no dependency expansion.\n",
            encoding="utf-8",
        )
        changed.append(rel(root, path))
    elif stage == "specs":
        path = directory / "specs" / "custom-header-payload" / "spec.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "## ADDED Requirements\n\n"
            "### Requirement: Support parameter-driven custom header payload\n\n"
            "The packaging tool MUST accept a parameter that supplies custom header payload content.\n\n"
            "#### Scenario: Pack with variable-length custom header payload\n\n"
            "- **WHEN** the caller provides custom header payload content of arbitrary supported length\n"
            "- **THEN** the package output stores that payload without corrupting the archive structure\n\n"
            "### Requirement: Preserve unpack correctness and compatibility\n\n"
            "The system MUST unpack both legacy and customized package outputs correctly.\n\n"
            "#### Scenario: Unpack customized package\n\n"
            "- **WHEN** a package contains customized header payload content\n"
            "- **THEN** unpack succeeds and restores the original packaged content\n\n"
            "#### Scenario: Legacy invocation remains valid\n\n"
            "- **WHEN** the original pack command is executed without the new parameter\n"
            "- **THEN** behavior remains compatible with the legacy tool contract\n",
            encoding="utf-8",
        )
        changed.append(rel(root, path))
    elif stage == "design":
        path = directory / "design.md"
        path.write_text(
            "# Technical Design\n\n## Context\n\nThe target C++ packaging project must support custom header payload customization under competition constraints."
            "\n\n## Goals\n\nImplement parameter-driven variable-length header payload support, preserve unpack correctness, preserve legacy entrypoints, and deliver the tool skill."
            "\n\n## Non-Goals\n\nNo build entrypoint change, no dependency expansion, no broad format redesign."
            "\n\n## Existing API Verification\n\n| API | Source | Result |\n|---|---|---|\n| protected surface | baseline | unchanged |"
            "\n\n## Architecture and Boundaries\n\nKeep implementation within existing pack/unpack, CLI parsing, and test directories plus the delivered skill asset."
            "\n\n## Decisions\n\nUse a backward-compatible header extension with explicit length-aware parsing and fallback-safe legacy behavior."
            "\n\n## Testing Strategy\n\nRun focused checks for variable-length header payloads, customized unpack, legacy CLI invocation, and skill-observable header inspection.\n",
            encoding="utf-8",
        )
        changed.append(rel(root, path))
    elif stage == "tasks":
        path = directory / "tasks.md"
        path.write_text(
            "# Tasks\n\n## 1. Implementation\n\n"
            "- [ ] 1.1 Implement parameter-driven variable-length custom header payload support with focused tests\n"
            "- [ ] 1.2 Preserve unpack correctness, original CLI compatibility, and unchanged build entrypoint with regression tests\n"
            "- [ ] 1.3 Deliver the tool skill for THX-related handling and header inspection, and verify end-to-end behavior\n",
            encoding="utf-8",
        )
        changed.append(rel(root, path))
    elif stage == "plan":
        path = directory / "plan.md"
        path.write_text(
            "# Execution Plan\n\n## Execution Strategy\n\nUse one isolated session per task."
            "\n\n## Tasks\n\n"
            "### Task 1.1\n\n"
            "- Theme: custom header payload, variable-length header payload\n"
            "- Verification: run focused custom-header pack checks and variable-length payload regression\n"
            "- Evidence: implementation diff, focused test output, and header-related validation logs\n"
            "- Implementation Targets: src/pack, src/header\n"
            "- Test Targets: tests/header, tests/pack\n\n"
            "### Task 1.2\n\n"
            "- Theme: unpack correctness, legacy CLI compatibility, unchanged build entrypoint\n"
            "- Verification: run customized unpack regression and original CLI compatibility checks\n"
            "- Evidence: unpack test output, compatibility test output, and build-entry stability proof\n"
            "- Implementation Targets: src/unpack, src/cli\n"
            "- Test Targets: tests/unpack, tests/compatibility\n\n"
            "### Task 1.3\n\n"
            "- Theme: skill delivery, THX handling, header inspection\n"
            "- Verification: run skill invocation checks for THX/header inspection and end-to-end tool behavior\n"
            "- Evidence: delivered skill files, skill invocation output, and end-to-end validation logs\n"
            "- Implementation Targets: skill/cpp-unitool-header, src/inspection\n"
            "- Test Targets: tests/skill, tests/inspection"
            "\n\n## Verification\n\nRun focused checks for custom-header pack, customized unpack, legacy CLI compatibility, unchanged build entrypoint, and skill behavior."
            "\n\n## Checkpoint Strategy\n\nCommit after each passing deterministic gate.\n",
            encoding="utf-8",
        )
        changed.append(rel(root, path))
    elif stage == "apply":
        task = current_task(root, state)
        if task["id"] == "1.1":
            impl = root / "src" / "pack" / "header_payload_fixture.txt"
            test = root / "tests" / "header" / "header_payload_fixture_test.txt"
        elif task["id"] == "1.2":
            impl = root / "src" / "unpack" / "compatibility_fixture.txt"
            test = root / "tests" / "unpack" / "compatibility_fixture_test.txt"
        else:
            impl = root / "src" / "inspection" / "fixture_skill_receipt.txt"
            test = root / "tests" / "skill" / "fixture_skill_test.txt"
        impl.parent.mkdir(parents=True, exist_ok=True)
        test.parent.mkdir(parents=True, exist_ok=True)
        impl.write_text(f"deterministic competition rehearsal implementation task {task['id']} completed\n", encoding="utf-8")
        test.write_text(f"deterministic competition rehearsal test task {task['id']} completed\n", encoding="utf-8")
        changed.extend([rel(root, impl), rel(root, test)])
        if task["id"] == "1.1":
            requirement_evidence = [
                {
                    "requirement": "Support parameter-driven custom header payload with variable-length header content",
                    "implementation_files": [rel(root, impl)],
                    "test_files": [rel(root, test)],
                    "status": "satisfied",
                }
            ]
        elif task["id"] == "1.2":
            requirement_evidence = [
                {
                    "requirement": "Preserve unpack correctness for customized packages",
                    "implementation_files": [rel(root, impl)],
                    "test_files": [rel(root, test)],
                    "status": "satisfied",
                },
                {
                    "requirement": "Preserve legacy CLI compatibility and unchanged build entrypoint",
                    "implementation_files": [rel(root, impl)],
                    "test_files": [rel(root, test)],
                    "status": "satisfied",
                },
            ]
        else:
            requirement_evidence = [
                {
                    "requirement": "Deliver the tool skill for THX handling and header inspection",
                    "implementation_files": [rel(root, impl)],
                    "test_files": [rel(root, test)],
                    "status": "satisfied",
                }
            ]
    elif stage == "verify":
        path = directory / "verify.md"
        path.write_text(
            "# Verification Report\n\n## Structural Validation\n\nPASS"
            "\n\n## Requirement Traceability\n\nVariable-length custom header payload, unpack correctness, legacy CLI compatibility, unchanged build entrypoint, and skill/header inspection evidence are mapped."
            "\n\n## Protected API and Scope\n\nPASS"
            "\n\n## Dependency Integrity\n\nPASS"
            "\n\n## Quality Gates\n\nConfigured commands are executed by the Runner. Custom header payload, unpack, compatibility, and skill checks are covered."
            "\n\n## Findings\n\nNone"
            "\n\n## Decision\n\nPASS\n",
            encoding="utf-8",
        )
        changed.append(rel(root, path))
    elif stage == "review":
        path = directory / "review.md"
        path.write_text(
            "# Independent Code Review\n\n## Scope Compliance\n\nPASS"
            "\n\n## Specification Compliance\n\nPASS"
            "\n\n## Clean Code Review\n\nPASS"
            "\n\n## Test Quality\n\nPASS"
            "\n\n## Findings\n\nNo blocking findings."
            "\n\n## Decision\n\nPASS\n",
            encoding="utf-8",
        )
        changed.append(rel(root, path))
    elif stage == "finalize":
        path = directory / "finalize.md"
        path.write_text(
            "# Finalize Receipt\n\n## Outcome\n\nPASS"
            "\n\n## Repository State\n\nReady for deterministic archive."
            "\n\n## Evidence\n\nSee `.sdd/evidence/` and stage handoffs."
            "\n\n## Residual Risks\n\nNone\n\n## Archive Authorization\n\nAUTHORIZED\n",
            encoding="utf-8",
        )
        changed.append(rel(root, path))
    elif stage == "retrospective":
        path = sdd_dir(root) / "changes" / state["change_id"] / "retrospective.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "# Retrospective\n\n## Delivered Outcome\n\nThe complete autonomous lifecycle closed."
            "\n\n## Planned Path vs Actual Path\n\nThe deterministic path matched the lifecycle."
            "\n\n## Failures and Recoveries\n\nNone."
            "\n\n## Agent Compliance\n\nAll stage boundaries and handoffs were observed."
            "\n\n## Quality Findings\n\nNo blocking findings."
            "\n\n## Remaining Risks\n\nReal-model behavior remains for later validation."
            "\n\n## Workflow Improvements\n\nUse this rehearsal as the control baseline.\n",
            encoding="utf-8",
        )
        changed.append(rel(root, path))
    else:
        raise SddError(f"Fixture executor cannot execute stage: {stage}")
    task = current_task(root, state) if stage == "apply" else None
    write_agent_result(
        root,
        f"Fixture completed {stage}",
        changed,
        task["id"] if task else None,
        requirement_evidence=requirement_evidence,
    )
    fixture_post_stage_delay_seconds = float(config.get("fixture_post_stage_delay_seconds", 0) or 0)
    if fixture_post_stage_delay_seconds > 0:
        time.sleep(fixture_post_stage_delay_seconds)


def main_spec_from_delta(delta: str, capability: str) -> str:
    body = re.sub(
        r"^##\s+(ADDED|MODIFIED|REMOVED|RENAMED)\s+Requirements\s*$",
        "## Requirements",
        delta,
        flags=re.MULTILINE,
    )
    if "## Requirements" not in body:
        body = "## Requirements\n\n" + body
    return f"# {capability.replace('-', ' ').title()}\n\n## Purpose\n\nVerified behavior delivered by the archived change.\n\n{body.strip()}\n"


def perform_archive(root: Path, state: dict[str, Any]) -> None:
    directory = change_dir(root, state)
    if not directory.exists():
        raise SddError(f"Active change directory is missing: {directory}")
    for delta in sorted((directory / "specs").glob("*/spec.md")):
        capability = delta.parent.name
        target = root / "openspec" / "specs" / capability / "spec.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(main_spec_from_delta(delta.read_text(encoding="utf-8"), capability), encoding="utf-8")
    archive = root / "openspec" / "changes" / "archive" / f"{dt.date.today().isoformat()}-{state['change_id']}"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        raise SddError(f"Archive already exists: {archive}")
    shutil.move(str(directory), str(archive))
    write_agent_result(
        root,
        "Runner synchronized main specs and archived the change",
        [rel(root, archive), "openspec/specs/"],
    )
    append_journal(root, {"event": "change_archived", "archive": rel(root, archive)})


def execute_stage(root: Path, state: dict[str, Any], dry_run: bool = False) -> None:
    if state["stage"] == "archive" and not dry_run:
        build_packet(root, state)
        perform_archive(root, state)
        return
    invoke_agent(root, state, dry_run)


def invoke_agent(root: Path, state: dict[str, Any], dry_run: bool) -> None:
    packet = build_packet(root, state)
    if dry_run:
        print(json.dumps(packet, indent=2, ensure_ascii=False))
        return
    config = load_config(root)
    result_path = sdd_dir(root) / "runtime" / "agent-result.json"
    if result_path.exists():
        result_path.unlink()
    if state.get("executor", config.get("executor", "opencode")) == "fixture":
        fixture_execute(root, state)
        append_journal(root, {"event": "fixture_finished", "stage": state["stage"]})
        return
    command = [
        "opencode",
        "run",
        "--format",
        "json",
        "--dir",
        str(root),
        "--title",
        f"sdd-{state['change_id']}-{state['stage']}",
        prompt_for(packet),
    ]
    model = config.get("model")
    if model:
        command[2:2] = ["--model", model]
    timeout = agent_timeout_for_state(root, state)
    try:
        result = run_command(command, root, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        timed_out = subprocess.CompletedProcess(
            command,
            -1,
            (exc.output or "") + f"\n[TIMEOUT] agent stage exceeded {timeout} seconds\n",
            None,
        )
        evidence = write_evidence(root, f"{state['stage']}-{state['iteration']}-agent-timeout", command, timed_out)
        append_journal(
            root,
            {
                "event": "agent_timed_out",
                "stage": state["stage"],
                "timeout_seconds": timeout,
                "evidence": evidence,
            },
        )
        raise SddError(f"Agent invocation timed out after {timeout} seconds; see {evidence}") from exc
    evidence = write_evidence(root, f"{state['stage']}-{state['iteration']}-agent", command, result)
    append_journal(
        root,
        {"event": "agent_finished", "stage": state["stage"], "exit_code": result.returncode, "evidence": evidence},
    )
    if result.returncode != 0:
        raise SddError(f"OpenCode invocation failed; see {evidence}")


def validate_sections(path: Path, sections: list[str]) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [section for section in sections if not re.search(rf"^##+\s+{re.escape(section)}\s*$", text, re.MULTILINE)]


def validate_stage_artifact(root: Path, state: dict[str, Any]) -> list[str]:
    stage = state["stage"]
    directory = change_dir(root, state)
    errors: list[str] = []
    if stage == "specs":
        specs = list((directory / "specs").glob("*/spec.md"))
        if not specs:
            return ["No delta spec found"]
        for spec in specs:
            text = spec.read_text(encoding="utf-8")
            if not re.search(r"\b(SHALL|MUST)\b", text):
                errors.append(f"{rel(root, spec)} has no SHALL/MUST requirement")
            if "#### Scenario:" not in text:
                errors.append(f"{rel(root, spec)} has no level-4 Scenario")
        return errors
    if stage == "tasks":
        artifact = directory / "tasks.md"
        if not artifact.exists():
            return ["Missing tasks.md"]
        text = artifact.read_text(encoding="utf-8")
        tasks = task_entries(root, state)
        if not tasks:
            errors.append("tasks.md contains no numbered unchecked task")
        if len(tasks) < MIN_APPLY_TASKS or len(tasks) > MAX_APPLY_TASKS:
            errors.append(f"tasks.md must contain {MIN_APPLY_TASKS}-{MAX_APPLY_TASKS} bounded tasks; found {len(tasks)}")
        unnumbered = re.findall(r"^- \[[ xX]\] (?!\d+\.\d+ ).+$", text, re.MULTILINE)
        if unnumbered:
            errors.append("tasks.md contains unnumbered or nested checkbox tasks")
        ids = [task["id"] for task in tasks]
        if len(ids) != len(set(ids)):
            errors.append("tasks.md contains duplicate task IDs")
        return errors
    if stage == "plan":
        artifact = directory / "plan.md"
        if not artifact.exists():
            return ["Missing plan.md"]
        for missing in validate_sections(artifact, REQUIRED_SECTIONS.get(stage, [])):
            errors.append(f"{rel(root, artifact)} missing section: {missing}")
        errors.extend(validate_plan_contracts(root, state))
        return errors
    if stage == "apply":
        return errors
    if stage == "archive":
        active = directory.exists()
        archives = list((root / "openspec" / "changes" / "archive").glob(f"*-{state['change_id']}"))
        if active:
            errors.append("Active change directory still exists")
        if not archives:
            errors.append("Archive directory not found")
        return errors
    if stage == "closed":
        return errors
    artifact = artifact_for(root, state, stage)
    if artifact is None or not artifact.exists():
        return [f"Missing required artifact: {required_output(root, state)}"]
    for missing in validate_sections(artifact, REQUIRED_SECTIONS.get(stage, [])):
        errors.append(f"{rel(root, artifact)} missing section: {missing}")
    if stage == "verify":
        text = normalize_text(artifact.read_text(encoding="utf-8"))
        expected_markers = {
            "custom_header_payload": ["custom header", "header payload", "variable-length", "variable length"],
            "unpack_correctness": ["unpack"],
            "compatibility": ["compatibility", "legacy cli", "original cli", "build entrypoint"],
            "skill_delivery": ["skill", "header inspection", "thx"],
        }
        for theme, markers in expected_markers.items():
            if not any(marker in text for marker in markers):
                errors.append(f"{rel(root, artifact)} missing competition verification evidence for {theme}")
    return errors


def validate_controls(root: Path) -> list[str]:
    manifest = load_json(sdd_dir(root) / "baseline" / "manifest.json")
    return (
        verify_hashes(root, manifest["control_hashes"])
        + verify_hashes(root, manifest["protected_files"])
        + verify_hashes(root, manifest["dependency_files"])
    )


def validate_scope(root: Path, stage: str) -> list[str]:
    competition = load_json(sdd_dir(root) / "policy" / "competition.yaml")
    changed = git_changed(root)
    allowed = competition["modification"]["allowed"]
    if stage == "apply":
        allowed = apply_allowed_paths(root, load_state(root))
    errors: list[str] = []
    for path in changed:
        if stage in {"apply", "review", "verify", "finalize", "archive"} and is_ephemeral_build_output(path):
            continue
        if path.startswith(".sdd/runtime/") or path.startswith(".sdd/evidence/"):
            continue
        if stage != "apply" and path.startswith("openspec/"):
            continue
        if stage == "retrospective" and path.startswith(".sdd/changes/"):
            continue
        if not matches(path, allowed):
            errors.append(f"Out-of-scope change: {path}")
    return errors


def verification_commands(root: Path, stage: str) -> list[list[str]]:
    policy = load_json(sdd_dir(root) / "policy" / "verification.yaml")
    gate_name = {
        "apply": "task_gate",
        "review": "review_gate",
        "verify": "verify_gate",
        "finalize": "closeout_gate",
        "archive": "closeout_gate",
    }.get(stage)
    names = policy.get(gate_name, []) if gate_name else []
    commands: list[list[str]] = []
    for name in names:
        value = policy["commands"].get(name)
        if value:
            commands.append(value)
    return commands


def execute_gates(root: Path, state: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    stage = state["stage"]
    errors = validate_controls(root)
    errors.extend(validate_scope(root, stage))
    errors.extend(validate_stage_artifact(root, state))
    result_path = sdd_dir(root) / "runtime" / "agent-result.json"
    agent_result: dict[str, Any] = {}
    if not result_path.exists():
        errors.append("Agent result is missing")
    else:
        try:
            agent_result = load_json(result_path)
            errors.extend(validate_agent_result(root, state, agent_result))
            errors.extend(validate_competition_requirement_coverage(root, state, agent_result))
            errors.extend(validate_plan_commitment_coverage(root, state, agent_result))
            if agent_result.get("status") != "completed":
                errors.append(f"Agent result status is not completed: {agent_result.get('status')}")
            if stage == "apply":
                task = current_task(root, state)
                if task is None:
                    errors.append("No current apply task exists")
                elif agent_result.get("task_id") != task["id"]:
                    errors.append(
                        f"Agent result task_id mismatch: expected {task['id']}, got {agent_result.get('task_id')}"
                    )
                else:
                    evidence = agent_result.get("requirement_evidence", [])
                    if isinstance(evidence, list):
                        errors.extend(
                            validate_apply_task_requirement_alignment(
                                root,
                                state,
                                task,
                                evidence,
                                current_task_contract(root, state),
                            )
                        )
        except SddError as exc:
            errors.append(str(exc))
    if stage == "apply":
        substantive = substantive_changed_paths(root)
        if not substantive:
            errors.append("Apply task made no substantive source or test change")
        declared = sorted(agent_result.get("files_changed", [])) if isinstance(agent_result.get("files_changed"), list) else []
        if declared and declared != sorted(substantive):
            errors.append(
                "Agent result files_changed does not match actual substantive changes: "
                f"declared={declared}, actual={sorted(substantive)}"
            )
    evidence: list[dict[str, Any]] = []
    should_test = stage in {"apply", "review", "verify", "finalize", "archive"}
    if should_test:
        commands = verification_commands(root, stage)
        if stage == "apply" and state.get("executor", load_config(root).get("executor")) != "fixture":
            focused, focused_errors = focused_test_commands(root, git_changed(root))
            commands.extend(focused)
            errors.extend(focused_errors)
        for index, command in enumerate(commands):
            if command and command[0] == "openspec.cmd" and os.name != "nt":
                command = ["openspec", *command[1:]]
            timeout_key = "focused_test_seconds" if stage == "apply" and index >= len(verification_commands(root, stage)) else "verification_seconds"
            timeout = load_config(root)["timeouts"].get(timeout_key, load_config(root)["timeouts"]["verification_seconds"])
            result = run_command(command, root, timeout=timeout)
            evidence_path = write_evidence(root, f"{stage}-{state['iteration']}-gate-{index}", command, result)
            evidence.append({"command": command, "exit_code": result.returncode, "path": evidence_path})
            if result.returncode != 0:
                errors.append(f"Verification command failed: {' '.join(command)}")
    return errors, evidence


def write_handoff(
    root: Path,
    state: dict[str, Any],
    completed_stage: str,
    next_stage: str,
    evidence: list[dict[str, Any]],
    soft_findings: list[str] | None = None,
) -> Path:
    handoff_dir = sdd_dir(root) / "runtime" / "handoffs"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    number = len(list(handoff_dir.glob("*.json"))) + 1
    artifacts: dict[str, str] = {}
    agent_result_path = sdd_dir(root) / "runtime" / "agent-result.json"
    agent_result = load_json(agent_result_path) if agent_result_path.exists() else {}
    directory = change_dir(root, state)
    if directory.exists():
        for path in directory.rglob("*"):
            if path.is_file():
                artifacts[rel(root, path)] = sha256(path)
    payload = {
        "schema_version": 1,
        "handoff_id": f"{number:03d}-{completed_stage}-to-{next_stage}",
        "run_id": state["run_id"],
        "change_id": state["change_id"],
        "completed_stage": completed_stage,
        "next_stage": next_stage,
        "objective": state["objective"],
        "repository": {"commit_before_checkpoint": git_head(root), "changed_files": git_changed(root)},
        "artifacts": artifacts,
        "evidence": evidence,
        "requirement_evidence": agent_result.get("requirement_evidence", []),
        "blocking_findings": [],
        "soft_findings": list(soft_findings or []),
        "open_findings": list(state.get("open_findings", [])),
        "residual_risks": list(agent_result.get("residual_risks", []))
        + ([] if completed_stage == "verify" else ["Full project verification has not run yet"]),
        "next_action": "execute_stage" if next_stage != "closed" else "emit_final_report",
        "created_at": now(),
    }
    path = handoff_dir / f"{number:03d}-{completed_stage}-to-{next_stage}.json"
    atomic_json(path, payload)
    atomic_json(sdd_dir(root) / "runtime" / "current-handoff.json", payload)
    audit = sdd_dir(root) / "changes" / state["change_id"] / "handoffs" / path.name
    atomic_json(audit, payload)
    return path


def checkpoint(root: Path, state: dict[str, Any], stage: str) -> str:
    config = load_config(root)
    if not config.get("git", {}).get("auto_commit", True):
        return git_head(root)
    git(root, "add", "--all")
    if not git(root, "status", "--porcelain", check=False):
        return git_head(root)
    git(root, "commit", "-m", f"sdd({state['change_id']}): complete {stage}")
    return git_head(root)


def next_stage_after(root: Path, state: dict[str, Any]) -> str:
    stage = state["stage"]
    if stage == "apply":
        if unchecked_tasks(root, state):
            return "apply"
        apply_file = change_dir(root, state) / "apply.md"
        if not apply_file.exists():
            apply_file.write_text(
                f"# Apply Receipt\n\n- Change: `{state['change_id']}`\n- Completed at: `{now()}`\n"
                f"- Iteration: `{state['iteration']}`\n- Tasks: all checked\n",
                encoding="utf-8",
            )
        return "verify"
    return STAGES[STAGES.index(stage) + 1]


def terminal_outcome(state: dict[str, Any]) -> str:
    if state.get("status") != "closed":
        return str(state.get("status", "unknown"))
    return str(state.get("terminal_outcome") or "closed_pass")


def forced_closeout_outcome(root: Path, state: dict[str, Any]) -> str:
    if state.get("stage") in {"apply", "review", "verify", "finalize", "archive", "retrospective", "closed"}:
        return "closed_partial"
    handoff_dir = sdd_dir(root) / "runtime" / "handoffs"
    if handoff_dir.exists() and any(handoff_dir.glob("*.json")):
        return "closed_partial"
    return "closed_fail"


def force_closeout(
    root: Path,
    state: dict[str, Any],
    *,
    trigger: str,
    reason: str,
    gate_errors: list[str] | None = None,
    recovery_actions: list[str] | None = None,
) -> dict[str, Any]:
    prior_stage = str(state.get("stage", "unknown"))
    outcome = forced_closeout_outcome(root, state)
    state["status"] = "closed"
    state["stage"] = "closed"
    state["terminal_outcome"] = outcome
    state["blocking_reason"] = reason
    state["pending_action"] = "emit_final_report"
    state["next_action"] = "emit_final_report"
    state["forced_closeout"] = {
        "trigger": trigger,
        "reason": reason,
        "stage": prior_stage,
        "gate_errors": list(gate_errors or []),
        "recovery_actions": list(recovery_actions or []),
        "created_at": now(),
    }
    save_state(root, state)
    emit_final_report(root, state)
    append_journal(
        root,
        {
            "event": "forced_closeout",
            "trigger": trigger,
            "stage": prior_stage,
            "outcome": outcome,
            "reason": reason,
            "gate_errors": list(gate_errors or []),
        },
    )
    return state


def gate_and_advance(root: Path) -> None:
    state = load_state(root)
    completed_task = current_task(root, state) if state["stage"] == "apply" else None
    errors, evidence = execute_gates(root, state)
    hard_errors, soft_errors = split_gate_findings(state["stage"], errors)
    if soft_errors:
        merge_open_findings(state, finding_records(state["stage"], soft_errors, deferred_to="verify"))
        append_journal(root, {"event": "gate_soft_findings_recorded", "stage": state["stage"], "errors": soft_errors})
    if hard_errors:
        state["status"] = "repair_required"
        state["pending_action"] = "execute_stage"
        key = state["stage"]
        state["retries"][key] = state["retries"].get(key, 0) + 1
        save_state(root, state)
        append_journal(root, {"event": "gate_failed", "stage": key, "errors": hard_errors, "soft_errors": soft_errors})
        maximum = stage_retry_budget(root)
        if state["retries"][key] > maximum:
            force_closeout(
                root,
                state,
                trigger="gate_retry_budget_exhausted",
                reason=f"Gate retry budget exhausted at stage {key}",
                gate_errors=hard_errors,
                recovery_actions=[
                    "Stopped retrying the same gate after retry budget exhaustion.",
                    "Recorded current gate errors for scoring and downstream review.",
                    "Emitted final delivery report and receipt instead of leaving the run mid-flight.",
                ],
            )
            print(f"FORCED_CLOSE {key} -> {terminal_outcome(load_state(root))}")
            return
        raise SddError("Gate failed:\n- " + "\n- ".join(hard_errors))
    if completed_task:
        complete_task(root, state, completed_task["id"])
        state.pop("task", None)
        append_journal(
            root,
            {"event": "apply_task_completed", "task_id": completed_task["id"], "title": completed_task["title"]},
        )
    completed = state["stage"]
    next_stage = next_stage_after(root, state)
    reconcile_open_findings(state, completed)
    handoff = write_handoff(root, state, completed, next_stage, evidence, soft_findings=soft_errors)
    if next_stage == "closed":
        preview = dict(state)
        preview["status"] = "closed"
        emit_final_report(root, preview)
    commit = checkpoint(root, state, completed)
    state["stage"] = next_stage
    state["status"] = "closed" if next_stage == "closed" else "running"
    if next_stage == "closed":
        state["terminal_outcome"] = "closed_pass"
    else:
        state.pop("terminal_outcome", None)
    if next_stage == "apply":
        next_task = unchecked_tasks(root, state)
        state["task"] = next_task[0]["id"] if next_task else None
    else:
        state["task"] = None
    state["last_verified_commit"] = commit
    state["last_handoff"] = rel(root, handoff)
    state["next_action"] = "emit_final_report" if next_stage == "closed" else "execute_stage"
    state["pending_action"] = "execute_stage"
    state["iteration"] += 1
    state["retries"].pop(completed, None)
    state.pop("blocking_reason", None)
    state.pop("forced_closeout", None)
    clear_failure_signature(root, state, completed)
    save_state(root, state)
    append_journal(root, {"event": "stage_advanced", "from": completed, "to": next_stage, "commit": commit})
    print(f"PASS {completed} -> {next_stage} at {commit}")


def run_once(args: argparse.Namespace) -> None:
    root = resolve_runtime_root(project_root(args.project))
    with execution_lock(root):
        state = load_state(root)
        if state["status"] == "closed":
            print("Run is already closed")
            return
        if not args.dry_run:
            validate_execution_preflight(root, state)
        execute_stage(root, state, args.dry_run)
        if not args.dry_run:
            gate_and_advance(root)


def gate(args: argparse.Namespace) -> None:
    gate_and_advance(resolve_runtime_root(project_root(args.project)))


def run_loop(args: argparse.Namespace) -> None:
    root = resolve_runtime_root(project_root(args.project))
    with execution_lock(root):
        steps = 0
        while steps < args.max_steps:
            state = load_state(root)
            if state["status"] in {"closed", "blocked"}:
                if state["status"] == "closed" and not (sdd_dir(root) / "delivery-report.md").exists():
                    emit_final_report(root, state)
                print(json.dumps(state, indent=2, ensure_ascii=False))
                return
            try:
                validate_execution_preflight(root, state)
                pending_action = state.get("pending_action", "execute_stage")
                if pending_action == "gate":
                    gate_and_advance(root)
                elif pending_action == "execute_stage":
                    if state["status"] == "repair_required":
                        restore_verified_checkpoint(root, state)
                        state["status"] = "running"
                        save_state(root, state)
                    execute_stage(root, state, False)
                    state = load_state(root)
                    state["pending_action"] = "gate"
                    save_state(root, state)
                    gate_and_advance(root)
                else:
                    raise SddError(f"Cannot continue run with pending action: {pending_action}")
            except SddError as exc:
                state = load_state(root)
                if state["status"] == "closed":
                    if not (sdd_dir(root) / "delivery-report.md").exists():
                        emit_final_report(root, state)
                    print(json.dumps(state, indent=2, ensure_ascii=False))
                    return
                key = state["stage"]
                signature, signature_count = record_failure_signature(root, state, str(exc))
                if state["status"] != "repair_required":
                    state["retries"][key] = state["retries"].get(key, 0) + 1
                maximum = stage_retry_budget(root)
                repeated_failure_budget = budget_value(root, "maximum_repeated_failure_signatures", 4)
                if signature_count > repeated_failure_budget:
                    force_closeout(
                        root,
                        state,
                        trigger="repeated_failure_signature_budget_exhausted",
                        reason=(
                            f"repeated failure signature exceeded budget: {signature} "
                            f"({signature_count}>{repeated_failure_budget})"
                        ),
                        recovery_actions=[
                            "Stopped repeating an identical failing step signature.",
                            "Captured the recurring failure signature in the final receipt.",
                            "Closed the run with best-effort scoring evidence.",
                        ],
                    )
                    print(json.dumps(load_state(root), indent=2, ensure_ascii=False))
                    return
                elif state["retries"].get(key, 0) > maximum:
                    force_closeout(
                        root,
                        state,
                        trigger="stage_retry_budget_exhausted",
                        reason=str(exc),
                        recovery_actions=[
                            "Stopped retrying the current stage after retry budget exhaustion.",
                            "Preserved current state, evidence, and error context in the final receipt.",
                            "Closed the run instead of leaving it blocked mid-stage.",
                        ],
                    )
                    print(json.dumps(load_state(root), indent=2, ensure_ascii=False))
                    return
                else:
                    state["status"] = "repair_required" if git_changed(root) else "running"
                save_state(root, state)
                append_journal(
                    root,
                    {"event": "repair_cycle_started", "stage": state["stage"], "reason": str(exc)},
                )
            steps += 1
        state = load_state(root)
        force_closeout(
            root,
            state,
            trigger="maximum_runner_steps_reached",
            reason=f"maximum runner steps reached: {args.max_steps}",
            recovery_actions=[
                "Stopped the unattended loop after exhausting the configured step budget.",
                "Preserved the latest state snapshot and evidence logs.",
                "Closed the run with a final receipt for downstream scoring.",
            ],
        )
        print(json.dumps(load_state(root), indent=2, ensure_ascii=False))
        return


def emit_final_report(root: Path, state: dict[str, Any]) -> Path:
    report = sdd_dir(root) / "delivery-report.md"
    receipt = sdd_dir(root) / "delivery-receipt.json"
    handoffs = list((sdd_dir(root) / "changes" / state["change_id"] / "handoffs").glob("*.json"))
    evidence = list((sdd_dir(root) / "evidence").glob("*.log"))
    outcome = terminal_outcome(state).upper()
    forced = state.get("forced_closeout", {})
    open_findings = list(state.get("open_findings", []))
    resolved_findings = list(state.get("resolved_findings", []))
    result_text = (
        "The persisted state, archive, handoffs, and configured gates completed consistently."
        if terminal_outcome(state) == "closed_pass"
        else (
            "The run was force-closed with best-effort scoring evidence after automated recovery limits were reached."
            if state["status"] == "closed"
            else f"The run stopped safely. Blocking reason: `{state.get('blocking_reason', 'unknown')}`."
        )
    )
    report.write_text(
        "# Autonomous SDD Delivery Report\n\n"
        f"- Outcome: `{outcome}`\n"
        f"- Terminal Status: `{state['status'].upper()}`\n"
        f"- Change: `{state['change_id']}`\n"
        f"- Objective: {state['objective']}\n"
        f"- Baseline commit: `{state['baseline_commit']}`\n"
        f"- Verified commit before final receipt: `{state['last_verified_commit']}`\n"
        f"- Model selection: `{state.get('model_selection', 'unknown')}`\n"
        f"- Stage handoffs: `{len(handoffs)}`\n"
        f"- Evidence logs: `{len(evidence)}`\n"
        f"- Open findings: `{len(open_findings)}`\n"
        f"- Resolved findings: `{len(resolved_findings)}`\n\n"
        f"## Result\n\n{result_text}\n",
        encoding="utf-8",
    )
    atomic_json(
        receipt,
        {
            "schema_version": 1,
            "run_id": state["run_id"],
            "change_id": state["change_id"],
            "status": state["status"],
            "outcome": terminal_outcome(state),
            "objective": state["objective"],
            "stage": state.get("stage"),
            "last_verified_commit": state.get("last_verified_commit"),
            "report": rel(root, report),
            "passed_handoffs": len(handoffs),
            "evidence_logs": len(evidence),
            "open_findings": open_findings,
            "resolved_findings": resolved_findings,
            "forced_closeout": forced if isinstance(forced, dict) else {},
            "score_signals": {
                "completed_lifecycle": terminal_outcome(state) == "closed_pass",
                "forced_closeout_used": bool(forced),
                "best_effort_result_available": state["status"] == "closed",
            },
        },
    )
    return report


def status(args: argparse.Namespace) -> None:
    root = resolve_runtime_root(project_root(args.project))
    print(json.dumps(runtime_overview(root), indent=2, ensure_ascii=False))


def recover(args: argparse.Namespace) -> None:
    requested_project = project_root(args.project)
    result = recovery_report(requested_project)
    root = resolve_runtime_root(requested_project)
    atomic_json(sdd_dir(root) / "runtime" / "recovery.json", result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["exit_code"] != 0:
        raise SddExit(result["reason"], result["exit_code"])


def autorecover(args: argparse.Namespace) -> None:
    requested_project = project_root(args.project)
    deadline = time.time() + max(0, float(getattr(args, "retry_seconds", 0) or 0))
    while True:
        result = recovery_report(requested_project)
        root = resolve_runtime_root(requested_project)
        atomic_json(sdd_dir(root) / "runtime" / "recovery.json", result)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        action = result["recommended_action"]
        try:
            if action == "resume":
                resume(argparse.Namespace(project=str(requested_project), dry_run=args.dry_run))
                return
            if action == "restore_and_resume":
                if not args.dry_run:
                    restore_verified_checkpoint(root, load_state(root))
                resume(argparse.Namespace(project=str(requested_project), dry_run=args.dry_run))
                return
            if action == "none":
                return
            raise SddExit(result["reason"], result["exit_code"])
        except SddError as exc:
            if "Another Runner owns the workspace lock" in str(exc) and time.time() < deadline:
                time.sleep(0.2)
                continue
            raise


def resolve_output_path(root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve()


def rehearse_recovery(args: argparse.Namespace) -> None:
    source_root = project_root(args.project)
    if not (source_root / ".sdd").exists():
        init_project(argparse.Namespace(project=str(source_root), force=False))
    ensure_git_identity(source_root)
    baseline_file = sdd_dir(source_root) / "baseline" / "manifest.json"
    if not baseline_file.exists():
        baseline(argparse.Namespace(project=str(source_root)))
    services = create_runtime_services(source_root)
    with services.locks():
        snapshot = services.workspace.initialize(VERSION)
    root = services.workspace.work_project_root
    objective_bundle = resolve_competition_objective(getattr(args, "task", None), source_root)
    objective = objective_bundle["effective_objective"]
    change_id = unique_change_id(root, args.change_id or slugify(objective))
    start(
        argparse.Namespace(
            project=str(root),
            change_id=change_id,
            objective=objective,
            objective_bundle=objective_bundle,
            executor="fixture",
            source_root=str(source_root),
            work_root=str(snapshot.work_root),
            source_head=snapshot.source_head,
            baseline_commit=snapshot.baseline_commit,
            run_branch=snapshot.run_branch,
            source_status=snapshot.source_status.to_dict(),
        )
    )
    write_active_run_locator(
        source_root,
        {
            "run_id": snapshot.work_root.parent.name,
            "run_root": str(snapshot.work_root.parent),
            "work_project_root": str(root),
            "source_root": str(source_root),
        },
    )
    lock_path = root / ".sdd" / "runtime" / "execution.lock"
    lock_path.write_text(json.dumps({"pid": 999999, "created_at": now()}), encoding="utf-8")
    recovery = recovery_report(source_root)
    autorecover(
        argparse.Namespace(
            project=str(source_root),
            dry_run=False,
            retry_seconds=args.retry_seconds,
        )
    )
    compete(
        argparse.Namespace(
            project=str(source_root),
            task=args.task,
            change_id=change_id,
            executor="fixture",
            max_steps=args.max_steps,
        )
    )
    final_state = load_state(source_root)
    artifacts_dir = resolve_output_path(source_root, getattr(args, "artifacts_dir", None))
    if artifacts_dir is not None:
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        atomic_json(artifacts_dir / "initial-recovery.json", recovery)
        atomic_json(artifacts_dir / "final-state.json", final_state)
    payload = {
        "kind": "recovery_rehearsal_result",
        "project": str(source_root),
        "change_id": change_id,
        "objective": objective,
        "initial_recovery_decision": recovery["decision"],
        "initial_recommended_action": recovery["recommended_action"],
        "final_status": final_state["status"],
        "delivery_commit": final_state.get("delivery_commit"),
        "report": str(source_root / ".sdd" / "delivery-report.md"),
        "json_out": str(resolve_output_path(source_root, getattr(args, "json_out", None))) if getattr(args, "json_out", None) else None,
        "artifacts_dir": str(artifacts_dir) if artifacts_dir is not None else None,
    }
    json_out = resolve_output_path(source_root, getattr(args, "json_out", None))
    if json_out is not None:
        json_out.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(json_out, payload)
    if artifacts_dir is not None:
        atomic_json(artifacts_dir / "rehearsal-result.json", payload)
    print(json.dumps(payload, ensure_ascii=False))


def recovery_report(requested_project: Path) -> dict[str, Any]:
    root = resolve_runtime_root(requested_project)
    state = load_state(root)
    metadata = runtime_metadata(root)
    errors = validate_controls(root)
    handoff_path = sdd_dir(root) / "runtime" / "current-handoff.json"
    if state.get("last_handoff") and not handoff_path.exists():
        errors.append("Current handoff is missing")
    if state.get("last_verified_commit") and git_head(root) != state["last_verified_commit"]:
        errors.append("Git HEAD differs from last verified commit")
    changed = git_changed(root)
    if state.get("status") == "running" and changed:
        errors.append("Running state has unexpected uncommitted changes: " + ", ".join(changed))
    decision = recovery_decision(state, errors, bool(changed))
    command = recovery_command(requested_project, decision)
    plan = recovery_plan(decision, command)
    exit_code = recovery_exit_code(decision)
    return {
        "status": "FAIL" if errors else "PASS",
        "run_id": state["run_id"],
        "stage": state["stage"],
        "git_head": git_head(root),
        "workspace": metadata,
        "errors": errors,
        "next_action": state["next_action"],
        "decision": decision["decision"],
        "recommended_action": decision["recommended_action"],
        "resume_supported": decision["recommended_action"] in {"resume", "restore_and_resume"},
        "reason": decision["reason"],
        "recommended_command": command,
        "recovery_plan": plan,
        "exit_code": exit_code,
    }


def recovery_decision(state: dict[str, Any], errors: list[str], dirty: bool) -> dict[str, str]:
    status = state.get("status")
    if status == "closed":
        return {
            "decision": "closed",
            "recommended_action": "none",
            "reason": "Run is already closed",
        }
    if status == "blocked":
        return {
            "decision": "blocked",
            "recommended_action": "manual_repair",
            "reason": state.get("blocking_reason", "Run is blocked"),
        }
    if status in {"running", "repair_required"} and dirty and state.get("last_verified_commit"):
        return {
            "decision": "restore_ready",
            "recommended_action": "restore_and_resume",
            "reason": "Unverified in-progress changes detected; restore last verified commit and resume current stage",
        }
    if errors:
        return {
            "decision": "manual_repair_required",
            "recommended_action": "manual_repair",
            "reason": errors[0],
        }
    pending_action = state.get("pending_action", "execute_stage")
    if pending_action in {"execute_stage", "gate"}:
        return {
            "decision": "resume_ready",
            "recommended_action": "resume",
            "reason": f"Run can continue from pending action: {pending_action}",
        }
    return {
        "decision": "manual_repair_required",
        "recommended_action": "manual_repair",
        "reason": f"Unsupported pending action: {pending_action}",
    }


def recovery_command(project: Path, decision: dict[str, str]) -> list[str]:
    action = decision["recommended_action"]
    if action == "resume":
        return [sys.executable, str(project / ".sdd" / "bin" / "sdd.py"), "--project", str(project), "resume"]
    if action == "restore_and_resume":
        return [sys.executable, str(project / ".sdd" / "bin" / "sdd.py"), "--project", str(project), "autorecover"]
    if action == "none":
        return []
    return []


def recovery_plan(decision: dict[str, str], command: list[str]) -> list[dict[str, Any]]:
    action = decision["recommended_action"]
    if action == "resume":
        return [
            {
                "kind": "execute",
                "description": decision["reason"],
                "command": command,
            }
        ]
    if action == "restore_and_resume":
        return [
            {
                "kind": "execute",
                "description": decision["reason"],
                "command": command,
            }
        ]
    if action == "manual_repair":
        return [
            {
                "kind": "manual",
                "description": decision["reason"],
                "command": [],
            }
        ]
    return [
        {
            "kind": "noop",
            "description": decision["reason"],
            "command": [],
        }
    ]


def recovery_exit_code(decision: dict[str, str]) -> int:
    mapping = {
        "resume_ready": 0,
        "restore_ready": 0,
        "closed": 0,
        "manual_repair_required": 3,
        "blocked": 4,
    }
    return mapping.get(decision["decision"], 2)


def runtime_metadata(root: Path) -> dict[str, Any]:
    state_path_value = state_path(root)
    if not state_path_value.exists():
        return {}
    metadata = load_state(root)
    work_root = metadata.get("work_root")
    return {
        "project_root": metadata.get("source_root", metadata.get("project_root")),
        "work_root": work_root,
        "work_project_root": str(root.resolve()),
        "baseline_commit": metadata.get("baseline_commit"),
        "run_branch": metadata.get("run_branch"),
        "source_head": metadata.get("source_head"),
        "source_status": metadata.get("source_status"),
    }


def runtime_overview(root: Path) -> dict[str, Any]:
    overview: dict[str, Any] = {"workspace": runtime_metadata(root)}
    state_path_value = state_path(root)
    if state_path_value.exists():
        overview["state"] = load_state(root)
    else:
        overview["state"] = None
    return overview


def resolve_runtime_root(project: Path) -> Path:
    locator = load_active_run_locator(project)
    if locator is not None:
        work_root = Path(str(locator["work_project_root"]))
        if work_root.exists() and state_path(work_root).exists():
            return work_root
        raise SddError(f"Recorded work copy is missing: {work_root}")
    if state_path(project).exists():
        return project
    raise SddError(f"No runtime state found in {project}")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="sdd", description="Autonomous competition SDD runner")
    result.add_argument("--project", help="Target project directory")
    result.add_argument("--version", action="version", version=VERSION)
    sub = result.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Install the project skeleton")
    init.add_argument("project")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=init_project)

    competition = sub.add_parser("compete", help="Run the entire competition workflow with one command")
    competition.add_argument(
        "--task",
        help="Optional task file path or inline task text; default is the built-in C++ competition objective",
    )
    competition.add_argument("--change-id", help="Optional kebab-case change identifier")
    competition.add_argument("--executor", choices=["opencode", "fixture"], default="opencode")
    competition.add_argument("--max-steps", type=int, default=50)
    competition.set_defaults(func=compete)

    sub.add_parser("doctor", help="Check required tools").set_defaults(func=doctor)
    sub.add_parser("baseline", help="Freeze policies and protected files").set_defaults(func=baseline)

    start_cmd = sub.add_parser("start", help="Start a bounded change")
    start_cmd.add_argument("change_id")
    start_cmd.add_argument("objective")
    start_cmd.set_defaults(func=start)

    once = sub.add_parser("run-once", help="Execute one stage or apply task")
    once.add_argument("--dry-run", action="store_true")
    once.set_defaults(func=run_once)

    sub.add_parser("gate", help="Validate outputs and advance without invoking OpenCode").set_defaults(func=gate)

    loop = sub.add_parser("run", help="Run autonomously until closed or blocked")
    loop.add_argument("--max-steps", type=int, default=50)
    loop.set_defaults(func=run_loop)

    resume_cmd = sub.add_parser("resume", help="Continue a resumable run from its latest checkpoint")
    resume_cmd.add_argument("--dry-run", action="store_true")
    resume_cmd.set_defaults(func=resume)

    autorecover_cmd = sub.add_parser("autorecover", help="Recover a run and execute safe automatic recovery steps")
    autorecover_cmd.add_argument("--dry-run", action="store_true")
    autorecover_cmd.add_argument("--retry-seconds", type=float, default=0)
    autorecover_cmd.set_defaults(func=autorecover)

    rehearse_recovery_cmd = sub.add_parser(
        "rehearse-recovery",
        help="Create a deterministic interrupted run, recover it automatically, and finish the workflow",
    )
    rehearse_recovery_cmd.add_argument(
        "--task",
        help="Optional task file path or inline task text; default is the built-in C++ competition objective",
    )
    rehearse_recovery_cmd.add_argument("--change-id", help="Optional kebab-case change identifier")
    rehearse_recovery_cmd.add_argument("--max-steps", type=int, default=50)
    rehearse_recovery_cmd.add_argument("--retry-seconds", type=float, default=10)
    rehearse_recovery_cmd.add_argument("--json-out", help="Optional path to write the final rehearsal summary JSON")
    rehearse_recovery_cmd.add_argument(
        "--artifacts-dir",
        help="Optional directory to write rehearsal artifacts such as initial recovery and final state JSON",
    )
    rehearse_recovery_cmd.set_defaults(func=rehearse_recovery)

    sub.add_parser("status", help="Print machine state").set_defaults(func=status)
    sub.add_parser("recover", help="Validate and recover a run").set_defaults(func=recover)
    sub.add_parser("migrate-tasks", help="Collapse legacy nested checkboxes into bounded tasks").set_defaults(
        func=migrate_tasks
    )
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        args.func(args)
        return 0
    except SddExit as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    except (SddError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
