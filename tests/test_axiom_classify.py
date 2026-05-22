"""Verify classify Lean failures vs real axiom policy violations."""

from lemma.lean.cheats import (
    axiom_scan_ok,
    declaration_fingerprints_from_lean_output,
    kernel_dependencies_from_lean_output,
    lean_driver_failed,
    proof_term_hash_from_lean_output,
    structural_fingerprint_from_lean_output,
)
from lemma.lean.sandbox import _verification_stdout_tail


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


def test_kernel_dependencies_parse_lean_emitted_json() -> None:
    text = 'LEMMA_KERNEL_DEPENDENCIES Submission.target ["Nat.add","True","True.intro"]'

    assert kernel_dependencies_from_lean_output(text) == ("Nat.add", "True", "True.intro")


def test_proof_term_hash_uses_lean_emitted_expr_key() -> None:
    text = "LEMMA_PROOF_TERM Submission.target (app const:True.intro:[] const:True:[])"

    assert proof_term_hash_from_lean_output(text)


def test_verify_stdout_tail_preserves_lemma_markers() -> None:
    text = "LEMMA_KERNEL_NORMAL_FORM abc\n" + ("x" * 3000)

    tail = _verification_stdout_tail(text, limit=20)

    assert "LEMMA_KERNEL_NORMAL_FORM abc" in tail
    assert tail.endswith("x" * 20)
