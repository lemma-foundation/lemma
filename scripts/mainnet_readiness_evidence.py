#!/usr/bin/env python3
"""Capture pre-mainnet runbook evidence for host/manual checklist gates."""

# ruff: noqa: E501, I001

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from dataclasses import dataclass, asdict
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class EvidenceCommand:
    checklist_item: str
    name: str
    command: str
    status: str
    returncode: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    error: str | None = None


def _run_command(*, cmd: str, cwd: Path, timeout: int = 120) -> tuple[int, str, str]:
    completed = subprocess.run(  # noqa: S603
        cmd,
        cwd=str(cwd),
        shell=True,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )  # nosec B602
    return completed.returncode, completed.stdout.strip(), completed.stderr.strip()


def _add_command(
    *,
    checklist_item: str,
    name: str,
    command: str,
    status: str,
    records: list[EvidenceCommand],
    returncode: int | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
    error: str | None = None,
) -> None:
    records.append(
        EvidenceCommand(
            checklist_item=checklist_item,
            name=name,
            command=command,
            status=status,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            error=error,
        )
    )


def _snapshot_commands(
    *,
    label: str,
    commands: list[str],
    repo_root: Path,
    records: list[EvidenceCommand],
    execute: bool,
) -> None:
    for index, command in enumerate(commands, start=1):
        if not execute:
            _add_command(
                checklist_item=label,
                name=f"{label}:{index}",
                command=command,
                status="skip",
                records=records,
            )
            continue

        try:
            returncode, stdout, stderr = _run_command(cmd=command, cwd=repo_root)
        except FileNotFoundError as exc:
            _add_command(
                checklist_item=label,
                name=f"{label}:{index}",
                command=command,
                status="fail",
                error=f"command not found: {exc}",
                records=records,
            )
            continue
        except Exception as exc:  # pragma: no cover
            _add_command(
                checklist_item=label,
                name=f"{label}:{index}",
                command=command,
                status="fail",
                error=str(exc),
                records=records,
            )
            continue

        status = "pass" if returncode == 0 else "warn"
        _add_command(
            checklist_item=label,
            name=f"{label}:{index}",
            command=command,
            status=status,
            returncode=returncode,
            stdout=stdout[:4096],
            stderr=stderr[:4096],
            records=records,
        )


def _pypi_reachable() -> bool:
    try:
        with socket.create_connection(("pypi.org", 443), timeout=1.5):
            return True
    except OSError:
        return False


def _summarize_item_commands(records: list[EvidenceCommand]) -> list[dict[str, int | str]]:
    summary: dict[str, dict[str, int]] = {}
    for record in records:
        bucket = summary.setdefault(
            record.checklist_item,
            {"pass": 0, "warn": 0, "fail": 0, "skip": 0},
        )
        bucket[record.status] += 1

    out: list[dict[str, int | str]] = []
    for key, counts in summary.items():
        out.append(
            {
                "checklist_item": key,
                "pass": counts["pass"],
                "warn": counts["warn"],
                "fail": counts["fail"],
                "skip": counts["skip"],
                "status": (
                    "fail"
                    if counts["fail"]
                    else ("warn" if counts["warn"] else ("pass" if counts["pass"] else "skip"))
                ),
            }
        )
    return sorted(out, key=lambda row: int(row["checklist_item"]))


