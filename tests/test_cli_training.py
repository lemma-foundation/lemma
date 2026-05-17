"""CLI smoke tests for task and submission commands."""

from __future__ import annotations

from click.testing import CliRunner
from lemma.cli.main import main


def _true_intro_proof() -> str:
    return "\n".join(
        [
            "import Mathlib",
            "",
            "namespace Submission",
            "",
            "theorem true_intro_sample : True := by",
            "  trivial",
            "",
            "end Submission",
            "",
        ]
    )


def test_tasks_list_uses_default_registry() -> None:
    result = CliRunner().invoke(main, ["tasks", "list"])

    assert result.exit_code == 0
    assert "lemma.sample.true_intro" in result.output


def test_submit_writes_task_bound_package(tmp_path) -> None:
    proof = tmp_path / "Submission.lean"
    proof.write_text(_true_intro_proof(), encoding="utf-8")
    output = tmp_path / "submission.json"

    result = CliRunner().invoke(
        main,
        [
            "submit",
            "lemma.sample.true_intro",
            "--submission",
            str(proof),
            "--solver-hotkey",
            "hk1",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0
    text = output.read_text(encoding="utf-8")
    assert '"task_id": "lemma.sample.true_intro"' in text
    assert '"target_sha256":' in text
