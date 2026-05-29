"""Workspace cache publishes and reuses in-place slots."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from lemma.lean.sandbox import LeanSandbox, VerifyResult
from lemma.lean.workspace import workspace_verify_cache_key
from lemma.problems.base import Problem


def _minimal_problem() -> Problem:
    return Problem(
        id="gen/test_k",
        theorem_name="t_test",
        type_expr="True",
        split="easy",
        lean_toolchain="leanprover/lean4:v4.30.0-rc2",
        mathlib_rev="5450b53e5ddc",
        imports=("Mathlib",),
    )


def test_warm_cache_verifies_in_slot_dir_not_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _minimal_problem()
    sub = """import Mathlib
namespace Submission
theorem t_test : True := by trivial
end Submission
"""
    cache = tmp_path / "ws_cache"
    key = workspace_verify_cache_key(p, sub, include_submission_fingerprint=False)
    slot = cache / key
    slot.mkdir(parents=True)
    (slot / ".lake").mkdir()
    (slot / ".lake" / "marker").write_text("warm", encoding="utf-8")

    seen: list[Path] = []

    def fake_host(self: LeanSandbox, work: Path) -> VerifyResult:  # noqa: ARG001
        seen.append(work.resolve())
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr(LeanSandbox, "_verify_host", fake_host)
    sb = LeanSandbox(use_docker=False, timeout_s=30, workspace_cache_dir=cache)
    vr = sb.verify(p, sub)
    assert vr.passed
    assert len(seen) == 1
    assert seen[0] == slot.resolve()
    assert (slot / ".lake" / "marker").read_text() == "warm"


def test_cold_cache_publish_moves_verified_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _minimal_problem()
    sub = """import Mathlib
namespace Submission
theorem t_test : True := by trivial
end Submission
"""
    cache = tmp_path / "ws_cache"
    key = workspace_verify_cache_key(p, sub, include_submission_fingerprint=False)
    slot = cache / key
    seen: list[Path] = []

    def fake_host(self: LeanSandbox, work: Path) -> VerifyResult:  # noqa: ARG001
        seen.append(work)
        (work / ".lake" / "packages" / "mathlib").mkdir(parents=True)
        (work / ".lake" / "marker").write_text("primed", encoding="utf-8")
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr(LeanSandbox, "_verify_host", fake_host)
    sb = LeanSandbox(use_docker=False, timeout_s=30, workspace_cache_dir=cache)
    vr = sb.verify(p, sub)

    assert vr.passed
    assert len(seen) == 1
    assert not seen[0].exists()
    assert (slot / ".lake" / "marker").read_text(encoding="utf-8") == "primed"
    assert (slot / "Submission.lean").exists()


def test_cold_cache_keeps_warm_lake_after_proof_compile_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = _minimal_problem()
    sub = """import Mathlib
namespace Submission
theorem t_test : True := by
  exact False.elim
end Submission
"""
    cache = tmp_path / "ws_cache"
    key = workspace_verify_cache_key(p, sub, include_submission_fingerprint=False)
    slot = cache / key

    def fake_host(self: LeanSandbox, work: Path) -> VerifyResult:  # noqa: ARG001
        (work / ".lake" / "packages" / "mathlib").mkdir(parents=True)
        return VerifyResult(passed=False, reason="compile_error")

    monkeypatch.setattr(LeanSandbox, "_verify_host", fake_host)
    sb = LeanSandbox(use_docker=False, timeout_s=30, workspace_cache_dir=cache)
    vr = sb.verify(p, sub)

    assert not vr.passed
    assert (slot / ".lake" / "packages" / "mathlib").is_dir()
    assert (slot / "Submission.lean").exists()


def test_cold_cache_singleflight_warms_once_for_concurrent_template(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = _minimal_problem()
    sub_a = """import Mathlib
namespace Submission
theorem t_test : True := by trivial
end Submission
"""
    sub_b = """import Mathlib
namespace Submission
theorem t_test : True := by
  trivial
end Submission
"""
    cache = tmp_path / "ws_cache"
    key = workspace_verify_cache_key(p, sub_a, include_submission_fingerprint=False)
    slot = cache / key
    seen: list[Path] = []

    def fake_host(self: LeanSandbox, work: Path) -> VerifyResult:  # noqa: ARG001
        seen.append(work.resolve())
        if work != slot:
            time.sleep(0.05)
            (work / ".lake" / "packages" / "mathlib").mkdir(parents=True)
            (work / ".lake" / "marker").write_text("primed", encoding="utf-8")
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr(LeanSandbox, "_verify_host", fake_host)
    sb = LeanSandbox(use_docker=False, timeout_s=30, workspace_cache_dir=cache)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda sub: sb.verify(p, sub), [sub_a, sub_b]))

    assert [r.passed for r in results] == [True, True]
    assert len(seen) == 2
    assert seen[0] != slot.resolve()
    assert seen[1] == slot.resolve()
    assert (slot / ".lake" / "marker").read_text(encoding="utf-8") == "primed"


def test_workspace_cache_prunes_old_warm_slots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _minimal_problem()
    sub = """import Mathlib
