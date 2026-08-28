"""Repository-scope and privacy configuration tests."""

from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"


def test_compose_database_is_local_and_named_for_development() -> None:
    compose = (REPOSITORY_ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "POSTGRES_DB: wedding_ecard_dev" in compose
    assert '"127.0.0.1:${POSTGRES_DEV_PORT:-5432}:5432"' in compose


def test_environment_examples_contain_placeholders_not_secrets() -> None:
    for path in (REPOSITORY_ROOT / ".env.example", BACKEND_ROOT / ".env.example"):
        content = path.read_text(encoding="utf-8")
        assert "CHANGE_ME" in content
        assert "APP_ENV=development" in content


def test_private_and_generated_artifacts_are_ignored() -> None:
    content = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")

    for required_pattern in (
        ".env",
        "private-storage/",
        "*.xlsx",
        "*.sql",
        "generated-cards/",
    ):
        assert required_pattern in content


def test_route_modules_are_limited_through_m8() -> None:
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
    }
