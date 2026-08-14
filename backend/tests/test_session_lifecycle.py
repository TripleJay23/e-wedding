"""Request-scoped database session lifecycle tests."""

from types import SimpleNamespace

from app.api.dependencies import get_database_session


class FakeSession:
    """Track session closure without a database connection."""

    closed = False

    def close(self) -> None:
        self.closed = True


def test_database_dependency_always_closes_session() -> None:
    session = FakeSession()
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(database=SimpleNamespace(session_factory=lambda: session))
        )
    )
    dependency = get_database_session(request)  # type: ignore[arg-type]

    assert next(dependency) is session
    dependency.close()

    assert session.closed is True
