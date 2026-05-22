"""Graph-shaped metadata for replayable corpus rows."""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, Field

from lemma.tasks import LemmaTask, SourceRef


class GraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    kind: str


class RowDependencies(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mathlib_imports: tuple[str, ...] = ()
    mathlib_theorems_used: tuple[str, ...] = ()
    lemma_rows_used: tuple[str, ...] = ()
    direct_dependency_count: int = Field(default=0, ge=0)
    dependency_depth: int = Field(default=0, ge=0)
    transitive_dependency_hash: str = ""


class RowGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_ids: dict[str, str]
    edges: tuple[GraphEdge, ...] = ()


def graph_node_id(kind: str, *parts: str) -> str:
    payload = "\n".join([kind, *(part.strip() for part in parts)])
    return f"{kind}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _source_key(source_ref: SourceRef) -> str:
    return "|".join(
        part
        for part in (
            source_ref.kind,
            source_ref.name,
            source_ref.url or "",
            source_ref.commit or "",
            source_ref.path or "",
        )
        if part
    )


def build_dependencies(task: LemmaTask, *, kernel_dependencies: tuple[str, ...] = ()) -> RowDependencies:
    imports = tuple(task.imports)
    kernel = tuple(dict.fromkeys(str(dep).strip() for dep in kernel_dependencies if str(dep).strip()))
    lemma_rows = tuple(str(row) for row in task.metadata.get("lemma_rows_used", ()) or ())
    if kernel:
        direct_count = len(kernel)
        depth = max((len(name.split(".")) for name in kernel), default=0)
        payload = "\n".join(kernel)
    else:
        direct_count = len(imports)
        depth = int(task.metadata.get("dependency_depth") or task.queue_depth or 0)
        payload = "\n".join([*imports, *lemma_rows])
    return RowDependencies(
        mathlib_imports=imports,
        mathlib_theorems_used=kernel,
        lemma_rows_used=lemma_rows,
        direct_dependency_count=direct_count,
        dependency_depth=depth,
        transitive_dependency_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    )


def build_row_graph(
    *,
    task: LemmaTask,
    proof_identity: str,
    proof_sha256: str,
    solver_hotkey: str,
    validator_hotkey: str,
) -> RowGraph:
    task_node = graph_node_id("task", task.id, str(task.task_version), task.target_sha256)
    proof_node = graph_node_id("proof", task.id, proof_sha256)
    identity_node = graph_node_id("identity", task.id, proof_identity)
    source_node = graph_node_id("source", task.source_stream, _source_key(task.source_ref), task.source_license)
    verifier_node = graph_node_id(
        "verifier",
        task.verifier_id,
        task.verifier_version,
        task.lean_toolchain,
        task.mathlib_rev,
    )
    solver_node = graph_node_id("solver", solver_hotkey)
    validator_node = graph_node_id("validator", validator_hotkey)
    nodes = {
        "task": task_node,
        "proof": proof_node,
        "identity": identity_node,
        "source": source_node,
        "verifier": verifier_node,
        "solver": solver_node,
        "validator": validator_node,
    }
    edges = (
        GraphEdge(source=proof_node, target=task_node, kind="proves"),
        GraphEdge(source=proof_node, target=identity_node, kind="has_identity"),
        GraphEdge(source=task_node, target=source_node, kind="derived_from"),
        GraphEdge(source=proof_node, target=verifier_node, kind="accepted_by"),
        GraphEdge(source=proof_node, target=solver_node, kind="submitted_by"),
        GraphEdge(source=proof_node, target=validator_node, kind="validated_by"),
    )
    return RowGraph(node_ids=nodes, edges=edges)
