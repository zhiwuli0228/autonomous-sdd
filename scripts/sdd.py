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
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any


VERSION = "0.1.0"
STAGES = [
    "brainstorm",
    "proposal",
    "specs",
    "design",
    "tasks",
    "plan",
    "apply",
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
    result = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        timeout=timeout,
        shell=False,
    )
    if check and result.returncode != 0:
        raise SddError(f"Command failed ({result.returncode}): {' '.join(args)}\n{result.stdout or ''}")
    return result


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
    output = git(root, "status", "--porcelain", check=False)
    changed: list[str] = []
    for line in output.splitlines():
        if len(line) >= 4:
            changed.append(line[3:].strip().replace("\\", "/"))
    return sorted(set(changed))


def policy_files(root: Path) -> list[Path]:
    return sorted((sdd_dir(root) / "policy").glob("*.yaml"))


def schema_files(root: Path) -> list[Path]:
    return sorted((root / "openspec" / "schemas").rglob("*.*"))


def protected_control_hashes(root: Path) -> dict[str, str]:
    files = policy_files(root) + schema_files(root)
    return {rel(root, path): sha256(path) for path in files if path.is_file()}


def capture_files(root: Path, patterns: list[str]) -> dict[str, str]:
    captured: dict[str, str] = {}
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file() and ".git" not in path.parts:
                captured[rel(root, path)] = sha256(path)
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
    shutil.copytree(source, root, dirs_exist_ok=True)
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
    print("Next: edit .sdd/policy/*.yaml, then run `sdd baseline`.")


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


def unchecked_tasks(root: Path, state: dict[str, Any]) -> list[str]:
    path = change_dir(root, state) / "tasks.md"
    if not path.exists():
        return []
    return re.findall(r"^- \[ \] (.+)$", path.read_text(encoding="utf-8"), flags=re.MULTILINE)


def required_output(root: Path, state: dict[str, Any]) -> str:
    stage = state["stage"]
    if stage == "specs":
        return f"openspec/changes/{state['change_id']}/specs/<capability>/spec.md"
    if stage == "apply":
        tasks = unchecked_tasks(root, state)
        return f"complete exactly one task: {tasks[0] if tasks else 'write apply.md receipt'}"
    if stage == "archive":
        return f"archive and sync change {state['change_id']}"
    if stage == "closed":
        return "none"
    artifact = artifact_for(root, state, stage)
    return rel(root, artifact) if artifact else stage


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
    packet = {
        "schema_version": 1,
        "run_id": state["run_id"],
        "change_id": state["change_id"],
        "stage": stage,
        "role": "implementation" if stage == "apply" else stage,
        "objective": state["objective"],
        "required_output": required_output(root, state),
        "required_reads": [
            ".sdd/runtime/state.json",
            ".sdd/runtime/current-handoff.json",
            f"openspec/changes/{state['change_id']}/.openspec.yaml",
        ],
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
        "Perform exactly the declared stage or one apply task. "
        "Do not commit or change lifecycle state. Do not modify policy, baseline, runner, schema, "
        "dependency manifests, protected API, or forbidden paths. "
        "Write .sdd/runtime/agent-result.json using status, summary, files_read, files_changed, "
        "commands_run, tests, deviations, and blocking_reason. "
        f"Current stage: {packet['stage']}. Required output: {packet['required_output']}."
    )


def invoke_agent(root: Path, state: dict[str, Any], dry_run: bool) -> None:
    packet = build_packet(root, state)
    if dry_run:
        print(json.dumps(packet, indent=2, ensure_ascii=False))
        return
    config = load_config(root)
    model = config["model"]
    command = [
        "opencode",
        "run",
        "--model",
        model,
        "--format",
        "json",
        "--dir",
        str(root),
        "--title",
        f"sdd-{state['change_id']}-{state['stage']}",
        prompt_for(packet),
    ]
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
        if not re.search(r"^- \[ \] \d+\.\d+ .+", artifact.read_text(encoding="utf-8"), re.MULTILINE):
            errors.append("tasks.md contains no numbered unchecked task")
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
    return verify_hashes(root, manifest["control_hashes"]) + verify_hashes(root, manifest["protected_files"])


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


def verification_commands(root: Path, full: bool) -> list[list[str]]:
    policy = load_json(sdd_dir(root) / "policy" / "verification.yaml")
    names = policy["full_gate"] if full else policy["task_gate"]
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
            if agent_result.get("status") != "completed":
                errors.append(f"Agent result status is not completed: {agent_result.get('status')}")
        except SddError as exc:
            errors.append(str(exc))
    evidence: list[dict[str, Any]] = []
    should_test = stage in {"apply", "verify", "finalize", "archive"}
    if should_test:
        commands = verification_commands(root, full=stage != "apply")
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
    completed = state["stage"]
    next_stage = next_stage_after(root, state)
    handoff = write_handoff(root, state, completed, next_stage, evidence)
    commit = checkpoint(root, state, completed)
    state["stage"] = next_stage
    state["status"] = "closed" if next_stage == "closed" else "running"
    state["last_verified_commit"] = commit
    state["last_handoff"] = rel(root, handoff)
    state["next_action"] = "emit_final_report" if next_stage == "closed" else "execute_stage"
    state["iteration"] += 1
    save_state(root, state)
    append_journal(root, {"event": "stage_advanced", "from": completed, "to": next_stage, "commit": commit})
    print(f"PASS {completed} -> {next_stage} at {commit}")


def run_once(args: argparse.Namespace) -> None:
    root = project_root(args.project)
    state = load_state(root)
    if state["status"] == "closed":
        print("Run is already closed")
        return
    invoke_agent(root, state, args.dry_run)
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
            print(json.dumps(state, indent=2, ensure_ascii=False))
            return
        try:
            invoke_agent(root, state, False)
            gate_and_advance(root)
        except SddError:
            state = load_state(root)
            if state["status"] == "blocked":
                raise
            append_journal(root, {"event": "repair_cycle_started", "stage": state["stage"]})
        steps += 1
    raise SddError(f"Maximum runner steps reached: {args.max_steps}")


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
