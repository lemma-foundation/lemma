"""Environment-driven settings for proof tasks and Lean verification."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import PydanticBaseSettingsSource


class LemmaSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=False,
    )

    def __init__(self, **data: Any) -> None:
        """Accept Python field-name kwargs without accepting field-name env vars."""
        for name, field in type(self).model_fields.items():
            if name not in data:
                continue
            alias = field.validation_alias
            if isinstance(alias, str):
                data.setdefault(alias, data[name])
                data.pop(name)
        super().__init__(**data)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Prefer `.env` over process env unless explicitly told otherwise."""
        if os.environ.get("LEMMA_PREFER_PROCESS_ENV", "").strip().lower() in {"1", "true", "yes"}:
            return init_settings, env_settings, dotenv_settings, file_secret_settings
        return init_settings, dotenv_settings, env_settings, file_secret_settings

    task_registry_url: str = Field(
        default="tasks/registry.json",
        validation_alias="LEMMA_TASK_REGISTRY_URL",
    )
    task_registry_sha256_expected: str | None = Field(
        default=None,
        validation_alias="LEMMA_TASK_REGISTRY_SHA256_EXPECTED",
    )
    task_http_timeout_s: float = Field(
        default=30.0,
        gt=0.0,
        validation_alias="LEMMA_TASK_HTTP_TIMEOUT_S",
    )
    corpus_index_url: str = Field(default="", validation_alias="LEMMA_CORPUS_INDEX_URL")
    corpus_output_dir: Path = Field(default=Path("corpus"), validation_alias="LEMMA_CORPUS_OUTPUT_DIR")
    operator_data_dir: Path = Field(default=Path("validator-data"), validation_alias="LEMMA_OPERATOR_DATA_DIR")
    submission_spool_dir: Path | None = Field(default=None, validation_alias="LEMMA_SUBMISSION_SPOOL_DIR")
    active_task_count: int = Field(default=20, ge=1, validation_alias="LEMMA_ACTIVE_K")
    frontier_depth: int = Field(default=0, ge=0, validation_alias="LEMMA_FRONTIER_DEPTH")
    active_queue_seed: str = Field(default="lemma-active-queue-v1", validation_alias="LEMMA_ACTIVE_QUEUE_SEED")
    schema_version: str = Field(default="v2", validation_alias="LEMMA_SCHEMA_VERSION")
    enabled_domains: tuple[str, ...] = Field(default=("lean",), validation_alias="LEMMA_ENABLED_DOMAINS")
    experimental_domains: tuple[str, ...] = Field(default=(), validation_alias="LEMMA_EXPERIMENTAL_DOMAINS")
    enable_experimental_verus: bool = Field(
        default=False,
        validation_alias="LEMMA_ENABLE_EXPERIMENTAL_VERUS",
    )

    prover_command: str = Field(default="", validation_alias="LEMMA_PROVER_COMMAND")
    prover_base_url: str = Field(default="", validation_alias="LEMMA_PROVER_BASE_URL")
    prover_api_key: str = Field(default="", validation_alias="LEMMA_PROVER_API_KEY")
    prover_model: str = Field(default="", validation_alias="LEMMA_PROVER_MODEL")
    prover_timeout_s: float = Field(default=300.0, gt=0.0, validation_alias="LEMMA_PROVER_TIMEOUT_S")

    wallet_cold: str = Field(default="default", validation_alias="BT_WALLET_COLD")
    wallet_hot: str = Field(default="default", validation_alias="BT_WALLET_HOT")
    netuid: int = Field(default=0, ge=0, validation_alias="BT_NETUID")
    unearned_allocation_policy: Literal["burn", "recycle", "hold"] = Field(
        default="burn",
        validation_alias="LEMMA_UNEARNED_ALLOCATION_POLICY",
    )
    unearned_uid: int | None = Field(default=0, ge=0, validation_alias="LEMMA_UNEARNED_UID")

    lean_sandbox_image: str = Field(default="lemma/lean-sandbox:latest", validation_alias="LEAN_SANDBOX_IMAGE")
    lean_verify_timeout_s: int = Field(default=300, ge=1, validation_alias="LEAN_VERIFY_TIMEOUT_S")
    lean_sandbox_cpu: float = Field(default=2.0, gt=0.0, validation_alias="LEAN_SANDBOX_CPU")
    lean_sandbox_mem_mb: int = Field(default=8192, ge=512, validation_alias="LEAN_SANDBOX_MEM_MB")
    lean_sandbox_network: str = Field(default="none", validation_alias="LEAN_SANDBOX_NETWORK")
    lean_use_docker: bool = Field(default=True, validation_alias="LEMMA_USE_DOCKER")
    allow_host_lean: bool = Field(default=False, validation_alias="LEMMA_ALLOW_HOST_LEAN")
    lean_verify_workspace_cache_dir: Path | None = Field(
        default=None,
        validation_alias="LEMMA_LEAN_VERIFY_WORKSPACE_CACHE_DIR",
    )
    lemma_lean_workspace_cache_max_dirs: int = Field(
        default=8,
        ge=0,
        validation_alias="LEMMA_LEAN_WORKSPACE_CACHE_MAX_DIRS",
    )
    lemma_lean_workspace_cache_max_bytes: int = Field(
        default=16 * 1024 * 1024 * 1024,
        ge=0,
        validation_alias="LEMMA_LEAN_WORKSPACE_CACHE_MAX_BYTES",
    )
    lemma_lean_workspace_cache_include_submission_hash: bool = Field(
        default=False,
        validation_alias="LEMMA_LEAN_WORKSPACE_CACHE_INCLUDE_SUBMISSION_HASH",
    )
    lemma_lean_docker_worker: str = Field(default="", validation_alias="LEMMA_LEAN_DOCKER_WORKER")
    lean_verify_remote_url: str | None = Field(default=None, validation_alias="LEMMA_LEAN_VERIFY_REMOTE_URL")
    lean_verify_remote_bearer: str | None = Field(default=None, validation_alias="LEMMA_LEAN_VERIFY_REMOTE_BEARER")
    lean_worker_allow_unauthenticated_non_loopback: bool = Field(
        default=False,
        validation_alias="LEMMA_LEAN_WORKER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK",
    )
    lean_verify_remote_timeout_margin_s: float = Field(
        default=30.0,
        ge=0.0,
        validation_alias="LEMMA_LEAN_VERIFY_REMOTE_TIMEOUT_MARGIN_S",
    )

    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
