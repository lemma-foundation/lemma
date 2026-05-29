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
    assert "[ -e \"$target\" ] || ln -s \"$p\" \"$target\"" in script
    assert "cp -a /opt/lemma-stub/lake-manifest.json ." in script
    assert "\nlake build Submission\n" in script
    assert "\nlake env lean AxiomCheck.lean\n" in script


def test_docker_verify_script_checks_cache_after_stub_hydration(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("LEMMA_LEAN_VERIFY_FULL_BUILD", raising=False)
    sb = LeanSandbox(use_docker=True, network_mode="bridge")

    script = sb._docker_verify_script_source(tmp_path)

    assert script.index("[ -e \"$target\" ] || ln -s \"$p\" \"$target\"") < script.index(
        "if [ ! -d .lake/packages/mathlib ]; then"
    )
    assert "  lake exe cache get\nfi\nlake build Submission" in script


def test_docker_verify_script_uses_workspace_build_target(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("LEMMA_LEAN_VERIFY_FULL_BUILD", raising=False)
    (tmp_path / ".lemma_build_target").write_text("Challenge\n", encoding="utf-8")
    sb = LeanSandbox(use_docker=True, network_mode="none")

    script = sb._docker_verify_script_source(tmp_path)

    assert "\nlake build Challenge Submission\n" in script


def test_docker_verify_script_skips_submission_for_internal_challenge_eval(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("LEMMA_LEAN_VERIFY_FULL_BUILD", raising=False)
    (tmp_path / ".lemma_build_target").write_text("Challenge\n", encoding="utf-8")
    (tmp_path / ".lemma_skip_submission").write_text("1\n", encoding="utf-8")
    sb = LeanSandbox(use_docker=True, network_mode="none")

    script = sb._docker_verify_script_source(tmp_path)

    assert "\nlake build Challenge\n" in script
    assert "\nlake build Challenge Submission\n" not in script


def test_docker_verify_script_skips_axiom_check_for_internal_eval(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("LEMMA_LEAN_VERIFY_FULL_BUILD", raising=False)
    (tmp_path / ".lemma_build_target").write_text("Challenge\n", encoding="utf-8")
    (tmp_path / ".lemma_skip_submission").write_text("1\n", encoding="utf-8")
    (tmp_path / ".lemma_skip_axiom_check").write_text("1\n", encoding="utf-8")
    sb = LeanSandbox(use_docker=True, network_mode="none")

    script = sb._docker_verify_script_source(tmp_path)

    assert "\nlake build Challenge\n" in script
    assert "AxiomCheck.lean" not in script


def test_docker_parse_logs_accepts_internal_eval_without_axiom_scan(tmp_path: Path) -> None:
    (tmp_path / ".lemma_skip_axiom_check").write_text("1\n", encoding="utf-8")
    sb = LeanSandbox(use_docker=True)

    result = sb._verify_docker_parse_logs(
        "LEMMA_AST_MUTATION {\"type_expr\":\"True\",\"params\":{}}\n",
        0,
        1.25,
        tmp_path,
        16_000,
    )

    assert result.passed is True
    assert result.reason == "ok"
    assert "LEMMA_AST_MUTATION" in result.stdout_tail


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


def test_docker_worker_exec_does_not_truncate_before_parse(tmp_path: Path, monkeypatch) -> None:
    early_markers = "depends on axioms: []\nLEMMA_AST_MUTATION {\"ok\":true}\n"
    stdout = early_markers + ("x" * 70_000)

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    def fake_parse(
        self: LeanSandbox,
        text: str,
        exit_status: int,
        elapsed: float,
        work: Path,
        log_tail: int,
    ) -> VerifyResult:
        assert "depends on axioms: []" in text
        assert 'LEMMA_AST_MUTATION {"ok":true}' in text
        assert len(text) > 64_000
        return VerifyResult(passed=True, reason="ok")

    monkeypatch.setattr("lemma.lean.sandbox.subprocess.run", fake_run)
    monkeypatch.setattr("lemma.lean.sandbox.LeanSandbox._verify_docker_parse_logs", fake_parse)

    sb = LeanSandbox(use_docker=True)
    vr = sb._verify_docker_cli_exec("worker-1", "/lemma-workspace/template", ".lemma_verify.sh", tmp_path, 123)

    assert vr.passed is True


def test_docker_worker_exec_reports_cli_connection_failures_as_docker_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:  # noqa: ARG001
        return subprocess.CompletedProcess(
            argv,
            1,
            stdout="",
            stderr="failed to connect to the docker API at unix:///tmp/docker.sock",
        )

    monkeypatch.setattr("lemma.lean.sandbox.subprocess.run", fake_run)

    vr = LeanSandbox(use_docker=True)._verify_docker_cli_exec(
        "worker-1",
        "/lemma-workspace/template",
        ".lemma_verify.sh",
        tmp_path,
        123,
    )

    assert vr.passed is False
    assert vr.reason == "docker_error"
    assert "docker API" in vr.stderr_tail


def test_docker_failure_tail_preserves_lemma_markers(tmp_path: Path) -> None:
    text = 'LEMMA_AST_MUTATION {"ok":true}\n' + ("x" * 70_000) + "\nerror: boom"

    vr = LeanSandbox(use_docker=True)._verify_docker_parse_logs(
        text,
        1,
        0.0,
        tmp_path,
        123,
    )

    assert vr.passed is False
    assert 'LEMMA_AST_MUTATION {"ok":true}' in vr.stderr_tail
