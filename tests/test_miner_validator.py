"""Miner and validator one-shot workflow tests."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import httpx
import pytest
from bittensor_wallet import Keypair
from lemma.chain.commitments import ChainCommitmentSubmission
from lemma.chain.weights import ChainWeightSubmission
from lemma.common.config import LemmaSettings
from lemma.lean.sandbox import VerifyResult
from lemma.miner import (
    ProverError,
    ProverResult,
    _normalize_prover_result,
    _strip_json_fence,
    mine_once,
    prover_input,
    run_openai_compatible_prover,
    run_prover_command,
)
from lemma.protocol import ProofResponse, TaskRequest
from lemma.submissions import LemmaSubmission, build_submission, sign_submission
from lemma.supply.controller import CurriculumTempoRecord, append_curriculum_record, read_curriculum_records
from lemma.supply.mathlib_snapshot import candidates_from_jsonl
from lemma.supply.types import registry_tasks_from_candidates
from lemma.task_supply import make_task, write_registry
from lemma.tasks import TaskRegistry
from lemma.validator import (
    ValidatorRunSummary,
    active_tasks_for_validation,
    cached_active_registry_for_tempo,
    current_active_tempo,
    curriculum_controlled_settings,
    validate_once,
)


def _task(task_id: str = "lemma.test.true", queue_depth: int = 0):
    return make_task(
        task_id=task_id,
        title="True task",
        theorem_name="test_true",
        type_expr="True",
        source_stream="human_curated",
        source_name="pytest",
        queue_depth=queue_depth,
    )


def _registry() -> TaskRegistry:
    return TaskRegistry(schema_version=1, tasks=(_task(),), sha256="0" * 64)


def test_current_active_tempo_uses_chain_tempo(monkeypatch: pytest.MonkeyPatch) -> None:
    class Hyperparams:
        tempo = 360

    class Subtensor:
        def __init__(self, network: str | None = None) -> None:
            self.network = network

        def get_current_block(self) -> int:
            return 7199

        def get_subnet_hyperparameters(self, netuid: int, block: int | None = None) -> Hyperparams:
            assert netuid == 467
            assert block == 7199
            return Hyperparams()

    monkeypatch.setitem(sys.modules, "bittensor", type("FakeBittensor", (), {"Subtensor": Subtensor})())

    settings = LemmaSettings(
        _env_file=None,
        netuid=467,
        active_tempo_source="chain",
    )

    assert current_active_tempo(settings) == 19


def _two_task_registry() -> TaskRegistry:
    return TaskRegistry(
        schema_version=1,
        tasks=(
            _task("lemma.test.active", queue_depth=0),
            _task("lemma.test.deep", queue_depth=2),
        ),
        sha256="0" * 64,
    )


def _proof(body: str = "  trivial") -> str:
    return "\n".join(
        [
            "import Mathlib",
            "",
            "namespace Submission",
            "",
            "theorem test_true : True := by",
            body,
            "",
            "end Submission",
            "",
        ]
    )


def _proof_for(theorem_name: str, body: str = "  trivial") -> str:
    return "\n".join(
        [
            "import Mathlib",
            "",
            "namespace Submission",
            "",
            f"theorem {theorem_name} : True := by",
            body,
            "",
            "end Submission",
            "",
        ]
    )


def _settings(tmp_path: Path) -> LemmaSettings:
    return LemmaSettings(
        _env_file=None,
        operator_data_dir=tmp_path / "operator",
        corpus_output_dir=tmp_path / "corpus",
        lean_use_docker=False,
    )


def test_local_prover_adapter_rejects_invalid_json(tmp_path: Path) -> None:
    script = tmp_path / "bad.py"
    script.write_text("print('not json')\n", encoding="utf-8")

    with pytest.raises(ProverError, match="invalid JSON"):
        run_prover_command(f"{sys.executable} {script}", _task(), 5)


def test_openai_compatible_prover_accepts_fenced_json() -> None:
    payload = {"task_id": "lemma.test.true", "proof_script": _proof()}

    assert json.loads(_strip_json_fence(f"```json\n{json.dumps(payload)}\n```")) == payload


def test_prover_result_normalizer_appends_submission_end() -> None:
    proof = ProverResult(
        task_id="lemma.test.true",
        proof_script="\n".join(
            [
                "import Mathlib",
                "",
                "namespace Submission",
                "",
                "theorem test_true : True := by",
                "  trivial",
            ]
        ),
    )

    normalized = _normalize_prover_result(_task(), proof)

    assert normalized.proof_script.endswith("end Submission\n")


def test_openai_compatible_prover_rejects_malformed_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": "not json"}}]}

    monkeypatch.setattr("lemma.miner.httpx.post", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(ProverError, match="invalid JSON"):
        run_openai_compatible_prover(
            _settings(tmp_path).model_copy(update={"prover_base_url": "https://example.test", "prover_model": "model"}),
            _task(),
        )


def test_openai_compatible_prover_wraps_http_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fail_post(*args: object, **kwargs: object) -> object:
        raise httpx.HTTPStatusError(
            "503 Service Unavailable",
            request=httpx.Request("POST", "https://example.test/chat/completions"),
            response=httpx.Response(503),
        )

    monkeypatch.setattr("lemma.miner.httpx.post", fail_post)

    with pytest.raises(ProverError, match="request failed"):
        run_openai_compatible_prover(
            _settings(tmp_path).model_copy(update={"prover_base_url": "https://example.test", "prover_model": "model"}),
            _task(),
        )


def test_openai_compatible_prover_accepts_proof_script_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"choices": [{"message": {"content": json.dumps({"proof_script": _proof()})}}]}

    monkeypatch.setattr("lemma.miner.httpx.post", lambda *args, **kwargs: FakeResponse())

    proof = run_openai_compatible_prover(
        _settings(tmp_path).model_copy(update={"prover_base_url": "https://example.test", "prover_model": "model"}),
        _task(),
    )

    assert proof.task_id == "lemma.test.true"


def test_openai_compatible_repair_prompt_includes_verifier_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"task_id": "lemma.test.true", "proof_script": _proof()}),
                        }
                    }
                ]
            }

    def fake_post(*args: object, **kwargs: object) -> FakeResponse:
        captured["json"] = kwargs["json"]
        return FakeResponse()

    monkeypatch.setattr("lemma.miner.httpx.post", fake_post)

    run_openai_compatible_prover(
        _settings(tmp_path).model_copy(update={"prover_base_url": "https://example.test", "prover_model": "model"}),
        _task(),
        failed_proof=ProverResult(task_id="lemma.test.true", proof_script="bad proof"),
        failed_verification=VerifyResult(passed=False, reason="compile_error", stderr_tail="unsolved goals"),
    )

    system_prompt = captured["json"]["messages"][0]["content"]
    assert "Nat.prime_iff.mpr pp" in system_prompt
    user_payload = json.loads(captured["json"]["messages"][1]["content"])
    assert user_payload["failed_attempt"]["proof_script"] == "bad proof"
    assert user_payload["failed_attempt"]["reason"] == "compile_error"
    assert user_payload["failed_attempt"]["stderr_tail"] == "unsolved goals"


def test_local_prover_adapter_times_out(tmp_path: Path) -> None:
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(2)\n", encoding="utf-8")

    with pytest.raises(ProverError, match="timed out"):
        run_prover_command(f"{sys.executable} {script}", _task(), 0.01)


def test_mine_once_rejects_local_verify_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    script = tmp_path / "prover.py"
    script.write_text(
        "import json, sys\n"
        "task = json.load(sys.stdin)\n"
        "print(json.dumps({'task_id': task['task_id'], 'proof_script': " + repr(_proof()) + "}))\n",
        encoding="utf-8",
    )

    def fake_verify(*args: object, **kwargs: object) -> VerifyResult:
        return VerifyResult(passed=False, reason="compile_error", stderr_tail="unknown identifier `bad`")

    monkeypatch.setattr("lemma.verifiers.lean.run_lean_verify", fake_verify)

    with pytest.raises(ProverError, match=r"local verification failed after 1 attempt\(s\)"):
        mine_once(_settings(tmp_path), prover_command=f"{sys.executable} {script}", registry=_registry())
    rows = [json.loads(line) for line in (tmp_path / "operator" / "miner-attempts.jsonl").read_text().splitlines()]
    assert rows == [
        {
            "created_at": rows[0]["created_at"],
            "passed_local_verify": False,
            "proof_script": _proof().strip(),
            "proof_sha256": rows[0]["proof_sha256"],
            "stderr_tail": "unknown identifier `bad`",
            "target_sha256": rows[0]["target_sha256"],
            "task_id": "lemma.test.true",
            "task_version": rows[0]["task_version"],
            "verify_reason": "compile_error",
        }
    ]


def test_mine_once_repairs_hosted_proof_after_compile_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = []

    def fake_prover(
        settings: LemmaSettings,
        task: object,
        *,
        failed_proof: ProverResult | None = None,
        failed_verification: VerifyResult | None = None,
    ) -> ProverResult:
        calls.append((failed_proof, failed_verification))
        body = "  exact bad" if failed_proof is None else "  trivial"
        return ProverResult(task_id="lemma.test.true", proof_script=_proof(body))

    def fake_verify(task: object, submission: object) -> VerifyResult:
        assert isinstance(submission, LemmaSubmission)
        if "exact bad" in submission.proof_script:
            return VerifyResult(passed=False, reason="compile_error", stderr_tail="unknown identifier")
        return VerifyResult(passed=True, reason="ok")

    class FakeVerifier:
        def verify(self, task: object, submission: object) -> VerifyResult:
            return fake_verify(task, submission)

    monkeypatch.setattr("lemma.miner.run_openai_compatible_prover", fake_prover)
    monkeypatch.setattr("lemma.miner.get_verifier", lambda *args, **kwargs: FakeVerifier())
    monkeypatch.setattr("lemma.miner.verify_result_from_adapter_result", lambda result: result)

    result = mine_once(
        _settings(tmp_path).model_copy(
            update={"prover_base_url": "https://example.test", "prover_model": "model", "prover_repair_attempts": 1}
        ),
        registry=_registry(),
    )

    assert result.verification.passed is True
    assert len(calls) == 2
    assert calls[1][0] is not None
    assert calls[1][1] is not None


def test_mine_once_tries_source_theorem_wrapper_before_hosted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task = _task().model_copy(update={"metadata": {"source_theorem_name": "known_true"}})
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)

    def fail_prover(*args: object, **kwargs: object) -> ProverResult:
        raise AssertionError("hosted prover should not run when source theorem wrapper verifies")

    class FakeVerifier:
        def verify(self, task: object, submission: object) -> VerifyResult:
            assert isinstance(submission, LemmaSubmission)
            return VerifyResult(passed="exact known_true" in submission.proof_script, reason="ok")

    monkeypatch.setattr("lemma.miner.run_openai_compatible_prover", fail_prover)
    monkeypatch.setattr("lemma.miner.get_verifier", lambda *args, **kwargs: FakeVerifier())
    monkeypatch.setattr("lemma.miner.verify_result_from_adapter_result", lambda result: result)

    result = mine_once(
        _settings(tmp_path).model_copy(update={"prover_base_url": "https://example.test", "prover_model": "model"}),
        registry=registry,
    )

    assert result.verification.passed is True
    assert result.submission.metadata["prover"] == "source_theorem_wrapper"


def test_mine_once_wraps_source_theorem_for_false_disjunction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task = make_task(
        task_id="lemma.test.or_false",
        title="Or false task",
        theorem_name="test_or_false",
        type_expr="True ∨ False",
        source_stream="human_curated",
        source_name="pytest",
        metadata={"source_theorem_name": "known_true"},
    )
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)

    def fail_prover(*args: object, **kwargs: object) -> ProverResult:
        raise AssertionError("hosted prover should not run when source theorem wrapper verifies")

    class FakeVerifier:
        def verify(self, task: object, submission: object) -> VerifyResult:
            assert isinstance(submission, LemmaSubmission)
            return VerifyResult(passed="exact Or.inl known_true" in submission.proof_script, reason="ok")

    monkeypatch.setattr("lemma.miner.run_openai_compatible_prover", fail_prover)
    monkeypatch.setattr("lemma.miner.get_verifier", lambda *args, **kwargs: FakeVerifier())
    monkeypatch.setattr("lemma.miner.verify_result_from_adapter_result", lambda result: result)

    result = mine_once(
        _settings(tmp_path).model_copy(update={"prover_base_url": "https://example.test", "prover_model": "model"}),
        registry=registry,
    )

    assert result.verification.passed is True
    assert result.submission.metadata["prover"] == "source_theorem_wrapper"


def test_mine_once_wraps_strengthened_source_theorem_for_false_disjunction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task = make_task(
        task_id="lemma.test.and_or_false",
        title="And or false task",
        theorem_name="test_and_or_false",
        type_expr="(True ∧ True) ∨ False",
        source_stream="human_curated",
        source_name="pytest",
        metadata={
            "source_theorem_name": "known_true",
            "mutation_chain": [
                {
                    "operator": "strengthen",
                    "params": {"rule": "conjoin_peer_conclusion", "peer_theorem_name": "known_peer"},
                },
                {"operator": "weaken", "params": {"rule": "false_disjunct"}},
            ],
        },
    )
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)

    def fail_prover(*args: object, **kwargs: object) -> ProverResult:
        raise AssertionError("hosted prover should not run when source theorem wrapper verifies")

    class FakeVerifier:
        def verify(self, task: object, submission: object) -> VerifyResult:
            assert isinstance(submission, LemmaSubmission)
            expected = "exact Or.inl (And.intro (known_true) known_peer)"
            return VerifyResult(passed=expected in submission.proof_script, reason="ok")

    monkeypatch.setattr("lemma.miner.run_openai_compatible_prover", fail_prover)
    monkeypatch.setattr("lemma.miner.get_verifier", lambda *args, **kwargs: FakeVerifier())
    monkeypatch.setattr("lemma.miner.verify_result_from_adapter_result", lambda result: result)

    result = mine_once(
        _settings(tmp_path).model_copy(update={"prover_base_url": "https://example.test", "prover_model": "model"}),
        registry=registry,
    )

    assert result.verification.passed is True
    assert result.submission.metadata["prover"] == "source_theorem_wrapper"


def test_mine_once_wraps_true_premise_source_theorem_without_overintroducing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task = make_task(
        task_id="lemma.test.true_wrapped_forall",
        title="True wrapped forall task",
        theorem_name="test_true_wrapped_forall",
        type_expr="True → (True → (∀ (α : Type _) [Ring α] [NoZeroDivisors α], IsDomain α))",
        source_stream="human_curated",
        source_name="pytest",
        metadata={"source_theorem_name": "NoZeroDivisors.to_isDomain"},
    )
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)

    def fail_prover(*args: object, **kwargs: object) -> ProverResult:
        raise AssertionError("hosted prover should not run when source theorem wrapper verifies")

    class FakeVerifier:
        def verify(self, task: object, submission: object) -> VerifyResult:
            assert isinstance(submission, LemmaSubmission)
            expected = "intro _\n  intro _\n  exact NoZeroDivisors.to_isDomain"
            return VerifyResult(passed=expected in submission.proof_script, reason="ok")

    monkeypatch.setattr("lemma.miner.run_openai_compatible_prover", fail_prover)
    monkeypatch.setattr("lemma.miner.get_verifier", lambda *args, **kwargs: FakeVerifier())
    monkeypatch.setattr("lemma.miner.verify_result_from_adapter_result", lambda result: result)

    result = mine_once(
        _settings(tmp_path).model_copy(update={"prover_base_url": "https://example.test", "prover_model": "model"}),
        registry=registry,
    )

    assert result.verification.passed is True
    assert result.submission.metadata["prover"] == "source_theorem_wrapper"


def test_mine_once_wraps_true_premise_before_strengthened_source_theorem(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task = make_task(
        task_id="lemma.test.true_premise_and_peer",
        title="True premise and peer task",
        theorem_name="test_true_premise_and_peer",
        type_expr="(True → True) ∧ True",
        source_stream="human_curated",
        source_name="pytest",
        metadata={
            "source_theorem_name": "known_true",
            "mutation_chain": [
                {"operator": "specialize", "params": {"fallback": "true_premise"}},
                {
                    "operator": "strengthen",
                    "params": {"rule": "conjoin_peer_conclusion", "peer_theorem_name": "known_peer"},
                },
            ],
        },
    )
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)

    def fail_prover(*args: object, **kwargs: object) -> ProverResult:
        raise AssertionError("hosted prover should not run when source theorem wrapper verifies")

    class FakeVerifier:
        def verify(self, task: object, submission: object) -> VerifyResult:
            assert isinstance(submission, LemmaSubmission)
            expected = "exact And.intro ((fun _ => known_true)) known_peer"
            return VerifyResult(passed=expected in submission.proof_script, reason="ok")

    monkeypatch.setattr("lemma.miner.run_openai_compatible_prover", fail_prover)
    monkeypatch.setattr("lemma.miner.get_verifier", lambda *args, **kwargs: FakeVerifier())
    monkeypatch.setattr("lemma.miner.verify_result_from_adapter_result", lambda result: result)

    result = mine_once(
        _settings(tmp_path).model_copy(update={"prover_base_url": "https://example.test", "prover_model": "model"}),
        registry=registry,
    )

    assert result.verification.passed is True
    assert result.submission.metadata["prover"] == "source_theorem_wrapper"


def test_mine_once_does_not_overintroduce_fallback_true_premises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task = make_task(
        task_id="lemma.test.double_true_premise",
        title="Double true premise task",
        theorem_name="test_double_true_premise",
        type_expr="True → (True → True)",
        source_stream="human_curated",
        source_name="pytest",
        metadata={
            "source_theorem_name": "known_true",
            "mutation_chain": [
                {"operator": "specialize", "params": {"fallback": "true_premise"}},
                {"operator": "specialize", "params": {"fallback": "true_premise"}},
            ],
        },
    )
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)

    def fail_prover(*args: object, **kwargs: object) -> ProverResult:
        raise AssertionError("hosted prover should not run when source theorem wrapper verifies")

    class FakeVerifier:
        def verify(self, task: object, submission: object) -> VerifyResult:
            assert isinstance(submission, LemmaSubmission)
            expected = "exact (fun _ => (fun _ => known_true))"
            return VerifyResult(
                passed=expected in submission.proof_script and "intro _" not in submission.proof_script,
                reason="ok",
            )

    monkeypatch.setattr("lemma.miner.run_openai_compatible_prover", fail_prover)
    monkeypatch.setattr("lemma.miner.get_verifier", lambda *args, **kwargs: FakeVerifier())
    monkeypatch.setattr("lemma.miner.verify_result_from_adapter_result", lambda result: result)

    result = mine_once(
        _settings(tmp_path).model_copy(update={"prover_base_url": "https://example.test", "prover_model": "model"}),
        registry=registry,
    )

    assert result.verification.passed is True
    assert result.submission.metadata["prover"] == "source_theorem_wrapper"


def test_mine_once_wraps_peer_premise_before_false_disjunction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task = make_task(
        task_id="lemma.test.peer_premise_or_false",
        title="Peer premise or false task",
        theorem_name="test_peer_premise_or_false",
        type_expr="(True) → (True ∨ False)",
        source_stream="human_curated",
        source_name="pytest",
        metadata={
            "source_theorem_name": "known_true",
            "mutation_chain": [
                {
                    "operator": "conjoin",
                    "params": {"mode": "peer_premise", "peer_theorem_name": "known_peer"},
                },
                {"operator": "weaken", "params": {"rule": "false_disjunct"}},
            ],
        },
    )
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)

    def fail_prover(*args: object, **kwargs: object) -> ProverResult:
        raise AssertionError("hosted prover should not run when source theorem wrapper verifies")

    class FakeVerifier:
        def verify(self, task: object, submission: object) -> VerifyResult:
            assert isinstance(submission, LemmaSubmission)
            expected = "intro _\n  exact Or.inl (known_true)"
            return VerifyResult(passed=expected in submission.proof_script, reason="ok")

    monkeypatch.setattr("lemma.miner.run_openai_compatible_prover", fail_prover)
    monkeypatch.setattr("lemma.miner.get_verifier", lambda *args, **kwargs: FakeVerifier())
    monkeypatch.setattr("lemma.miner.verify_result_from_adapter_result", lambda result: result)

    result = mine_once(
        _settings(tmp_path).model_copy(update={"prover_base_url": "https://example.test", "prover_model": "model"}),
        registry=registry,
    )

    assert result.verification.passed is True
    assert result.submission.metadata["prover"] == "source_theorem_wrapper"


def test_mine_once_wraps_fresh_prop_hypothesis_strengthened_source_theorem(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task = make_task(
        task_id="lemma.test.fresh_prop_and",
        title="Fresh prop and task",
        theorem_name="test_fresh_prop_and",
        type_expr="∀ P : Prop, P → (True ∧ True)",
        source_stream="human_curated",
        source_name="pytest",
        metadata={
            "source_theorem_name": "known_true",
            "mutation_chain": [
                {
                    "operator": "strengthen",
                    "params": {"rule": "conjoin_peer_conclusion", "peer_theorem_name": "known_peer"},
                },
                {
                    "operator": "generalize",
                    "params": {"target": "fresh_prop_hypothesis", "binder_type": "Prop"},
                },
            ],
        },
    )
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)

    def fail_prover(*args: object, **kwargs: object) -> ProverResult:
        raise AssertionError("hosted prover should not run when source theorem wrapper verifies")

    class FakeVerifier:
        def verify(self, task: object, submission: object) -> VerifyResult:
            assert isinstance(submission, LemmaSubmission)
            expected = "exact (fun _ _ => And.intro (known_true) known_peer)"
            return VerifyResult(passed=expected in submission.proof_script, reason="ok")

    monkeypatch.setattr("lemma.miner.run_openai_compatible_prover", fail_prover)
    monkeypatch.setattr("lemma.miner.get_verifier", lambda *args, **kwargs: FakeVerifier())
    monkeypatch.setattr("lemma.miner.verify_result_from_adapter_result", lambda result: result)

    result = mine_once(
        _settings(tmp_path).model_copy(update={"prover_base_url": "https://example.test", "prover_model": "model"}),
        registry=registry,
    )

    assert result.verification.passed is True
    assert result.submission.metadata["prover"] == "source_theorem_wrapper"


def test_mine_once_wraps_reversed_source_theorem_inside_fresh_prop_hypothesis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task = make_task(
        task_id="lemma.test.fresh_prop_symm",
        title="Fresh prop symm task",
        theorem_name="test_fresh_prop_symm",
        type_expr="∀ P : Prop, P → (False = True)",
        source_stream="human_curated",
        source_name="pytest",
        metadata={
            "source_theorem_name": "known_false_true",
            "mutation_chain": [
                {"operator": "symm", "params": {"rule": "reverse_relation", "relation": "="}},
                {
                    "operator": "generalize",
                    "params": {"target": "fresh_prop_hypothesis", "binder_type": "Prop"},
                },
            ],
        },
    )
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)

    def fail_prover(*args: object, **kwargs: object) -> ProverResult:
        raise AssertionError("hosted prover should not run when source theorem wrapper verifies")

    class FakeVerifier:
        def verify(self, task: object, submission: object) -> VerifyResult:
            assert isinstance(submission, LemmaSubmission)
            expected = "exact (fun _ _ => (known_false_true).symm)"
            return VerifyResult(passed=expected in submission.proof_script, reason="ok")

    monkeypatch.setattr("lemma.miner.run_openai_compatible_prover", fail_prover)
    monkeypatch.setattr("lemma.miner.get_verifier", lambda *args, **kwargs: FakeVerifier())
    monkeypatch.setattr("lemma.miner.verify_result_from_adapter_result", lambda result: result)

    result = mine_once(
        _settings(tmp_path).model_copy(update={"prover_base_url": "https://example.test", "prover_model": "model"}),
        registry=registry,
    )

    assert result.verification.passed is True
    assert result.submission.metadata["prover"] == "source_theorem_wrapper"


def test_mine_once_specializes_reversed_source_theorem(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task = make_task(
        task_id="lemma.test.specialized_symm",
        title="Specialized symm task",
        theorem_name="test_specialized_symm",
        type_expr="true = true",
        source_stream="human_curated",
        source_name="pytest",
        metadata={
            "source_theorem_name": "known_bool",
            "mutation_chain": [
                {"operator": "symm", "params": {"rule": "reverse_relation", "relation": "="}},
                {
                    "operator": "specialize",
                    "params": {"binder": "b", "binder_type": "Bool", "value": "true"},
                },
            ],
        },
    )
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)

    def fail_prover(*args: object, **kwargs: object) -> ProverResult:
        raise AssertionError("hosted prover should not run when source theorem wrapper verifies")

    class FakeVerifier:
        def verify(self, task: object, submission: object) -> VerifyResult:
            assert isinstance(submission, LemmaSubmission)
            expected = "exact ((known_bool (true : Bool))).symm"
            return VerifyResult(passed=expected in submission.proof_script, reason="ok")

    monkeypatch.setattr("lemma.miner.run_openai_compatible_prover", fail_prover)
    monkeypatch.setattr("lemma.miner.get_verifier", lambda *args, **kwargs: FakeVerifier())
    monkeypatch.setattr("lemma.miner.verify_result_from_adapter_result", lambda result: result)

    result = mine_once(
        _settings(tmp_path).model_copy(update={"prover_base_url": "https://example.test", "prover_model": "model"}),
        registry=registry,
    )

    assert result.verification.passed is True
    assert result.submission.metadata["prover"] == "source_theorem_wrapper"


def test_mine_once_wraps_fresh_prop_before_strengthening_inside_source_conjunct(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task = make_task(
        task_id="lemma.test.fresh_prop_then_and",
        title="Fresh prop then and task",
        theorem_name="test_fresh_prop_then_and",
        type_expr="(∀ P : Prop, P → True) ∧ True",
        source_stream="human_curated",
        source_name="pytest",
        metadata={
            "source_theorem_name": "known_true",
            "mutation_chain": [
                {
                    "operator": "generalize",
                    "params": {"target": "fresh_prop_hypothesis", "binder_type": "Prop"},
                },
                {
                    "operator": "strengthen",
                    "params": {"rule": "conjoin_peer_conclusion", "peer_theorem_name": "known_peer"},
                },
            ],
        },
    )
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)

    def fail_prover(*args: object, **kwargs: object) -> ProverResult:
        raise AssertionError("hosted prover should not run when source theorem wrapper verifies")

    class FakeVerifier:
        def verify(self, task: object, submission: object) -> VerifyResult:
            assert isinstance(submission, LemmaSubmission)
            expected = "exact And.intro ((fun _ _ => known_true)) known_peer"
            return VerifyResult(passed=expected in submission.proof_script, reason="ok")

    monkeypatch.setattr("lemma.miner.run_openai_compatible_prover", fail_prover)
    monkeypatch.setattr("lemma.miner.get_verifier", lambda *args, **kwargs: FakeVerifier())
    monkeypatch.setattr("lemma.miner.verify_result_from_adapter_result", lambda result: result)

    result = mine_once(
        _settings(tmp_path).model_copy(update={"prover_base_url": "https://example.test", "prover_model": "model"}),
        registry=registry,
    )

    assert result.verification.passed is True
    assert result.submission.metadata["prover"] == "source_theorem_wrapper"


def test_mine_once_wraps_self_conjunction_source_theorem(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task = make_task(
        task_id="lemma.test.self_conjoin",
        title="Self conjoin task",
        theorem_name="test_self_conjoin",
        type_expr="∀ P : Prop, P → (True ∧ True)",
        source_stream="human_curated",
        source_name="pytest",
        metadata={
            "source_theorem_name": "known_true",
            "mutation_chain": [
                {"operator": "conjoin-self", "params": {"rule": "conjoin_self"}},
                {
                    "operator": "generalize",
                    "params": {"target": "fresh_prop_hypothesis", "binder_type": "Prop"},
                },
            ],
        },
    )
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)

    def fail_prover(*args: object, **kwargs: object) -> ProverResult:
        raise AssertionError("hosted prover should not run when source theorem wrapper verifies")

    class FakeVerifier:
        def verify(self, task: object, submission: object) -> VerifyResult:
            assert isinstance(submission, LemmaSubmission)
            expected = "exact (fun _ _ => And.intro (known_true) (known_true))"
            return VerifyResult(passed=expected in submission.proof_script, reason="ok")

    monkeypatch.setattr("lemma.miner.run_openai_compatible_prover", fail_prover)
    monkeypatch.setattr("lemma.miner.get_verifier", lambda *args, **kwargs: FakeVerifier())
    monkeypatch.setattr("lemma.miner.verify_result_from_adapter_result", lambda result: result)

    result = mine_once(
        _settings(tmp_path).model_copy(update={"prover_base_url": "https://example.test", "prover_model": "model"}),
        registry=registry,
    )

    assert result.verification.passed is True
    assert result.submission.metadata["prover"] == "source_theorem_wrapper"


def test_mine_once_wraps_substitute_type_fallback_inside_fresh_prop_hypothesis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task = make_task(
        task_id="lemma.test.unsupported_type_and_fresh_prop",
        title="Unsupported type fallback and fresh prop task",
        theorem_name="test_unsupported_type_and_fresh_prop",
        type_expr="∀ P : Prop, P → (True → True)",
        source_stream="human_curated",
        source_name="pytest",
        metadata={
            "source_theorem_name": "known_true",
            "mutation_chain": [
                {"operator": "substitute-type", "params": {"fallback": "no_supported_type_occurrence"}},
                {
                    "operator": "generalize",
                    "params": {"target": "fresh_prop_hypothesis", "binder_type": "Prop"},
                },
            ],
        },
    )
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)

    def fail_prover(*args: object, **kwargs: object) -> ProverResult:
        raise AssertionError("hosted prover should not run when source theorem wrapper verifies")

    class FakeVerifier:
        def verify(self, task: object, submission: object) -> VerifyResult:
            assert isinstance(submission, LemmaSubmission)
            expected = "exact (fun _ _ => (fun _ => known_true))"
            return VerifyResult(passed=expected in submission.proof_script, reason="ok")

    monkeypatch.setattr("lemma.miner.run_openai_compatible_prover", fail_prover)
    monkeypatch.setattr("lemma.miner.get_verifier", lambda *args, **kwargs: FakeVerifier())
    monkeypatch.setattr("lemma.miner.verify_result_from_adapter_result", lambda result: result)

    result = mine_once(
        _settings(tmp_path).model_copy(update={"prover_base_url": "https://example.test", "prover_model": "model"}),
        registry=registry,
    )

    assert result.verification.passed is True
    assert result.submission.metadata["prover"] == "source_theorem_wrapper"


def test_mine_once_does_not_reuse_source_theorem_after_type_substitution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task = _task().model_copy(
        update={
            "metadata": {
                "source_theorem_name": "known_true",
                "mutation_chain": [
                    {
                        "operator": "substitute-type",
                        "params": {"source": "Complex.re", "target": "Complex.im"},
                    }
                ],
            }
        }
    )
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)
    calls = []

    def fake_prover(*args: object, **kwargs: object) -> ProverResult:
        calls.append(kwargs)
        return ProverResult(task_id=task.id, proof_script=_proof("  trivial"))

    class FakeVerifier:
        def verify(self, task: object, submission: object) -> VerifyResult:
            assert isinstance(submission, LemmaSubmission)
            return VerifyResult(passed="trivial" in submission.proof_script, reason="ok")

    monkeypatch.setattr("lemma.miner.run_openai_compatible_prover", fake_prover)
    monkeypatch.setattr("lemma.miner.get_verifier", lambda *args, **kwargs: FakeVerifier())
    monkeypatch.setattr("lemma.miner.verify_result_from_adapter_result", lambda result: result)

    result = mine_once(
        _settings(tmp_path).model_copy(update={"prover_base_url": "https://example.test", "prover_model": "model"}),
        registry=registry,
    )

    assert result.verification.passed is True
    assert calls == [{}]
    assert "source_theorem_name" not in prover_input(task, timeout_s=1.0)


def test_mine_once_falls_back_to_hosted_when_source_theorem_wrapper_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task = _task().model_copy(update={"metadata": {"source_theorem_name": "known_true"}})
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)
    calls = []

    def fake_prover(*args: object, **kwargs: object) -> ProverResult:
        calls.append(kwargs)
        return ProverResult(task_id=task.id, proof_script=_proof("  trivial"))

    class FakeVerifier:
        def verify(self, task: object, submission: object) -> VerifyResult:
            assert isinstance(submission, LemmaSubmission)
            return VerifyResult(passed="trivial" in submission.proof_script, reason="ok")

    monkeypatch.setattr("lemma.miner.run_openai_compatible_prover", fake_prover)
    monkeypatch.setattr("lemma.miner.get_verifier", lambda *args, **kwargs: FakeVerifier())
    monkeypatch.setattr("lemma.miner.verify_result_from_adapter_result", lambda result: result)

    result = mine_once(
        _settings(tmp_path).model_copy(update={"prover_base_url": "https://example.test", "prover_model": "model"}),
        registry=registry,
    )

    assert result.verification.passed is True
    assert len(calls) == 1


def test_mine_once_defaults_to_validator_active_window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    first = _task("lemma.test.first")
    second = _task("lemma.test.second")
    registry = TaskRegistry(schema_version=1, tasks=(first, second), sha256="0" * 64)
    settings = _settings(tmp_path).model_copy(
        update={"active_task_count": 1, "active_queue_seed": "miner-active-0", "active_tempo_seconds": 10**12}
    )
    active_task = active_tasks_for_validation(registry, settings, tempo=0)[0]
    assert active_task.id == second.id

    def fake_solve(_settings: LemmaSettings, task: object, *, prover_command: str | None = None) -> ProverResult:
        assert isinstance(task, type(first))
        return ProverResult(task_id=task.id, proof_script=_proof_for(task.theorem_name))

    monkeypatch.setattr("lemma.miner.solve_task", fake_solve)
    monkeypatch.setattr(
        "lemma.verifiers.lean.run_lean_verify",
        lambda *args, **kwargs: VerifyResult(passed=True, reason="ok"),
    )

    result = mine_once(settings, registry=registry)

    assert result.task.id == active_task.id


def test_curriculum_state_applies_after_recorded_tempo(tmp_path: Path) -> None:
    state_path = tmp_path / "curriculum.jsonl"
    append_curriculum_record(
        state_path,
        CurriculumTempoRecord(
            tempo=4,
            active_K=2,
            frontier_depth=1,
            ema_solve_rate=0.5,
            solved_slots=1,
            parked_task_ids=(),
            action="hold",
            variant_stream_requested=False,
        ),
    )
    registry = TaskRegistry(
        schema_version=1,
        tasks=(
            _task("lemma.test.first", queue_depth=0),
            _task("lemma.test.second", queue_depth=1),
        ),
        sha256="0" * 64,
    )
    settings = _settings(tmp_path).model_copy(
        update={
            "active_task_count": 1,
            "frontier_depth": 0,
            "active_queue_seed": "curriculum-state",
            "curriculum_retarget_enabled": True,
            "curriculum_state_jsonl": state_path,
        }
    )

    same_tempo = active_tasks_for_validation(registry, settings, tempo=4)
    next_tempo = active_tasks_for_validation(registry, settings, tempo=5)
    effective_tempo = active_tasks_for_validation(registry, settings, tempo=6)

    assert len(same_tempo) == 1
    assert len(next_tempo) == 1
    assert len(effective_tempo) == 2
    assert {task.frontier_depth for task in effective_tempo} == {1}


def test_production_curriculum_state_must_be_marked_public(tmp_path: Path) -> None:
    state_path = tmp_path / "curriculum.jsonl"
    append_curriculum_record(
        state_path,
        CurriculumTempoRecord(
            tempo=4,
            active_K=2,
            frontier_depth=1,
            ema_solve_rate=0.5,
            solved_slots=1,
            parked_task_ids=(),
            action="hold",
            variant_stream_requested=False,
        ),
    )
    settings = _settings(tmp_path).model_copy(
        update={
            "active_task_count": 1,
            "frontier_depth": 0,
            "protocol_mode": "production",
            "curriculum_retarget_enabled": True,
            "curriculum_state_jsonl": state_path,
        }
    )

    with pytest.raises(RuntimeError, match="LEMMA_CURRICULUM_STATE_PUBLIC"):
        curriculum_controlled_settings(settings, tempo=5)

    public = curriculum_controlled_settings(settings.model_copy(update={"curriculum_state_public": True}), tempo=6)

    assert public.active_task_count == 2
    assert public.frontier_depth == 1


def test_curriculum_state_rejects_stale_active_registry_cache(tmp_path: Path) -> None:
    state_path = tmp_path / "curriculum.jsonl"
    append_curriculum_record(
        state_path,
        CurriculumTempoRecord(
            tempo=4,
            active_K=2,
            frontier_depth=1,
            ema_solve_rate=0.5,
            solved_slots=1,
            parked_task_ids=(),
            action="hold",
            variant_stream_requested=False,
        ),
    )
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "tempo-6.registry.json"
    write_registry([_task("lemma.test.old").model_copy(update={"frontier_depth": 0})], cache_path)
    settings = _settings(tmp_path).model_copy(
        update={
            "active_task_count": 1,
            "frontier_depth": 0,
            "active_registry_cache_dir": cache_dir,
            "curriculum_retarget_enabled": True,
            "curriculum_state_jsonl": state_path,
        }
    )
    effective = curriculum_controlled_settings(settings, tempo=6)

    assert cached_active_registry_for_tempo(effective, tempo=6) is None

    write_registry(
        [
            _task("lemma.test.new-a").model_copy(update={"frontier_depth": 1}),
            _task("lemma.test.new-b").model_copy(update={"frontier_depth": 1}),
        ],
        cache_path,
    )

    cached = cached_active_registry_for_tempo(effective, tempo=6)
    assert cached is not None
    assert len(cached.tasks) == 2


def test_production_implicit_active_registry_cache_rejects_stale_cache(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_path = cache_dir / "tempo-6.registry.json"
    write_registry(
        [
            _task("lemma.procedural.stale").model_copy(
                update={
                    "source_stream": "procedural",
                    "frontier_depth": 0,
                }
            )
        ],
        cache_path,
    )
    settings = _settings(tmp_path).model_copy(
        update={
            "protocol_mode": "production",
            "task_supply_mode": "procedural",
            "active_task_count": 1,
            "frontier_depth": 0,
            "active_registry_cache_dir": cache_dir,
        }
    )

    assert cached_active_registry_for_tempo(settings, tempo=6) is None


def test_validate_once_retargets_curriculum_state_after_tempo(tmp_path: Path) -> None:
    state_path = tmp_path / "curriculum.jsonl"
    first = _task("lemma.test.first")
    second = _task("lemma.test.second")
    registry = TaskRegistry(schema_version=1, tasks=(first, second), sha256="0" * 64)
    settings = _settings(tmp_path).model_copy(
        update={
            "active_task_count": 2,
            "active_queue_seed": "curriculum-retarget",
            "curriculum_retarget_enabled": True,
            "curriculum_state_jsonl": state_path,
            "validator_capacity": 4,
            "curriculum_beta": 0.0,
            "curriculum_k_max": 4,
        }
    )

    validate_once(
        settings,
        [build_submission(first, solver_hotkey="hk-a", proof_script=_proof())],
        registry=registry,
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        tempo=9,
        no_set_weights=True,
    )

    records = read_curriculum_records(state_path)
    assert len(records) == 1
    assert records[0].tempo == 9
    assert records[0].solved_slots == 1
    assert records[0].active_K == 3
    assert records[0].frontier_depth == 0
    assert records[0].retarget_receipt == {
        "version": "lemma-curriculum-retarget-v1",
        "activation_tempo": 11,
        "previous_active_K": 2,
        "previous_frontier_depth": 0,
        "previous_ema_solve_rate": 0.5,
        "solved_slots": 1,
        "solve_rate": 0.5,
        "validator_capacity": 4,
        "config": {
            "beta": 0.0,
            "low_band": 0.4,
            "high_band": 0.7,
            "k_min": 1,
            "k_max": 4,
            "cost_budget_s": 0.0,
            "base_task_cost_s": 0.0,
            "depth_cost_multiplier": 2.0,
        },
        "next_active_K": 3,
        "next_frontier_depth": 0,
        "next_ema_solve_rate": 0.5,
    }
    curriculum_dir = tmp_path / "operator" / "canonical" / "sn0" / "curriculum"
    manifest = json.loads((curriculum_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["latest_tempo"] == 9
    assert manifest["latest_active_K"] == 3
    assert (curriculum_dir / "tempo-000009.json").is_file()
    public_row = json.loads((curriculum_dir / "curriculum.jsonl").read_text(encoding="utf-8"))
    assert public_row["tempo"] == 9
    assert public_row["retarget_receipt"] == records[0].retarget_receipt

    validate_once(settings, [], registry=registry, tempo=9, no_set_weights=True)

    assert len(read_curriculum_records(state_path)) == 1


def test_validate_once_does_not_duplicate_corpus_rows_for_same_tempo(tmp_path: Path) -> None:
    task = _task()
    registry = TaskRegistry(schema_version=1, tasks=(task,), sha256="0" * 64)
    settings = _settings(tmp_path)
    submission = build_submission(task, solver_hotkey="hk-a", proof_script=_proof())

    for _ in range(2):
        validate_once(
            settings,
            [submission],
            registry=registry,
            verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
            tempo=9,
            no_set_weights=True,
        )

    files = sorted(settings.corpus_output_dir.glob("epoch-*.jsonl"))
    rows = [json.loads(line) for path in files for line in path.read_text(encoding="utf-8").splitlines()]

    assert len(files) == 1
    assert len(rows) == 1
    assert rows[0]["tempo"] == 9


def test_validator_scores_and_writes_alternate_corpus_rows(tmp_path: Path) -> None:
    task = _task()
    submissions = [
        build_submission(task, solver_hotkey="hk-a", proof_script=_proof("  trivial")),
        build_submission(task, solver_hotkey="hk-b", proof_script=_proof("  exact True.intro")),
        build_submission(task, solver_hotkey="hk-c", proof_script=_proof("  exact True.intro")),
    ]

    result = validate_once(
        _settings(tmp_path),
        submissions,
        registry=_registry(),
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        validator_hotkey="vhk",
        epoch=7,
        no_set_weights=True,
    )

    assert result.score.credits == {"hk-a": 1}
    assert result.score.scores == {"hk-a": 1.0}
    assert [(row.solver_hotkey, row.rewarded) for row in result.corpus_rows] == [("hk-a", True), ("hk-b", False)]
    assert (tmp_path / "corpus" / "epoch-7.jsonl").exists()
    assert (tmp_path / "corpus" / "corpus-index.json").exists()
    score_events = (tmp_path / "operator" / "score-events.jsonl").read_text(encoding="utf-8")
    assert '"score":1.0' in score_events
    assert '"rewarded":false' in score_events
    run_summary = ValidatorRunSummary.model_validate_json(
        (tmp_path / "operator" / "validator-runs.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert result.summary == run_summary
    assert run_summary.registry_sha256 == "0" * 64
    assert run_summary.active_K == 1
    assert run_summary.frontier_depth == 0
    assert run_summary.verified_count == 3
    assert run_summary.accepted_unique_count == 2
    assert run_summary.rewarded_count == 1
    assert run_summary.score_event_count == 2
    assert run_summary.corpus_row_count == 2
    assert run_summary.unearned_share == 0.0
    assert run_summary.unearned_policy == "burn"
    assert run_summary.weights_set is False
    assert run_summary.active_pool_directory_sha256
    assert run_summary.accepted_merkle_root
    assert run_summary.accepted_directory_sha256
    assert run_summary.tempo_commitment_payload.startswith("lemma-tempo-v1:")
    tempo_dir = f"tempo-{run_summary.active_tempo:06d}"
    assert (tmp_path / "operator" / "canonical" / "sn0" / "active-pools" / tempo_dir / "manifest.json").exists()
    assert (tmp_path / "operator" / "canonical" / "sn0" / "tempos" / tempo_dir / "manifest.json").exists()


def test_validator_keeps_scoring_when_optional_s3_publish_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from lemma.corpus import publish

    def fail_publish(*args: object, **kwargs: object) -> object:
        raise RuntimeError("object store unavailable")

    monkeypatch.setattr(publish, "publish_paths_to_s3", fail_publish)
    settings = _settings(tmp_path).model_copy(
        update={
            "canonical_publish_s3_uri": "s3://lemma-corpus/live",
            "canonical_publish_endpoint_url": "https://s3.example",
            "canonical_publish_aws_command": "aws",
        }
    )

    result = validate_once(
        settings,
        [build_submission(_task(), solver_hotkey="hk", proof_script=_proof())],
        registry=_registry(),
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),  # noqa: ARG005
        no_set_weights=True,
    )

    assert result.summary.accepted_unique_count == 1
    assert result.summary.canonical_publish_count == 0
    publish_rows = [
        json.loads(line)
        for line in (tmp_path / "operator" / "canonical-publish.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert publish_rows == [
        {"kind": "s3_publish_error", "error_type": "RuntimeError", "error": "object store unavailable"}
    ]


def test_validator_scores_from_recorded_kernel_dependencies(tmp_path: Path) -> None:
    task = _task("lemma.test.active", queue_depth=0)
    submission = build_submission(task, solver_hotkey="hk", proof_script=_proof())
    registry = TaskRegistry(
        schema_version=1,
        tasks=(task, _task("lemma.test.unsolved", queue_depth=0)),
        sha256="0" * 64,
    )

    result = validate_once(
        _settings(tmp_path),
        [submission],
        registry=registry,
        verify_submission=lambda task, submission: VerifyResult(  # noqa: ARG005
            passed=True,
            reason="ok",
            kernel_dependencies=("True", "True.intro"),
        ),
        no_set_weights=True,
    )

    assert result.score.scores["hk"] == pytest.approx(1.43 / 2.43)
    assert result.corpus_rows[0].dependencies.mathlib_theorems_used == ("True", "True.intro")
    assert result.corpus_rows[0].metadata["slot_weight_inputs"]["kernel_dependencies_recorded"] is True


def test_validator_no_epoch_uses_next_numbered_local_file(tmp_path: Path) -> None:
    task = _task()
    submission = build_submission(task, solver_hotkey="hk-a", proof_script=_proof())
    settings = _settings(tmp_path)

    validate_once(
        settings,
        [submission],
        registry=_registry(),
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        tempo=1,
        no_set_weights=True,
    )
    validate_once(
        settings,
        [submission],
        registry=_registry(),
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        tempo=2,
        no_set_weights=True,
    )

    assert (tmp_path / "corpus" / "epoch-000001.jsonl").exists()
    assert (tmp_path / "corpus" / "epoch-000002.jsonl").exists()
    assert not (tmp_path / "corpus" / "epoch-local.jsonl").exists()


def test_validator_rejects_bad_target_hash_and_unsigned_live_submission(tmp_path: Path) -> None:
    task = _task()
    bad_target = build_submission(task, solver_hotkey="hk-a", proof_script=_proof()).model_copy(
        update={"target_sha256": "0" * 64}
    )
    unsigned = build_submission(task, solver_hotkey="hk-b", proof_script=_proof())

    result = validate_once(
        _settings(tmp_path),
        [bad_target, unsigned],
        registry=_registry(),
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        require_signatures=True,
        no_set_weights=True,
    )

    assert result.verification_records == ()
    receipts = (tmp_path / "operator" / "verification-records.jsonl").read_text(encoding="utf-8")
    assert "target_sha256 mismatch" in receipts
    assert "unsigned" in receipts


def test_validator_accepts_signed_live_submission(tmp_path: Path) -> None:
    task = _task()
    keypair = Keypair.create_from_uri("//LemmaSignedMiner")
    submission = sign_submission(
        build_submission(task, solver_hotkey=keypair.ss58_address, proof_script=_proof()),
        keypair,
    )

    result = validate_once(
        _settings(tmp_path),
        [submission],
        registry=_registry(),
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        require_signatures=True,
        no_set_weights=True,
    )

    assert result.verification_records[0].solver_hotkey == keypair.ss58_address
    assert result.score.scores == {keypair.ss58_address: 1.0}


def test_sign_submission_recomputes_payload_hash_after_hotkey_rebind() -> None:
    keypair = Keypair.create_from_uri("//LemmaReboundMiner")
    unsigned = build_submission(_task(), solver_hotkey="wallet-name", proof_script=_proof())
    rebound = unsigned.model_copy(update={"solver_hotkey": keypair.ss58_address})

    signed = sign_submission(rebound, keypair)

    assert signed.solver_hotkey == keypair.ss58_address
    assert signed.signature_payload_sha256 != unsigned.signature_payload_sha256
    assert LemmaSubmission.model_validate_json(signed.model_dump_json()).signature_payload_sha256 == (
        signed.signature_payload_sha256
    )


def test_signed_submission_binds_commit_reveal_fields() -> None:
    task = _task()
    keypair = Keypair.create_from_uri("//LemmaCommitRevealMiner")
    submission = sign_submission(
        build_submission(task, solver_hotkey=keypair.ss58_address, proof_script=_proof()).model_copy(
            update={
                "timelock_ciphertext": "ciphertext",
                "drand_round": 10,
                "commit_block": 42,
                "commit_extrinsic_hash": "0xabc",
            }
        ),
        keypair,
    )

    tampered = submission.model_copy(update={"drand_round": 11, "signature_payload_sha256": ""})

    assert submission.verify_signature() is True
    assert tampered.verify_signature() is False


def test_validator_rejects_bad_live_submission_signature(tmp_path: Path) -> None:
    task = _task()
    signer = Keypair.create_from_uri("//LemmaSigner")
    impostor = Keypair.create_from_uri("//LemmaImpostor")
    submission = sign_submission(
        build_submission(task, solver_hotkey=impostor.ss58_address, proof_script=_proof()),
        signer,
    )

    result = validate_once(
        _settings(tmp_path),
        [submission],
        registry=_registry(),
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        require_signatures=True,
        no_set_weights=True,
    )

    assert result.verification_records == ()
    receipts = (tmp_path / "operator" / "verification-records.jsonl").read_text(encoding="utf-8")
    assert "signature is invalid" in receipts


def test_validator_zero_credit_epoch_routes_unearned_share(tmp_path: Path) -> None:
    task = _task()
    submission = build_submission(task, solver_hotkey="hk-a", proof_script=_proof())

    result = validate_once(
        _settings(tmp_path),
        [submission],
        registry=_registry(),
        verify_submission=lambda task, submission: VerifyResult(passed=False, reason="compile_error"),
        no_set_weights=False,
    )

    assert result.score.miner_weights == {}
    assert result.score.weights == {"burn_uid:0": 1.0}
    assert result.score.unearned_share == 1.0
    assert result.weights_set is False
    assert result.corpus_rows == ()
    run_summary = ValidatorRunSummary.model_validate_json(
        (tmp_path / "operator" / "validator-runs.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert run_summary.verified_count == 1
    assert run_summary.accepted_unique_count == 0
    assert run_summary.unearned_share == 1.0
    assert run_summary.weights_set is False


def test_validator_submits_weights_only_when_enabled(tmp_path: Path) -> None:
    submission = build_submission(_task(), solver_hotkey="hk-a", proof_script=_proof())
    calls: list[dict[str, float]] = []

    def fake_submit_weights(
        _settings_arg: LemmaSettings, weights: dict[str, float]
    ) -> ChainWeightSubmission:
        calls.append(weights)
        return ChainWeightSubmission(
            success=True,
            uids=(3,),
            weights=(1.0,),
            message="included",
            extrinsic_function="set_weights_extrinsic",
            extrinsic_hash="0xabc",
            block_hash="0xdef",
            block_number=42,
            extrinsic_fee_rao=123,
        )

    result = validate_once(
        _settings(tmp_path).model_copy(update={"enable_set_weights": True}),
        [submission],
        registry=_registry(),
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        no_set_weights=False,
        submit_weights=fake_submit_weights,
    )

    assert result.weights_set is True
    assert result.weight_submission == ChainWeightSubmission(
        success=True,
        uids=(3,),
        weights=(1.0,),
        message="included",
        extrinsic_function="set_weights_extrinsic",
        extrinsic_hash="0xabc",
        block_hash="0xdef",
        block_number=42,
        extrinsic_fee_rao=123,
    )
    assert result.summary.chain_weight_uids == (3,)
    assert result.summary.chain_weight_values == (1.0,)
    assert calls == [{"hk-a": 1.0}]
    receipt_path = tmp_path / "operator" / "weight-submissions.jsonl"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt == {
        "bt_network": "default",
        "block_hash": "0xdef",
        "block_number": 42,
        "extrinsic_fee_rao": 123,
        "extrinsic_function": "set_weights_extrinsic",
        "extrinsic_hash": "0xabc",
        "message": "included",
        "netuid": 0,
        "registry_sha256": "0" * 64,
        "schema_version": 1,
        "submitted_at": receipt["submitted_at"],
        "success": True,
        "uids": [3],
        "weights": [1.0],
    }
    run_summary = ValidatorRunSummary.model_validate_json(
        (tmp_path / "operator" / "validator-runs.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert run_summary.chain_weight_uids == (3,)
    assert run_summary.chain_weight_values == (1.0,)


def test_validator_can_submit_tempo_commitment(tmp_path: Path) -> None:
    submission = build_submission(_task(), solver_hotkey="hk-a", proof_script=_proof())
    calls: list[str] = []

    def fake_submit_commitment(_settings_arg: LemmaSettings, payload: str) -> ChainCommitmentSubmission:
        calls.append(payload)
        return ChainCommitmentSubmission(
            success=True,
            payload=payload,
            hotkey="vhk",
            message="included",
            extrinsic_function="set_commitment",
            extrinsic_hash="0xabc",
            block_hash="0xdef",
            block_number=42,
            extrinsic_fee_rao=123,
        )

    result = validate_once(
        _settings(tmp_path).model_copy(update={"enable_set_commitment": True}),
        [submission],
        registry=_registry(),
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        no_set_weights=True,
        submit_commitment=fake_submit_commitment,
    )

    assert calls == [result.summary.tempo_commitment_payload]
    assert result.summary.chain_commitment_set is True
    assert result.commitment_submission is not None
    receipt = json.loads((tmp_path / "operator" / "commitment-submissions.jsonl").read_text(encoding="utf-8"))
    assert receipt["success"] is True
    assert receipt["payload"] == result.summary.tempo_commitment_payload


def test_production_commitment_requires_cid_publish() -> None:
    from lemma.validator import _require_cid_publish_for_production_commitment

    settings = LemmaSettings(
        _env_file=None,
        protocol_mode="production",
        enable_set_commitment=True,
    )

    with pytest.raises(RuntimeError, match="LEMMA_CANONICAL_PUBLISH_IPFS_API_URL"):
        _require_cid_publish_for_production_commitment(settings)

    _require_cid_publish_for_production_commitment(
        settings.model_copy(update={"canonical_publish_ipfs_api_url": "http://ipfs.local:5001"})
    )


def test_validator_logs_failed_weight_submission_before_raising(tmp_path: Path) -> None:
    submission = build_submission(_task(), solver_hotkey="hk-a", proof_script=_proof())

    def fake_submit_weights(
        _settings_arg: LemmaSettings, _weights: dict[str, float]
    ) -> ChainWeightSubmission:
        return ChainWeightSubmission(success=False, uids=(0,), weights=(1.0,), message="rejected")

    with pytest.raises(RuntimeError, match="set_weights failed: rejected"):
        validate_once(
            _settings(tmp_path).model_copy(update={"enable_set_weights": True}),
            [submission],
            registry=_registry(),
            verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
            no_set_weights=False,
            submit_weights=fake_submit_weights,
        )

    receipt_path = tmp_path / "operator" / "weight-submissions.jsonl"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["success"] is False
    assert receipt["uids"] == [0]
    assert receipt["weights"] == [1.0]
    assert not (tmp_path / "operator" / "validator-runs.jsonl").exists()


def test_validator_uses_deterministic_active_window_not_full_registry(tmp_path: Path) -> None:
    registry = _two_task_registry()
    active, deep = registry.tasks
    submissions = [
        build_submission(active, solver_hotkey="hk-a", proof_script=_proof("  trivial")),
        build_submission(deep, solver_hotkey="hk-b", proof_script=_proof("  exact True.intro")),
    ]

    result = validate_once(
        _settings(tmp_path),
        submissions,
        registry=registry,
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        validator_hotkey="vhk",
        no_set_weights=True,
    )

    assert result.score.credits == {"hk-a": 1}
    assert result.score.score_events[0].active_K == 1
    assert result.corpus_rows[0].active_K == 1
    assert result.corpus_rows[0].queue_depth == 0
    receipts = (tmp_path / "operator" / "verification-records.jsonl").read_text(encoding="utf-8")
    assert "inactive_task" in receipts


def test_mathlib_snapshot_registry_loads_through_validator_path(tmp_path: Path) -> None:
    manifest = tmp_path / "mathlib-snapshot.jsonl"
    rows = [
        {
            "theorem_name": "registry_smoke_active_true",
            "type_expr": "True",
            "mathlib_rev": "abc123",
            "source_path": "Mathlib/Smoke.lean",
            "source_license": "Apache-2.0",
            "source_line": 10,
            "queue_depth": 0,
        },
        {
            "theorem_name": "registry_smoke_deep_true",
            "type_expr": "True",
            "mathlib_rev": "abc123",
            "source_path": "Mathlib/Smoke.lean",
            "source_license": "Apache-2.0",
            "source_line": 20,
            "queue_depth": 2,
        },
    ]
    manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    tasks = registry_tasks_from_candidates(candidates_from_jsonl(manifest), seed="validator-smoke", frontier_depth=0)
    registry_path = tmp_path / "registry.json"
    write_registry(tasks, registry_path)
    registry_sha256 = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    active_task = next(task for task in tasks if task.queue_depth == 0)
    deep_task = next(task for task in tasks if task.queue_depth == 2)
    submissions = [
        build_submission(active_task, solver_hotkey="hk-a", proof_script=_proof_for(active_task.theorem_name)),
        build_submission(deep_task, solver_hotkey="hk-b", proof_script=_proof_for(deep_task.theorem_name)),
    ]
    settings = _settings(tmp_path).model_copy(
        update={
            "task_registry_url": str(registry_path),
            "task_registry_sha256_expected": registry_sha256,
            "active_task_count": 2,
            "frontier_depth": 0,
            "active_queue_seed": "validator-smoke",
        }
    )

    result = validate_once(
        settings,
        submissions,
        verify_submission=lambda task, submission: VerifyResult(passed=True, reason="ok"),
        validator_hotkey="vhk",
        epoch=9,
        tempo=3,
        no_set_weights=True,
    )

    assert result.score.credits == {"hk-a": 1}
    assert result.score.miner_weights == {"hk-a": 1.0}
    assert result.score.unearned_share == 0.0
    assert result.score.score_events[0].active_K == 1
    assert result.corpus_rows[0].task_id == active_task.id
    assert result.corpus_rows[0].source_stream == "mathlib_snapshot"
    assert result.corpus_rows[0].source_ref.path == "Mathlib/Smoke.lean"
    assert result.corpus_rows[0].queue_position == 0
    assert result.corpus_rows[0].frontier_depth == 0
    assert result.corpus_rows[0].active_K == 1
    receipts = (tmp_path / "operator" / "verification-records.jsonl").read_text(encoding="utf-8")
    assert "inactive_task" in receipts
    assert (tmp_path / "corpus" / "epoch-9.jsonl").exists()
    assert (tmp_path / "corpus" / "corpus-index.json").exists()


def test_protocol_signing_payloads_are_stable() -> None:
    task = _task()
    submission = build_submission(task, solver_hotkey="hk-a", proof_script=_proof(), created_at="2026-01-01T00:00:00Z")
    request = TaskRequest(validator_hotkey="vhk", epoch=1, tasks=(task,))
    response = ProofResponse(miner_hotkey="hk-a", submissions=(submission,))

    assert request.signing_payload() == request.model_copy().signing_payload()
    assert response.signing_payload() == response.model_copy().signing_payload()
    assert submission.signature_payload_sha256 in response.signing_payload()
