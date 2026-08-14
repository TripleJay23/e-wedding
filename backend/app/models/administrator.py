"""Single-administrator persistence model."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AdministratorRole(StrEnum):
    """The only authenticating role in the admin-only MVP."""

    SUPER_ADMIN = "SUPER_ADMIN"


class Administrator(Base):
    """The sole persisted application administrator."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("singleton_key = 1", name="ck_users_singleton_key"),
        CheckConstraint(
            "username ~ '^[a-z0-9][a-z0-9._-]{2,63}$'",
            name="ck_users_username_canonical",
        ),
        CheckConstraint(
            "password_hash LIKE '$argon2id$%'",
            name="ck_users_password_hash_argon2id",
        ),
        CheckConstraint("role = 'SUPER_ADMIN'", name="ck_users_role_super_admin"),
        UniqueConstraint("singleton_key", name="uq_users_singleton_key"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    singleton_key: Mapped[int] = mapped_column(SmallInteger, default=1, server_default="1")
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[AdministratorRole] = mapped_column(
        Enum(
            AdministratorRole,
            name="administrator_role",
            native_enum=False,
            create_constraint=False,
            length=32,
        )
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
