from pydantic_settings import BaseSettings, SettingsConfigDict


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

    app_role_password: str = "app_role_dev_pw"
    maintenance_role_password: str = "maintenance_role_dev_pw"

    # REQUIREMENTS.md Scenario B 1b: single global setting, not per-policy-rule.
    retention_window_days: int = 365


settings = Settings()
