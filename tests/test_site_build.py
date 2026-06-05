"""Static real-task board and solved-proof explorer rendering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lemma.site import (
    SiteConfig,
    build_site,
    build_site_from_atlas,
    render_board,
    render_solved,
)

PRIVATE_PATH = "/" + "Users/example/secret"


def _bundle(task_id: str, *, task_format: str = "patch") -> dict[str, Any]:
    return {
        "task_id": task_id,
        "title": f"Task {task_id}",
        "task_format": task_format,
        "task_class": "source_sorry",
        "source_value": "medium",
        "source_stream": "sorrydb",
        "source_ref": {
            "kind": "sorrydb",
            "name": "ExampleRepo",
            "url": "https://github.com/example/example-repo",
            "commit": "0123456789abcdef",
            "path": "Example/Gap.lean",
        },
        "source_license": "Apache-2.0",
        "imports": ["Mathlib"],
        "allowed_files": ["Example/Gap.lean"],
        "allowed_imports": [],
        "theorem_name": "gap_lemma",
        "type_expr": "True",
        "statement": "theorem gap_lemma : True := by sorry",
        "target_sha256": "d" * 64,
        "target_type_sha256": "e" * 64,
        "environment_sha256": "a" * 64,
        "lean_toolchain": "leanprover/lean4:v4.30.0-rc2",
        "mathlib_rev": "5450b53e5ddc",
        "reproduction_command": "lake build Example",
        "activation_status": "paid",
        "difficulty_band": "easy",
    }


def _solved(task_id: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "task_format": "patch",
        "source_ref": {
            "kind": "sorrydb",
            "name": "ExampleRepo",
            "url": "https://github.com/example/example-repo",
            "commit": "0123456789abcdef",
            "path": "Example/Gap.lean",
        },
        "source_license": "Apache-2.0",
        "target_sha256": "d" * 64,
        "target_type_sha256": "e" * 64,
        "environment_sha256": "a" * 64,
        "reproduction_command": "lake build Example",
        "artifact_kind": "patch",
        "artifact_sha256": "c" * 64,
        "proof_identity": "pi-" + task_id,
        "proof_identity_strength": "strong",
        "miner_hotkey": "miner-hk",
        "validator_hotkey": "val-hk",
        "block": 42,
        "tempo": 7,
        "rewarded": True,
        "apply_instructions": "Check out the repo and apply the accepted patch, then reproduce the validator check.",
    }


def test_board_shows_source_environment_and_status() -> None:
    bundles = [_bundle("lemma.a"), _bundle("lemma.b")]
    html = render_board(bundles, solved_ids={"lemma.a"}, config=SiteConfig())
    assert "Task board" in html
    assert "lemma.a" in html and "lemma.b" in html
    assert ">Solved<" in html
    assert ">Open<" in html
    assert "github.com/example/example-repo/tree/0123456789abcdef" in html
    assert "lake build Example" in html
    assert "aaaaaaaaaaaa" in html  # short environment hash


def test_solved_explorer_shows_apply_and_replay() -> None:
    html = render_solved([_solved("lemma.a")], config=SiteConfig())
    assert "Solved proof explorer" in html
    assert "apply the accepted patch" in html
    assert "lake build Example" in html
    assert ">Accepted<" in html
    assert "GitHub" in html


def test_solved_explorer_renders_optional_mirrors() -> None:
    config = SiteConfig(hippius_url="https://s3.example/atlas", huggingface_url="https://hf.example/atlas")
    html = render_solved([_solved("lemma.a")], config=config)
    assert "Hippius" in html
    assert "Hugging Face" in html


def test_build_site_writes_three_pages(tmp_path: Path) -> None:
    manifest = build_site(
        tmp_path,
        bundles=[_bundle("lemma.a")],
        solved=[_solved("lemma.a")],
        config=SiteConfig(),
    )
    assert manifest["bundle_count"] == 1
    assert manifest["solved_count"] == 1
    for page in ("index.html", "board.html", "solved.html"):
        assert (tmp_path / page).is_file()
    index_html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "missing-proof gaps" in index_html
    assert "0 open" in index_html or "<b>0</b> open" in index_html


def test_build_site_escapes_untrusted_content_and_avoids_leaks(tmp_path: Path) -> None:
    nasty = _bundle("lemma.x")
    nasty["title"] = "<script>alert('xss')</script>"
    nasty["source_ref"]["path"] = PRIVATE_PATH
    build_site(tmp_path, bundles=[nasty], solved=[], config=SiteConfig())
    board = (tmp_path / "board.html").read_text(encoding="utf-8")
    assert "<script>alert" not in board
    assert "&lt;script&gt;" in board
    # path is not rendered on the board, so no private path should appear
    assert PRIVATE_PATH not in board


def test_build_site_empty_states(tmp_path: Path) -> None:
    build_site(tmp_path, bundles=[], solved=[], config=SiteConfig())
    board = (tmp_path / "board.html").read_text(encoding="utf-8")
    solved = (tmp_path / "solved.html").read_text(encoding="utf-8")
    assert "No real tasks are published yet." in board
    assert "No accepted proofs are published yet." in solved


def test_build_site_from_atlas_reads_artifacts(tmp_path: Path) -> None:
    atlas = tmp_path / "atlas"
    bundles_dir = atlas / "tasks" / "sn467" / "bundles"
    bundles_dir.mkdir(parents=True)
    (bundles_dir / "index.json").write_text(
        json.dumps({"schema_version": 1, "netuid": "sn467", "bundles": [_bundle("lemma.a")]}),
        encoding="utf-8",
    )
    proofs_dir = atlas / "proofs" / "sn467"
    proofs_dir.mkdir(parents=True)
    (proofs_dir / "solved-ledger.json").write_text(
        json.dumps({"schema_version": 1, "netuid": "sn467", "solved": [_solved("lemma.a")]}),
        encoding="utf-8",
    )
    out = tmp_path / "site"
    manifest = build_site_from_atlas(atlas, out, config=SiteConfig())
    assert manifest["bundle_count"] == 1
    assert manifest["solved_count"] == 1
    assert (out / "board.html").is_file()


def test_pages_share_theme_toggle_and_clean_nav(tmp_path: Path) -> None:
    build_site(tmp_path, bundles=[_bundle("lemma.a")], solved=[_solved("lemma.a")], config=SiteConfig())
    for page in ("index.html", "board.html", "solved.html"):
        html = (tmp_path / page).read_text(encoding="utf-8")
        # Shared site assets and theme boot, matching the homepage.
        assert 'href="assets/styles.css?v=20260605c"' in html
        assert 'src="assets/site.js?v=20260605c"' in html
        assert "data-theme-toggle" in html
        assert 'localStorage.getItem("lemma-theme")' in html
        # Nav targets are uniform: Data -> bare atlas repo, Docs -> docs folder.
        assert ">Data<" in html and ">Docs<" in html
        assert 'href="https://github.com/lemma-foundation/lemma-proof-atlas"' in html
        assert 'href="https://github.com/lemma-foundation/lemma/tree/main/docs"' in html
        assert "lemma-proof-atlas/blob/main" not in html
        assert "lemma-proof-atlas/tree/main" not in html
        # Same brand link on every page so the header is identical.
        assert '<a class="brand" href="index.html" aria-label="Lemma home">' in html
        assert "<span>Lemma</span>" in html


def test_nav_data_and_docs_targets_are_uniform() -> None:
    html = render_board([_bundle("lemma.a")], solved_ids=set(), config=SiteConfig())
    assert 'href="https://github.com/lemma-foundation/lemma-proof-atlas"' in html
    assert 'href="https://github.com/lemma-foundation/lemma/tree/main/docs"' in html


def test_build_site_from_atlas_handles_missing_artifacts(tmp_path: Path) -> None:
    manifest = build_site_from_atlas(tmp_path / "empty-atlas", tmp_path / "site", config=SiteConfig())
    assert manifest["bundle_count"] == 0
    assert manifest["solved_count"] == 0
    assert (tmp_path / "site" / "index.html").is_file()
