"""Affine/model-miner export helpers."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from lemma.corpus.rows import CorpusRowV2


def affine_training_row(row: CorpusRowV2) -> dict[str, str]:
    prompt = row.prompt
    imports = "\n".join(f"import {name}" for name in prompt.get("imports", []))
    statement = str(prompt.get("statement") or "")
    proof = str(row.accepted_artifact.get("proof") or "")
    return {
        "input": f"{imports}\n\n{statement}".strip(),
        "target": proof,
        "domain": row.domain_id,
        "verifier": row.verifier_id,
    }


def write_affine_jsonl(rows: Iterable[CorpusRowV2], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "".join(json.dumps(affine_training_row(row), sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
