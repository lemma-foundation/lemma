"""Verify classify Lean failures vs real axiom policy violations."""

from lemma.lean.cheats import (
    axiom_scan_ok,
    declaration_fingerprints_from_lean_output,
    lean_driver_failed,
    structural_fingerprint_from_lean_output,
)


def test_build_failed_triggers_driver_failed_heuristic() -> None:
    text = "error: build failed\ninfo: mathlib: running post-update hooks\n"
    assert lean_driver_failed(text)


def test_parse_axioms_none_when_no_print_line() -> None:
    text = "error: build failed\n"
    ok, found = axiom_scan_ok(text)
    assert ok is False
    assert found is None


def test_structural_fingerprint_hashes_printed_declarations() -> None:
    text = "\n".join(
        [
            "LEMMA_DECL_FINGERPRINT_START Submission.target",
            "theorem Submission.target : True :=",
            "  True.intro",
            "LEMMA_DECL_FINGERPRINT_END Submission.target",
        ]
    )

    assert structural_fingerprint_from_lean_output(text)
    assert declaration_fingerprints_from_lean_output(text)["Submission.target"]
