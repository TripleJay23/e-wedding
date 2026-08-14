"""Create the clean rebuild baseline.

Revision ID: 20260814_0001
Revises: None
Create Date: 2026-08-14
"""

from collections.abc import Sequence

revision: str = "20260814_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Establish an intentionally empty domain-schema baseline."""


def downgrade() -> None:
    """Remove the intentionally empty domain-schema baseline."""
