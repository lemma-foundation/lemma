#!/usr/bin/env python3
"""Run local pre-mainnet readiness checks that are safe to automate."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from lemma.common.config import LemmaSettings
from scripts import leak_check

Status = Literal["pass", "warn", "fail", "skip"]


@dataclass(frozen=True)
class AuditCheck:
    name: str
    status: Status
    detail: str


def _read_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _read_last_jsonl_row(path: Path) -> dict[str, Any] | None:
    rows = _read_jsonl_rows(path)
    if not rows:
        return None
    return rows[-1]


def _read_matching_jsonl_rows(
    path: Path,
    predicate,
) -> list[dict[str, Any]]:
    return [row for row in _read_jsonl_rows(path) if predicate(row)]


def _read_recent_jsonl_rows(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if not path.exists() or limit <= 0:
        return []
    return _read_matching_jsonl_rows(path, lambda _payload: True)[-limit:]


def _checkpoint_parity_check(*, checks: list[AuditCheck], settings: LemmaSettings) -> None:
    history_block_raw = os.environ.get("LEMMA_HISTORY_BLOCK", "").strip()
    if not history_block_raw:
        _add(
            checks,
            "second-validator commitment parity",
            "skip" if settings.protocol_mode != "production" else "fail",
            "LEMMA_HISTORY_BLOCK is not set; historical commitment readback was not validated",
        )
        return

    try:
        history_block = int(history_block_raw)
    except ValueError:
        _add(
            checks,
            "second-validator commitment parity",
            "fail" if settings.protocol_mode == "production" else "warn",
            "LEMMA_HISTORY_BLOCK must be an integer block height",
        )
        return

    from lemma.chain.commitments import read_all_commitments

    try:
        commitments = read_all_commitments(settings, block=history_block)
    except Exception as exc:
        status: Status = "warn" if settings.protocol_mode != "production" else "fail"
        _add(
            checks,
            "second-validator commitment parity",
            status,
            f"historical commitment readback failed at block {history_block}: {exc}",
        )
        return

    _add(
        checks,
        "second-validator commitment parity",
        "pass" if commitments else "warn",
        (
            f"historical commitment readback at block {history_block}: "
            f"{len(commitments)} commitments"
        ),
    )


def _check_hardening_bundle_files(checks: list[AuditCheck]) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    required_paths = [
        ("scripts/lemma-sync-active-registry-cache", repo_root / "scripts/lemma-sync-active-registry-cache", True),
        ("lemma/cli/main.py", repo_root / "lemma/cli/main.py", False),
        ("scripts/publish_proof_atlas_snapshot.py", repo_root / "scripts/publish_proof_atlas_snapshot.py", False),
    ]
    missing = []
    not_executable: list[str] = []
    for name, path, needs_exec in required_paths:
        if not path.exists():
            missing.append(name)
        elif needs_exec and not os.access(path, os.X_OK):
            not_executable.append(name)
    if missing:
        _add(
            checks,
            "hardening bundle files",
            "warn",
            "missing required hardening files: " + ", ".join(sorted(missing)),
        )
    else:
        if not_executable:
            _add(
                checks,
                "hardening bundle files",
                "warn",
                "hardening files are present but not executable: " + ", ".join(sorted(not_executable)),
            )
            return
        _add(
            checks,
            "hardening bundle files",
            "pass",
            "required hardening files present for cache/build and publish runtime paths",
        )


def _to_int(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _add(checks: list[AuditCheck], name: str, status: Status, detail: str) -> None:
    checks.append(AuditCheck(name=name, status=status, detail=detail))


def _operator_alert_checks(checks: list[AuditCheck], operator_data_dir: Path) -> None:
    from lemma.operator import build_operator_alerts

    settings = LemmaSettings(operator_data_dir=operator_data_dir)
    if not operator_data_dir.exists():
        _add(
            checks,
            "operator alerts",
            "warn",
            "operator data directory not present; run local services before operator-alert checks",
        )
        return

    try:
        alert_report = build_operator_alerts(settings, recent_runs=8, recent_failures=3)
    except Exception as exc:  # pragma: no cover - defensive path for bad local state
        _add(
            checks,
            "operator alerts",
            "warn",
            f"operator alert build failed: {exc}",
        )
        return

    if alert_report.critical_count:
        _add(
            checks,
            "operator alerts",
            "fail",
            f"{alert_report.critical_count} critical operator alerts present",
        )
        return
    if alert_report.warning_count:
        _add(
            checks,
            "operator alerts",
            "warn",
            f"{alert_report.warning_count} warning operator alerts present",
        )
        return

    _add(checks, "operator alerts", "pass", "no critical operator alerts")


def _add_chain_write_check(
    checks: list[AuditCheck],
    *,
    metric_name: str,
    expected: bool,
    operator_data_dir: Path,
    active_tempo: int | None,
    submission_file: str,
    evidence_label: str,
) -> None:
    if not expected:
        _add(
            checks,
            metric_name,
            "warn",
            f"latest run did not write {evidence_label}",
        )
        return

    if active_tempo is None:
        _add(
            checks,
            metric_name,
            "warn",
            f"latest run is missing active_tempo; cannot verify {evidence_label} submission",
        )
        return

    def _matches(payload: dict[str, Any]) -> bool:
        tempo_value = _to_int(payload.get("active_tempo"))
        if tempo_value is not None:
            return tempo_value == active_tempo
        return _to_int(payload.get("tempo")) == active_tempo

    tempo_rows = _read_matching_jsonl_rows(
        operator_data_dir / submission_file,
        _matches,
    )
    has_tempo_rows = bool(tempo_rows)
    rows = tempo_rows if has_tempo_rows else _read_matching_jsonl_rows(
        operator_data_dir / submission_file,
        lambda _row: True,
    )
    if not rows:
        _add(
            checks,
            metric_name,
            "fail",
            f"missing {evidence_label} submission file records in {submission_file}",
        )
        return

    submission = rows[-1]
    success = submission.get("success")
    if not isinstance(success, bool):
        _add(
            checks,
            metric_name,
            "warn",
            f"{evidence_label} receipt for tempo {active_tempo} is missing boolean success",
        )
        return

    status: Status = "pass" if success else "fail"
    detail = f"latest {evidence_label} succeeded for tempo {active_tempo} with chain receipt"
    if status == "fail":
        detail = f"{evidence_label} submission failed for tempo {active_tempo}"

    success_states = {
        payload.get("success")
        for payload in rows
        if isinstance(payload.get("success"), bool)
    }
    if len(success_states) > 1 and status == "pass":
        status = "warn"
        detail = (
            f"{evidence_label} success history for tempo {active_tempo} is mixed;"
            " latest submission was successful but prior attempts indicate oscillation"
        )

    if not has_tempo_rows and status == "pass":
        status = "warn"
        detail = (
            f"{evidence_label} submission for tempo {active_tempo} is not tempo-tagged;"
            " using latest receipt instead"
        )
        if success_states:
            # preserve the positive indication once the fallback limitation is explained.
            detail = f"{detail} with chain receipt"

    def _payload_signature(payload: dict[str, Any]) -> str | None:
        if evidence_label == "chain weights":
            uids = payload.get("uids")
            weights = payload.get("weights")
            if isinstance(uids, list) and isinstance(weights, list):
                return json.dumps({"uids": uids, "weights": weights}, sort_keys=True)
            return None
        payload_value = payload.get("payload")
        if evidence_label == "storage commitments" and isinstance(payload_value, str):
            return payload_value
        return None

    successful_payload_signatures = {
        signature
        for payload in rows
        if payload.get("success") is True
        for signature in [_payload_signature(payload)]
        if signature is not None
    }
    if status == "pass" and len(successful_payload_signatures) > 1:
        status = "warn"
        detail = (
            f"{evidence_label} for tempo {active_tempo} shows multiple successful payloads;"
            " this may indicate oscillation or duplicate-write rewriting"
        )

    if status == "fail":
        _add(checks, metric_name, status, detail)
        return

    if evidence_label == "storage commitments":
        readback_matches = submission.get("readback_matches")
        if isinstance(readback_matches, bool) and not readback_matches:
            status = "warn"
            detail = (
                f"{evidence_label} for tempo {active_tempo} did not match on-chain readback;"
                " this indicates a replay or routing mismatch"
            )

    extrinsic_hash = submission.get("extrinsic_hash")
    if not isinstance(extrinsic_hash, str) or not extrinsic_hash.strip():
        _add(
            checks,
            metric_name,
            "warn",
            f"{evidence_label} succeeded for tempo {active_tempo} but extrinsic hash is missing",
        )
        return

    if evidence_label == "chain weights":
        uids = submission.get("uids")
        if uids is None:
            _add(
                checks,
                metric_name,
                "warn",
                f"chain-weight receipt for tempo {active_tempo} missing resolved uids",
            )
            return
        if not isinstance(uids, list) or not uids:
            _add(
                checks,
                metric_name,
                "warn",
                f"chain-weight receipt for tempo {active_tempo} contains no resolved uids",
            )
            return
        if isinstance(uids, list) and "weights" not in submission:
            _add(
                checks,
                metric_name,
                "warn",
                f"chain-weight receipt for tempo {active_tempo} is missing resolved weights",
            )
            return
        weights = submission.get("weights")
        if not isinstance(weights, list) or not weights:
            _add(
                checks,
                metric_name,
                "warn",
                f"chain-weight receipt for tempo {active_tempo} contains no resolved weights",
            )
            return
        if len(weights) != len(uids):
            _add(
                checks,
                metric_name,
                "warn",
                f"chain-weight receipt for tempo {active_tempo} has mismatched uids/weights lengths",
            )
            return

    if evidence_label == "storage commitments":
        payload = submission.get("payload")
        if not isinstance(payload, str) or not payload.strip():
            _add(
                checks,
                metric_name,
                "warn",
                f"storage commitment receipt for tempo {active_tempo} is missing commitment payload",
            )
            return

    _add(
        checks,
        metric_name,
        status,
        detail,
    )


def _add_metric_check(
    checks: list[AuditCheck], *, key: str, label: str, latest: dict[str, object]
) -> None:
    raw = latest.get(key)
    if not isinstance(raw, int):
        _add(
            checks,
            f"tempo work evidence: {label}",
            "warn",
            f"latest run row missing integer {key}",
        )
        return
    if raw <= 0:
        _add(
            checks,
            f"tempo work evidence: {label}",
            "warn",
            f"latest run {label} is {raw}",
        )
    else:
        _add(
            checks,
            f"tempo work evidence: {label}",
            "pass",
            f"latest run {label} is {raw}",
        )


def _leak_check_report(checks: list[AuditCheck], *, repo_root: Path) -> None:
    try:
        findings = leak_check.check_repo(repo_root)
    except Exception as exc:  # pragma: no cover - operational environment dependent
        _add(
            checks,
            "privacy hygiene check",
            "warn",
            f"privacy leak check could not run: {exc}",
        )
        return

    if findings:
        sample = ", ".join(findings[:3])
        _add(
            checks,
            "privacy hygiene check",
            "fail",
            f"privacy leak scan found {len(findings)} finding(s): {sample}",
        )
        return

    _add(
        checks,
        "privacy hygiene check",
        "pass",
        "privacy leak check passed for repository artifacts",
    )


def _parse_iso_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _add_burn_in_checks(
    checks: list[AuditCheck],
    *,
    operator_data_dir: Path,
    protocol_mode: str,
) -> None:
    validator_runs = operator_data_dir / "validator-runs.jsonl"
    rows = _read_jsonl_rows(validator_runs)
    if not rows:
        status = "fail" if protocol_mode == "production" else "warn"
        _add(
            checks,
            "burn-in continuity: 72h closed window",
            status,
            "validator-runs.jsonl missing; cannot assess closed burn-in continuity",
        )
        _add(
            checks,
            "burn-in continuity: 7d public window",
            status,
            "validator-runs.jsonl missing; cannot assess public burn-in continuity",
        )
        _add(
            checks,
            "burn-in continuity: tempo progression",
            status,
            "validator-runs.jsonl missing; cannot validate tempo progression",
        )
        _add(
            checks,
            "burn-in continuity: work progress",
            status,
            "validator-runs.jsonl missing; cannot validate work continuity",
        )
        return

    parsed_runs = [(row, _parse_iso_time(row.get("run_at"))) for row in rows]
    valid_times = [stamp for _row, stamp in parsed_runs if stamp is not None]
    if len(valid_times) < 2:
        status = "fail" if protocol_mode == "production" else "warn"
        _add(
            checks,
            "burn-in continuity: 72h closed window",
            status,
            "insufficient timestamped validator runs for closed burn-in continuity",
        )
        _add(
            checks,
            "burn-in continuity: 7d public window",
            status,
            "insufficient timestamped validator runs for public burn-in continuity",
        )
        _add(
            checks,
            "burn-in continuity: tempo progression",
            status,
            "insufficient timestamped run data for tempo progression check",
        )
        _add(
            checks,
            "burn-in continuity: work progress",
            status,
            "insufficient timestamped run data for work continuity check",
        )
        return

    duration_hours = (valid_times[-1] - valid_times[0]).total_seconds() / 3600
    status_72 = "pass" if duration_hours >= 72 else ("warn" if protocol_mode != "production" else "fail")
    status_7d = "pass" if duration_hours >= 168 else ("warn" if protocol_mode != "production" else "fail")
    _add(
        checks,
        "burn-in continuity: 72h closed window",
        status_72,
        (
            f"validator history spans {duration_hours:.1f}h, meeting the 72h closed-burn requirement"
            if status_72 == "pass"
            else f"validator history spans {duration_hours:.1f}h; 72h closed-burn requirement not met"
        ),
    )
    _add(
        checks,
        "burn-in continuity: 7d public window",
        status_7d,
        (
            f"validator history spans {duration_hours:.1f}h, meeting the 168h public burn requirement"
            if status_7d == "pass"
            else f"validator history spans {duration_hours:.1f}h; 168h public burn requirement not met"
        ),
    )

    tempos = [_to_int(row.get("active_tempo")) for row in rows]
    tempos = [tempo for tempo in tempos if tempo is not None]
    if len(tempos) >= 2 and any(
        right < left for left, right in zip(tempos, tempos[1:], strict=False)
    ):
        status = "fail" if protocol_mode == "production" else "warn"
        _add(
            checks,
            "burn-in continuity: tempo progression",
            status,
            "tempo sequence regressed; possible manual state reset or replay reorder",
        )
    else:
        _add(
            checks,
            "burn-in continuity: tempo progression",
            "pass",
            "tempo sequence is non-decreasing across observed validator runs",
        )

    zero_progress = sum(
        1
        for row in rows
        if _to_int(row.get("bucket_reveals_consumed")) in (0, None)
        or _to_int(row.get("verified_count")) in (0, None)
        or _to_int(row.get("accepted_unique_count")) in (0, None)
    )
    if zero_progress:
        status = "fail" if protocol_mode == "production" else "warn"
        _add(
            checks,
            "burn-in continuity: work progress",
            status,
            f"validator history includes {zero_progress} run(s) with zero/missing core progress",
        )
    else:
        _add(
            checks,
            "burn-in continuity: work progress",
            "pass",
            "validator run history shows nonzero core progress",
        )


def run_audit(settings: LemmaSettings) -> tuple[list[AuditCheck], int]:
    checks: list[AuditCheck] = []

    # 2) validator protocol path: all validators derive or hydrate the same active set.
    if os.environ.get("LEMMA_ACTIVE_REGISTRY_ROLE"):
        _add(
            checks,
            "single validator path",
            "fail",
            "LEMMA_ACTIVE_REGISTRY_ROLE is no longer supported; validators run one protocol path",
        )
    else:
        _add(
            checks,
            "single validator path",
            "pass",
            "validators derive or hydrate active registries without specialized roles",
        )

    # 6/11) commitment write/readback parity requirements.
    if settings.enable_set_commitment and not settings.canonical_publish_ipfs_api_url.strip():
        _add(
            checks,
            "commitment publication path",
            "fail",
            "set-commitment requires LEMMA_CANONICAL_PUBLISH_IPFS_API_URL for CID-bound payloads",
        )
    else:
        _add(
            checks,
            "commitment publication path",
            "pass",
            "commitment publication path check satisfied",
        )

    if settings.protocol_mode == "production" and settings.chain_commitment_checkpoint_dir is None:
        _add(
            checks,
            "second-validator commitment parity",
            "fail",
            "LEMMA_CHAIN_COMMITMENT_CHECKPOINT_DIR is unset; StateDiscardedError fallback will fail",
        )
    elif settings.chain_commitment_checkpoint_dir is None:
        _add(
            checks,
            "second-validator commitment parity",
            "warn",
            "LEMMA_CHAIN_COMMITMENT_CHECKPOINT_DIR is unset outside production",
        )
    else:
        checkpoint_root = settings.chain_commitment_checkpoint_dir
        if not checkpoint_root.exists():
            _add(
                checks,
                "second-validator commitment parity",
                "fail" if settings.protocol_mode == "production" else "warn",
                f"checkpoint directory does not exist yet: {checkpoint_root}",
            )
        else:
            _add(
                checks,
                "second-validator commitment parity",
                "pass",
                f"checkpoint cache directory present: {checkpoint_root}",
            )
            _checkpoint_parity_check(checks=checks, settings=settings)

    # 5) public registry caching behavior.
    if settings.active_registry_cache_dir is None:
        _add(
            checks,
            "active registry cache path",
            "warn",
            "LEMMA_ACTIVE_REGISTRY_CACHE_DIR is not set; builder may generate live cache in-place",
        )
    else:
        _add(
            checks,
            "active registry cache path",
            "pass",
            f"active registry cache dir configured: {settings.active_registry_cache_dir}",
        )
    _check_hardening_bundle_files(checks)

    # 12) release and chain-write readiness via local artifacts.
    if settings.protocol_mode == "production":
        if (
            not settings.require_submission_signatures
            or not settings.require_commit_reveal
            or not settings.require_strong_proof_identity
        ):
            _add(
                checks,
                "proof/auth gates",
                "fail",
                "production requires submission signatures, commit/reveal, and strong proof identity",
            )
        else:
            _add(
                checks,
                "proof/auth gates",
                "pass",
                "production proof/auth gates enabled",
            )
    else:
        _add(checks, "proof/auth gates", "skip", "production proof/auth gates are not required in this mode")

    canonical_publish_configured = bool(settings.canonical_publish_ipfs_api_url.strip()) or bool(
        settings.canonical_publish_s3_uri.strip()
    )

    # 14) local artifact leakage hygiene.
    _leak_check_report(checks, repo_root=Path(__file__).resolve().parents[1])

    validator_runs = settings.operator_data_dir / "validator-runs.jsonl"
    latest = _read_last_jsonl_row(validator_runs)
    if latest is not None and isinstance(latest.get("accepted_unique_count"), int):
        _add_metric_check(
            checks,
            key="bucket_reveals_consumed",
            label="bucket_reveals_consumed",
            latest=latest,
        )
        _add_metric_check(
            checks,
            key="verified_count",
            label="verified_count",
            latest=latest,
        )
        _add_metric_check(
            checks,
            key="accepted_unique_count",
            label="accepted_unique_count",
            latest=latest,
        )
        _add_metric_check(
            checks,
            key="corpus_row_count",
            label="corpus_row_count",
            latest=latest,
        )

        if latest.get("chain_commitment_set") is True:
            payload = latest.get("tempo_commitment_payload")
            if not isinstance(payload, str) or not payload.strip():
                _add(
                    checks,
                    "tempo commitment payload",
                    "warn",
                    "latest run expected chain commitment but tempo_commitment_payload is missing",
                )
            else:
                _add(
                    checks,
                    "tempo commitment payload",
                    "pass",
                    "latest run includes a tempo commitment payload",
                )

        active_tempo = latest.get("active_tempo")
        active_tempo_value = _to_int(active_tempo)
        _add_chain_write_check(
            checks,
            metric_name="chain-write evidence",
            expected=latest.get("weights_set") is True,
            operator_data_dir=settings.operator_data_dir,
            active_tempo=active_tempo_value,
            submission_file="weight-submissions.jsonl",
            evidence_label="chain weights",
        )
        _add_chain_write_check(
            checks,
            metric_name="artifact commitment evidence",
            expected=latest.get("chain_commitment_set") is True,
            operator_data_dir=settings.operator_data_dir,
            active_tempo=active_tempo_value,
            submission_file="commitment-submissions.jsonl",
            evidence_label="storage commitments",
        )

        canonical_publish_count = latest.get("canonical_publish_count")
        accepted_count = _to_int(latest.get("accepted_unique_count")) or 0
        if isinstance(canonical_publish_count, int):
            if canonical_publish_count > 0:
                _add(
                    checks,
                    "artifact visibility evidence",
                    "pass",
                    f"latest run reported {canonical_publish_count} canonical publish records",
                )
            elif canonical_publish_configured:
                if latest.get("chain_commitment_set") is True:
                    _add(
                        checks,
                        "commitment publication gate",
                        "fail",
                        "chain commitment recorded without canonical publish records",
                    )
                else:
                    if accepted_count > 0:
                        _add(
                            checks,
                            "canonical publish staging",
                            "warn",
                            "accepted outputs exist but canonical publish produced no records",
                        )
                    else:
                        _add(
                            checks,
                            "artifact visibility evidence",
                            "warn",
                            "latest run had no recorded canonical publish records",
                        )
            else:
                if accepted_count > 0:
                    _add(
                        checks,
                        "artifact visibility evidence",
                        "warn",
                        "accepted outputs exist but canonical publish is not configured",
                    )
                else:
                    _add(
                        checks,
                        "artifact visibility evidence",
                        "warn",
                        "canonical publish is not configured for this process",
                    )
        else:
            _add(
                checks,
                "artifact visibility evidence",
                "warn",
                "latest run missing canonical publish count",
            )

        canonical_publish_rows = _read_recent_jsonl_rows(
            settings.operator_data_dir / "canonical-publish.jsonl",
            limit=8,
        )
        publish_rows = [
            row
            for row in canonical_publish_rows
            if isinstance(row.get("kind"), str) and not row.get("kind").endswith("_publish_error")
        ]
        publish_error_rows = [
            row
            for row in canonical_publish_rows
            if isinstance(row.get("kind"), str) and row.get("kind").endswith("_publish_error")
        ]
        if publish_error_rows:
            latest_error = next(
                (row for row in reversed(publish_error_rows) if isinstance(row.get("error"), str)),
                None,
            )
            error_text = str(latest_error.get("error")) if latest_error is not None else "unknown error"
            _add(
                checks,
                "canonical publish staging errors",
                "warn",
                f"recent canonical publish failure: {error_text[:200]}",
            )
        elif canonical_publish_rows:
            _add(
                checks,
                "canonical publish staging errors",
                "pass",
                "no recent canonical publish failures found",
            )
            if (
                canonical_publish_count is not None
                and canonical_publish_count > 0
                and not publish_rows
            ):
                _add(
                    checks,
                    "canonical publish staging integrity",
                    "warn",
                    (
                        "canonical publish count is nonzero but recent canonical-publish log rows do not"
                        " include non-error payloads"
                    ),
                )
        else:
            if canonical_publish_count is not None and canonical_publish_count > 0:
                _add(
                    checks,
                    "canonical publish staging errors",
                    "warn",
                    (
                        "canonical publish count is nonzero but no recent canonical-publish log file"
                        " was found"
                    ),
                )
            else:
                _add(
                    checks,
                    "canonical publish staging errors",
                    "skip",
                    "no canonical publish log file found",
                )
    elif validator_runs.exists():
        _add(
            checks,
            "tempo work evidence",
            "warn",
            "validator-runs.jsonl exists but is missing accepted_unique_count",
        )
    else:
        _add(
            checks,
            "tempo work evidence",
            "warn",
            "validator-runs.jsonl is missing; live tempo evidence not yet collected",
        )

    _add_burn_in_checks(
        checks,
        operator_data_dir=settings.operator_data_dir,
        protocol_mode=settings.protocol_mode,
    )

    _operator_alert_checks(checks, settings.operator_data_dir)

    worst = 0
    for check in checks:
        if check.status == "fail":
            worst = 2
            break
        if check.status == "warn":
            worst = max(worst, 1)
    return checks, worst


def _print_checks(checks: list[AuditCheck], as_json: bool) -> None:
    if as_json:
        payload = [check.__dict__ for check in checks]
        print(json.dumps(payload, sort_keys=True, indent=2))
        return
    for check in checks:
        print(f"[{check.status.upper()}] {check.name}: {check.detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print JSON output")
    args = parser.parse_args()

    checks, code = run_audit(LemmaSettings())
    _print_checks(checks, as_json=args.json)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
