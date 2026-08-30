"""Repository-scope and privacy configuration tests."""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"


def test_compose_database_is_local_and_named_for_development() -> None:
    compose = (REPOSITORY_ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "POSTGRES_DB: wedding_ecard_dev" in compose
    assert '"127.0.0.1:${POSTGRES_DEV_PORT:-5432}:5432"' in compose


def test_staging_compose_and_example_use_distinct_canonical_identity() -> None:
    compose = (REPOSITORY_ROOT / "compose.staging.yaml").read_text(encoding="utf-8")
    example = (REPOSITORY_ROOT / ".env.staging.example").read_text(encoding="utf-8")

    assert "name: wedding-ecard-backend-staging" in compose
    assert "image: postgres:17-alpine" in compose
    assert "POSTGRES_DB: wedding_ecard_staging" in compose
    assert "POSTGRES_USER: wedding_staging_app" in compose
    assert "name: wedding-ecard-m10-staging-v2-postgres-data" in compose
    assert "wedding-ecard-staging-postgres-data" not in compose
    assert "APP_ENV=staging" in example
    assert "CHANGE_ME" in example


def test_environment_examples_contain_placeholders_not_secrets() -> None:
    for path in (REPOSITORY_ROOT / ".env.example", BACKEND_ROOT / ".env.example"):
        content = path.read_text(encoding="utf-8")
        assert "CHANGE_ME" in content
        assert "APP_ENV=development" in content

    staging_content = (REPOSITORY_ROOT / ".env.staging.example").read_text(encoding="utf-8")
    assert "CHANGE_ME" in staging_content
    assert "APP_ENV=staging" in staging_content


def test_private_and_generated_artifacts_are_ignored() -> None:
    content = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")

    for required_pattern in (
        ".env",
        ".env.*",
        "private-storage/",
        "*.xlsx",
        "*.sql",
        "generated-cards/",
    ):
        assert required_pattern in content


def test_route_modules_are_limited_through_m9() -> None:
    route_files = {path.stem for path in (BACKEND_ROOT / "app" / "api" / "routes").glob("*.py")}

    assert route_files == {
        "__init__",
        "auth",
        "attendance",
        "attendance_corrections",
        "cards",
        "events",
        "guests",
        "health",
        "imports",
        "qr",
        "reports",
    }
