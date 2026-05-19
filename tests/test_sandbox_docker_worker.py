"""Docker worker name passed explicitly (LemmaSettings / `.env`)."""

import subprocess
from pathlib import Path

from lemma.lean.sandbox import LeanSandbox, VerifyResult


def test_docker_worker_kwarg_not_os_env() -> None:
    sb = LeanSandbox(use_docker=True, docker_worker="my-worker")
    assert sb.docker_worker == "my-worker"


def test_docker_verify_script_source_is_line_oriented(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("LEMMA_LEAN_VERIFY_FULL_BUILD", raising=False)
    sb = LeanSandbox(use_docker=True, network_mode="none")

    script = sb._docker_verify_script_source(tmp_path)

    assert "lake exe cache get" not in script
    assert "cp -a /opt/lemma-stub/lake-manifest.json ." in script
    assert "\nlake build Submission\n" in script
    assert "\nlake env lean AxiomCheck.lean\n" in script


def test_docker_worker_exec_uses_workdir_argv(tmp_path: Path, monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    def fake_parse(
        self: LeanSandbox,
        text: str,
        exit_status: int,
        elapsed: float,
        work: Path,
        log_tail: int,
    ) -> VerifyResult:
        assert text == "\nok"
        assert exit_status == 0
        assert work == tmp_path
        assert log_tail == 123
        assert elapsed >= 0
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.lean.sandbox.subprocess.run", fake_run)
    monkeypatch.setattr("lemma.lean.sandbox.LeanSandbox._verify_docker_parse_logs", fake_parse)

    sb = LeanSandbox(use_docker=True)
    vr = sb._verify_docker_cli_exec("worker-1", "/lemma-workspace/template", ".lemma_verify.sh", tmp_path, 123)

    assert vr.passed is True
    assert calls == [
        ["docker", "exec", "--workdir", "/lemma-workspace/template", "worker-1", "bash", ".lemma_verify.sh"],
    ]
