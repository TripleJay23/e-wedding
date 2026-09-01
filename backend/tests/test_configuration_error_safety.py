"""Secret-canary regression tests for configuration and operator error boundaries."""

from __future__ import annotations

import secrets
import traceback
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.cli import staging_alembic, staging_bootstrap_admin, staging_rotate_admin_password
from app.cli.bootstrap_admin import AdministratorBootstrapSettings
from app.cli.staging_alembic import StagingMigrationAuthorizationSettings
from app.cli.staging_bootstrap_admin import StagingAdministratorBootstrapSettings
from app.cli.staging_rotate_admin_password import (
    StagingAdministratorPasswordRotationSettings,
)
from app.core import settings as settings_module
from app.core.configuration_errors import (
    SENSITIVE_CONFIGURATION_FIELDS,
    SafeConfigurationError,
    format_configuration_validation_error,
    serialize_configuration_validation_errors,
)
from app.core.settings import Settings
from app.main import create_app

_CONFIGURATION_FIELDS = (
    "APP_ENV",
    "DATABASE_URL",
    "JWT_SECRET",
    "JWT_ISSUER",
    "JWT_AUDIENCE",
    "PRIVATE_STORAGE_ROOT",
    "IMPORT_EVIDENCE_STORAGE_ROOT",
    "QR_IMAGE_STORAGE_ROOT",
    "TEMPLATE_STORAGE_ROOT",
    "EVENT_PHOTO_STORAGE_ROOT",
    "GENERATED_CARD_STORAGE_ROOT",
    "STAGING_ADMIN_BOOTSTRAP_USERNAME",
    "STAGING_ADMIN_BOOTSTRAP_PASSWORD",
    "STAGING_ADMIN_ROTATION_EXPECTED_UUID",
    "STAGING_ADMIN_ROTATION_PASSWORD",
    "STAGING_MIGRATION_AUTHORIZATION",
)


def _canary(label: str) -> str:
    return f"{label}_{secrets.token_urlsafe(18)}:/+?"


def _assert_absent(output: str, *canaries: str) -> None:
    for canary in canaries:
        assert canary not in output
        assert quote(canary, safe="") not in output
        assert repr(canary) not in output


def _configure_staging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    database_secret: str,
    admin_secret: str,
    jwt_secret: str,
    database_name: str = "wedding_ecard_staging",
) -> None:
    for field in _CONFIGURATION_FIELDS:
        monkeypatch.delenv(field, raising=False)
    encoded_db_secret = quote(database_secret, safe="")
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://wedding_staging_app:"
        f"{encoded_db_secret}@127.0.0.1:55432/{database_name}",
    )
    monkeypatch.setenv("JWT_SECRET", f"{jwt_secret}_padding_for_minimum_length")
    monkeypatch.setenv("JWT_ISSUER", "synthetic-issuer")
    monkeypatch.setenv("JWT_AUDIENCE", "synthetic-audience")
    private_root = tmp_path / "private"
    monkeypatch.setenv("PRIVATE_STORAGE_ROOT", str(private_root))
    monkeypatch.setenv(
        "IMPORT_EVIDENCE_STORAGE_ROOT",
        str(private_root / "import-evidence"),
    )
    monkeypatch.setenv("STAGING_ADMIN_BOOTSTRAP_PASSWORD", admin_secret)
    monkeypatch.setenv(
        "STAGING_ADMIN_ROTATION_EXPECTED_UUID",
        "00000000-0000-0000-0000-000000000001",
    )
    monkeypatch.setenv("STAGING_ADMIN_ROTATION_PASSWORD", admin_secret)


