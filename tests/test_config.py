"""P3 — code-review finding "hardcoded dev secrets with no fail-secure guard":
Settings refuses to construct under ENVIRONMENT=production while any of its
secret fields still hold their hardcoded dev defaults. No DB fixture needed —
pure construction of the Settings model, stays fast per conftest.py's lazy-fixture
convention.
"""

import pytest
from pydantic import ValidationError

from audit_log_service.core.config import Settings


def test_production_environment_with_default_secrets_raises() -> None:
    with pytest.raises(ValidationError, match="Refusing to start"):
        Settings(environment="production")


def test_production_environment_with_overridden_secrets_does_not_raise() -> None:
    Settings(
        environment="production",
        app_role_password="a-real-password",
        maintenance_role_password="another-real-password",
        export_signing_key_seed_hex="f" * 64,
    )


def test_development_environment_does_not_raise_even_with_default_secrets() -> None:
    Settings(environment="development")


def test_default_environment_is_development() -> None:
    assert Settings().environment == "development"
