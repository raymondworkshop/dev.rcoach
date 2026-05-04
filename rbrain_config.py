"""Shared rbrain paths and Ollama settings.

Override via (highest priority last):
1. Optional YAML: path in env RBRAIN_CONFIG, else ./rbrain.yaml, else ./rbrain_config.yaml
2. Environment variables (override YAML)

Env vars:
  RBRAIN_WIKI_ROOT       — absolute or relative path to vault (default: ./rbrain-wiki)
  RBRAIN_OLLAMA_BASE_URL — e.g. http://localhost:11434 (no trailing path)
  RBRAIN_GENERATE_MODEL  — Ollama model for /api/generate
  RBRAIN_EMBED_MODEL     — Ollama model for /api/embeddings
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parent


def _load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_rbrain_config() -> Dict[str, Any]:
    file_cfg: Dict[str, Any] = {}
    env_path = os.environ.get("RBRAIN_CONFIG")
    if env_path and Path(env_path).is_file():
        file_cfg = _load_yaml(Path(env_path))
    else:
        for candidate in (_REPO_ROOT / "rbrain.yaml", _REPO_ROOT / "rbrain_config.yaml"):
            if candidate.is_file():
                file_cfg = _load_yaml(candidate)
                break

    wiki = (
        os.environ.get("RBRAIN_WIKI_ROOT")
        or file_cfg.get("wiki_root")
        or str(_REPO_ROOT / "rbrain-wiki")
    )
    wiki_root = str(Path(wiki).expanduser().resolve())

    base = (
        os.environ.get("RBRAIN_OLLAMA_BASE_URL")
        or file_cfg.get("ollama_base_url")
        or os.environ.get("OLLAMA_HOST")
        or "http://localhost:11434"
    )
    base = base.rstrip("/")

    models = file_cfg.get("models") if isinstance(file_cfg.get("models"), dict) else {}
    generate_model = (
        os.environ.get("RBRAIN_GENERATE_MODEL")
        or models.get("generate")
        or "rbrain"
    )
    embed_model = (
        os.environ.get("RBRAIN_EMBED_MODEL")
        or models.get("embed")
        or "nomic-embed-text"
    )

    wiki_path = Path(wiki_root)
    return {
        "wiki_root": wiki_root,
        "ollama_base_url": base,
        "ollama_generate_url": f"{base}/api/generate",
        "ollama_embeddings_url": f"{base}/api/embeddings",
        "generate_model": generate_model,
        "embed_model": embed_model,
        "atoms_dir": str(wiki_path / "atoms"),
        "raw_dir": str(wiki_path / "raw"),
        "vector_index_path": str(wiki_path / "vector_index.json"),
        "queries_dir": str(wiki_path / "raw" / "queries"),
        "wiki_curated_dir": str(wiki_path / "wiki"),
    }


_CFG: Optional[Dict[str, Any]] = None


def get_config() -> Dict[str, Any]:
    global _CFG
    if _CFG is None:
        _CFG = load_rbrain_config()
    return _CFG


def reset_config() -> None:
    """Test hooks or reload after editing yaml."""
    global _CFG
    _CFG = None
