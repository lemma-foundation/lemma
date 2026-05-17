"""Skip redundant ``lake exe cache get`` when Mathlib is already in the workspace."""

from pathlib import Path

import pytest
from lemma.lean.sandbox import lake_exe_cache_get_needed


def test_cache_get_needed_when_cold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEMMA_LEAN_ALWAYS_CACHE_GET", raising=False)
    w = tmp_path / "ws"
    w.mkdir()
    (w / ".lake").mkdir()
    assert lake_exe_cache_get_needed(w) is True


def test_cache_get_skipped_when_mathlib_package_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEMMA_LEAN_ALWAYS_CACHE_GET", raising=False)
    w = tmp_path / "ws"
    (w / ".lake" / "packages" / "mathlib").mkdir(parents=True)
    assert lake_exe_cache_get_needed(w) is False


def test_always_cache_get_forces(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LEMMA_LEAN_ALWAYS_CACHE_GET", "1")
    w = tmp_path / "ws"
    (w / ".lake" / "packages" / "mathlib").mkdir(parents=True)
    assert lake_exe_cache_get_needed(w) is True
