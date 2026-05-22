"""Procedural Lean task mutation engines."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol

from lemma.common.config import LemmaSettings
from lemma.lean.verify_runner import run_lean_verify
from lemma.problems.base import Problem
from lemma.supply.operator_bundle import SMALL_VALUES_BY_TYPE, TYPE_SUBSTITUTIONS
from lemma.supply.types import TaskCandidate


@dataclass(frozen=True)
class MutationResult:
    type_expr: str
    params: dict[str, object]


class ProceduralMutationEngine(Protocol):
    def apply(
        self,
        source: TaskCandidate,
        type_expr: str,
        operator: str,
        *,
        step: int,
        param_seed: str,
        peer: TaskCandidate,
    ) -> MutationResult:
        """Apply one deterministic procedural mutation step."""


class PreviewMutationEngine:
    """Fast dev-only preview engine used with assumed gates."""

    def apply(
        self,
        source: TaskCandidate,
        type_expr: str,
        operator: str,
        *,
        step: int,
        param_seed: str,
        peer: TaskCandidate,
    ) -> MutationResult:
        _ = source
        expr = type_expr.strip()
        if operator == "generalize":
            binder = f"lemma_p{step}_{param_seed[:6]}"
            return MutationResult(
                f"∀ {binder} : Prop, {binder} → ({expr})",
                {"target": "fresh_prop_hypothesis", "binder": binder, "binder_type": "Prop"},
            )
        if operator == "specialize":
            return _preview_specialize(expr, param_seed=param_seed)
        if operator == "conjoin":
            return MutationResult(
                f"({peer.type_expr.strip()}) → ({expr})",
                _peer_params(
                    "peer_premise",
                    peer,
                    mode_key="mode",
                ),
            )
        if operator == "substitute-type":
            return _preview_substitute_type(expr, param_seed=param_seed)
        if operator == "strengthen":
            return MutationResult(
                f"({expr}) ∧ ({peer.type_expr.strip()})",
                _peer_params(
                    "conjoin_peer_conclusion",
                    peer,
                    mode_key="rule",
                ),
            )
        if operator == "weaken":
            return _preview_weaken(expr)
        raise ValueError(f"unknown procedural operator: {operator}")


class LeanAstMutationEngine:
    """Lean parser/elaborator-backed production mutation engine."""

    _MARKER = "LEMMA_AST_MUTATION "

    def __init__(self, settings: LemmaSettings) -> None:
        self.settings = settings
        self.timeout_s = min(settings.lean_verify_timeout_s, settings.procedural_gate_timeout_s)

    def apply(
        self,
        source: TaskCandidate,
        type_expr: str,
        operator: str,
        *,
        step: int,
        param_seed: str,
        peer: TaskCandidate,
    ) -> MutationResult:
        problem = Problem(
            id=f"{source.id}.mutation.{step}",
            theorem_name="lemma_ast_mutation_dummy",
            type_expr="True",
            split="procedural_mutation",
            lean_toolchain=source.lean_toolchain,
            mathlib_rev=source.mathlib_rev,
            imports=_combined_imports(source.imports, peer.imports),
            extra={
                "challenge_full": _lean_mutator_source(
                    type_expr=type_expr,
                    operator=operator,
                    step=step,
                    param_seed=param_seed,
                    peer=peer,
                ),
                "lean_build_target": "Challenge",
                "lean_eval_commands": ("#eval! LemmaProceduralMutator.emit",),
                "submission_policy": "strict_envelope",
            },
        )
        result = run_lean_verify(
            self.settings,
            verify_timeout_s=self.timeout_s,
            problem=problem,
            proof_script=_dummy_submission(problem.imports),
            submission_policy="strict_envelope",
        )
        if not result.passed:
            detail = result.stderr_tail or result.stdout_tail or result.reason
            raise ValueError(f"Lean AST mutation failed for {source.id}:{step}:{operator}: {detail[:800]}")
        return self._parse_result(result.stdout_tail + "\n" + result.stderr_tail)

    def _parse_result(self, output: str) -> MutationResult:
        for line in output.splitlines():
            if not line.startswith(self._MARKER):
                continue
            payload = json.loads(line.removeprefix(self._MARKER))
            type_expr = payload.get("type_expr")
            params = payload.get("params")
            if not isinstance(type_expr, str) or not type_expr.strip() or not isinstance(params, dict):
                raise ValueError("Lean AST mutation emitted malformed result")
            return MutationResult(type_expr=type_expr.strip(), params={**params, "engine": "lean_ast_elaborator"})
        raise ValueError("Lean AST mutation emitted no result")


_SAFE_IDENT = re.compile(r"[^A-Za-z0-9_]+")
_LEAN_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_']*$")


def _peer_params(value: str, peer: TaskCandidate, *, mode_key: str) -> dict[str, object]:
    return {
        mode_key: value,
        "peer_source_id": peer.id,
        "peer_theorem_name": peer.theorem_name,
        "peer_target_sha256": _hash_text(peer.statement),
    }


def _preview_specialize(expr: str, *, param_seed: str) -> MutationResult:
    binder = _split_forall(expr)
    if binder is None:
        return MutationResult(f"True → ({expr})", {"fallback": "true_premise"})
    name, binder_type, body = binder
    value = _small_value(binder_type, param_seed)
    if value is None:
        return MutationResult(f"True → ({expr})", {"fallback": "unsupported_binder_type", "binder_type": binder_type})
    typed_value = value if binder_type == "Prop" else f"({value} : {binder_type})"
    return MutationResult(
        _replace_ident(body, name, typed_value),
        {"binder": name, "binder_type": binder_type, "value": value},
    )


def _preview_substitute_type(expr: str, *, param_seed: str) -> MutationResult:
    offset = _hash_int(param_seed) % len(TYPE_SUBSTITUTIONS)
    ordered = TYPE_SUBSTITUTIONS[offset:] + TYPE_SUBSTITUTIONS[:offset]
    for source_type, replacement_type in ordered:
        output = _replace_type_name(expr, source_type, replacement_type)
        if output != expr:
            return MutationResult(output, {"from": source_type, "to": replacement_type})
    return MutationResult(f"True → ({expr})", {"fallback": "no_supported_type_occurrence"})


def _preview_weaken(expr: str) -> MutationResult:
    implication = _split_top_level_arrow(expr)
    if implication is not None:
        premise, conclusion = implication
        return MutationResult(
            f"True → ({conclusion})",
            {"rule": "replace_first_premise_with_true", "premise_sha256": _hash_text(premise)},
        )
    return MutationResult(f"({expr}) ∨ False", {"rule": "false_disjunct"})


def _split_forall(expr: str) -> tuple[str, str, str] | None:
    stripped = expr.strip()
    if not stripped.startswith("∀ "):
        return None
    comma = _top_level_index(stripped, ",")
    if comma is None:
        return None
    binder = stripped[2:comma].strip()
    body = stripped[comma + 1 :].strip()
    if ":" not in binder:
        return None
    name, binder_type = (part.strip() for part in binder.split(":", 1))
    if not _LEAN_IDENT.fullmatch(name) or not binder_type:
        return None
    return name, binder_type, body


def _split_top_level_arrow(expr: str) -> tuple[str, str] | None:
    stripped = expr.strip()
    arrow = _top_level_index(stripped, "→")
    if arrow is None:
        arrow = _top_level_index(stripped, "->")
        width = 2
    else:
        width = 1
    if arrow is None:
        return None
    return stripped[:arrow].strip(), stripped[arrow + width :].strip()


def _top_level_index(value: str, marker: str) -> int | None:
    depth = 0
    i = 0
    while i < len(value):
        char = value[i]
        if char in "([{":
            depth += 1
        elif char in ")]}" and depth > 0:
            depth -= 1
        elif depth == 0 and value.startswith(marker, i):
            return i
        i += 1
    return None


def _small_value(binder_type: str, seed: str) -> str | None:
    values = SMALL_VALUES_BY_TYPE.get(binder_type.strip())
    if not values:
        return None
    return values[_hash_int(seed) % len(values)]


def _replace_ident(expr: str, name: str, replacement: str) -> str:
    return re.sub(rf"(?<![A-Za-z0-9_'.]){re.escape(name)}(?![A-Za-z0-9_'.])", replacement, expr)


def _replace_type_name(expr: str, source_type: str, replacement_type: str) -> str:
    return re.sub(rf"(?<![A-Za-z0-9_'.]){re.escape(source_type)}(?![A-Za-z0-9_'.])", replacement_type, expr)


def _lean_mutator_source(
    *,
    type_expr: str,
    operator: str,
    step: int,
    param_seed: str,
    peer: TaskCandidate,
) -> str:
    binder = _safe_binder(step, param_seed)
    return f"""import Lean

