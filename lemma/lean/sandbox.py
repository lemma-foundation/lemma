"""Run Lean verification inside Docker (or optionally on host)."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, Field

from lemma.lean.cheats import (
    axiom_scan_ok,
    declaration_fingerprints_from_lean_output,
    kernel_dependencies_from_lean_output,
    lake_build_environment_failed,
    lean_driver_failed,
    proof_term_hash_from_lean_output,
    structural_fingerprint_from_lean_output,
)
from lemma.lean.submission_policy import (
    scan_submission_policy,
    submission_policy_for_problem,
    submission_policy_stderr_tail,
)
from lemma.lean.workspace import materialize_workspace, workspace_verify_cache_key
from lemma.problems.base import Problem

_DOCKER_VERIFY_SCRIPT = ".lemma_verify.sh"
_LEMMA_OUTPUT_PREFIX = "LEMMA_"


@lru_cache(maxsize=512)
def _template_slot_lock(cache_key: str) -> threading.RLock:
    """Serialize in-place verify + cache publish for one theorem template."""
    return threading.RLock()


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _lean_num_threads_value() -> str:
    """Threads for Lean's runtime pool ([docs](https://lean-lang.org/doc/reference/latest/IO/Tasks-and-Threads/)).

    Override with ``LEMMA_LEAN_NUM_THREADS``. Default: logical cores (cap 64).
    """
    raw = os.environ.get("LEMMA_LEAN_NUM_THREADS", "").strip()
    if raw:
        return raw
    return str(min(64, max(1, os.cpu_count() or 8)))


def _merge_lean_process_env(base: dict[str, str]) -> dict[str, str]:
    """Ensure subprocess lake/lean runs with an explicit ``LEAN_NUM_THREADS`` when unset."""
    out = dict(base)
    if "LEAN_NUM_THREADS" not in out:
        out["LEAN_NUM_THREADS"] = _lean_num_threads_value()
    return out


def _lake_build_argv(work: Path) -> list[str]:
    """Build the workspace target needed for this verification pass."""
    if _env_truthy("LEMMA_LEAN_VERIFY_FULL_BUILD"):
        return ["lake", "build"]
    target_path = work / ".lemma_build_target"
    target = target_path.read_text(encoding="utf-8").strip() if target_path.exists() else "Submission"
    if target not in {"Challenge", "Solution", "Submission"}:
        return ["lake", "build", "Submission"]
    if target == "Challenge":
        if (work / ".lemma_skip_submission").exists():
            return ["lake", "build", "Challenge"]
        return ["lake", "build", "Challenge", "Submission"]
    return ["lake", "build", target]


def _skip_axiom_check(work: Path) -> bool:
    return (work / ".lemma_skip_axiom_check").exists()


def _docker_network_allows_remote_cache(network_mode: str) -> bool:
    nm = (network_mode or "none").strip().lower()
    return nm not in ("none", "no", "")


def lake_exe_cache_get_needed(work: Path) -> bool:
    """Whether to run ``lake exe cache get`` — skip when Mathlib checkout already exists (warm workspace).

    Override with ``LEMMA_LEAN_ALWAYS_CACHE_GET=1`` to fetch even when the workspace is warm.
    """
    if _env_truthy("LEMMA_LEAN_ALWAYS_CACHE_GET"):
        return True
    return not (work / ".lake" / "packages" / "mathlib").is_dir()


def docker_worker_container_path(work: Path, host_root: Path, mount_point: Path) -> str:
    """Map a host workspace path to the path inside a worker container (same bind-mount root)."""
    rel = work.resolve().relative_to(Path(host_root).resolve())
    return str((mount_point / rel).as_posix())


def _docker_container_logs_text(container: Any) -> str:
    """Merge stdout+stderr from ``container.logs()`` (Lake often prints failures to stdout)."""
    raw = container.logs(stdout=True, stderr=True, timestamps=False)
    if isinstance(raw, tuple):
        out_b, err_b = raw
        return (
            (out_b or b"").decode("utf-8", errors="replace") + "\n" + (err_b or b"").decode("utf-8", errors="replace")
        )
    return str(raw.decode("utf-8", errors="replace"))


def _verification_stdout_tail(text: str, *, limit: int = 2000) -> str:
    tail = text[-limit:]
    marker_lines = [line for line in text.splitlines() if line.startswith(_LEMMA_OUTPUT_PREFIX)]
    if not marker_lines:
        return tail
    marker_text = "\n".join(marker_lines)
    if marker_text in tail:
        return tail
    return f"{marker_text}\n{tail}"


def _dir_size_bytes(root: Path) -> int:
    total = 0
    for p in root.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


VerifyReason = Literal[
    "ok",
    "compile_error",
    "axiom_violation",
    "cheat_token",
    "policy_violation",
    "timeout",
    "oom",
    "docker_error",
    "remote_error",
]


class VerifyResult(BaseModel):
    passed: bool
    reason: VerifyReason
    stderr_tail: str = ""
    stdout_tail: str = ""
    build_seconds: float = 0.0
    proof_term_hash: str | None = None
    structural_fingerprint: str | None = None
    declaration_fingerprints: dict[str, str] = Field(default_factory=dict)
    kernel_dependencies: tuple[str, ...] = ()


class LeanSandbox:
    """Verify ``Submission.lean`` against a ``Problem`` in an isolated environment."""

    def __init__(
        self,
        image: str = "lemma/lean-sandbox:latest",
        cpu: float = 2.0,
        mem_mb: int = 8192,
        timeout_s: int = 600,
        network_mode: str = "none",
        use_docker: bool = True,
        workspace_cache_dir: Path | None = None,
        docker_worker: str | None = None,
        workspace_cache_include_submission_hash: bool = False,
        workspace_cache_max_dirs: int = 8,
        workspace_cache_max_bytes: int = 16 * 1024 * 1024 * 1024,
    ) -> None:
        self.image = image
        self.cpu = cpu
        self.mem_mb = mem_mb
        self.timeout_s = timeout_s
        self.network_mode = network_mode
        # Caller passes resolved preference (typically ``LemmaSettings.lean_use_docker``).
        self.use_docker = bool(use_docker)
        self.workspace_cache_dir = workspace_cache_dir
        self.workspace_cache_include_submission_hash = bool(workspace_cache_include_submission_hash)
        self.workspace_cache_max_dirs = max(0, int(workspace_cache_max_dirs))
        self.workspace_cache_max_bytes = max(0, int(workspace_cache_max_bytes))
        # Prefer explicit constructor / LemmaSettings (`.env`); ``os.environ`` alone is not populated from
        # pydantic's dotenv load unless the process exported the variable.
        _dw = docker_worker if docker_worker is not None else os.environ.get("LEMMA_LEAN_DOCKER_WORKER")
        self.docker_worker = (_dw or "").strip()

    def verify(
        self,
        problem: Problem,
        submission_src: str,
        *,
        submission_policy: str | None = None,
    ) -> VerifyResult:
        policy = submission_policy_for_problem(problem, submission_policy)
        scan = scan_submission_policy(problem, submission_src, policy=policy)
        if not scan.ok:
            return VerifyResult(
                passed=False,
                reason="policy_violation",
                stderr_tail=submission_policy_stderr_tail(scan),
            )

        if self.workspace_cache_dir is None:
            work = Path(tempfile.mkdtemp(prefix="lemma-lean-"))
            try:
                materialize_workspace(
                    work,
                    problem,
                    submission_src,
                    preserve_lake=False,
                    submission_policy=policy,
                )
                return self._verify_docker(work) if self.use_docker else self._verify_host(work)
            finally:
                shutil.rmtree(work, ignore_errors=True)

        # One template slot owns warmup and in-place reuse. Cold callers serialize
        # until a passing proof publishes the warm `.lake`, then later proofs reuse it.
        self.workspace_cache_dir.mkdir(parents=True, exist_ok=True)
        cache_key = workspace_verify_cache_key(
            problem,
            submission_src,
            include_submission_fingerprint=self.workspace_cache_include_submission_hash,
        )
        slot = self.workspace_cache_dir / cache_key
        with _template_slot_lock(cache_key):
            self._prune_workspace_cache(protect_name=cache_key)
            if slot.is_dir() and (slot / ".lake").is_dir():
                logger.debug("lean workspace cache hit template={} mode=in_place", cache_key)
                materialize_workspace(
                    slot,
                    problem,
                    submission_src,
                    preserve_lake=True,
                    submission_policy=policy,
                )
                return self._verify_docker(slot) if self.use_docker else self._verify_host(slot)

            work = Path(tempfile.mkdtemp(prefix="lemma-lean-", dir=str(self.workspace_cache_dir)))
            try:
                materialize_workspace(
                    work,
                    problem,
                    submission_src,
                    preserve_lake=False,
                    submission_policy=policy,
                )
                vr = self._verify_docker(work) if self.use_docker else self._verify_host(work)
                if self._workspace_cache_publishable(work, vr):
                    self._publish_workspace_cache(slot, work, cache_key)
                    self._prune_workspace_cache(protect_name=cache_key)
                return vr
            finally:
                shutil.rmtree(work, ignore_errors=True)

    def _workspace_cache_publishable(self, work: Path, result: VerifyResult) -> bool:
        """Keep dependency warmup even when the submitted proof itself failed."""
        if self.workspace_cache_dir is None or not (work / ".lake" / "packages" / "mathlib").is_dir():
            return False
        return result.reason not in {"timeout", "oom", "docker_error", "remote_error"}

    def _publish_workspace_cache(self, slot: Path, work: Path, key: str) -> None:
        """First passing verify for this template — keep the verified workspace as the warm slot."""
        if self.workspace_cache_dir is None or not (work / ".lake").is_dir():
            return
        lock = _template_slot_lock(key)
        with lock:
            if (slot / ".lake").is_dir():
                return
            if slot.exists():
                logger.warning("lean workspace cache publish skipped; slot exists without .lake: {}", slot)
                return
            try:
                work.rename(slot)
                logger.debug("lean workspace cache primed template={}", key)
            except OSError as e:
                logger.warning("lean workspace cache publish (rename) failed: {}", e)

    def _prune_workspace_cache(self, *, protect_name: str) -> None:
        """Keep warm workspace slots bounded; stale temp dirs are safe to drop after a day."""
        if self.workspace_cache_dir is None or (
            self.workspace_cache_max_dirs <= 0 and self.workspace_cache_max_bytes <= 0
        ):
            return
        root = self.workspace_cache_dir
        now = time.time()
        warm_slots: list[tuple[float, str, Path, int]] = []
        stale_temps: list[Path] = []
        try:
            entries = [p for p in root.iterdir() if p.is_dir()]
        except OSError as e:
            logger.debug("lean workspace cache prune skipped: {}", e)
            return

        track_bytes = self.workspace_cache_max_bytes > 0
        for p in entries:
            try:
                st = p.stat()
            except OSError:
                continue
            if p.name.startswith("lemma-lean-"):
                if now - st.st_mtime > 86_400:
                    stale_temps.append(p)
                continue
            warm_slots.append((st.st_mtime, p.name, p, _dir_size_bytes(p) if track_bytes else 0))

        to_delete: list[Path] = []
        warm_sorted = sorted(warm_slots)
        if self.workspace_cache_max_dirs > 0:
            delete_count = max(0, len(warm_slots) - self.workspace_cache_max_dirs)
            to_delete.extend([p for _, name, p, _size in warm_sorted if name != protect_name][:delete_count])

        if self.workspace_cache_max_bytes > 0:
            delete_names = {p.name for p in to_delete}
            total = sum(size for _mtime, _name, _path, size in warm_slots)
            for _mtime, name, p, size in warm_sorted:
                if total <= self.workspace_cache_max_bytes:
                    break
                if name == protect_name or name in delete_names:
                    continue
                to_delete.append(p)
                delete_names.add(name)
                total -= size
            if total > self.workspace_cache_max_bytes:
                logger.warning(
                    "lean workspace cache still above byte cap after pruning; protected slot={} total_bytes={} cap={}",
                    protect_name,
                    total,
                    self.workspace_cache_max_bytes,
                )
        for p in [*to_delete, *stale_temps]:
            try:
                shutil.rmtree(p)
                logger.info("lean workspace cache pruned {}", p)
            except OSError as e:
                logger.warning("lean workspace cache prune failed for {}: {}", p, e)

    def _maybe_lake_cache_get(self, work: Path, env: dict[str, str]) -> None:
        """Warm mathlib cache when possible (matches Docker stub workflow); ignore failures."""
        if os.environ.get("LEMMA_SKIP_LAKE_CACHE", "").strip().lower() in ("1", "true", "yes"):
            return
        if not lake_exe_cache_get_needed(work):
            logger.debug("skipping lake exe cache get — warm workspace at {}", work)
            return
        try:
            subprocess.run(
                ["lake", "exe", "cache", "get"],
                cwd=work,
                capture_output=True,
                text=True,
                timeout=min(float(self.timeout_s), 900.0),
                env=_merge_lean_process_env(env),
            )
        except (subprocess.TimeoutExpired, OSError):
            pass

    def _verify_host(self, work: Path) -> VerifyResult:
        t0 = time.monotonic()
        env = _merge_lean_process_env(os.environ.copy())
        self._maybe_lake_cache_get(work, env)
        try:
            r = subprocess.run(
                _lake_build_argv(work),
                cwd=work,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return VerifyResult(passed=False, reason="timeout", stderr_tail="lake build timeout")
        except OSError as e:
            return VerifyResult(passed=False, reason="docker_error", stderr_tail=str(e))

        elapsed = time.monotonic() - t0
        if r.returncode != 0:
            # Lake often prints errors on stdout; always merge both (same as Docker log merge).
            combined = ((r.stderr or "") + "\n" + (r.stdout or ""))[-16_000:]
            return VerifyResult(
                passed=False,
                reason="compile_error",
                stderr_tail=combined,
                build_seconds=elapsed,
            )

        if _skip_axiom_check(work):
            out = (r.stdout or "") + "\n" + (r.stderr or "")
            return VerifyResult(
                passed=True,
                reason="ok",
                stdout_tail=_verification_stdout_tail(out),
                build_seconds=elapsed,
            )

        try:
            r2 = subprocess.run(
                ["lake", "env", "lean", str(work / "AxiomCheck.lean")],
                cwd=work,
                capture_output=True,
                text=True,
                timeout=min(self.timeout_s, 120),
                env=env,
            )
        except subprocess.TimeoutExpired:
            return VerifyResult(passed=False, reason="timeout", stderr_tail="axiom check timeout")
        out = (r2.stdout or "") + "\n" + (r2.stderr or "")
        if lake_build_environment_failed(out):
            return VerifyResult(
                passed=False,
                reason="compile_error",
                stderr_tail=out[-4000:],
                stdout_tail=out[-4000:],
                build_seconds=elapsed,
            )
        ok, found = axiom_scan_ok(out)
        if not ok:
            extra = f" axioms={found}" if found else ""
            # Parse failure (found is None) means no usable #print axioms line — usually lake/Lean failed.
            if found is None or lean_driver_failed(out):
                return VerifyResult(
                    passed=False,
                    reason="compile_error",
                    stderr_tail=(out[-4000:] + extra),
                    stdout_tail=out[-4000:],
                    build_seconds=elapsed,
                )
            return VerifyResult(
                passed=False,
                reason="axiom_violation",
                stderr_tail=(out[-4000:] + extra),
                stdout_tail=out[-4000:],
                build_seconds=elapsed,
            )
        return VerifyResult(
            passed=True,
            reason="ok",
            stdout_tail=_verification_stdout_tail(out),
            build_seconds=elapsed,
            proof_term_hash=proof_term_hash_from_lean_output(out),
            structural_fingerprint=structural_fingerprint_from_lean_output(out),
            declaration_fingerprints=declaration_fingerprints_from_lean_output(out),
            kernel_dependencies=kernel_dependencies_from_lean_output(out),
        )

    def _docker_worker_host_root(self) -> Path | None:
        """Host directory that is bind-mounted at ``LEMMA_LEAN_DOCKER_WORKER_MOUNT`` in the worker container."""
        raw = os.environ.get("LEMMA_LEAN_DOCKER_WORKER_HOST_ROOT", "").strip()
        if raw:
            return Path(raw).expanduser().resolve()
        if self.workspace_cache_dir is not None:
            return self.workspace_cache_dir.resolve()
        return None

    def _docker_worker_mount_point(self) -> Path:
        mp = os.environ.get("LEMMA_LEAN_DOCKER_WORKER_MOUNT", "/lemma-workspace").strip()
        return Path(mp if mp else "/lemma-workspace")

    def _docker_verify_script_source(self, work: Path) -> str:
        """Script run inside the sandbox (cwd = workspace): stub ``.lake``, build, axiom check."""
        lines = [
            f"export LEAN_NUM_THREADS={shlex.quote(_lean_num_threads_value())}",
            "set -euo pipefail",
            "if [ -d /opt/lemma-stub/.lake ]; then",
            "  mkdir -p .lake/packages",
            "  if [ -d /opt/lemma-stub/.lake/build ] && [ ! -d .lake/build ]; then",
            "    cp -a /opt/lemma-stub/.lake/build .lake/ 2>/dev/null || true",
            "  fi",
            "  for p in /opt/lemma-stub/.lake/packages/*; do",
            "    [ -e \"$p\" ] || continue",
            "    target=\".lake/packages/$(basename \"$p\")\"",
            "    [ -e \"$target\" ] || ln -s \"$p\" \"$target\"",
            "  done",
            "fi",
            "if [ -d /opt/lemma-stub ] && [ ! -f lake-manifest.json ]; then",
            "  cp -a /opt/lemma-stub/lake-manifest.json . 2>/dev/null || true",
            "fi",
        ]
        if _docker_network_allows_remote_cache(self.network_mode):
            lines.extend(
                [
                    "if [ ! -d .lake/packages/mathlib ]; then",
                    "  lake exe cache get",
                    "fi",
                ]
            )
        lines.append(shlex.join(_lake_build_argv(work)))
        if not _skip_axiom_check(work):
            lines.append("lake env lean AxiomCheck.lean")
        return "\n".join(lines) + "\n"

    def _write_docker_verify_script(self, work: Path) -> str:
        (work / _DOCKER_VERIFY_SCRIPT).write_text(self._docker_verify_script_source(work), encoding="utf-8")
        return _DOCKER_VERIFY_SCRIPT

    def _verify_docker_parse_logs(
        self,
        text: str,
        exit_status: int,
        elapsed: float,
        work: Path,
        log_tail: int,
    ) -> VerifyResult:
        """Shared exit-code / log interpretation for one-shot containers and ``docker exec`` workers."""
        diagnostic_tail = _verification_stdout_tail(text, limit=log_tail)
        if exit_status != 0:
            if exit_status == 137:
                return VerifyResult(
                    passed=False,
                    reason="oom",
                    stderr_tail=diagnostic_tail,
                    build_seconds=elapsed,
                )
            if lake_build_environment_failed(text):
                return VerifyResult(
                    passed=False,
                    reason="compile_error",
                    stderr_tail=diagnostic_tail,
                    build_seconds=elapsed,
                )
            ok_ax, found_ax = axiom_scan_ok(text)
            if not ok_ax:
                if found_ax is None or lean_driver_failed(text):
                    return VerifyResult(
                        passed=False,
                        reason="compile_error",
                        stderr_tail=diagnostic_tail,
                        build_seconds=elapsed,
                    )
                extra_ax = f" axioms={found_ax}"
                return VerifyResult(
                    passed=False,
                    reason="axiom_violation",
                    stderr_tail=diagnostic_tail + extra_ax,
                    build_seconds=elapsed,
                )
            return VerifyResult(
                passed=False,
                reason="compile_error",
                stderr_tail=diagnostic_tail,
                build_seconds=elapsed,
            )

        if lake_build_environment_failed(text):
            return VerifyResult(
                passed=False,
                reason="compile_error",
                stderr_tail=diagnostic_tail,
                build_seconds=elapsed,
            )
        if _skip_axiom_check(work):
            return VerifyResult(
                passed=True,
                reason="ok",
                stdout_tail=_verification_stdout_tail(text),
                build_seconds=elapsed,
            )
        ok, found = axiom_scan_ok(text)
        if not ok:
            extra = f" axioms={found}" if found else ""
            if found is None or lean_driver_failed(text):
                return VerifyResult(
                    passed=False,
                    reason="compile_error",
                    stderr_tail=diagnostic_tail + extra,
                    build_seconds=elapsed,
                )
            return VerifyResult(
                passed=False,
                reason="axiom_violation",
                stdout_tail=_verification_stdout_tail(text, limit=4000) + extra,
                build_seconds=elapsed,
            )
        return VerifyResult(
            passed=True,
            reason="ok",
            stdout_tail=_verification_stdout_tail(text),
            build_seconds=elapsed,
            proof_term_hash=proof_term_hash_from_lean_output(text),
            structural_fingerprint=structural_fingerprint_from_lean_output(text),
            declaration_fingerprints=declaration_fingerprints_from_lean_output(text),
            kernel_dependencies=kernel_dependencies_from_lean_output(text),
        )

    def _verify_docker_cli_exec(
        self,
        worker: str,
        container_workdir: str,
        script_name: str,
        work: Path,
        log_tail: int,
    ) -> VerifyResult:
        """Run the same inner script via ``docker exec`` into a long-lived worker (avoids per-job ``docker run``)."""
        t0 = time.monotonic()
        try:
            r = subprocess.run(
                ["docker", "exec", "--workdir", container_workdir, worker, "bash", script_name],
                capture_output=True,
                text=True,
                timeout=float(self.timeout_s),
            )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - t0
            return VerifyResult(
                passed=False,
                reason="timeout",
                stderr_tail="docker exec timed out",
                build_seconds=elapsed,
            )
        elapsed = time.monotonic() - t0
        if _env_truthy("LEMMA_LEAN_VERIFY_TIMING"):
            logger.info(
                "lean verify timing docker_exec wall_s={:.3f} worker={} LEAN_NUM_THREADS={}",
                elapsed,
                worker,
                _lean_num_threads_value(),
            )
        text = (r.stderr or "") + "\n" + (r.stdout or "")
        rc = r.returncode
        if rc is None:
            rc = -1
        return self._verify_docker_parse_logs(text, int(rc), elapsed, work, log_tail)

    def _verify_docker(self, work: Path) -> VerifyResult:
        try:
            import docker.errors
            from requests.exceptions import ReadTimeout
        except ImportError:
            return VerifyResult(passed=False, reason="docker_error", stderr_tail="docker SDK missing")

        import docker

        script_name = self._write_docker_verify_script(work)
        log_tail = 16_000

        # Optional: long-lived worker + `docker exec` — avoids container create/start/remove overhead
        # (often hundreds of ms per verify on Linux, >1s on Docker Desktop). Requires the host root of the
        # workspace cache to match the worker bind-mount (see docs/production.md).
        worker = self.docker_worker
        if worker and shutil.which("docker"):
            root = self._docker_worker_host_root()
            if root is None:
                logger.warning(
                    "LEMMA_LEAN_DOCKER_WORKER is set but no host cache root is configured — set "
                    "LEMMA_LEAN_DOCKER_WORKER_HOST_ROOT or LEMMA_LEAN_VERIFY_WORKSPACE_CACHE_DIR; "
                    "using one-shot container",
                )
            else:
                try:
                    cdir = docker_worker_container_path(work, root, self._docker_worker_mount_point())
                except ValueError:
                    logger.debug(
                        "docker worker skipped — {} is not under {}; using one-shot container",
                        work,
                        root,
                    )
                else:
                    logger.debug("lean docker verify via exec worker={} container_workdir={}", worker, cdir)
                    return self._verify_docker_cli_exec(worker, cdir, script_name, work, log_tail)

        client = docker.from_env()
        nano_cpus = int(self.cpu * 1e9)
        mem = self.mem_mb * 1024 * 1024
        t0 = time.monotonic()
        container = None
        host_root = self._docker_worker_host_root()
        mount_point = self._docker_worker_mount_point()
        volumes = {str(work.resolve()): {"bind": "/work", "mode": "rw"}}
        working_dir = "/work"
        if host_root is not None:
            try:
                working_dir = docker_worker_container_path(work, host_root, mount_point)
                volumes = {str(host_root): {"bind": str(mount_point), "mode": "rw"}}
            except ValueError:
                working_dir = "/work"

        try:
            container = client.containers.create(
                image=self.image,
                command=["bash", script_name],
                volumes=volumes,
                working_dir=working_dir,
                network_mode=self.network_mode,
                nano_cpus=nano_cpus,
                mem_limit=mem,
                user="0:0",
            )
            container.start()
            try:
                wait_result = container.wait(timeout=float(self.timeout_s))
            except ReadTimeout:
                elapsed = time.monotonic() - t0
                try:
                    container.kill()
                except Exception as e:  # noqa: BLE001
                    logger.debug("docker container kill after timeout failed: {}", e)
                text = _docker_container_logs_text(container)
                return VerifyResult(
                    passed=False,
                    reason="timeout",
                    stderr_tail=text[-log_tail:],
                    build_seconds=elapsed,
                )
            elapsed = time.monotonic() - t0
            text = _docker_container_logs_text(container)
            exit_status = int(wait_result["StatusCode"]) if isinstance(wait_result, dict) else int(wait_result)
            if _env_truthy("LEMMA_LEAN_VERIFY_TIMING"):
                logger.info(
                    "lean verify timing docker_one_shot wall_s={:.3f} LEAN_NUM_THREADS={}",
                    elapsed,
                    _lean_num_threads_value(),
                )
            return self._verify_docker_parse_logs(text, exit_status, elapsed, work, log_tail)
        except docker.errors.APIError as e:
            elapsed = time.monotonic() - t0
            return VerifyResult(
                passed=False,
                reason="docker_error",
                stderr_tail=str(e)[-8000:],
                build_seconds=elapsed,
            )
        except Exception as e:  # noqa: BLE001 — surface docker failures
            elapsed = time.monotonic() - t0
            err = str(e)
            if "timeout" in err.lower() or "timed out" in err.lower():
                return VerifyResult(passed=False, reason="timeout", stderr_tail=err, build_seconds=elapsed)
            if "137" in err or "OOM" in err or "non-zero exit: 137" in err:
                return VerifyResult(passed=False, reason="oom", stderr_tail=err, build_seconds=elapsed)
            return VerifyResult(passed=False, reason="docker_error", stderr_tail=err[-8000:], build_seconds=elapsed)
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception as e:  # noqa: BLE001
                    logger.debug("docker container cleanup failed: {}", e)
