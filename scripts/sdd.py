#!/usr/bin/env python3
"""Autonomous SDD runner.

The runner intentionally uses only the Python standard library. Files ending in
.yaml are JSON-compatible YAML so they can be parsed deterministically without
installing dependencies on a competition machine.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any


VERSION = "0.2.2"
MIN_APPLY_TASKS = 3
MAX_APPLY_TASKS = 20
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
            changed.append(path.strip('"'))
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
    root = project_root(args.project)
    objective = read_task(args.task, root)
    preexisting_git = (root / ".git").exists()
    if preexisting_git and git_changed(root):
        raise SddError("One-command competition execution requires a clean repository before start")
    if not (root / ".sdd").exists():
        init_project(argparse.Namespace(project=str(root), force=False))
    ensure_git_identity(root)
    detected = detect_project(root)
    configure_detected_project(root, detected)
    config = load_config(root)
    config["executor"] = args.executor
    save_config(root, config)
    if not preexisting_git or not git(root, "rev-parse", "--verify", "HEAD", check=False):
        commit_all(root, "chore: establish competition baseline")
    else:
        commit_all(root, "chore: install autonomous competition harness")
    baseline(argparse.Namespace(project=str(root)))
    change_id = unique_change_id(root, args.change_id or slugify(objective))
    start(argparse.Namespace(project=str(root), change_id=change_id, objective=objective))
    try:
        run_loop(argparse.Namespace(project=str(root), max_steps=args.max_steps))
    finally:
        if state_path(root).exists():
            state = load_state(root)
            if state["status"] in {"closed", "blocked"}:
                report = emit_final_report(root, state)
                if git_changed(root):
                    final_commit = commit_all(root, f"sdd({state['change_id']}): record final delivery")
                    state["delivery_report"] = rel(root, report)
                    state["delivery_commit"] = final_commit
                    save_state(root, state)
    final = load_state(root)
    print(f"RESULT={final['status'].upper()}")
    print(f"REPORT={sdd_dir(root) / 'delivery-report.md'}")
    if final["status"] != "closed":
        raise SddError(f"Competition run ended as {final['status']}")


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
    if (root / "pyproject.toml").exists() or (root / "pytest.ini").exists():
        return {
            "kind": "python",
            "quick_check": [sys.executable, "-m", "compileall", "-q", "src"],
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


def read_task(value: str, root: Path) -> str:
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
    manifest = {
        "schema_version": 1,
        "created_at": now(),
        "runner_version": VERSION,
        "git_head": git_head(root),
        "control_hashes": protected_control_hashes(root),
        "protected_files": capture_files(root, protected_patterns),
        "dependency_files": capture_files(root, competition["dependencies"]["manifest_paths"]),
    }
    atomic_json(sdd_dir(root) / "baseline" / "manifest.json", manifest)
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
    state = {
        "schema_version": 1,
        "run_id": f"{dt.datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:8]}",
        "change_id": change,
        "objective": args.objective,
        "stage": "brainstorm",
        "status": "running",
        "iteration": 0,
        "task": None,
        "baseline_commit": load_json(baseline_file)["git_head"],
        "last_verified_commit": git_head(root),
        "last_handoff": None,
        "next_action": "execute_stage",
        "model_selection": load_config(root).get("model") or "opencode-default",
        "retries": {},
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
    pattern = re.compile(r"^- \[(?P<mark>[ xX])\] (?P<id>\d+\.\d+) (?P<title>.+)$", re.MULTILINE)
    for match in pattern.finditer(path.read_text(encoding="utf-8")):
        entries.append(
            {
                "id": match.group("id"),
                "title": match.group("title").strip(),
                "completed": match.group("mark").lower() == "x",
            }
        )
    return entries


def unchecked_tasks(root: Path, state: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry for entry in task_entries(root, state) if not entry["completed"]]


def current_task(root: Path, state: dict[str, Any]) -> dict[str, Any] | None:
    tasks = unchecked_tasks(root, state)
    return tasks[0] if tasks else None


def complete_task(root: Path, state: dict[str, Any], task_id: str) -> None:
    path = change_dir(root, state) / "tasks.md"
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^- \[ \] {re.escape(task_id)} (?P<title>.+)$", re.MULTILINE)
    updated, count = pattern.subn(rf"- [x] {task_id} \g<title>", text, count=1)
    if count != 1:
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
    reads = [
        ".sdd/AGENT-INSTRUCTIONS.md",
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
        for name in [
            "brainstorm.md",
            "proposal.md",
            "design.md",
            "tasks.md",
            "plan.md",
            "apply.md",
            "review.md",
            "verify.md",
        ]:
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
        allowed = sorted(set(allowed) & set(project_policy["change_boundaries"]["allowed"])) or project_policy[
            "change_boundaries"
        ]["allowed"]
        allowed.extend(
            [
                f"openspec/changes/{state['change_id']}/tasks.md",
                f"openspec/changes/{state['change_id']}/apply.md",
                ".sdd/runtime/**",
                ".sdd/evidence/**",
            ]
        )
    packet = {
        "schema_version": 1,
        "run_id": state["run_id"],
        "change_id": state["change_id"],
        "stage": stage,
        "role": "implementation" if stage == "apply" else stage,
        "objective": state["objective"],
        "required_output": required_output(root, state),
        "task_id": current_task(root, state)["id"] if stage == "apply" and current_task(root, state) else None,
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
        ],
        "acceptance": {
            "write_result": ".sdd/runtime/agent-result.json",
            "result_statuses": ["completed", "failed", "blocked"],
            "required_sections": REQUIRED_SECTIONS.get(stage, []),
            "tests": load_json(sdd_dir(root) / "policy" / "verification.yaml")["commands"],
        },
    }
    packet_path = sdd_dir(root) / "runtime" / "task-packet.json"
    atomic_json(packet_path, packet)
    return packet


def prompt_for(packet: dict[str, Any]) -> str:
    return (
        "You are a bounded executor in an unattended competition workflow. "
        "Read .sdd/runtime/task-packet.json and every required file. "
        "Use the listed stage template exactly and preserve every required section. "
        "Perform exactly the declared stage or one apply task. "
        "Do not commit or change lifecycle state. Do not modify policy, baseline, runner, schema, "
        "dependency manifests, protected API, or forbidden paths. "
        "Write .sdd/runtime/agent-result.json using status, summary, files_read, files_changed, "
        "commands_run, tests, deviations, blocking_reason, and task_id. "
        "For apply, task_id must exactly match the packet and you must not edit task checkboxes; "
        "the Runner owns task completion state. "
        "Do not ask questions; if essential intent is ambiguous, return status blocked with the exact reason. "
        f"Current stage: {packet['stage']}. Required output: {packet['required_output']}."
    )


def write_agent_result(root: Path, summary: str, changed: list[str], task_id: str | None = None) -> None:
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
        },
    )


def fixture_execute(root: Path, state: dict[str, Any]) -> None:
    """Deterministic lifecycle executor used to validate orchestration itself."""
    stage = state["stage"]
    if load_config(root).get("fixture_fail_stage") == stage:
        raise SddError(f"Injected deterministic failure at stage {stage}")
    directory = change_dir(root, state)
    changed: list[str] = []
    if stage == "brainstorm":
        path = directory / "brainstorm.md"
        path.write_text(
            "# Brainstorm\n\n## Objective\n\n"
            + state["objective"]
            + "\n\n## Current State\n\nProject inspected by the deterministic rehearsal executor."
            "\n\n## Binding Constraints\n\nPreserve protected APIs, dependencies, policies, and build files."
            "\n\n## Scope\n\nDeliver only the requested bounded behavior."
            "\n\n## Alternatives\n\n### Option A\n\nMinimal compatible change.\n\n### Option B\n\nBroader redesign."
            "\n\n## Decision\n\nUse the minimal compatible change.\n\n## Risks\n\nRegression risk is controlled by tests."
            "\n\n## Blocking Ambiguities\n\nNone\n",
            encoding="utf-8",
        )
        changed.append(rel(root, path))
    elif stage == "proposal":
        path = directory / "proposal.md"
        path.write_text(
            "# Change Proposal\n\n## Why\n\n"
            + state["objective"]
            + "\n\n## What Changes\n\nAdd one bounded sample capability and its verification."
            "\n\n## Capabilities\n\n### New Capabilities\n\n- `competition-sample`: Bounded competition behavior."
            "\n\n### Modified Capabilities\n\n- None\n\n## Impact\n\nSource and tests only; no API or dependency changes.\n",
            encoding="utf-8",
        )
        changed.append(rel(root, path))
    elif stage == "specs":
        path = directory / "specs" / "competition-sample" / "spec.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "## ADDED Requirements\n\n### Requirement: Produce bounded competition behavior\n\n"
            "The system MUST provide the requested behavior without changing protected interfaces.\n\n"
            "#### Scenario: Successful bounded delivery\n\n"
            "- **WHEN** the competition task is executed\n"
            "- **THEN** the requested behavior and automated evidence are produced\n",
            encoding="utf-8",
        )
        changed.append(rel(root, path))
    elif stage == "design":
        path = directory / "design.md"
        path.write_text(
            "# Technical Design\n\n## Context\n\nA bounded competition change is required."
            "\n\n## Goals\n\nImplement the requested behavior with tests."
            "\n\n## Non-Goals\n\nNo API, dependency, policy, or architecture expansion."
            "\n\n## Existing API Verification\n\n| API | Source | Result |\n|---|---|---|\n| protected surface | baseline | unchanged |"
            "\n\n## Architecture and Boundaries\n\nKeep implementation within detected source paths."
            "\n\n## Decisions\n\nUse a minimal compatible implementation."
            "\n\n## Testing Strategy\n\nRun focused behavior checks and configured full gates.\n",
            encoding="utf-8",
        )
        changed.append(rel(root, path))
    elif stage == "tasks":
        path = directory / "tasks.md"
        path.write_text(
            "# Tasks\n\n## 1. Implementation\n\n"
            "- [ ] 1.1 Implement the bounded sample behavior and focused evidence\n"
            "- [ ] 1.2 Add bounded integration evidence\n"
            "- [ ] 1.3 Verify the implementation and clean-code constraints\n",
            encoding="utf-8",
        )
        changed.append(rel(root, path))
    elif stage == "plan":
        path = directory / "plan.md"
        path.write_text(
            "# Execution Plan\n\n## Execution Strategy\n\nUse one isolated session per task."
            "\n\n## Tasks\n\nImplement one task at a time using minimal changes and verification."
            "\n\n## Verification\n\nRun configured task and full gates."
            "\n\n## Checkpoint Strategy\n\nCommit after each passing deterministic gate.\n",
            encoding="utf-8",
        )
        changed.append(rel(root, path))
    elif stage == "apply":
        task = current_task(root, state)
        output = root / "src" / f"autonomous_sdd_rehearsal_{task['id'].replace('.', '_')}.txt"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(f"deterministic competition rehearsal task {task['id']} completed\n", encoding="utf-8")
        changed.append(rel(root, output))
    elif stage == "verify":
        path = directory / "verify.md"
        path.write_text(
            "# Verification Report\n\n## Structural Validation\n\nPASS"
            "\n\n## Requirement Traceability\n\nRequirement, implementation, and evidence are mapped."
            "\n\n## Protected API and Scope\n\nPASS"
            "\n\n## Dependency Integrity\n\nPASS"
            "\n\n## Quality Gates\n\nConfigured commands are executed by the Runner."
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
    write_agent_result(root, f"Fixture completed {stage}", changed, task["id"] if task else None)


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
    if config.get("executor", "opencode") == "fixture":
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
    timeout = int(config["timeouts"]["agent_seconds"])
    result = run_command(command, root, timeout=timeout)
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
    errors: list[str] = []
    for path in changed:
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
    if not result_path.exists():
        errors.append("Agent result is missing")
    else:
        try:
            agent_result = load_json(result_path)
            required = {
                "status",
                "summary",
                "files_read",
                "files_changed",
                "commands_run",
                "tests",
                "deviations",
                "blocking_reason",
            }
            missing = sorted(required - set(agent_result))
            if missing:
                errors.append("Agent result missing fields: " + ", ".join(missing))
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
        except SddError as exc:
            errors.append(str(exc))
    if stage == "apply":
        substantive = [
            path
            for path in git_changed(root)
            if not path.startswith(("openspec/", ".sdd/"))
        ]
        if not substantive:
            errors.append("Apply task made no substantive source or test change")
    evidence: list[dict[str, Any]] = []
    should_test = stage in {"apply", "review", "verify", "finalize", "archive"}
    if should_test:
        commands = verification_commands(root, stage)
        for index, command in enumerate(commands):
            if command and command[0] == "openspec.cmd" and os.name != "nt":
                command = ["openspec", *command[1:]]
            result = run_command(command, root, timeout=load_config(root)["timeouts"]["verification_seconds"])
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
) -> Path:
    handoff_dir = sdd_dir(root) / "runtime" / "handoffs"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    number = len(list(handoff_dir.glob("*.json"))) + 1
    artifacts: dict[str, str] = {}
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
        "blocking_findings": [],
        "residual_risks": [],
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


def gate_and_advance(root: Path) -> None:
    state = load_state(root)
    completed_task = current_task(root, state) if state["stage"] == "apply" else None
    errors, evidence = execute_gates(root, state)
    if errors:
        state["status"] = "repair_required"
        key = state["stage"]
        state["retries"][key] = state["retries"].get(key, 0) + 1
        save_state(root, state)
        append_journal(root, {"event": "gate_failed", "stage": key, "errors": errors})
        maximum = load_json(sdd_dir(root) / "policy" / "autonomy.yaml")["retry"]["per_stage"]
        if state["retries"][key] > maximum:
            state["status"] = "blocked"
            state["blocking_reason"] = "retry_budget_exhausted"
            save_state(root, state)
        raise SddError("Gate failed:\n- " + "\n- ".join(errors))
    if completed_task:
        complete_task(root, state, completed_task["id"])
        append_journal(
            root,
            {"event": "apply_task_completed", "task_id": completed_task["id"], "title": completed_task["title"]},
        )
    completed = state["stage"]
    next_stage = next_stage_after(root, state)
    handoff = write_handoff(root, state, completed, next_stage, evidence)
    if next_stage == "closed":
        preview = dict(state)
        preview["status"] = "closed"
        emit_final_report(root, preview)
    commit = checkpoint(root, state, completed)
    state["stage"] = next_stage
    state["status"] = "closed" if next_stage == "closed" else "running"
    state["last_verified_commit"] = commit
    state["last_handoff"] = rel(root, handoff)
    state["next_action"] = "emit_final_report" if next_stage == "closed" else "execute_stage"
    state["iteration"] += 1
    state["retries"].pop(completed, None)
    save_state(root, state)
    append_journal(root, {"event": "stage_advanced", "from": completed, "to": next_stage, "commit": commit})
    print(f"PASS {completed} -> {next_stage} at {commit}")


def run_once(args: argparse.Namespace) -> None:
    root = project_root(args.project)
    state = load_state(root)
    if state["status"] == "closed":
        print("Run is already closed")
        return
    execute_stage(root, state, args.dry_run)
    if not args.dry_run:
        gate_and_advance(root)


def gate(args: argparse.Namespace) -> None:
    gate_and_advance(project_root(args.project))


def run_loop(args: argparse.Namespace) -> None:
    root = project_root(args.project)
    steps = 0
    while steps < args.max_steps:
        state = load_state(root)
        if state["status"] in {"closed", "blocked"}:
            if state["status"] == "closed" and not (sdd_dir(root) / "delivery-report.md").exists():
                emit_final_report(root, state)
            print(json.dumps(state, indent=2, ensure_ascii=False))
            return
        try:
            execute_stage(root, state, False)
            gate_and_advance(root)
        except SddError as exc:
            state = load_state(root)
            key = state["stage"]
            if state["status"] != "repair_required":
                state["retries"][key] = state["retries"].get(key, 0) + 1
            maximum = load_json(sdd_dir(root) / "policy" / "autonomy.yaml")["retry"]["per_stage"]
            if state["retries"].get(key, 0) > maximum:
                state["status"] = "blocked"
                state["blocking_reason"] = str(exc)
            else:
                state["status"] = "running"
            save_state(root, state)
            if state["status"] == "blocked":
                raise
            append_journal(
                root,
                {"event": "repair_cycle_started", "stage": state["stage"], "reason": str(exc)},
            )
        steps += 1
    state = load_state(root)
    state["status"] = "blocked"
    state["blocking_reason"] = f"maximum runner steps reached: {args.max_steps}"
    save_state(root, state)
    raise SddError(state["blocking_reason"])


def emit_final_report(root: Path, state: dict[str, Any]) -> Path:
    report = sdd_dir(root) / "delivery-report.md"
    handoffs = list((sdd_dir(root) / "changes" / state["change_id"] / "handoffs").glob("*.json"))
    evidence = list((sdd_dir(root) / "evidence").glob("*.log"))
    result_text = (
        "The persisted state, archive, handoffs, and configured gates completed consistently."
        if state["status"] == "closed"
        else f"The run stopped safely. Blocking reason: `{state.get('blocking_reason', 'unknown')}`."
    )
    report.write_text(
        "# Autonomous SDD Delivery Report\n\n"
        f"- Outcome: `{state['status'].upper()}`\n"
        f"- Change: `{state['change_id']}`\n"
        f"- Objective: {state['objective']}\n"
        f"- Baseline commit: `{state['baseline_commit']}`\n"
        f"- Verified commit before final receipt: `{state['last_verified_commit']}`\n"
        f"- Model selection: `{state.get('model_selection', 'unknown')}`\n"
        f"- Stage handoffs: `{len(handoffs)}`\n"
        f"- Evidence logs: `{len(evidence)}`\n\n"
        f"## Result\n\n{result_text}\n",
        encoding="utf-8",
    )
    return report


def status(args: argparse.Namespace) -> None:
    root = project_root(args.project)
    print(json.dumps(load_state(root), indent=2, ensure_ascii=False))


def recover(args: argparse.Namespace) -> None:
    root = project_root(args.project)
    state = load_state(root)
    errors = validate_controls(root)
    handoff_path = sdd_dir(root) / "runtime" / "current-handoff.json"
    if state.get("last_handoff") and not handoff_path.exists():
        errors.append("Current handoff is missing")
    if state.get("last_verified_commit") and git_head(root) != state["last_verified_commit"]:
        errors.append("Git HEAD differs from last verified commit")
    result = {
        "status": "FAIL" if errors else "PASS",
        "run_id": state["run_id"],
        "stage": state["stage"],
        "git_head": git_head(root),
        "errors": errors,
        "next_action": state["next_action"],
    }
    atomic_json(sdd_dir(root) / "runtime" / "recovery.json", result)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if errors:
        raise SddError("Recovery failed")


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
    competition.add_argument("--task", required=True, help="Task file path or inline task text")
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
    except (SddError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