def _captured_output(capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    return captured.out + captured.err


def test_sensitive_configuration_classification_covers_current_secret_fields() -> None:
    assert {
        "DATABASE_URL",
        "DATABASE_PASSWORD",
        "POSTGRES_STAGING_PASSWORD",
        "ADMIN_BOOTSTRAP_PASSWORD",
        "STAGING_ADMIN_BOOTSTRAP_PASSWORD",
        "STAGING_ADMIN_ROTATION_PASSWORD",
        "JWT_SECRET",
        "STAGING_MIGRATION_AUTHORIZATION",
    } <= SENSITIVE_CONFIGURATION_FIELDS


def test_secret_bearing_settings_models_hide_inputs_in_raw_validation_errors(
    tmp_path: Path,
) -> None:
    database_secret = _canary("DB")
    admin_secret = _canary("ADMIN")
    jwt_secret = _canary("JWT")
    env_file = tmp_path / "synthetic-staging.env"
    env_file.write_text(
        "APP_ENV=staging\n"
        "DATABASE_URL=postgresql+psycopg://wedding_staging_app:"
        f"{quote(database_secret, safe='')}@127.0.0.1/wedding_ecard_staging\n"
        f"JWT_SECRET={jwt_secret}_padding_for_minimum_length\n"
        "JWT_ISSUER=synthetic-issuer\n"
        "JWT_AUDIENCE=synthetic-audience\n"
        f"PRIVATE_STORAGE_ROOT={tmp_path / 'private'}\n"
        f"IMPORT_EVIDENCE_STORAGE_ROOT={tmp_path / 'private' / 'import-evidence'}\n"
        f"POSTGRES_STAGING_PASSWORD={database_secret}\n"
        f"STAGING_ADMIN_BOOTSTRAP_PASSWORD={admin_secret}\n"
        "STAGING_ADMIN_ROTATION_EXPECTED_UUID=00000000-0000-0000-0000-000000000001\n"
        f"STAGING_ADMIN_ROTATION_PASSWORD={admin_secret}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=env_file)

    raw_rendering = str(error.value) + repr(error.value)
    assert "input_value" not in raw_rendering
    assert "input_type" not in raw_rendering
    _assert_absent(raw_rendering, database_secret, admin_secret, jwt_secret)

    for settings_type in (
        Settings,
        AdministratorBootstrapSettings,
        StagingMigrationAuthorizationSettings,
        StagingAdministratorBootstrapSettings,
        StagingAdministratorPasswordRotationSettings,
    ):
        assert settings_type.model_config["hide_input_in_errors"] is True


def test_formatter_discards_input_context_url_and_raw_exception_repr(tmp_path: Path) -> None:
    database_secret = _canary("DB")
    jwt_secret = _canary("JWT")
    values = {
        "APP_ENV": "staging",
        "DATABASE_URL": (
            "postgresql+psycopg://wedding_staging_app:"
            f"{quote(database_secret, safe='')}@127.0.0.1/wedding_ecard_staging"
        ),
        "JWT_SECRET": f"{jwt_secret}_padding_for_minimum_length",
        "JWT_ISSUER": "synthetic-issuer",
        "JWT_AUDIENCE": "synthetic-audience",
        "PRIVATE_STORAGE_ROOT": "relative-private",
        "IMPORT_EVIDENCE_STORAGE_ROOT": tmp_path / "private" / "evidence",
    }

    with pytest.raises(ValidationError) as error:
        Settings(**values)

    serialized = serialize_configuration_validation_errors(error.value)
    output = format_configuration_validation_error(error.value)

    assert serialized == [
        {
            "type": "value_error",
            "location": "configuration",
            "message": "Value does not satisfy configuration requirements",
        }
    ]
    assert "input" not in output
    assert "ctx" not in output
    assert "errors.pydantic.dev" not in output
    _assert_absent(output, database_secret, jwt_secret)


def test_formatter_discards_secret_bearing_validation_context() -> None:
    secret = _canary("CONTEXT")
    error = ValidationError.from_exception_data(
        "SyntheticConfiguration",
        [
            {
                "type": "value_error",
                "loc": ("JWT_SECRET",),
                "input": secret,
                "ctx": {"error": ValueError(secret)},
            }
        ],
    )

    output = format_configuration_validation_error(error)

    assert "location=JWT_SECRET" in output
    _assert_absent(output, secret)


@pytest.mark.parametrize(
    "scenario",
    [
        "missing_app_env",
        "invalid_app_env",
        "malformed_database",
        "wrong_database_identity",
        "missing_database_password",
        "malformed_jwt",
    ],
)
def test_staging_alembic_configuration_refusals_are_bounded_and_secret_free(
    scenario: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_secret = _canary("DB")
    admin_secret = _canary("ADMIN")
    jwt_secret = _canary("JWT")
    _configure_staging(
        monkeypatch,
        tmp_path,
        database_secret=database_secret,
        admin_secret=admin_secret,
        jwt_secret=jwt_secret,
    )
    if scenario == "missing_app_env":
        monkeypatch.delenv("APP_ENV")
    elif scenario == "invalid_app_env":
        monkeypatch.setenv("APP_ENV", _canary("ENV"))
    elif scenario == "malformed_database":
        monkeypatch.setenv("DATABASE_URL", f"not-a-url-{quote(database_secret, safe='')}")
    elif scenario == "wrong_database_identity":
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+psycopg://wedding_staging_app:"
            f"{quote(database_secret, safe='')}@127.0.0.1:55432/wedding_ecard_dev",
        )
    elif scenario == "missing_database_password":
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+psycopg://wedding_staging_app@127.0.0.1:55432/wedding_ecard_staging",
        )
    else:
        monkeypatch.setenv("JWT_SECRET", jwt_secret)

    assert staging_alembic.main(["current"]) == 2

    output = _captured_output(capsys)
    assert len(output) <= 2_000
    assert "Traceback" not in output
    _assert_absent(output, database_secret, admin_secret, jwt_secret)


def test_staging_migration_authorization_refusal_is_secret_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_secret = _canary("DB")
    admin_secret = _canary("ADMIN")
    jwt_secret = _canary("JWT")
    authorization_secret = _canary("AUTH")
    _configure_staging(
        monkeypatch,
        tmp_path,
        database_secret=database_secret,
        admin_secret=admin_secret,
        jwt_secret=jwt_secret,
    )
    monkeypatch.setenv("STAGING_MIGRATION_AUTHORIZATION", authorization_secret)

    assert staging_alembic.main(["upgrade", "head"]) == 2

    output = _captured_output(capsys)
    assert "explicit staging migration authorization is invalid" in output
    assert "Traceback" not in output
    _assert_absent(
        output,
        database_secret,
        admin_secret,
        jwt_secret,
        authorization_secret,
    )


@pytest.mark.parametrize("scenario", ["missing_admin_password", "invalid_admin_password"])
def test_staging_admin_configuration_refusals_are_secret_free(
    scenario: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_secret = _canary("DB")
    admin_secret = _canary("ADMIN")
    jwt_secret = _canary("JWT")
    _configure_staging(
        monkeypatch,
        tmp_path,
        database_secret=database_secret,
        admin_secret=admin_secret,
        jwt_secret=jwt_secret,
    )
    if scenario == "missing_admin_password":
        monkeypatch.delenv("STAGING_ADMIN_BOOTSTRAP_PASSWORD")
    else:
        monkeypatch.setenv(
            "STAGING_ADMIN_BOOTSTRAP_PASSWORD",
            f"CHANGE_ME_{admin_secret}",
        )

    assert staging_bootstrap_admin.main() == 2

    output = _captured_output(capsys)
    assert "Traceback" not in output
    _assert_absent(output, database_secret, admin_secret, jwt_secret)


@pytest.mark.parametrize(
    "scenario",
    [
        "missing_app_env",
        "invalid_integration_env",
        "wrong_database_identity",
        "missing_replacement_password",
        "invalid_replacement_password",
        "invalid_expected_uuid",
    ],
)
def test_staging_admin_rotation_configuration_refusals_are_secret_free(
    scenario: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_secret = _canary("DB")
    admin_secret = _canary("ROTATION")
    jwt_secret = _canary("JWT")
    _configure_staging(
        monkeypatch,
        tmp_path,
        database_secret=database_secret,
        admin_secret=admin_secret,
        jwt_secret=jwt_secret,
    )
    if scenario == "missing_app_env":
        monkeypatch.delenv("APP_ENV")
    elif scenario == "invalid_integration_env":
        monkeypatch.setenv("APP_ENV", "integration")
    elif scenario == "wrong_database_identity":
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+psycopg://wedding_staging_app:"
            f"{quote(database_secret, safe='')}@127.0.0.1:55432/wedding_ecard_dev",
        )
    elif scenario == "missing_replacement_password":
        monkeypatch.delenv("STAGING_ADMIN_ROTATION_PASSWORD")
    elif scenario == "invalid_replacement_password":
        monkeypatch.setenv(
            "STAGING_ADMIN_ROTATION_PASSWORD",
            f"CHANGE_ME_{admin_secret}",
        )
    else:
        monkeypatch.setenv("STAGING_ADMIN_ROTATION_EXPECTED_UUID", admin_secret)

    assert staging_rotate_admin_password.main() == 2

    output = _captured_output(capsys)
    assert len(output) <= 2_000
    assert "Traceback" not in output
    _assert_absent(output, database_secret, admin_secret, jwt_secret)


@pytest.mark.parametrize("operator", ["migration", "admin", "rotation"])
def test_staging_database_exception_context_is_not_rendered(
    operator: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_secret = _canary("DB")
    admin_secret = _canary("ADMIN")
    jwt_secret = _canary("JWT")
    exception_secret = _canary("DATABASE_EXCEPTION")
    _configure_staging(
        monkeypatch,
        tmp_path,
        database_secret=database_secret,
        admin_secret=admin_secret,
        jwt_secret=jwt_secret,
    )

    def fail_with_secret(*args: object, **kwargs: object) -> None:
        raise SQLAlchemyError(exception_secret)

    if operator == "migration":
        monkeypatch.setattr(staging_alembic, "execute", fail_with_secret)
        result = staging_alembic.main(["current"])
    elif operator == "admin":
        monkeypatch.setattr(staging_bootstrap_admin, "execute", fail_with_secret)
        result = staging_bootstrap_admin.main()
    else:
        monkeypatch.setattr(staging_rotate_admin_password, "execute", fail_with_secret)
        result = staging_rotate_admin_password.main()

    assert result == 2
    output = _captured_output(capsys)
    assert "database operation failed after identity validation" in output
    assert "Traceback" not in output
    _assert_absent(
        output,
        database_secret,
        admin_secret,
        jwt_secret,
        exception_secret,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda monkeypatch, canary: monkeypatch.delenv("APP_ENV"),
        lambda monkeypatch, canary: monkeypatch.setenv("JWT_SECRET", canary),
        lambda monkeypatch, canary: monkeypatch.setenv(
            "DATABASE_URL", f"malformed-{quote(canary, safe='')}"
        ),
    ],
)
def test_application_startup_configuration_failure_has_safe_traceback(
    mutation: Callable[[pytest.MonkeyPatch, str], None],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_secret = _canary("DB")
    admin_secret = _canary("ADMIN")
    jwt_secret = _canary("JWT")
    mutation_secret = f"MUTATION_{secrets.token_hex(4)}"
    _configure_staging(
        monkeypatch,
        tmp_path,
        database_secret=database_secret,
        admin_secret=admin_secret,
        jwt_secret=jwt_secret,
    )
    mutation(monkeypatch, mutation_secret)
    settings_module.get_settings.cache_clear()

    with pytest.raises(SafeConfigurationError) as error:
        create_app()

    output = "".join(traceback.format_exception(error.value))
    assert "SafeConfigurationError" in output
    _assert_absent(output, database_secret, admin_secret, jwt_secret, mutation_secret)
    settings_module.get_settings.cache_clear()
