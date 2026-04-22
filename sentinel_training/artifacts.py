from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ArtifactConfig


@dataclass(frozen=True)
class ArtifactPaths:
    artifact_dir: Path
    model_path: Path
    metadata_path: Path
    checksums_path: Path
    legacy_model_path: Path | None


def save_artifacts(
    model: Any,
    metadata: dict[str, Any],
    artifact_config: ArtifactConfig,
    experiment_name: str,
) -> ArtifactPaths:
    artifact_dir = artifact_config.artifact_root / experiment_name
    artifact_dir.mkdir(parents=True, exist_ok=True)

    model_path = artifact_dir / "model.json"
    metadata_path = artifact_dir / "metadata.json"
    checksums_path = artifact_dir / "checksums.json"
    model.save_model(str(model_path))
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")

    legacy_model_path: Path | None = None
    if artifact_config.legacy_model_path is not None:
        legacy_model_path = artifact_config.legacy_model_path
        if not legacy_model_path.is_absolute():
            legacy_model_path = (Path.cwd() / legacy_model_path).resolve()
        legacy_model_path.parent.mkdir(parents=True, exist_ok=True)
        model.save_model(str(legacy_model_path))

    checksums = build_checksums(
        model_path=model_path,
        metadata_path=metadata_path,
        legacy_model_path=legacy_model_path,
    )
    checksums_path.write_text(json.dumps(checksums, indent=2, sort_keys=True), encoding="utf-8")

    return ArtifactPaths(
        artifact_dir=artifact_dir,
        model_path=model_path,
        metadata_path=metadata_path,
        checksums_path=checksums_path,
        legacy_model_path=legacy_model_path,
    )


def fingerprint_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_bytes(content: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(content)
    return digest.hexdigest()


def build_checksums(
    model_path: Path,
    metadata_path: Path,
    legacy_model_path: Path | None,
) -> dict[str, object]:
    file_hashes: dict[str, dict[str, str]] = {
        "model": {
            "path": model_path.name,
            "sha256": fingerprint_file(model_path),
        },
        "metadata": {
            "path": metadata_path.name,
            "sha256": fingerprint_file(metadata_path),
        },
    }
    if legacy_model_path is not None:
        file_hashes["legacy_model"] = {
            "path": str(legacy_model_path),
            "sha256": fingerprint_file(legacy_model_path),
        }

    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "files": file_hashes,
    }
