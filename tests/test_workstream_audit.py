"""Workstream audit command planning tests."""

from __future__ import annotations

from pathlib import Path

from scripts.workstream_audit import build_steps


def _commands(steps):
    return [step.command for step in steps]


def test_quick_audit_includes_targeted_checks_and_privacy_scan(tmp_path: Path) -> None:
    site = tmp_path / "lemmasub.net"
    site.mkdir()

    steps = build_steps("quick", site)
    commands = _commands(steps)

    assert ("uv", "run", "ruff", "check", ".") in commands
    assert ("uv", "run", "mypy", "lemma") in commands
    assert any(command[:4] == ("uv", "run", "python", "scripts/leak_check.py") for command in commands)
    assert any("tests/test_miner_validator.py" in command for command in commands)
    assert any(step.name == "site current-problems json" and step.cwd == site for step in steps)


def test_full_audit_uses_full_pytest_and_security_checks(tmp_path: Path) -> None:
    steps = build_steps("full", tmp_path / "missing-site")
    commands = _commands(steps)

    assert ("uv", "run", "bandit", "-q", "-r", "lemma", "scripts", "-ll") in commands
    assert any(command[:3] == ("uv", "run", "pip-audit") for command in commands)
    assert ("uv", "run", "pytest", "tests", "-q", "--ignore=tests/test_docker_golden.py") in commands
    assert all(not step.name.startswith("site ") for step in steps)


def test_skip_site_omits_site_steps_and_site_leak_scan(tmp_path: Path) -> None:
    site = tmp_path / "lemmasub.net"
    site.mkdir()

    steps = build_steps("quick", site, skip_site=True)

    assert all(step.cwd != site for step in steps)
    leak_command = next(step.command for step in steps if step.name == "privacy leak check")
    assert str(site) not in leak_command
