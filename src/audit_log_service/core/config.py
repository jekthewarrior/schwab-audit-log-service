from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# P3: named once, reused as both the field defaults below and the fail-secure
# guard's comparison values — a single source of truth, so the guard can never
# silently drift from what the defaults actually are.
_DEV_APP_ROLE_PASSWORD = "app_role_dev_pw"  # nosec B105
_DEV_MAINTENANCE_ROLE_PASSWORD = "maintenance_role_dev_pw"  # nosec B105
_DEV_EXPORT_SIGNING_KEY_SEED_HEX = (
    "4307d2f61c47efa0ec7d92be9eb55bc1069260444459c48466616829726d5e6f"
)


class Principal(BaseModel):
    """An authenticated API caller (REQUIREMENTS.md C7). `roles` gates which
    endpoints a principal may call (C9); `resource_scope`, when set, restricts a
    `reader`/`compliance` principal to a specific allow-list of `resourceId`s
    (C12) — `None` means unscoped.
    """

    principal_id: str
    roles: frozenset[str]
    resource_scope: frozenset[str] | None = None


def _default_api_keys() -> dict[str, Principal]:
    # Dev-fixed API key -> Principal map (C7/C11), same pattern as the export
    # signing key seed and DB role passwords below: fixed, documented, no
    # secrets-manager integration. principal_ids match the actor conventions
    # already used elsewhere in the docs (compliance-officer-1, cron-scheduler).
    # None of these carry a resource_scope by default — scoping (C12) is opt-in
    # per principal, exercised explicitly in tests/test_cross_tenant.py rather
    # than baked into these defaults.
    return {
        "dev-writer-key": Principal(principal_id="producer-service", roles=frozenset({"writer"})),
        "dev-reader-key": Principal(principal_id="reader-service", roles=frozenset({"reader"})),
        "dev-compliance-key": Principal(
            principal_id="compliance-officer-1", roles=frozenset({"compliance"})
        ),
        "dev-scheduler-key": Principal(
            principal_id="cron-scheduler", roles=frozenset({"scheduler"})
        ),
    }


class Settings(BaseSettings):
    """DB connection settings, split by privilege level per REQUIREMENTS.md 2a.

    Dev-only defaults below (matching docker-compose.yml). Production would source
    these from a secrets manager, and admin_database_url in particular should never
    be reachable from application runtime code — it exists only for Alembic.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # app_role: SELECT, INSERT only. Used by the running application (writes,
    # queries, verify).
    database_url: str = "postgresql+asyncpg://app_role:app_role_dev_pw@localhost:5432/audit_log"

    # maintenance_role: SELECT, INSERT, and column-scoped UPDATE (never on
    # sequence_number/content_hash/prev_hash). Used only by redaction (B1) and
    # retention (B2) services.
    maintenance_database_url: str = (
        "postgresql+asyncpg://maintenance_role:maintenance_role_dev_pw@localhost:5432/audit_log"
    )

    # Superuser/owner connection. Used only by Alembic migrations — never imported
    # by application runtime code.
    admin_database_url: str = "postgresql+asyncpg://audit:audit@localhost:5432/audit_log"

    app_role_password: str = _DEV_APP_ROLE_PASSWORD
    maintenance_role_password: str = _DEV_MAINTENANCE_ROLE_PASSWORD

    # REQUIREMENTS.md Scenario B 1b: single global setting, not per-policy-rule.
    retention_window_days: int = 365

    # Ed25519 signing key for export bundles (Scenario B 5c). Dev-only fixed seed —
    # production would load this from a secrets manager/KMS, never a hardcoded
    # default (documented, not silently simplified — see the operational note in
    # REQUIREMENTS.md 5c).
    export_signing_key_seed_hex: str = _DEV_EXPORT_SIGNING_KEY_SEED_HEX
    export_signing_key_id: str = "dev-key-1"

    # C7/C8/C9: dev-fixed API key -> Principal map, gating every endpoint except
    # GET /health and GET /audit/export/public-key.
    api_keys: dict[str, Principal] = Field(default_factory=_default_api_keys)

    # P3 (code-review finding: "hardcoded dev secrets with no fail-secure
    # guard"). Local dev/test never sets this — default stays "development", so
    # existing behavior (docker compose, the test suite) is unchanged. A real
    # deployment must set ENVIRONMENT=production explicitly, which then requires
    # every dev-default secret below to have been overridden — see the validator.
    environment: Literal["development", "test", "production"] = "development"

    @model_validator(mode="after")
    def _refuse_to_boot_with_dev_secrets_in_production(self) -> Self:
        if self.environment != "production":
            return self
        dev_defaults = {
            "app_role_password": _DEV_APP_ROLE_PASSWORD,
            "maintenance_role_password": _DEV_MAINTENANCE_ROLE_PASSWORD,
            "export_signing_key_seed_hex": _DEV_EXPORT_SIGNING_KEY_SEED_HEX,
        }
        leaked = [name for name, default in dev_defaults.items() if getattr(self, name) == default]
        if leaked:
            raise ValueError(
                "Refusing to start with ENVIRONMENT=production while these settings "
                f"still hold their hardcoded dev defaults: {', '.join(leaked)}. "
                "Override them (env vars/.env/secrets manager) before deploying."
            )
        return self


settings = Settings()
