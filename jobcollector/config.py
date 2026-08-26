"""Load the YAML configuration describing job sources."""
from __future__ import annotations

from pathlib import Path

import yaml

from .models import SourceConfig


def load_config(path: str | Path) -> SourceConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"Config file not found: {p}. Copy companies.example.yaml to companies.yaml "
            "or run `jobcollect init-config`."
        )
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"Config file {p} must contain a YAML mapping at the top level")
    return SourceConfig.model_validate(raw)
