"""Durable JSON, journal, and digest helpers."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any, Mapping

from .errors import WorkspaceError


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkspaceError(f"Missing required JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WorkspaceError(f"Invalid JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkspaceError(f"Expected a JSON object in {path}")
    return value


def write_json_atomic(path: Path, value: Any, *, overwrite: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not overwrite and path.exists():
        raise WorkspaceError(f"Refusing to overwrite immutable file: {path}")
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(dict(value), ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
