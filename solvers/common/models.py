"""Shared helper for the ONNX model files.

Heavy models are NOT committed to git — they live on Hugging Face so a plain
`git clone` stays light (see README → "Models"). Anything that loads a model
should use `model_path()` so a missing file produces ONE clear, actionable
message instead of a bare `FileNotFoundError` / silent None.
"""
from __future__ import annotations

import os
from pathlib import Path

HF_REPO = os.getenv("HF_REPO", "VeyraAgent/cf-solver-models")
HF_BASE = f"https://huggingface.co/{HF_REPO}/resolve/main"

# Shown whenever a required model is absent.
FETCH_HINT = (
    "run ./scripts/fetch_models.sh to download the ONNX models from "
    f"Hugging Face ({HF_REPO})"
)


def model_path(path: str | os.PathLike) -> Path:
    """Return the model Path, raising a clear error if it is missing."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"model not found: {p}\n"
            f"  → {FETCH_HINT}\n"
            f"  → or download it directly: {HF_BASE}/{_rel(p)}"
        )
    return p


def model_path_or_none(path: str | os.PathLike) -> Path | None:
    """Return the model Path if present, else None (caller has a fallback)."""
    p = Path(path)
    return p if p.exists() else None


def missing_msg(path: str | os.PathLike) -> str:
    """One-line message for a solver result when the model is absent."""
    p = Path(path)
    return f"model missing ({p.name}) — {FETCH_HINT}"


def _rel(p: Path) -> str:
    """Best-effort repo-relative path for the HF direct-download URL."""
    s = str(p).replace("\\", "/")
    for marker in ("/solvers/", "/models/"):
        i = s.find(marker)
        if i != -1:
            return s[i + 1:]
    return p.name
