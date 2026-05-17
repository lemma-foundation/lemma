"""Verify classify Lean failures vs real axiom policy violations."""

from lemma.lean.cheats import axiom_scan_ok, lean_driver_failed


def test_build_failed_triggers_driver_failed_heuristic() -> None:
    text = "error: build failed\ninfo: mathlib: running post-update hooks\n"
    assert lean_driver_failed(text)


def test_parse_axioms_none_when_no_print_line() -> None:
    text = "error: build failed\n"
    ok, found = axiom_scan_ok(text)
    assert ok is False
    assert found is None
