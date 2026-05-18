"""Corpus export helpers."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from lemma.corpus import read_jsonl
from lemma.corpus.cards import write_dataset_card
from lemma.corpus.rows import CorpusRowV2, build_corpus_row_v2
from lemma.corpus.splits import split_for_row
from lemma.lean.sandbox import VerifyResult
from lemma.submissions import build_submission

ExportFormat = Literal["jsonl", "parquet", "hf"]


def rows_v2_from_legacy_dir(
    corpus_dir: Path,
    *,
    domain: str = "lean",
    useful_only: bool = False,
    license_filter: str | None = None,
    exclude_near_duplicates: bool = False,
) -> list[CorpusRowV2]:
    rows: list[CorpusRowV2] = []
    for path in sorted(corpus_dir.glob("*.jsonl")):
        if path.name == "corpus-index.json":
            continue
        for row in read_jsonl(path):
            task = row.to_task()
            if task.domain_id != domain:
                continue
            if useful_only and not row.quality.useful_verified_row:
                continue
            if license_filter:
                wanted = license_filter.strip().lower()
                if wanted == "commercial-safe":
                    if row.quality.license_state not in {"clean_open", "attribution_required"}:
                        continue
                elif row.quality.license_state != wanted and row.source_license.lower() != wanted:
                    continue
            if exclude_near_duplicates and row.quality.near_duplicate_score >= 0.9:
                continue
            submission = build_submission(
                task,
                solver_hotkey=row.solver_hotkey,
                proof_script=row.proof_script,
                created_at=row.accepted_at,
            )
            rows.append(
                build_corpus_row_v2(
                    task,
                    submission,
                    VerifyResult(passed=True, reason="ok", proof_term_hash=row.proof_term_hash),
                    validator_hotkey=row.validator_hotkey,
                    block=row.epoch or 0,
                    timestamp=row.accepted_at,
                    rewarded=row.rewarded,
                )
            )
    return rows


def export_rows(rows: Iterable[CorpusRowV2], *, output: Path, fmt: ExportFormat = "jsonl") -> dict[str, Any]:
    materialized = list(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "jsonl":
        _write_jsonl(materialized, output)
    elif fmt == "parquet":
        _write_parquet(materialized, output)
    elif fmt == "hf":
        _write_hf_dir(materialized, output)
    else:
        raise ValueError(f"unsupported export format: {fmt}")
    metadata = export_metadata(materialized)
    if fmt != "hf":
        output.with_suffix(output.suffix + ".metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return metadata


def export_metadata(rows: list[CorpusRowV2]) -> dict[str, Any]:
    domain = rows[0].domain_id if rows else "lean"
    verifier_id = rows[0].verifier_id if rows else "lake-build"
    verifier_version = rows[0].verifier_version if rows else "lemma-lean-v1"
    return {
        "dataset_version": datetime.now(UTC).strftime("%Y-%m-%d-block-0"),
        "domain_id": domain,
        "num_rows": len(rows),
        "verifier_id": verifier_id,
        "verifier_version": verifier_version,
        "license": "CC-BY-4.0",
        "quality": _count(str(row.metadata.get("quality", {}).get("useful_verified_row", False)) for row in rows),
        "proof_identity_strength": _count(
            str(row.accepted_artifact.get("proof_identity_strength", "weak")) for row in rows
        ),
    }


def _count(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _write_jsonl(rows: list[CorpusRowV2], output: Path) -> None:
    output.write_text("".join(row.model_dump_json(exclude_none=True) + "\n" for row in rows), encoding="utf-8")


def _write_parquet(rows: list[CorpusRowV2], output: Path) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as e:  # pragma: no cover - optional dependency path
        raise RuntimeError("Parquet export requires pyarrow") from e
    table = pa.Table.from_pylist([row.model_dump(mode="json") for row in rows])
    pq.write_table(table, output)


def _write_hf_dir(rows: list[CorpusRowV2], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    by_split: dict[str, list[CorpusRowV2]] = {"train": [], "validation": [], "test": []}
    for row in rows:
        by_split[split_for_row(row.row_id)].append(row)
    for split, split_rows in by_split.items():
        _write_jsonl(split_rows, output / f"{split}.jsonl")
    metadata = export_metadata(rows)
    (output / "dataset_info.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_dataset_card(metadata, output / "README.md")
