"""Add exact M6 template, design, photo, and permanent-card persistence.

Revision ID: 20260820_0006
Revises: 20260820_0005
Create Date: 2026-08-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0006"
down_revision: str | None = "20260820_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create immutable M6 inputs, artifacts, constraints, and lifecycle bridges."""
    op.create_unique_constraint(
        "uq_guest_qr_credentials_id_event_guest",
        "guest_qr_credentials",
        ["id", "event_id", "guest_id"],
    )
    op.create_table(
        "template_presets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("template_key", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "template_key = 'wedding_floral'",
            name="ck_template_presets_supported_key",
        ),
        sa.CheckConstraint(
            "char_length(btrim(display_name)) BETWEEN 1 AND 120",
            name="ck_template_presets_display_name_bounded",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_key", name="uq_template_presets_template_key"),
    )
    op.create_table(
        "template_preset_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("preset_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.SmallInteger(), nullable=False),
        sa.Column("raw_package_sha256", sa.String(length=64), nullable=False),
        sa.Column("logical_package_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("approved_preview_sha256", sa.String(length=64), nullable=False),
        sa.Column("package_storage_key", sa.String(length=255), nullable=False),
        sa.Column("canvas_width", sa.Integer(), nullable=False),
        sa.Column("canvas_height", sa.Integer(), nullable=False),
        sa.Column("asset_count", sa.SmallInteger(), nullable=False),
        sa.Column("font_count", sa.SmallInteger(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "VERIFIED",
                name="template_version_status",
                native_enum=False,
                create_constraint=False,
                length=16,
            ),
            server_default="VERIFIED",
            nullable=False,
        ),
        sa.Column("verified_by", sa.Uuid(), nullable=False),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version = 10",
            name="ck_template_preset_versions_supported_version",
        ),
        sa.CheckConstraint(
            "raw_package_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_template_preset_versions_raw_sha256",
        ),
        sa.CheckConstraint(
            "logical_package_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_template_preset_versions_logical_sha256",
        ),
        sa.CheckConstraint(
            "manifest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_template_preset_versions_manifest_sha256",
        ),
        sa.CheckConstraint(
            "approved_preview_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_template_preset_versions_preview_sha256",
        ),
        sa.CheckConstraint(
            "package_storage_key = 'wedding_floral/v10/wedding_floral_v10.template-package.zip'",
            name="ck_template_preset_versions_storage_key",
        ),
        sa.CheckConstraint(
            "canvas_width = 1240 AND canvas_height = 1748",
            name="ck_template_preset_versions_canvas",
        ),
        sa.CheckConstraint(
            "asset_count = 6 AND font_count = 2",
            name="ck_template_preset_versions_inventory",
        ),
        sa.CheckConstraint(
            "status = 'VERIFIED'",
            name="ck_template_preset_versions_status",
        ),
        sa.ForeignKeyConstraint(["preset_id"], ["template_presets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["verified_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "preset_id",
            "version",
            name="uq_template_preset_versions_preset_version",
        ),
        sa.UniqueConstraint(
            "raw_package_sha256",
            name="uq_template_preset_versions_raw_sha256",
        ),
        sa.UniqueConstraint(
            "logical_package_sha256",
            name="uq_template_preset_versions_logical_sha256",
        ),
    )
    op.create_table(
        "event_photos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=32), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("pixel_width", sa.Integer(), nullable=False),
        sa.Column("pixel_height", sa.Integer(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "storage_key = replace(event_id::text, '-', '') || '/' || "
            "replace(id::text, '-', '') || '.png'",
            name="ck_event_photos_storage_key",
        ),
        sa.CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_event_photos_sha256"),
        sa.CheckConstraint("media_type = 'image/png'", name="ck_event_photos_media_type"),
        sa.CheckConstraint("byte_size > 0", name="ck_event_photos_byte_size"),
        sa.CheckConstraint(
            "pixel_width BETWEEN 256 AND 12000 AND pixel_height BETWEEN 256 AND 12000",
            name="ck_event_photos_dimensions",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "event_id", name="uq_event_photos_id_event"),
        sa.UniqueConstraint("storage_key", name="uq_event_photos_storage_key"),
    )
    op.create_index(
        "ix_event_photos_event_created_id",
        "event_photos",
        ["event_id", "created_at", "id"],
    )
    op.create_index(
        "uq_event_photos_one_primary_event",
        "event_photos",
        ["event_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )
    op.create_table(
        "event_card_designs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_event_card_designs_event"),
        sa.UniqueConstraint("id", "event_id", name="uq_event_card_designs_id_event"),
    )
    op.create_table(
        "event_card_design_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("design_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("template_version_id", sa.Uuid(), nullable=False),
        sa.Column("event_photo_id", sa.Uuid(), nullable=False),
        sa.Column(
            "photo_fit",
            sa.Enum(
                "COVER",
                "CONTAIN",
                name="photo_fit",
                native_enum=False,
                create_constraint=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("photo_focus_x", sa.Numeric(precision=4, scale=3), nullable=False),
        sa.Column("photo_focus_y", sa.Numeric(precision=4, scale=3), nullable=False),
        sa.Column("photo_zoom", sa.Numeric(precision=4, scale=3), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision_number >= 1",
            name="ck_event_card_design_revisions_number",
        ),
        sa.CheckConstraint(
            "photo_fit IN ('COVER', 'CONTAIN')",
            name="ck_event_card_design_revisions_photo_fit",
        ),
        sa.CheckConstraint(
            "photo_focus_x BETWEEN 0 AND 1 AND photo_focus_y BETWEEN 0 AND 1",
            name="ck_event_card_design_revisions_focus",
        ),
        sa.CheckConstraint(
            "photo_zoom BETWEEN 1 AND 3",
            name="ck_event_card_design_revisions_zoom",
        ),
        sa.CheckConstraint(
            "photo_fit = 'COVER' OR photo_zoom = 1",
            name="ck_event_card_design_revisions_contain_zoom",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["design_id", "event_id"],
            ["event_card_designs.id", "event_card_designs.event_id"],
            name="fk_event_card_design_revisions_design_event",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["event_photo_id", "event_id"],
            ["event_photos.id", "event_photos.event_id"],
            name="fk_event_card_design_revisions_photo_event",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["template_version_id"],
            ["template_preset_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "design_id",
            "revision_number",
            name="uq_event_card_design_revisions_design_number",
        ),
        sa.UniqueConstraint(
            "id",
            "event_id",
            name="uq_event_card_design_revisions_id_event",
        ),
        sa.UniqueConstraint(
            "id",
            "event_id",
            "template_version_id",
            name="uq_event_card_design_revisions_id_event_template",
        ),
    )
    op.create_index(
        "ix_event_card_design_revisions_event_number",
        "event_card_design_revisions",
        ["event_id", "revision_number"],
    )
    op.create_table(
        "event_card_design_approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("design_revision_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ACTIVE",
                "REVOKED",
                name="design_approval_status",
                native_enum=False,
                create_constraint=False,
                length=16,
            ),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column("approved_by", sa.Uuid(), nullable=False),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_by", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'REVOKED')",
            name="ck_event_card_design_approvals_status",
        ),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND revoked_by IS NULL AND revoked_at IS NULL) OR "
            "(status = 'REVOKED' AND revoked_by IS NOT NULL AND revoked_at IS NOT NULL)",
            name="ck_event_card_design_approvals_status_timestamps",
        ),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["design_revision_id", "event_id"],
            ["event_card_design_revisions.id", "event_card_design_revisions.event_id"],
            name="fk_event_card_design_approvals_revision_event",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["revoked_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "event_id",
            "design_revision_id",
            name="uq_event_card_design_approvals_id_event_revision",
        ),
    )
    op.create_index(
        "ix_event_card_design_approvals_event_approved_id",
        "event_card_design_approvals",
        ["event_id", "approved_at", "id"],
    )
    op.create_index(
        "uq_event_card_design_approvals_one_active_event",
        "event_card_design_approvals",
        ["event_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_table(
        "generated_guest_cards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("guest_id", sa.Uuid(), nullable=False),
        sa.Column("design_revision_id", sa.Uuid(), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("template_version_id", sa.Uuid(), nullable=False),
        sa.Column("qr_credential_id", sa.Uuid(), nullable=False),
        sa.Column("render_input_sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("media_type", sa.String(length=32), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("pixel_width", sa.Integer(), nullable=False),
        sa.Column("pixel_height", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "CURRENT",
                "STALE",
                name="generated_card_status",
                native_enum=False,
                create_constraint=False,
                length=16,
            ),
            server_default="CURRENT",
            nullable=False,
        ),
        sa.Column("generated_by", sa.Uuid(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("stale_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "render_input_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_generated_guest_cards_render_input_sha256",
        ),
        sa.CheckConstraint(
            "artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_generated_guest_cards_artifact_sha256",
        ),
        sa.CheckConstraint(
            "storage_key = replace(event_id::text, '-', '') || '/' || "
            "replace(guest_id::text, '-', '') || '/' || replace(id::text, '-', '') || '.png'",
            name="ck_generated_guest_cards_storage_key",
        ),
        sa.CheckConstraint(
            "media_type = 'image/png'",
            name="ck_generated_guest_cards_media_type",
        ),
        sa.CheckConstraint(
            "byte_size > 0",
            name="ck_generated_guest_cards_byte_size",
        ),
        sa.CheckConstraint(
            "pixel_width = 1240 AND pixel_height = 1748",
            name="ck_generated_guest_cards_dimensions",
        ),
        sa.CheckConstraint(
            "status IN ('CURRENT', 'STALE')",
            name="ck_generated_guest_cards_status",
        ),
        sa.CheckConstraint(
            "(status = 'CURRENT' AND stale_at IS NULL) OR "
            "(status = 'STALE' AND stale_at IS NOT NULL)",
            name="ck_generated_guest_cards_status_timestamp",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id", "event_id", "design_revision_id"],
            [
                "event_card_design_approvals.id",
                "event_card_design_approvals.event_id",
                "event_card_design_approvals.design_revision_id",
            ],
            name="fk_generated_guest_cards_approval_event_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["generated_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["guest_id", "event_id"],
            ["guests.id", "guests.event_id"],
            name="fk_generated_guest_cards_guest_event",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["qr_credential_id", "event_id", "guest_id"],
            [
                "guest_qr_credentials.id",
                "guest_qr_credentials.event_id",
                "guest_qr_credentials.guest_id",
            ],
            name="fk_generated_guest_cards_qr_event_guest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["design_revision_id", "event_id", "template_version_id"],
            [
                "event_card_design_revisions.id",
                "event_card_design_revisions.event_id",
                "event_card_design_revisions.template_version_id",
            ],
            name="fk_generated_guest_cards_revision_event_template",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "render_input_sha256",
            name="uq_generated_guest_cards_render_input_sha256",
        ),
        sa.UniqueConstraint("storage_key", name="uq_generated_guest_cards_storage_key"),
    )
    op.create_index(
        "ix_generated_guest_cards_event_generated_id",
        "generated_guest_cards",
        ["event_id", "generated_at", "id"],
    )
    op.create_index(
        "ix_generated_guest_cards_guest_generated_id",
        "generated_guest_cards",
        ["guest_id", "generated_at", "id"],
    )
    op.create_index(
        "uq_generated_guest_cards_one_current_guest",
        "generated_guest_cards",
        ["guest_id"],
        unique=True,
        postgresql_where=sa.text("status = 'CURRENT'"),
    )
    _create_lifecycle_triggers()


def _create_lifecycle_triggers() -> None:
    op.execute(
        """
        CREATE FUNCTION protect_template_preset_version_immutability()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'verified template versions are immutable'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_template_preset_versions_immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_template_preset_versions_immutable
        BEFORE UPDATE ON template_preset_versions
        FOR EACH ROW
        EXECUTE FUNCTION protect_template_preset_version_immutability()
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_event_photo_identity()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.event_id IS DISTINCT FROM OLD.event_id
               OR NEW.storage_key IS DISTINCT FROM OLD.storage_key
               OR NEW.sha256 IS DISTINCT FROM OLD.sha256
               OR NEW.media_type IS DISTINCT FROM OLD.media_type
               OR NEW.byte_size IS DISTINCT FROM OLD.byte_size
               OR NEW.pixel_width IS DISTINCT FROM OLD.pixel_width
               OR NEW.pixel_height IS DISTINCT FROM OLD.pixel_height
               OR NEW.uploaded_by IS DISTINCT FROM OLD.uploaded_by
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'event photo artifact identity is immutable'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_event_photos_identity_immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_event_photos_identity
        BEFORE UPDATE ON event_photos
        FOR EACH ROW
        EXECUTE FUNCTION protect_event_photo_identity()
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_event_card_design_revision_immutability()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'event card design revisions are immutable'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'ck_event_card_design_revisions_immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_event_card_design_revisions_immutable
        BEFORE UPDATE ON event_card_design_revisions
        FOR EACH ROW
        EXECUTE FUNCTION protect_event_card_design_revision_immutability()
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_event_card_design_approval_lifecycle()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.event_id IS DISTINCT FROM OLD.event_id
               OR NEW.design_revision_id IS DISTINCT FROM OLD.design_revision_id
               OR NEW.approved_by IS DISTINCT FROM OLD.approved_by
               OR NEW.approved_at IS DISTINCT FROM OLD.approved_at THEN
                RAISE EXCEPTION 'design approval identity is immutable'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_event_card_design_approvals_identity_immutable';
            END IF;
            IF OLD.status = 'REVOKED' AND NEW.status <> 'REVOKED' THEN
                RAISE EXCEPTION 'revoked design approvals are terminal'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_event_card_design_approvals_revoked_terminal';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_event_card_design_approvals_lifecycle
        BEFORE UPDATE ON event_card_design_approvals
        FOR EACH ROW
        EXECUTE FUNCTION protect_event_card_design_approval_lifecycle()
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_generated_guest_card_lifecycle()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'UPDATE' AND (
               NEW.id IS DISTINCT FROM OLD.id
               OR NEW.event_id IS DISTINCT FROM OLD.event_id
               OR NEW.guest_id IS DISTINCT FROM OLD.guest_id
               OR NEW.design_revision_id IS DISTINCT FROM OLD.design_revision_id
               OR NEW.approval_id IS DISTINCT FROM OLD.approval_id
               OR NEW.template_version_id IS DISTINCT FROM OLD.template_version_id
               OR NEW.qr_credential_id IS DISTINCT FROM OLD.qr_credential_id
               OR NEW.render_input_sha256 IS DISTINCT FROM OLD.render_input_sha256
               OR NEW.storage_key IS DISTINCT FROM OLD.storage_key
               OR NEW.artifact_sha256 IS DISTINCT FROM OLD.artifact_sha256
               OR NEW.media_type IS DISTINCT FROM OLD.media_type
               OR NEW.byte_size IS DISTINCT FROM OLD.byte_size
               OR NEW.pixel_width IS DISTINCT FROM OLD.pixel_width
               OR NEW.pixel_height IS DISTINCT FROM OLD.pixel_height
               OR NEW.generated_by IS DISTINCT FROM OLD.generated_by
               OR NEW.generated_at IS DISTINCT FROM OLD.generated_at) THEN
                RAISE EXCEPTION 'generated card artifact identity is immutable'
                    USING ERRCODE = '23514',
                          CONSTRAINT = 'ck_generated_guest_cards_identity_immutable';
            END IF;
            IF NEW.status = 'CURRENT' THEN
                IF NOT EXISTS (
                    SELECT 1
                    FROM guest_qr_credentials credential
                    WHERE credential.id = NEW.qr_credential_id
                      AND credential.event_id = NEW.event_id
                      AND credential.guest_id = NEW.guest_id
                      AND credential.status = 'ACTIVE'
                ) THEN
                    RAISE EXCEPTION 'current generated card requires an active QR credential'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'ck_generated_guest_cards_current_qr_active';
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                    FROM event_card_design_approvals approval
                    WHERE approval.id = NEW.approval_id
                      AND approval.event_id = NEW.event_id
                      AND approval.design_revision_id = NEW.design_revision_id
                      AND approval.status = 'ACTIVE'
                ) THEN
                    RAISE EXCEPTION 'current generated card requires an active design approval'
                        USING ERRCODE = '23514',
                              CONSTRAINT = 'ck_generated_guest_cards_current_approval_active';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_generated_guest_cards_lifecycle
        BEFORE INSERT OR UPDATE ON generated_guest_cards
        FOR EACH ROW
        EXECUTE FUNCTION protect_generated_guest_card_lifecycle()
        """
    )
    op.execute(
        """
        CREATE FUNCTION stale_generated_cards_for_qr_lifecycle()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.status <> 'ACTIVE' THEN
                UPDATE generated_guest_cards
                SET status = 'STALE', stale_at = COALESCE(stale_at, now())
                WHERE qr_credential_id = NEW.id AND status = 'CURRENT';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_guest_qr_credentials_stale_cards
        AFTER UPDATE OF status ON guest_qr_credentials
        FOR EACH ROW
        EXECUTE FUNCTION stale_generated_cards_for_qr_lifecycle()
        """
    )
    op.execute(
        """
        CREATE FUNCTION stale_generated_cards_for_design_approval()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.status = 'ACTIVE' THEN
                UPDATE generated_guest_cards
                SET status = 'STALE', stale_at = COALESCE(stale_at, now())
                WHERE event_id = NEW.event_id
                  AND approval_id <> NEW.id
                  AND status = 'CURRENT';
            ELSE
                UPDATE generated_guest_cards
                SET status = 'STALE', stale_at = COALESCE(stale_at, now())
                WHERE approval_id = NEW.id AND status = 'CURRENT';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_event_card_design_approvals_stale_cards
        AFTER INSERT OR UPDATE OF status ON event_card_design_approvals
        FOR EACH ROW
        EXECUTE FUNCTION stale_generated_cards_for_design_approval()
        """
    )


def downgrade() -> None:
    """Remove only M6 card persistence and its QR composite relationship key."""
    op.execute(
        "DROP TRIGGER trg_event_card_design_approvals_stale_cards ON event_card_design_approvals"
    )
    op.execute("DROP FUNCTION stale_generated_cards_for_design_approval()")
    op.execute("DROP TRIGGER trg_guest_qr_credentials_stale_cards ON guest_qr_credentials")
    op.execute("DROP FUNCTION stale_generated_cards_for_qr_lifecycle()")
    op.execute("DROP TRIGGER trg_generated_guest_cards_lifecycle ON generated_guest_cards")
    op.execute("DROP FUNCTION protect_generated_guest_card_lifecycle()")
    op.execute(
        "DROP TRIGGER trg_event_card_design_approvals_lifecycle ON event_card_design_approvals"
    )
    op.execute("DROP FUNCTION protect_event_card_design_approval_lifecycle()")
    op.execute(
        "DROP TRIGGER trg_event_card_design_revisions_immutable ON event_card_design_revisions"
    )
    op.execute("DROP FUNCTION protect_event_card_design_revision_immutability()")
    op.execute("DROP TRIGGER trg_event_photos_identity ON event_photos")
    op.execute("DROP FUNCTION protect_event_photo_identity()")
    op.execute("DROP TRIGGER trg_template_preset_versions_immutable ON template_preset_versions")
    op.execute("DROP FUNCTION protect_template_preset_version_immutability()")
    op.drop_index(
        "uq_generated_guest_cards_one_current_guest",
        table_name="generated_guest_cards",
    )
    op.drop_index(
        "ix_generated_guest_cards_guest_generated_id",
        table_name="generated_guest_cards",
    )
    op.drop_index(
        "ix_generated_guest_cards_event_generated_id",
        table_name="generated_guest_cards",
    )
    op.drop_table("generated_guest_cards")
    op.drop_index(
        "uq_event_card_design_approvals_one_active_event",
        table_name="event_card_design_approvals",
    )
    op.drop_index(
        "ix_event_card_design_approvals_event_approved_id",
        table_name="event_card_design_approvals",
    )
    op.drop_table("event_card_design_approvals")
    op.drop_index(
        "ix_event_card_design_revisions_event_number",
        table_name="event_card_design_revisions",
    )
    op.drop_table("event_card_design_revisions")
    op.drop_table("event_card_designs")
    op.drop_index(
        "uq_event_photos_one_primary_event",
        table_name="event_photos",
    )
    op.drop_index("ix_event_photos_event_created_id", table_name="event_photos")
    op.drop_table("event_photos")
    op.drop_table("template_preset_versions")
    op.drop_table("template_presets")
    op.drop_constraint(
        "uq_guest_qr_credentials_id_event_guest",
        "guest_qr_credentials",
        type_="unique",
    )
