"""Dataset card generation."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def dataset_card(metadata: dict[str, Any]) -> str:
    domain = str(metadata.get("domain_id") or "lean")
    title = str(metadata.get("title") or f"Lemma {domain.title()} Proof Data")
    return "\n".join(
        [
            f"# {title}",
            "",
            f"Domain: {domain}",
            f"Verifier: {metadata.get('verifier_id', 'lake-build')}",
            f"Verifier version: {metadata.get('verifier_version', 'lemma-lean-v1')}",
            f"License: {metadata.get('license', 'CC-BY-4.0')}",
            f"Rows: {metadata.get('num_rows', 0)}",
            "",
            "Fields: prompt, accepted_artifact, verification, provenance, metadata.",
            "",
            "Intended use: training and evaluating reasoning models on verifier-accepted artifacts.",
            "",
        ]
    )


def write_dataset_card(metadata: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dataset_card(metadata), encoding="utf-8")
