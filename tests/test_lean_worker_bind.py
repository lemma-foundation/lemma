from lemma.common.config import LemmaSettings
from lemma.lean.worker_http import lean_worker_bind_error


def test_lean_worker_allows_loopback_without_bearer() -> None:
    settings = LemmaSettings(_env_file=None)

    assert lean_worker_bind_error("localhost", settings) is None
    assert lean_worker_bind_error("::1", settings) is None


def test_lean_worker_rejects_non_loopback_without_bearer() -> None:
    settings = LemmaSettings(_env_file=None)

    err = lean_worker_bind_error("example.com", settings)

    assert err is not None
    assert "refuses unauthenticated non-loopback binds" in err
    assert "lemma worker --serve" in err


def test_lean_worker_allows_non_loopback_with_bearer() -> None:
    settings = LemmaSettings(_env_file=None, lean_verify_remote_bearer="secret")

    assert lean_worker_bind_error("example.com", settings) is None


def test_lean_worker_allows_explicit_dev_non_loopback_override() -> None:
    settings = LemmaSettings(
        _env_file=None,
        lean_worker_allow_unauthenticated_non_loopback=True,
    )

    assert lean_worker_bind_error("example.com", settings) is None