def run_audit(
    *,
    repo_root: Path,
    output: Path | None,
    execute: bool,
) -> int:
    records: list[EvidenceCommand] = []
    env = os.environ
    workstream_audit_command = "uv run python scripts/workstream_audit.py --profile mainnet --skip-site"
    if not _pypi_reachable():
        workstream_audit_command += " --skip-pip-audit"

    checklist_plan = {
        "1": [
            "git -C . rev-parse HEAD",
            "git -C . rev-parse @{upstream}",
            "git -C . status --short --branch",
            "git -C . remote -v",
            """uv run python - <<'PY'\nfrom pathlib import Path\nimport subprocess\n\ndef env_value(line: str, key: str) -> str | None:\n    marker = f\"{key}=\"\n    if marker not in line:\n        return None\n    value = line.split(marker, 1)[1].strip()\n    if not value:\n        return None\n    value = value.split()[0]\n    return value.strip('\"\\\'')\n\nhead = subprocess.check_output([\"git\", \"-C\", str(Path(\".\").resolve()), \"rev-parse\", \"HEAD\"], text=True).strip()\nunit_patterns = (\n    \"lemma-validator*.service\",\n    \"lemma-active-registry-prebuild.service\",\n    \"lemma-validator-bucket.service\",\n    \"lemma-publisher*.service\",\n)\nunit_paths = []\nfor pattern in unit_patterns:\n    unit_paths.extend(sorted(Path(\"/etc/systemd/system\").glob(pattern)))\n\nobserved = []\nfor path in sorted(set(unit_paths)):\n    text = path.read_text(encoding=\"utf-8\", errors=\"ignore\")\n    values = [\n        value for line in text.splitlines() for value in [env_value(line, \"LEMMA_GIT_SHA\")] if value\n    ]\n    if not values:\n        print(f\"{path.name}:missing_lemma_git_sha\")\n        continue\n    for value in values:\n        observed.append(value)\n        print(f\"{path.name}:lemma_git_sha={value}\")\n\nif not observed:\n    raise SystemExit(\"no runtime service units with LEMMA_GIT_SHA were found for rollout parity checks\")\nif len(set(observed)) != 1:\n    raise SystemExit(f\"LEMMA_GIT_SHA mismatch across services: {sorted(set(observed))}\")\nif observed[0] != head:\n    raise SystemExit(f\"LEMMA_GIT_SHA mismatch with local repo HEAD: {observed[0]} != {head}\")\nPY""",
        ],
        "2": [
            """uv run python - <<'PY'\nfrom pathlib import Path\nimport os\nfrom lemma.common.config import LemmaSettings\n\n\ndef env_value(line: str, key: str) -> str | None:\n    marker = f'{key}='\n    if marker not in line:\n        return None\n    value = line.split(marker, 1)[1].strip()\n    if not value:\n        return None\n    value = value.split()[0]\n    return value.strip('\"').strip("'")\n\nsystemd_root = Path('/etc/systemd/system')\npatterns = (\n    'lemma-validator*.service',\n    'lemma-active-registry-prebuild.service',\n    'lemma-validator-bucket.service',\n    'lemma-publisher*.service',\n)\nservice_paths = sorted({path for pattern in patterns for path in systemd_root.glob(pattern)})\n\nsettings = LemmaSettings()\nexplicit_role = os.environ.get('LEMMA_ACTIVE_REGISTRY_ROLE', '<unset>')\nprint(f'explicit_role={explicit_role}')\nprint(f'runtime_role={settings.active_registry_role}')\nprint(f'role_requires_public_cache={\"true\" if settings.active_registry_role != \"builder\" else \"false\"}')\n\nif not service_paths:\n    raise SystemExit('no runtime service units found for role validation')\n\nviolations: list[str] = []\nfor unit_path in sorted(service_paths):\n    text = unit_path.read_text(encoding='utf-8', errors='ignore')\n    values = [\n        value for line in text.splitlines() for value in [env_value(line, 'LEMMA_ACTIVE_REGISTRY_ROLE')] if value\n    ]\n    if not values:\n        print(f'{unit_path.name}:LEMMA_ACTIVE_REGISTRY_ROLE=<unset>')\n        if settings.active_registry_role == 'auditor':\n            violations.append(f'{unit_path.name}:unset role defaults to auditor')\n        continue\n\n    for role in values:\n        print(f'{unit_path.name}:LEMMA_ACTIVE_REGISTRY_ROLE={role}')\n        if role not in {'builder', 'auditor'}:\n            violations.append(f'{unit_path.name}:invalid role {role}')\n\nif violations:\n    raise SystemExit('role semantics violations: ' + ', '.join(violations))\n\nif explicit_role != '<unset>' and explicit_role not in {'builder', 'auditor'}:\n    raise SystemExit(f'explicit role override is invalid: {explicit_role}')\n\nprint('result=pass')\nPY""",
        ],
        "3": [
            "systemctl list-unit-files 'lemma-validator*.service' 2>/dev/null || true",
            """uv run python - <<'PY'\nimport pathlib\nunits = sorted(pathlib.Path('/etc/systemd/system').glob('lemma-validator*.service'))\nprint(f'validator_service_count={len(units)}')\nfor unit in units:\n    print(unit.name)\nif len(units) < 2:\n    raise SystemExit('expected at least 2 validator service units for second-validator readiness')\nPY""",
            "ls -l /etc/systemd/system/lemma-validator*.service /etc/systemd/system/lemma-*.service 2>/dev/null || true",
            "cat /etc/systemd/system/lemma-validator*.service 2>/dev/null || true",
            "grep -n \"LEMMA_ACTIVE_REGISTRY_ROLE\\|LEMMA_ENABLE_SET_WEIGHTS\\|LEMMA_ENABLE_SET_COMMITMENT\\|LEMMA_HOTKEY\\|LEMMA_ACTIVE_REGISTRY_CACHE_DIR\\|LEMMA_GIT_SHA\" /etc/systemd/system/lemma-validator*.service 2>/dev/null || true",
            "systemctl is-active lemma-validator-bucket.service 2>/dev/null || true",
            "systemctl is-active lemma-active-registry-prebuild.service 2>/dev/null || true",
            "systemctl is-active lemma-miner-bucket@*.service 2>/dev/null || true",
        ],
        "6": [
            "uv run pytest tests/test_miner_validator.py::test_validator_does_not_submit_commitment_after_ipfs_publish_failure -q",
            "uv run pytest tests/test_miner_validator.py::test_validator_does_not_submit_commitment_after_s3_publish_failure -q",
        ],
        "4": [
            "git rev-parse --show-toplevel",
            "find /var/lib/lemma-operator -maxdepth 2 -type d -name .gitkeep -prune -o -print 2>/dev/null || true",
            "find /var/lib/lemma-operator -maxdepth 2 -type f -name '*.jsonl' -print 2>/dev/null || true",
            "ls -lah /var/lib/lemma-operator 2>/dev/null || true",
            """uv run python - <<'PY'\nfrom pathlib import Path\n\ndef env_value(line: str, key: str) -> str | None:\n    marker = f\"{key}=\"\n    if marker not in line:\n        return None\n    value = line.split(marker, 1)[1].strip()\n    if not value:\n        return None\n    value = value.split()[0]\n    return value.strip('\"\\\'')\n\nunit_paths = sorted(Path(\"/etc/systemd/system\").glob(\"lemma-validator*.service\"))\nunit_dirs = []\nfor unit_path in unit_paths:\n    text = unit_path.read_text(encoding=\"utf-8\", errors=\"ignore\")\n    values = [\n        value for line in text.splitlines() for value in [env_value(line, \"LEMMA_OPERATOR_DATA_DIR\")] if value\n    ]\n    unit_dirs.extend(values)\n\nif not unit_dirs:\n    raise SystemExit(\"no LEMMA_OPERATOR_DATA_DIR entries found on validator units\")\n\nif len(set(unit_dirs)) < 2:\n    raise SystemExit(\"validator units appear to share a single operator data directory; second-validator clean-state requirement is unmet\")\n\nfor path in sorted(set(unit_dirs)):\n    print(f\"lemma_operator_data_dir={path}\")\nPY""",
        ],
        "5": [
            "ls -l /opt/lemma/bin /var/lib/lemma-operator/active-registries 2>/dev/null || true",
            "ls -l scripts/lemma-sync-active-registry-cache lemma/cli/main.py scripts/publish_corpus_snapshot.py",
            "test -x scripts/lemma-sync-active-registry-cache || true",
            "test -f scripts/lemma-sync-active-registry-cache",
            "test -f scripts/publish_corpus_snapshot.py && test -f lemma/cli/main.py",
        ],
        "7": [
            "uv run python scripts/pre_mainnet_checklist.py",
            "uv run python scripts/pre_mainnet_checklist.py --json",
            "uv run python scripts/publish_corpus_snapshot.py --repo \"${LEMMA_CORPUS_REPO}\" --netuid \"sn${BT_NETUID}\" --sync-corpus-dir \"${LEMMA_CORPUS_OUTPUT_DIR}\" --sync-canonical-dir \"${LEMMA_CANONICAL_OUTPUT_DIR}/sn${BT_NETUID}\" --sync-registry-cache-dir \"${LEMMA_ACTIVE_REGISTRY_CACHE_DIR}\" --dry-run",
            "uv run python scripts/publish_chain_commitment.py --repo \"${LEMMA_CORPUS_REPO}\" --netuid \"sn${BT_NETUID}\" --bt-netuid \"${BT_NETUID}\" --readback --hotkey \"${VALIDATOR_HOTKEY}\"",
            """uv run python - <<'PY'\nimport json\nimport os\nfrom pathlib import Path\nfrom lemma.corpus.storage import directory_digest\n\npath = Path(os.environ.get('LEMMA_OPERATOR_DATA_DIR', 'operator')) / 'validator-runs.jsonl'\nif not path.exists():\n    raise SystemExit('validator-runs.jsonl missing')\nrows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]\nif not rows:\n    raise SystemExit('validator-runs.jsonl is empty')\nrow = rows[-1]\n\ntempo = row.get('active_tempo')\nif isinstance(tempo, str):\n    try:\n        tempo = int(tempo)\n    except ValueError:\n        tempo = None\nif not isinstance(tempo, int):\n    raise SystemExit('latest run missing active_tempo')\n\noutput_root = os.environ.get('LEMMA_CANONICAL_OUTPUT_DIR', '')\nif not output_root:\n    raise SystemExit('LEMMA_CANONICAL_OUTPUT_DIR is not set')\nroot = Path(output_root)\nif not root.exists():\n    raise SystemExit(f'LEMMA_CANONICAL_OUTPUT_DIR does not exist: {output_root}')\n\nnetuid = os.environ.get('BT_NETUID')\nif not netuid:\n    raise SystemExit('BT_NETUID is not set')\n\ntempo_dir = root / f'sn{netuid}' / 'tempos' / f'tempo-{tempo:06d}'\nif not tempo_dir.is_dir():\n    raise SystemExit(f'canonical tempo directory missing: {tempo_dir}')\n\nactual = directory_digest(tempo_dir)\nprint(f'active_tempo={tempo}')\nprint(f'active_tempo_directory_sha256={actual}')\nexpected = row.get('accepted_directory_sha256')\nif isinstance(expected, str) and expected:\n    print(f'run_accepted_directory_sha256={expected}')\n    if expected != actual:\n        raise SystemExit('accepted_directory_sha256 mismatch between run row and local tempo directory')\nPY""",
        ],
        "8": [
            """uv run python - <<'PY'\nimport json\nfrom pathlib import Path\nimport os\n\npath = Path(os.environ.get('LEMMA_OPERATOR_DATA_DIR', 'operator')) / 'validator-runs.jsonl'\nif not path.exists():\n    raise SystemExit('validator-runs.jsonl missing')\n\nrows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]\nif not rows:\n    raise SystemExit('validator-runs.jsonl is empty')\n\nrow = rows[-1]\nfor field in ('bucket_reveals_consumed', 'verified_count', 'accepted_unique_count', 'corpus_row_count'):\n    value = row.get(field)\n    if not isinstance(value, int):\n        raise SystemExit(f'{field} missing or non-integer in latest row: {value!r}')\n    if value <= 0:\n        raise SystemExit(f'{field} is not positive in latest row: {value}')\n    print(f'{field}:{value}')\nPY""",
        ],
        "9": [
            """uv run python - <<'PY'\nimport json\nfrom pathlib import Path\nimport os\n\ndef parse_tempo(row: dict) -> int | None:\n    tempo = row.get('active_tempo', row.get('tempo'))\n    if isinstance(tempo, int):\n        return tempo\n    if isinstance(tempo, str):\n        try:\n            return int(tempo)\n        except ValueError:\n            return None\n    return None\n\n\ndef payload_signature(row: dict) -> str:\n    return json.dumps({'uids': row.get('uids'), 'weights': row.get('weights')}, sort_keys=True)\n\npath = Path(os.environ.get('LEMMA_OPERATOR_DATA_DIR', 'operator')) / 'weight-submissions.jsonl'\nif not path.exists():\n    raise SystemExit('weight-submissions.jsonl missing')\nrows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]\nif not rows:\n    raise SystemExit('weight-submissions.jsonl is empty')\n\nlatest = rows[-1]\nif latest.get('success') is not True:\n    raise SystemExit('latest weight submission did not complete successfully')\nfor field in ('extrinsic_hash', 'uids', 'weights'):\n    if field not in latest:\n        raise SystemExit(f'latest weight submission missing {field}')\n\nuids = latest.get('uids')\nweights = latest.get('weights')\nif not isinstance(uids, list) or not isinstance(weights, list) or len(uids) == 0:\n    raise SystemExit('latest weight submission uids/weights are invalid or empty')\nif len(uids) != len(weights):\n    raise SystemExit('latest weight submission uids/weights length mismatch')\n\ntempo = parse_tempo(latest)\nif tempo is None:\n    raise SystemExit('latest weight submission missing tempo')\n\nsuccess_rows = [row for row in rows if row.get('success') is True and parse_tempo(row) == tempo]\nsignatures = {payload_signature(row) for row in success_rows}\nif len(signatures) > 1:\n    raise SystemExit(f'weight submission payload oscillated for tempo {tempo}')\n\nif not success_rows:\n    raise SystemExit(f'no successful weight submissions found for tempo {tempo}')\n\nprint(f'latest_weight_tempo={tempo}')\nprint(f'latest_weight_payload_signature={next(iter(signatures))}')\nprint(f'latest_weight_success_count={len(success_rows)}')\nPY""",
        ],
        "10": [
            """uv run python - <<'PY'\nimport json\nfrom pathlib import Path\nimport os\n\ndef parse_tempo(row: dict) -> int | None:\n    tempo = row.get('active_tempo', row.get('tempo'))\n    if isinstance(tempo, int):\n        return tempo\n    if isinstance(tempo, str):\n        try:\n            return int(tempo)\n        except ValueError:\n            return None\n    return None\n\npath = Path(os.environ.get('LEMMA_OPERATOR_DATA_DIR', 'operator')) / 'commitment-submissions.jsonl'\nif not path.exists():\n    raise SystemExit('commitment-submissions.jsonl missing')\nrows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]\nif not rows:\n    raise SystemExit('commitment-submissions.jsonl is empty')\n\nlatest = rows[-1]\nif latest.get('success') is not True:\n    raise SystemExit('latest commitment submission did not complete successfully')\n\npayload = latest.get('payload')\nif not isinstance(payload, str) or not payload.strip():\n    raise SystemExit('latest commitment submission payload is missing')\n\nextrinsic_hash = latest.get('extrinsic_hash')\nif not isinstance(extrinsic_hash, str) or not extrinsic_hash.strip():\n    raise SystemExit('latest commitment submission extrinsic_hash is missing')\n\ntempo = parse_tempo(latest)\nif tempo is None:\n    raise SystemExit('latest commitment submission missing tempo')\n\nsuccess_rows = [row for row in rows if row.get('success') is True and parse_tempo(row) == tempo]\nif not success_rows:\n    raise SystemExit(f'no successful commitment submissions found for tempo {tempo}')\n\nif len({row.get('payload') for row in success_rows}) > 1:\n    raise SystemExit(f'commitment payload oscillated for tempo {tempo}')\n\nif latest.get('readback_matches') is False:\n    raise SystemExit(f'commitment readback mismatch for tempo {tempo}')\n\nprint(f'latest_commitment_tempo={tempo}')\nprint(f'latest_commitment_payload={payload}')\nprint(f'latest_commitment_readback={latest.get(\"readback_matches\", \"unknown\")}')\nprint(f'latest_commitment_success_count={len(success_rows)}')\nPY""",
        ],
        "11": [
            """uv run python - <<'PY'\nimport os\nfrom lemma.common.config import LemmaSettings\nfrom lemma.chain.commitments import read_all_commitments\nsettings = LemmaSettings()\nhistory_block_raw = os.environ.get('LEMMA_HISTORY_BLOCK')\nif not history_block_raw:\n    raise SystemExit('LEMMA_HISTORY_BLOCK is required for parity readback evidence')\nhistory_block = int(history_block_raw)\ncommitments = read_all_commitments(settings, block=history_block)\nprint(f'historical_commitments={len(commitments)}')\nPY""",
            """uv run python - <<'PY'\nfrom pathlib import Path\nimport os\nroot = os.environ.get('LEMMA_CHAIN_COMMITMENT_CHECKPOINT_DIR')\nif not root:\n    raise SystemExit('LEMMA_CHAIN_COMMITMENT_CHECKPOINT_DIR is not set')\nroot_path = Path(root)\nif not root_path.exists():\n    raise SystemExit('commitment checkpoint directory missing')\nprint(f'checkpoint_dir={root}')\nPY""",
        ],
        "12": [
            "uv run lemma operator alerts --recent-runs 8 --recent-failures 5",
            """uv run python - <<'PY'\nimport json\nimport sys\nfrom lemma.operator import build_operator_alerts\nfrom lemma.common.config import LemmaSettings\nreport = build_operator_alerts(LemmaSettings())\nprint(json.dumps(report.model_dump(), sort_keys=True))\nfor alert in report.alerts:\n    print(f'{alert.code}:{alert.level}:{alert.message}')\nPY""",
            "systemctl list-units --state=failed 'lemma-validator*.service' 'lemma-active-registry-prebuild.service' 'lemma-miner-bucket@*.service' 2>/dev/null || true",
            "systemctl list-units --state=active 'lemma-validator*.service' 'lemma-active-registry-prebuild.service' 'lemma-miner-bucket@*.service' 2>/dev/null || true",
            "journalctl -u lemma-validator-bucket.service -n 120 --no-pager --output=short-precise 2>/dev/null | rg -i 'restart|failed|failed-with|timed out|start operation' || true",
        ],
        "13": [
            "ls -1 docs/operator-registry-flow.md docs/production.md",
            "rg -n \"role flip|cache|publisher|restart|rollback|key rotation\" docs/operator-registry-flow.md docs/production.md",
            "rg -n \"preflight|operator alerts|active registry|burn-in|mainnet\" docs/mainnet-readiness.md",
        ],
        "15": [
            """uv run python - <<'PY'\nimport json\nfrom datetime import datetime\nfrom pathlib import Path\nimport os\npath = Path(os.environ.get('LEMMA_OPERATOR_DATA_DIR', 'operator')).joinpath('validator-runs.jsonl')\nif not path.exists():\n    raise SystemExit('validator-runs.jsonl missing')\nrows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]\nif not rows:\n    raise SystemExit('validator-runs.jsonl is empty')\nrun_at = [r['run_at'] for r in rows if isinstance(r.get('run_at'), str)]\nif not run_at:\n    raise SystemExit('run_at missing from validator rows')\nstart = datetime.fromisoformat(run_at[0].replace('Z', '+00:00'))\nend = datetime.fromisoformat(run_at[-1].replace('Z', '+00:00'))\nprint(f'run_count={len(run_at)}')\nprint(f'duration_hours={(end-start).total_seconds() / 3600:.2f}')\nPY""",
            """uv run python - <<'PY'\nimport json\nfrom pathlib import Path\nimport os\npath = Path(os.environ.get('LEMMA_OPERATOR_DATA_DIR', 'operator')).joinpath('validator-runs.jsonl')\nif not path.exists():\n    raise SystemExit(f'{path} missing')\nrows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]\nzero_accept = sum(1 for row in rows if isinstance(row.get('accepted_unique_count'), int) and row.get('accepted_unique_count') == 0)\nzero_reveals = sum(1 for row in rows if isinstance(row.get('verified_count'), int) and row.get('verified_count') == 0)\nprint(f'zero_accept_rows={zero_accept}')\nprint(f'zero_reveal_rows={zero_reveals}')\nPY""",
        ],
        "14": [
            "uv run python scripts/leak_check.py --repo .",
        ],
        "16": [
            workstream_audit_command,
            "uv run python scripts/pre_mainnet_checklist.py --json",
        ],
    }

    for item, commands in checklist_plan.items():
        _snapshot_commands(
            label=item,
            commands=commands,
            repo_root=repo_root,
            records=records,
            execute=execute,
        )

    item_summaries = _summarize_item_commands(records)

    if output is None:
        output = repo_root / f"mainnet-readiness-evidence-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    output.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "repo": str(repo_root),
        "execute": execute,
        "exit_status": "pass" if not any(record.status == "fail" for record in records) else "fail",
        "item_summaries": item_summaries,
        "operator_data_dir": env.get("LEMMA_OPERATOR_DATA_DIR"),
        "netuid": env.get("BT_NETUID"),
        "network": env.get("BT_NETWORK"),
        "records": [asdict(record) for record in records],
    }
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    worst = "pass"
    for record in records:
        if record.status == "fail":
            worst = "fail"
            break
        if record.status == "warn" and worst == "pass":
            worst = "warn"

    print(f"wrote evidence artifact: {output}")
    print(f"items={len(records)} status={worst}")
    return 1 if worst == "fail" else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="path to the lemma repository root",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="path to write JSON evidence",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="run read-only evidence commands and capture outputs",
    )
    args = parser.parse_args()

    output = args.output
    if output is not None:
        output = output.expanduser().resolve()
    return run_audit(
        repo_root=args.repo.resolve(),
        output=output,
        execute=args.execute,
    )


if __name__ == "__main__":
    sys.exit(main())
