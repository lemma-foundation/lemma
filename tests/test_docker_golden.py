"""Docker sandbox golden path (opt-in)."""

import os
import socket

import pytest


def _docker_socket_accessible() -> bool:
    docker_host = os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock")
    socket_path = (
        docker_host[7:] if docker_host.startswith("unix://") else "/var/run/docker.sock"
    )
    if not os.path.exists(socket_path):
        return False
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        try:
            sock.connect(socket_path)
            return True
        except OSError:
            return False


pytestmark = pytest.mark.docker


@pytest.mark.skipif(os.environ.get("RUN_DOCKER_LEAN") != "1", reason="set RUN_DOCKER_LEAN=1")
@pytest.mark.skipif(not _docker_socket_accessible(), reason="docker socket unavailable or inaccessible from Python")
def test_docker_two_plus_two() -> None:
    from lemma.lean.sandbox import LeanSandbox
    from lemma.problems.base import Problem

    p = Problem(
        id="test/local_two_plus_two",
        theorem_name="two_plus_two_eq_four",
        type_expr="(2 : Nat) + 2 = 4",
        split="test",
        lean_toolchain="leanprover/lean4:v4.30.0-rc2",
        mathlib_rev="5450b53e5ddc",
        imports=("Mathlib",),
    )
    submission = """import Mathlib

namespace Submission

theorem two_plus_two_eq_four : (2 : Nat) + 2 = 4 := by rfl

end Submission
"""
    # Fresh workspaces often run ``lake`` against Mathlib over HTTPS; ``network_mode=none``
    # blocks DNS (see docs/production.md — bridge only when bootstrap needs the network).
    sb = LeanSandbox(
        image=os.environ.get("LEAN_SANDBOX_IMAGE", "lemma/lean-sandbox:latest"),
        use_docker=True,
        network_mode=os.environ.get("LEAN_SANDBOX_NETWORK", "bridge"),
        timeout_s=1200,
    )
    vr = sb.verify(p, submission)
    assert vr.passed, vr.stderr_tail