open Lean

namespace LemmaProceduralMutator

def inputSource : String := {_lean_string(type_expr)}
def peerSource : String := {_lean_string(peer.type_expr)}
def operatorName : String := {_lean_string(operator)}
def binderName : String := {_lean_string(binder)}
def paramSeed : String := {_lean_string(param_seed)}
def peerSourceId : String := {_lean_string(peer.id)}
def peerTheoremName : String := {_lean_string(peer.theorem_name)}
def peerTargetSha256 : String := {_lean_string(_hash_text(peer.statement))}
def substitutions : List (String × String) := {_lean_pairs(TYPE_SUBSTITUTIONS)}
def smallValues : List (String × List String) := {_lean_string_tuple_list(SMALL_VALUES_BY_TYPE)}

def parseTermOrThrow (source : String) : Elab.Command.CommandElabM (TSyntax `term) := do
  let env ← getEnv
  match Parser.runParserCategory env `term source with
  | Except.ok stx => pure ⟨stx⟩
  | Except.error e => throwError e

def ppTerm (stx : TSyntax `term) : Elab.Command.CommandElabM String := do
  pure ((← Elab.Command.liftCoreM <| PrettyPrinter.ppCategory `term stx.raw).pretty)

def requireProp (stx : TSyntax `term) : Elab.Command.CommandElabM Unit := do
  Elab.Command.runTermElabM fun _ => do
    let expr ← Elab.Term.elabType stx.raw
    unless (← Meta.isProp expr) do
      throwError "mutated statement did not elaborate to Prop"

partial def replaceIdent (target : Name) (replacement : Syntax) (stx : Syntax) : Syntax :=
  match stx with
  | Syntax.ident info raw value preresolved =>
      if value == target then replacement else Syntax.ident info raw value preresolved
  | Syntax.node info kind args => Syntax.node info kind (args.map (replaceIdent target replacement))
  | _ => stx

def smallValueFor (typeText : String) : Option String :=
  (smallValues.lookup typeText).bind fun values =>
    if values.isEmpty then none else some (values.getD (paramSeed.hash.toNat % values.length) "")

def jsonObj (items : List (String × Json)) : Json :=
  Json.mkObj items

def peerParams (key value : String) : Json :=
  jsonObj [
    (key, Json.str value),
    ("peer_source_id", Json.str peerSourceId),
    ("peer_theorem_name", Json.str peerTheoremName),
    ("peer_target_sha256", Json.str peerTargetSha256)
  ]

def typedValueTerm (binderType value : String) : Elab.Command.CommandElabM (TSyntax `term) := do
  parseTermOrThrow <| if binderType == "Prop" then value else "(" ++ value ++ " : " ++ binderType ++ ")"

