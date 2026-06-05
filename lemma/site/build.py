"""Render the real-task board and solved-proof explorer as static HTML."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

PRODUCT_STATEMENT = (
    "Miners earn subnet rewards by filling real Lean missing-proof gaps under deterministic public validation."
)

_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f7faf8; --paper: #ffffff; --ink: #1f2622; --muted: #58665f;
  --line: #d7e2dc; --soft: #e8f1ec; --green: #18745f; --blue: #325e9d;
  --gold: #a96727; --code: #171b18; --code-ink: #eef7f1; --radius: 8px; --max: 1120px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #121714; --paper: #19201b; --ink: #eef3ef; --muted: #9fb0a6;
    --line: #2b352e; --soft: #1f2822; --green: #5fd0b0; --blue: #8fb4ee; --gold: #e0a868;
    --code: #0d100e; --code-ink: #eef7f1;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
a { color: var(--blue); }
.wrap { max-width: var(--max); margin: 0 auto; padding: 0 20px; }
.site-header {
  border-bottom: 1px solid var(--line); background: var(--paper);
  position: sticky; top: 0; z-index: 5;
}
.site-header .wrap { display: flex; align-items: center; gap: 18px; height: 60px; }
.brand { font-weight: 700; font-size: 18px; color: var(--ink); text-decoration: none; }
.site-nav { margin-left: auto; display: flex; gap: 16px; }
.site-nav a { text-decoration: none; color: var(--muted); font-weight: 600; }
.site-nav a[aria-current="page"] { color: var(--green); }
h1 { font-size: 28px; margin: 28px 0 6px; }
h2 { font-size: 20px; margin: 28px 0 10px; }
.lede { color: var(--muted); max-width: 70ch; }
.eyebrow {
  text-transform: uppercase; letter-spacing: .08em; font-size: 12px;
  font-weight: 700; color: var(--green); margin: 24px 0 0;
}
.cards { display: grid; gap: 14px; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); margin: 18px 0 40px; }
.card {
  border: 1px solid var(--line); border-radius: var(--radius); background: var(--paper);
  padding: 16px 16px 14px; display: flex; flex-direction: column; gap: 8px;
}
.card h3 { margin: 0; font-size: 16px; }
.tags { display: flex; flex-wrap: wrap; gap: 6px; }
.tag {
  font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 999px;
  background: var(--soft); color: var(--green); border: 1px solid var(--line);
}
.tag.open { color: var(--gold); }
.tag.solved { color: var(--green); }
.meta { font-size: 13px; color: var(--muted); margin: 0; }
.meta b { color: var(--ink); font-weight: 600; }
.links { display: flex; flex-wrap: wrap; gap: 12px; font-size: 13px; font-weight: 600; }
code, pre {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
pre.cmd {
  background: var(--code); color: var(--code-ink); border-radius: 6px;
  padding: 10px 12px; overflow-x: auto; font-size: 13px; margin: 4px 0 0;
}
.apply { background: var(--soft); border-radius: 6px; padding: 10px 12px; font-size: 14px; margin: 0; }
.empty {
  color: var(--muted); border: 1px dashed var(--line);
  border-radius: var(--radius); padding: 24px; text-align: center;
}
.site-footer { border-top: 1px solid var(--line); background: var(--paper); margin-top: 40px; }
.site-footer .wrap { padding: 20px; color: var(--muted); font-size: 14px; }
"""


@dataclass(frozen=True)
class SiteConfig:
    """Public link configuration for the rendered preview."""

    netuid: str = "sn467"
    atlas_base_url: str = "https://github.com/lemma-foundation/lemma-proof-atlas/blob/main"
    docs_url: str = "https://github.com/lemma-foundation/lemma/tree/main/docs"
    github_repo_url: str = "https://github.com/lemma-foundation/lemma"
    hippius_url: str | None = None
    huggingface_url: str | None = None


def _short(value: str | None, length: int = 12) -> str:
    text = (value or "").strip()
    return text[:length] if text else ""


def _source_link(source_ref: Mapping[str, Any]) -> str:
    url = str(source_ref.get("url") or "").strip()
    name = str(source_ref.get("name") or source_ref.get("kind") or "source")
    commit = str(source_ref.get("commit") or "").strip()
    if not url:
        return escape(name)
    href = url
    if commit and url.startswith("https://github.com/"):
        href = f"{url.rstrip('/')}/tree/{commit}"
    return f'<a href="{escape(href, quote=True)}" rel="noopener noreferrer" target="_blank">{escape(name)}</a>'


