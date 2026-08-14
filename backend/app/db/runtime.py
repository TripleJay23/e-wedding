"""SQLAlchemy engine and session lifecycle."""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import Settings
from app.db.guards import DatabaseIdentity, validated_database_url


class DatabaseRuntime:
    """Own the process database engine and session factory."""

    def __init__(self, settings: Settings) -> None:
        url, identity = validated_database_url(settings)
        self.identity: DatabaseIdentity = identity
        self.engine: Engine = create_engine(url, pool_pre_ping=True)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )

    def close(self) -> None:
        """Dispose all pooled database connections."""
        self.engine.dispose()
