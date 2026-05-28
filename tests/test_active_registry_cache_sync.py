from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path

from lemma.supply.gates import GATE_VERSION
from lemma.supply.operator_bundle import OPERATOR_BUNDLE_VERSION, procedural_operator_bundle_hash
from lemma.supply.source_pool import SOURCE_SAMPLING_VERSION
from lemma.task_supply import make_task, write_registry
from lemma.tasks import load_task_registry

ROOT = Path(__file__).resolve().parents[1]


def _load_sync_module():
    path = ROOT / "scripts" / "lemma-sync-active-registry-cache"
    loader = importlib.machinery.SourceFileLoader("lemma_sync_active_registry_cache", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_sync_active_registry_cache_hydrates_public_tempo_cache(monkeypatch, tmp_path: Path) -> None:
    module = _load_sync_module()
    public = tmp_path / "public"
    public.mkdir()
    cache = tmp_path / "cache"
    registry_path = public / "registry.json"
    task = make_task(
        task_id="lemma.procedural.public-cache",
        title="Public cache",
        theorem_name="public_cache",
        type_expr="True",
        source_stream="procedural",
        source_name="pytest",
        frontier_depth=0,
        metadata={
            "gate_version": GATE_VERSION,
            "operator_bundle_hash": procedural_operator_bundle_hash(),
            "operator_bundle_version": OPERATOR_BUNDLE_VERSION,
            "source_sampling_version": SOURCE_SAMPLING_VERSION,
        },
    )
    write_registry([task], registry_path)
    registry_sha = load_task_registry(registry_path.read_bytes()).sha256
    index = {
        "schema_version": 1,
        "netuid": "sn467",
        "registries": {"7": {"sha256": registry_sha, "path": "registry.json"}},
    }
    index_path = public / "index.json"
    index_path.write_text(json.dumps(index), encoding="utf-8")

    monkeypatch.setenv("LEMMA_ACTIVE_REGISTRY_CACHE_DIR", str(cache))
    monkeypatch.setenv("LEMMA_ACTIVE_REGISTRY_CACHE_INDEX_URL", index_path.as_uri())
    monkeypatch.setenv("LEMMA_ACTIVE_K", "1")
    monkeypatch.setattr(module, "current_active_tempo", lambda settings: 7)

    module.main()

    hydrated = cache / "tempo-7.registry.json"
    assert hydrated.is_file()
    assert load_task_registry(hydrated.read_bytes()).sha256 == registry_sha


def test_sync_active_registry_cache_keeps_existing_cache_when_public_hash_changes(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    module = _load_sync_module()
    public = tmp_path / "public"
    public.mkdir()
    cache = tmp_path / "cache"
    cache.mkdir()
    metadata = {
        "gate_version": GATE_VERSION,
        "operator_bundle_hash": procedural_operator_bundle_hash(),
        "operator_bundle_version": OPERATOR_BUNDLE_VERSION,
        "source_sampling_version": SOURCE_SAMPLING_VERSION,
    }
    cached_task = make_task(
        task_id="lemma.procedural.cached",
        title="Cached",
        theorem_name="cached",
        type_expr="True",
        source_stream="procedural",
        source_name="pytest",
        frontier_depth=0,
        metadata=metadata,
    )
    public_task = make_task(
        task_id="lemma.procedural.public",
        title="Public",
        theorem_name="public",
        type_expr="True",
        source_stream="procedural",
        source_name="pytest",
        frontier_depth=0,
        metadata=metadata,
    )
    cache_path = cache / "tempo-7.registry.json"
    public_path = public / "registry.json"
    write_registry([cached_task], cache_path)
    write_registry([public_task], public_path)
    cached_sha = load_task_registry(cache_path.read_bytes()).sha256
    public_sha = load_task_registry(public_path.read_bytes()).sha256
    assert cached_sha != public_sha
    index = {
        "schema_version": 1,
        "netuid": "sn467",
        "registries": {"7": {"sha256": public_sha, "path": "registry.json"}},
    }
    index_path = public / "index.json"
    index_path.write_text(json.dumps(index), encoding="utf-8")

    monkeypatch.setenv("LEMMA_ACTIVE_REGISTRY_CACHE_DIR", str(cache))
    monkeypatch.setenv("LEMMA_ACTIVE_REGISTRY_CACHE_INDEX_URL", index_path.as_uri())
    monkeypatch.setenv("LEMMA_ACTIVE_K", "1")
    monkeypatch.setattr(module, "current_active_tempo", lambda settings: 7)

    module.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "cache": "existing_cache_sha_mismatch",
        "tempo": 7,
        "registry_sha256": cached_sha,
        "public_registry_sha256": public_sha,
    }
    assert load_task_registry(cache_path.read_bytes()).sha256 == cached_sha


def test_sync_active_registry_cache_reports_present_cache_without_public_index(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    module = _load_sync_module()
    cache = tmp_path / "cache"
    cache.mkdir()
    task = make_task(
        task_id="lemma.procedural.local-cache",
        title="Local cache",
        theorem_name="local_cache",
        type_expr="True",
        source_stream="procedural",
        source_name="pytest",
        frontier_depth=0,
        metadata={
            "gate_version": GATE_VERSION,
            "operator_bundle_hash": procedural_operator_bundle_hash(),
            "operator_bundle_version": OPERATOR_BUNDLE_VERSION,
            "source_sampling_version": SOURCE_SAMPLING_VERSION,
        },
    )
    cache_path = cache / "tempo-7.registry.json"
    write_registry([task], cache_path)
    registry_sha = load_task_registry(cache_path.read_bytes()).sha256

    monkeypatch.setenv("LEMMA_ACTIVE_REGISTRY_CACHE_DIR", str(cache))
    monkeypatch.delenv("LEMMA_ACTIVE_REGISTRY_CACHE_INDEX_URL", raising=False)
    monkeypatch.setenv("LEMMA_ACTIVE_K", "1")
    monkeypatch.setattr(module, "current_active_tempo", lambda settings: 7)

    module.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"cache": "present", "tempo": 7, "registry_sha256": registry_sha}


def test_sync_active_registry_cache_reports_missing_index_for_auditors(monkeypatch, tmp_path: Path, capsys) -> None:
    module = _load_sync_module()

    monkeypatch.setenv("LEMMA_ACTIVE_REGISTRY_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("LEMMA_ACTIVE_REGISTRY_CACHE_INDEX_URL", raising=False)
    monkeypatch.setenv("LEMMA_ACTIVE_REGISTRY_ROLE", "auditor")
    monkeypatch.setattr(module, "current_active_tempo", lambda settings: 7)

    module.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"cache": "missing_public_index_url", "tempo": 7}


def test_sync_active_registry_cache_cache_busts_http_fetches(monkeypatch) -> None:
    module = _load_sync_module()
    seen_urls: list[str] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:  # noqa: ANN002
            return None

        def read(self) -> bytes:
            return b"{}"

    def fake_urlopen(request, timeout):  # noqa: ANN001
        seen_urls.append(request.full_url)
        assert timeout == 20
        return FakeResponse()

    monkeypatch.setattr(module, "urlopen", fake_urlopen)
    monkeypatch.setattr(module.time, "time", lambda: 123)

    assert module._fetch("https://example.test/index.json") == b"{}"
    assert module._fetch("https://example.test/index.json?raw=1") == b"{}"

    assert seen_urls == [
        "https://example.test/index.json?t=123",
        "https://example.test/index.json?raw=1&t=123",
    ]