def trueArrow (expr : TSyntax `term) : Elab.Command.CommandElabM (TSyntax `term) := do
  parseTermOrThrow <| "True → (" ++ (← ppTerm expr) ++ ")"

def falseDisjunct (expr : TSyntax `term) : Elab.Command.CommandElabM (TSyntax `term) := do
  parseTermOrThrow <| "(" ++ (← ppTerm expr) ++ ") ∨ False"

partial def substituteFirstType
    (expr : TSyntax `term)
    (choices : List (String × String))
    : Elab.Command.CommandElabM (Option (TSyntax `term × Json)) := do
  match choices with
  | [] => pure none
  | (fromType, toType) :: rest =>
      let replacement ← parseTermOrThrow toType
      let output : TSyntax `term := ⟨replaceIdent (Name.mkSimple fromType) replacement.raw expr.raw⟩
      if toString output.raw == toString expr.raw then
        substituteFirstType expr rest
      else
        pure <| some (output, jsonObj [("from", Json.str fromType), ("to", Json.str toType)])

def mutate (expr peer : TSyntax `term) : Elab.Command.CommandElabM (TSyntax `term × Json) := do
  if operatorName == "generalize" then
    let binder := mkIdent (Name.mkSimple binderName)
    let output ← `(term| ∀ $binder:ident : Prop, $binder → ($expr))
    pure (output, jsonObj [
      ("target", Json.str "fresh_prop_hypothesis"),
      ("binder", Json.str binderName),
      ("binder_type", Json.str "Prop")
    ])
  else if operatorName == "specialize" then
    match expr with
    | `(term| ∀ $x:ident : $ty, $body) =>
        let typeText ← ppTerm ty
        match smallValueFor typeText with
        | none =>
            let output ← trueArrow expr
            pure (output, jsonObj [
              ("fallback", Json.str "unsupported_binder_type"),
              ("binder_type", Json.str typeText)
            ])
        | some value =>
            let replacement ← typedValueTerm typeText value
            let output : TSyntax `term := ⟨replaceIdent x.getId replacement.raw body.raw⟩
            pure (output, jsonObj [
              ("binder", Json.str x.getId.toString),
              ("binder_type", Json.str typeText),
              ("value", Json.str value)
            ])
    | _ =>
        let output ← trueArrow expr
        pure (output, jsonObj [("fallback", Json.str "true_premise")])
  else if operatorName == "conjoin" then
    let output ← `(term| ($peer) → ($expr))
    pure (output, peerParams "mode" "peer_premise")
  else if operatorName == "substitute-type" then
    match ← substituteFirstType expr substitutions with
    | some result => pure result
    | none =>
        let output ← trueArrow expr
        pure (output, jsonObj [("fallback", Json.str "no_supported_type_occurrence")])
  else if operatorName == "strengthen" then
    let output ← `(term| ($expr) ∧ ($peer))
    pure (output, peerParams "rule" "conjoin_peer_conclusion")
  else if operatorName == "weaken" then
    match expr with
    | `(term| $premise → $conclusion) =>
        let output ← trueArrow conclusion
        let premiseText ← ppTerm premise
        pure (output, jsonObj [
          ("rule", Json.str "replace_first_premise_with_true"),
          ("premise_syntax_hash", Json.str (toString premiseText.hash))
        ])
    | _ =>
        let output ← falseDisjunct expr
        pure (output, jsonObj [("rule", Json.str "false_disjunct")])
  else
    throwError "unknown procedural operator: {{operatorName}}"

def emit : Elab.Command.CommandElabM Unit := do
  let expr ← parseTermOrThrow inputSource
  let peer ← parseTermOrThrow peerSource
  requireProp expr
  requireProp peer
  let (output, params) ← mutate expr peer
  requireProp output
  let rendered ← ppTerm output
  let payload := jsonObj [("type_expr", Json.str rendered), ("params", params)]
  IO.println <| "LEMMA_AST_MUTATION " ++ payload.compress

end LemmaProceduralMutator
"""


def _safe_binder(step: int, seed: str) -> str:
    stem = _SAFE_IDENT.sub("_", seed).strip("_")[:6]
    if not stem or stem[0].isdigit():
        stem = f"p{stem}"
    return f"lemma_p{step}_{stem}"


def _lean_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _lean_pairs(values: tuple[tuple[str, str], ...]) -> str:
    items = ", ".join(f"({_lean_string(left)}, {_lean_string(right)})" for left, right in values)
    return f"[{items}]"


def _lean_string_tuple_list(values: dict[str, tuple[str, ...]]) -> str:
    items = []
    for key, entry in values.items():
        inner = ", ".join(_lean_string(item) for item in entry)
        items.append(f"({_lean_string(key)}, [{inner}])")
    return "[" + ", ".join(items) + "]"


def _combined_imports(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    for item in (*left, *right):
        if item not in out:
            out.append(item)
    return tuple(out)


def _dummy_submission(imports: tuple[str, ...]) -> str:
    return "\n".join(
        [
            *(f"import {module}" for module in imports),
            "",
            "namespace Submission",
            "",
            "theorem lemma_ast_mutation_dummy : True := by",
            "  trivial",
            "",
            "end Submission",
            "",
        ]
    )


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_int(value: str) -> int:
    return int(_hash_text(value), 16)
