"""Materialize a per-problem Lake workspace for sandbox verification."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from lemma.lean.submission_policy import submission_axiom_check_names, submission_policy_for_problem
from lemma.problems.base import Problem

_SKIP_AXIOM_CHECK_FLAG = ".lemma_skip_axiom_check"
_SKIP_SUBMISSION_FLAG = ".lemma_skip_submission"


def workspace_template_cache_key(problem: Problem) -> str:
    """Stable id for Challenge/Solution/lakefile template (same epoch ⇒ same key)."""
    h = hashlib.sha256()
    for part in (
        problem.id,
        problem.mathlib_rev,
        problem.lean_toolchain,
        problem.challenge_source(),
        problem.solution_source(),
    ):
        h.update(part.encode("utf-8"))
        h.update(b"\x1e")
    return h.hexdigest()[:48]


def workspace_verify_cache_key(
    problem: Problem,
    submission_src: str,
    *,
    include_submission_fingerprint: bool,
) -> str:
    """Disk slot id for ``LeanSandbox.verify`` — template key, optionally plus proof text.

    Default (fingerprint off): one warm ``.lake`` per theorem template; ``Submission.lean`` is overwritten
    each verify (incremental ``lake build Submission``).

    With fingerprint on: distinct proof bodies use distinct cache subdirs (more isolation, less reuse).
    """
    base = workspace_template_cache_key(problem)
    if not include_submission_fingerprint:
        return base
    fp = hashlib.sha256(submission_src.encode("utf-8")).hexdigest()[:16]
    return f"{base}_{fp}"


def materialize_workspace(
    dest: Path,
    problem: Problem,
    submission_lean: str,
    *,
    preserve_lake: bool = False,
    submission_policy: str | None = None,
) -> None:
    """
    Write Challenge, Solution, Submission, lakefile, toolchain, and axiom check driver.

    ``dest`` must be empty or will be created; caller typically uses a temp directory.
    If ``preserve_lake`` is True and ``dest/.lake`` already exists, source files are
    overwritten in place so Lake can incrementally rebuild ``Submission`` only.
    """
    policy = submission_policy_for_problem(problem, submission_policy)
    build_target = _lean_build_target(problem)
    if preserve_lake and dest.exists() and (dest / ".lake").is_dir():
        (dest / "Challenge.lean").write_text(problem.challenge_source(), encoding="utf-8")
        (dest / "Solution.lean").write_text(problem.solution_source(), encoding="utf-8")
        (dest / "Submission.lean").write_text(submission_lean, encoding="utf-8")
        (dest / "lean-toolchain").write_text(problem.lean_toolchain.strip() + "\n", encoding="utf-8")
        lake = _lakefile_toml(problem)
        (dest / "lakefile.toml").write_text(lake, encoding="utf-8")
        (dest / ".lemma_build_target").write_text(build_target + "\n", encoding="utf-8")
        _write_bool_flag(dest, _SKIP_AXIOM_CHECK_FLAG, _skip_axiom_check(problem))
        _write_skip_submission_flag(dest, problem)
        (dest / "AxiomCheck.lean").write_text(_axiom_check_source(problem, submission_lean, policy), encoding="utf-8")
        return

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    (dest / "Challenge.lean").write_text(problem.challenge_source(), encoding="utf-8")
    (dest / "Solution.lean").write_text(problem.solution_source(), encoding="utf-8")
    (dest / "Submission.lean").write_text(submission_lean, encoding="utf-8")

    (dest / "lean-toolchain").write_text(problem.lean_toolchain.strip() + "\n", encoding="utf-8")

    (dest / "lakefile.toml").write_text(_lakefile_toml(problem), encoding="utf-8")
    (dest / ".lemma_build_target").write_text(build_target + "\n", encoding="utf-8")
    _write_bool_flag(dest, _SKIP_AXIOM_CHECK_FLAG, _skip_axiom_check(problem))
    _write_skip_submission_flag(dest, problem)

    # Check axioms on the submitted theorem in ``Submission`` (not ``Solution``): the
    # Solution module only bridges Challenge ↔ Submission and may not expose names
    # the way ``lake env lean`` expects for every workspace layout.
    (dest / "AxiomCheck.lean").write_text(_axiom_check_source(problem, submission_lean, policy), encoding="utf-8")


def _lean_build_target(problem: Problem) -> str:
    raw = str(problem.extra.get("lean_build_target") or "Submission").strip()
    if raw not in {"Challenge", "Solution", "Submission"}:
        raise ValueError(f"unsupported Lean build target: {raw}")
    return raw


def _skip_submission_axiom_check(problem: Problem) -> bool:
    return bool(problem.extra.get("lean_skip_submission_axiom_check"))


def _skip_axiom_check(problem: Problem) -> bool:
    return bool(problem.extra.get("lean_skip_axiom_check"))


def _write_bool_flag(dest: Path, name: str, enabled: bool) -> None:
    path = dest / name
    if enabled:
        path.write_text("1\n", encoding="utf-8")
    elif path.exists():
        path.unlink()


def _write_skip_submission_flag(dest: Path, problem: Problem) -> None:
    _write_bool_flag(dest, _SKIP_SUBMISSION_FLAG, _skip_submission_axiom_check(problem))


def _lakefile_toml(problem: Problem) -> str:
    # Must match `name` in `lemma/lean/template/lakefile.toml` (Lean sandbox image bakes `/opt/lemma-stub/.lake`
    # under that project name so `cp` in the container can warm this workspace).
    lean_options = ["autoImplicit = false"]
    max_heartbeats = _lean_max_heartbeats(problem)
    if max_heartbeats is not None:
        lean_options.append(f"maxHeartbeats = {max_heartbeats}")
    lean_options_toml = "\n".join(lean_options)
    return f'''name = "lemma_stub"
version = "0.1.0"
defaultTargets = ["Challenge", "Solution", "Submission"]

[leanOptions]
{lean_options_toml}

[[require]]
name = "mathlib"
git = "https://github.com/leanprover-community/mathlib4.git"
rev = "{problem.mathlib_rev}"

[[lean_lib]]
name = "Challenge"

[[lean_lib]]
name = "Solution"

[[lean_lib]]
name = "Submission"
'''


def _lean_max_heartbeats(problem: Problem) -> int | None:
    raw = problem.extra.get("lean_max_heartbeats")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _axiom_check_source(problem: Problem, submission_lean: str, policy: str) -> str:
    fingerprint_names = _extra_fingerprint_names(problem)
    eval_commands = _extra_eval_commands(problem)
    skip_submission = _skip_submission_axiom_check(problem)
    submission_names = [] if skip_submission else submission_axiom_check_names(problem, submission_lean, policy=policy)
    if skip_submission:
        lines = ["import Challenge" if fingerprint_names or eval_commands else "import Init"]
    else:
        lines = ["import Submission"]
    if not skip_submission and (fingerprint_names or eval_commands):
        lines.append("import Challenge")
    lines.append("")
    if submission_names:
        lines.extend(_dependency_audit_source(submission_names))
    lines.extend(eval_commands)
    if skip_submission:
        lines.append("#print axioms True.intro")
    for name in submission_names:
        _append_declaration_fingerprint(lines, f"Submission.{name}", include_axioms=True)
    for name in fingerprint_names:
        _append_declaration_fingerprint(lines, name, include_axioms=False)
    return "\n".join(lines) + "\n"


def _extra_fingerprint_names(problem: Problem) -> tuple[str, ...]:
    raw = problem.extra.get("lean_fingerprint_names") or ()
    if isinstance(raw, str):
        raw = (raw,)
    if not isinstance(raw, (list, tuple)):
        return ()
    out: list[str] = []
    for item in raw:
        name = str(item).strip()
        if name and name not in out:
            out.append(name)
    return tuple(out)


def _extra_eval_commands(problem: Problem) -> list[str]:
    raw = problem.extra.get("lean_eval_commands") or ()
    if isinstance(raw, str):
        raw = (raw,)
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    for item in raw:
        command = str(item).strip()
        if command:
            out.append(command)
    if out:
        out.append("")
    return out


def _dependency_audit_source(names: list[str]) -> list[str]:
    lines = [
        "namespace LemmaDependencyAudit",
        "",
        "open Lean",
        "",
        "def dependencyNamesFor (name : Name) : CoreM (Array Name) := do",
        "  let env ← getEnv",
        "  let info ← match env.find? name with",
        "  | some info => pure info",
        "  | none => throwError s!\"unknown declaration {name}\"",
        "  let typeNames := info.type.getUsedConstants",
        "  let valueNames := match info with",
        "  | ConstantInfo.thmInfo data => data.value.getUsedConstants",
        "  | ConstantInfo.defnInfo data => data.value.getUsedConstants",
        "  | ConstantInfo.opaqueInfo data => data.value.getUsedConstants",
        "  | _ => #[]",
        "  pure <| (typeNames ++ valueNames).qsort (fun left right => left.toString < right.toString)",
        "",
        "def binderInfoKey : BinderInfo → String",
        "  | BinderInfo.default => \"default\"",
        "  | BinderInfo.implicit => \"implicit\"",
        "  | BinderInfo.strictImplicit => \"strictImplicit\"",
        "  | BinderInfo.instImplicit => \"instImplicit\"",
        "",
        "def literalKey : Literal → String",
        "  | Literal.natVal n => \"nat:\" ++ toString n",
        "  | Literal.strVal value => \"str:\" ++ reprStr value",
        "",
        "partial def exprKey : Expr → String",
        "  | Expr.bvar i => \"bvar:\" ++ toString i",
        "  | Expr.fvar id => \"fvar:\" ++ toString id.name",
        "  | Expr.mvar id => \"mvar:\" ++ toString id.name",
        "  | Expr.sort level => \"sort:\" ++ toString level",
        "  | Expr.const name levels => \"const:\" ++ name.toString ++ \":\" ++ toString levels",
        "  | Expr.app fn arg => \"(app \" ++ exprKey fn ++ \" \" ++ exprKey arg ++ \")\"",
        "  | Expr.lam _ domain body info =>",
        "      \"(lam \" ++ binderInfoKey info ++ \" \" ++ exprKey domain ++ \" \" ++ exprKey body ++ \")\"",
        "  | Expr.forallE _ domain body info =>",
        "      \"(forall \" ++ binderInfoKey info ++ \" \" ++ exprKey domain ++ \" \" ++ exprKey body ++ \")\"",
        "  | Expr.letE _ type value body _ =>",
        "      \"(let \" ++ exprKey type ++ \" \" ++ exprKey value ++ \" \" ++ exprKey body ++ \")\"",
        "  | Expr.lit literal => \"lit:\" ++ literalKey literal",
        "  | Expr.mdata _ body => exprKey body",
        "  | Expr.proj structName index body =>",
        "      \"(proj \" ++ structName.toString ++ \" \" ++ toString index ++ \" \" ++ exprKey body ++ \")\"",
        "",
        "def proofExprFor (name : Name) : CoreM Expr := do",
        "  let env ← getEnv",
        "  let info ← match env.find? name with",
        "  | some info => pure info",
        "  | none => throwError s!\"unknown declaration {name}\"",
        "  match info with",
        "  | ConstantInfo.thmInfo data => pure data.value",
        "  | ConstantInfo.defnInfo data => pure data.value",
        "  | ConstantInfo.opaqueInfo data => pure data.value",
        "  | _ => throwError s!\"declaration {name} has no proof term\"",
        "",
        "def emit (name : Name) : CoreM Unit := do",
        "  let deps ← dependencyNamesFor name",
        "  let payload := Json.arr (deps.map fun dep => Json.str dep.toString)",
        "  IO.println <| \"LEMMA_KERNEL_DEPENDENCIES \" ++ name.toString ++ \" \" ++ payload.compress",
        "  let proof ← proofExprFor name",
        "  IO.println <| \"LEMMA_PROOF_TERM \" ++ name.toString ++ \" \" ++ exprKey proof",
        "",
        "end LemmaDependencyAudit",
        "",
    ]
    lines.extend(f"#eval! LemmaDependencyAudit.emit `Submission.{name}" for name in names)
    lines.append("")
    return lines


def _append_declaration_fingerprint(lines: list[str], name: str, *, include_axioms: bool) -> None:
    if include_axioms:
        lines.append(f"#print axioms {name}")
    lines.append(f'#eval IO.println "LEMMA_DECL_FINGERPRINT_START {name}"')
    lines.append(f"#print {name}")
    lines.append(f'#eval IO.println "LEMMA_DECL_FINGERPRINT_END {name}"')
