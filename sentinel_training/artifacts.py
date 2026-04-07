from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ArtifactConfig


@dataclass(frozen=True)
class ArtifactPaths:
    artifact_dir: Path
    model_path: Path
    metadata_path: Path
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
    model.save_model(str(model_path))
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")

    legacy_model_path: Path | None = None
    if artifact_config.legacy_model_path is not None:
        legacy_model_path = artifact_config.legacy_model_path
        if not legacy_model_path.is_absolute():
            legacy_model_path = (Path.cwd() / legacy_model_path).resolve()
        legacy_model_path.parent.mkdir(parents=True, exist_ok=True)
        model.save_model(str(legacy_model_path))

    return ArtifactPaths(
        artifact_dir=artifact_dir,
        model_path=model_path,
        metadata_path=metadata_path,
        legacy_model_path=legacy_model_path,
    )