def _page(title: str, body: str, *, active_nav: str, config: SiteConfig) -> str:
    def nav(label: str, href: str, key: str) -> str:
        current = ' aria-current="page"' if key == active_nav else ""
        return f'<a href="{escape(href, quote=True)}"{current}>{escape(label)}</a>'

    nav_links = "\n        ".join(
        [
            nav("Board", "board.html", "board"),
            nav("Solved", "solved.html", "solved"),
            f'<a href="{escape(config.docs_url, quote=True)}" rel="noopener noreferrer" target="_blank">Docs</a>',
            f'<a href="{escape(config.atlas_base_url, quote=True)}" rel="noopener noreferrer" target="_blank">Data</a>',
        ]
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{escape(PRODUCT_STATEMENT, quote=True)}">
  <title>{escape(title)}</title>
  <style>{_STYLE}</style>
</head>
<body>
  <header class="site-header">
    <div class="wrap">
      <a class="brand" href="index.html">Lemma</a>
      <nav class="site-nav" aria-label="Primary">
        {nav_links}
      </nav>
    </div>
  </header>
  <main class="wrap">
{body}
  </main>
  <footer class="site-footer">
    <div class="wrap">{escape(PRODUCT_STATEMENT)}</div>
  </footer>
</body>
</html>
"""


def render_index(*, bundle_count: int, solved_count: int, config: SiteConfig) -> str:
    open_count = max(bundle_count - solved_count, 0)
    body = f"""    <p class="eyebrow">Open verified proof work</p>
    <h1>Real Lean missing-proof tasks, verified in public.</h1>
    <p class="lede">{escape(PRODUCT_STATEMENT)}</p>
    <div class="cards">
      <div class="card">
        <h3>Task board</h3>
        <p class="meta"><b>{open_count}</b> open · <b>{bundle_count}</b> total real tasks</p>
        <p class="meta">Source project, task class, validation environment, and replay command for each task.</p>
        <p class="links"><a href="board.html">Open the task board</a></p>
      </div>
      <div class="card">
        <h3>Solved proof explorer</h3>
        <p class="meta"><b>{solved_count}</b> accepted proofs</p>
        <p class="meta">Inspect and reuse each verified proof or patch, with apply and replay instructions.</p>
        <p class="links"><a href="solved.html">Open the solved explorer</a></p>
      </div>
    </div>
"""
    return _page("Lemma - real verified proof work", body, active_nav="index", config=config)


def _board_card(bundle: Mapping[str, Any], *, solved: bool, config: SiteConfig) -> str:
    source_ref = bundle.get("source_ref") if isinstance(bundle.get("source_ref"), Mapping) else {}
    title = str(bundle.get("title") or bundle.get("task_id") or "task")
    task_id = str(bundle.get("task_id") or "")
    status_tag = (
        '<span class="tag solved">Solved</span>' if solved else '<span class="tag open">Open</span>'
    )
    tags = [
        status_tag,
        f'<span class="tag">{escape(str(bundle.get("task_format") or "proof"))}</span>',
        f'<span class="tag">{escape(str(bundle.get("task_class") or ""))}</span>',
        f'<span class="tag">{escape(str(bundle.get("source_value") or ""))}</span>',
    ]
    env = _short(str(bundle.get("environment_sha256") or "")) or "unpinned"
    repro = str(bundle.get("reproduction_command") or "")
    bundle_url = f"{config.atlas_base_url.rstrip('/')}/tasks/{config.netuid}/bundles/index.json"
    links = [f'<a href="{escape(bundle_url, quote=True)}" rel="noopener noreferrer" target="_blank">Task bundle</a>']
    if source_ref.get("url"):
        links.append(_source_link(source_ref) + " repo")
    repro_block = f'<pre class="cmd">{escape(repro)}</pre>' if repro else ""
    source_kind = escape(str(source_ref.get("kind") or ""))
    toolchain = escape(str(bundle.get("lean_toolchain") or ""))
    source_meta = f'<p class="meta"><b>Source</b> {_source_link(source_ref)} · {source_kind}</p>'
    env_meta = f'<p class="meta"><b>Environment</b> <code>{escape(env)}</code> · {toolchain}</p>'
    return f"""      <article class="card">
        <div class="tags">{''.join(tags)}</div>
        <h3>{escape(title)}</h3>
        <p class="meta"><b>Task</b> <code>{escape(task_id)}</code></p>
        {source_meta}
        {env_meta}
        <p class="links">{' · '.join(links)}</p>
        {repro_block}
      </article>"""


def render_board(bundles: Sequence[Mapping[str, Any]], solved_ids: set[str], *, config: SiteConfig) -> str:
    if bundles:
        cards = "\n".join(
            _board_card(bundle, solved=str(bundle.get("task_id") or "") in solved_ids, config=config)
            for bundle in bundles
        )
        body = (
            '    <p class="eyebrow">Active real tasks</p>\n'
            "    <h1>Task board</h1>\n"
            f'    <p class="lede">{escape(PRODUCT_STATEMENT)}</p>\n'
            f'    <div class="cards">\n{cards}\n    </div>\n'
        )
    else:
        body = (
            '    <p class="eyebrow">Active real tasks</p>\n'
            "    <h1>Task board</h1>\n"
            '    <p class="empty">No real tasks are published yet.</p>\n'
        )
    return _page("Lemma - task board", body, active_nav="board", config=config)


def _solved_card(entry: Mapping[str, Any], *, config: SiteConfig) -> str:
    source_ref = entry.get("source_ref") if isinstance(entry.get("source_ref"), Mapping) else {}
    task_id = str(entry.get("task_id") or "")
    kind = str(entry.get("artifact_kind") or "proof")
    commit = str(source_ref.get("commit") or "")
    apply_text = str(entry.get("apply_instructions") or "")
    repro = str(entry.get("reproduction_command") or "")
    identity = _short(str(entry.get("proof_identity") or ""), 16)
    mirrors = [
        f'<a href="{escape(config.atlas_base_url, quote=True)}" rel="noopener noreferrer" target="_blank">GitHub</a>'
    ]
    if config.hippius_url:
        mirrors.append(
            f'<a href="{escape(config.hippius_url, quote=True)}" rel="noopener noreferrer" target="_blank">Hippius</a>'
        )
    if config.huggingface_url:
        mirrors.append(
            f'<a href="{escape(config.huggingface_url, quote=True)}" '
            'rel="noopener noreferrer" target="_blank">Hugging Face</a>'
        )
    repro_block = f'<pre class="cmd">{escape(repro)}</pre>' if repro else ""
    commit_block = ""
    if commit:
        commit_block = f'<p class="meta"><b>Source commit</b> <code>{escape(_short(commit, 16))}</code></p>'
    return f"""      <article class="card">
        <div class="tags">
          <span class="tag solved">Accepted</span>
          <span class="tag">{escape(kind)}</span>
          <span class="tag">{escape(str(entry.get('proof_identity_strength') or ''))}</span>
        </div>
        <h3><code>{escape(task_id)}</code></h3>
        <p class="meta"><b>Source</b> {_source_link(source_ref)}</p>
        {commit_block}
        <p class="meta"><b>Proof identity</b> <code>{escape(identity)}</code></p>
        <p class="apply">{escape(apply_text)}</p>
        {repro_block}
        <p class="links"><b>Mirrors:</b> {' · '.join(mirrors)}</p>
      </article>"""


def render_solved(solved: Sequence[Mapping[str, Any]], *, config: SiteConfig) -> str:
    if solved:
        cards = "\n".join(_solved_card(entry, config=config) for entry in solved)
        body = (
            '    <p class="eyebrow">Verified, reusable proofs</p>\n'
            "    <h1>Solved proof explorer</h1>\n"
            '    <p class="lede">Inspect and reuse each verified proof or patch. '
            "Every entry can be replayed from public inputs.</p>\n"
            f'    <div class="cards">\n{cards}\n    </div>\n'
        )
    else:
        body = (
            '    <p class="eyebrow">Verified, reusable proofs</p>\n'
            "    <h1>Solved proof explorer</h1>\n"
            '    <p class="empty">No accepted proofs are published yet.</p>\n'
        )
    return _page("Lemma - solved proof explorer", body, active_nav="solved", config=config)


def build_site(
    out_dir: Path,
    *,
    bundles: Sequence[Mapping[str, Any]],
    solved: Sequence[Mapping[str, Any]],
    config: SiteConfig | None = None,
) -> dict[str, Any]:
    """Render the preview pages into ``out_dir`` and return a small manifest."""
    cfg = config or SiteConfig()
    solved_ids = {str(entry.get("task_id") or "") for entry in solved}
    pages = {
        "index.html": render_index(bundle_count=len(bundles), solved_count=len(solved), config=cfg),
        "board.html": render_board(bundles, solved_ids, config=cfg),
        "solved.html": render_solved(solved, config=cfg),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, html in pages.items():
        (out_dir / name).write_text(html, encoding="utf-8")
    return {
        "netuid": cfg.netuid,
        "bundle_count": len(bundles),
        "solved_count": len(solved),
        "pages": sorted(pages),
        "out_dir": str(out_dir),
    }


def _load_list(path: Path, key: str) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(parsed, Mapping):
        value = parsed.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def build_site_from_atlas(
    atlas_repo: Path,
    out_dir: Path,
    *,
    config: SiteConfig | None = None,
) -> dict[str, Any]:
    """Build the preview from a Proof Atlas checkout's real-task artifacts."""
    cfg = config or SiteConfig()
    bundles = _load_list(atlas_repo / "tasks" / cfg.netuid / "bundles" / "index.json", "bundles")
    solved = _load_list(atlas_repo / "proofs" / cfg.netuid / "solved-ledger.json", "solved")
    return build_site(out_dir, bundles=bundles, solved=solved, config=cfg)