namespace Submission
theorem t_test : True := by trivial
end Submission
"""
    cache = tmp_path / "ws_cache"
    key = workspace_verify_cache_key(p, sub, include_submission_fingerprint=False)
    current = cache / key
    newest = cache / "newest"
    old_a = cache / "old_a"
    old_b = cache / "old_b"
    for i, path in enumerate([old_a, old_b, newest, current], start=1):
        (path / ".lake").mkdir(parents=True)
        os.utime(path, (float(i), float(i)))

    def fake_host(self: LeanSandbox, work: Path) -> VerifyResult:  # noqa: ARG001
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr(LeanSandbox, "_verify_host", fake_host)
    sb = LeanSandbox(use_docker=False, timeout_s=30, workspace_cache_dir=cache, workspace_cache_max_dirs=2)
    vr = sb.verify(p, sub)

    assert vr.passed
    assert current.is_dir()
    assert newest.is_dir()
    assert not old_a.exists()
    assert not old_b.exists()


def test_workspace_cache_prunes_stale_temp_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _minimal_problem()
    sub = """import Mathlib
namespace Submission
theorem t_test : True := by trivial
end Submission
"""
    cache = tmp_path / "ws_cache"
    key = workspace_verify_cache_key(p, sub, include_submission_fingerprint=False)
    current = cache / key
    fresh_temp = cache / "lemma-lean-fresh"
    stale_temp = cache / "lemma-lean-stale"
    for path in [current, fresh_temp, stale_temp]:
        (path / ".lake").mkdir(parents=True)
    now = time.time()
    os.utime(stale_temp, (now - 90_000, now - 90_000))

    def fake_host(self: LeanSandbox, work: Path) -> VerifyResult:  # noqa: ARG001
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr(LeanSandbox, "_verify_host", fake_host)
    sb = LeanSandbox(use_docker=False, timeout_s=30, workspace_cache_dir=cache, workspace_cache_max_dirs=8)
    vr = sb.verify(p, sub)

    assert vr.passed
    assert current.is_dir()
    assert fresh_temp.is_dir()
    assert not stale_temp.exists()


def test_workspace_cache_prunes_by_total_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _minimal_problem()
    sub = """import Mathlib
namespace Submission
theorem t_test : True := by trivial
end Submission
"""
    cache = tmp_path / "ws_cache"
    key = workspace_verify_cache_key(p, sub, include_submission_fingerprint=False)
    current = cache / key
    old_big = cache / "old_big"
    newer_small = cache / "newer_small"
    for i, (path, size) in enumerate([(old_big, 2000), (newer_small, 10)], start=1):
        (path / ".lake").mkdir(parents=True)
        (path / "payload.bin").write_bytes(b"x" * size)
        os.utime(path, (float(i), float(i)))

    def fake_host(self: LeanSandbox, work: Path) -> VerifyResult:  # noqa: ARG001
        (work / ".lake" / "packages" / "mathlib").mkdir(parents=True)
        (work / "payload.bin").write_bytes(b"x" * 20)
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr(LeanSandbox, "_verify_host", fake_host)
    sb = LeanSandbox(
        use_docker=False,
        timeout_s=30,
        workspace_cache_dir=cache,
        workspace_cache_max_dirs=8,
        workspace_cache_max_bytes=5000,
    )
    vr = sb.verify(p, sub)

    assert vr.passed
    assert current.is_dir()
    assert newer_small.is_dir()
    assert not old_big.exists()


def test_warm_workspace_cache_prunes_by_total_bytes_after_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = _minimal_problem()
    sub = """import Mathlib
namespace Submission
theorem t_test : True := by trivial
end Submission
"""
    cache = tmp_path / "ws_cache"
    key = workspace_verify_cache_key(p, sub, include_submission_fingerprint=False)
    current = cache / key
    old_big = cache / "old_big"
    for i, (path, size) in enumerate([(old_big, 2000), (current, 20)], start=1):
        (path / ".lake").mkdir(parents=True)
        (path / "payload.bin").write_bytes(b"x" * size)
        os.utime(path, (float(i), float(i)))

    def fake_host(self: LeanSandbox, work: Path) -> VerifyResult:  # noqa: ARG001
        (work / "payload.bin").write_bytes(b"x" * 20)
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr(LeanSandbox, "_verify_host", fake_host)
    sb = LeanSandbox(
        use_docker=False,
        timeout_s=30,
        workspace_cache_dir=cache,
        workspace_cache_max_dirs=8,
        workspace_cache_max_bytes=500,
    )
    vr = sb.verify(p, sub)

    assert vr.passed
    assert current.is_dir()
    assert not old_big.exists()
