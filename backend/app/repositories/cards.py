"""Bounded PostgreSQL persistence operations for M6 card generation."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models.cards import (
    DesignApprovalStatus,
    EventCardDesign,
    EventCardDesignApproval,
    EventCardDesignRevision,
    EventPhoto,
    GeneratedCardStatus,
    GeneratedGuestCard,
    TemplatePreset,
    TemplatePresetVersion,
)
from app.models.event import Event
from app.models.guest import Guest, GuestStatus
from app.models.qr import GuestQrCredential, QrCredentialStatus

DesignContext = tuple[
    EventCardDesignApproval,
    EventCardDesignRevision,
    TemplatePresetVersion,
    TemplatePreset,
    EventPhoto,
]


class CardRepository:
    """Keep M6 reads event-scoped, indexed, and explicit."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get_template(
        self,
        template_key: str,
        version: int,
    ) -> tuple[TemplatePreset, TemplatePresetVersion] | None:
        statement = (
            select(TemplatePreset, TemplatePresetVersion)
            .join(TemplatePresetVersion, TemplatePresetVersion.preset_id == TemplatePreset.id)
            .where(
                TemplatePreset.template_key == template_key,
                TemplatePresetVersion.version == version,
            )
        )
        row = self._session.execute(statement).one_or_none()
        return None if row is None else (row[0], row[1])

    def get_preset(self, template_key: str) -> TemplatePreset | None:
        return self._session.execute(
            select(TemplatePreset).where(TemplatePreset.template_key == template_key)
        ).scalar_one_or_none()

    def list_templates(
        self, *, limit: int, offset: int
    ) -> list[tuple[TemplatePreset, TemplatePresetVersion]]:
        statement = (
            select(TemplatePreset, TemplatePresetVersion)
            .join(TemplatePresetVersion, TemplatePresetVersion.preset_id == TemplatePreset.id)
            .order_by(TemplatePreset.template_key, TemplatePresetVersion.version)
            .limit(limit)
            .offset(offset)
        )
        return [(row[0], row[1]) for row in self._session.execute(statement)]

    def get_event(self, event_id: UUID, *, for_update: bool = False) -> Event | None:
        statement = select(Event).where(Event.id == event_id)
        if for_update:
            statement = statement.with_for_update(of=Event)
        return self._session.execute(statement).scalar_one_or_none()

    def list_photos(self, event_id: UUID, *, limit: int, offset: int) -> list[EventPhoto]:
        statement = (
            select(EventPhoto)
            .where(EventPhoto.event_id == event_id)
            .order_by(EventPhoto.created_at.desc(), EventPhoto.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.execute(statement).scalars())

    def get_photo(self, event_id: UUID, photo_id: UUID) -> EventPhoto | None:
        return self._session.execute(
            select(EventPhoto).where(
                EventPhoto.event_id == event_id,
                EventPhoto.id == photo_id,
            )
        ).scalar_one_or_none()

    def get_primary_photo(self, event_id: UUID) -> EventPhoto | None:
        return self._session.execute(
            select(EventPhoto).where(
                EventPhoto.event_id == event_id,
                EventPhoto.is_primary.is_(True),
            )
        ).scalar_one_or_none()

    def get_design(self, event_id: UUID) -> EventCardDesign | None:
        return self._session.execute(
            select(EventCardDesign).where(EventCardDesign.event_id == event_id)
        ).scalar_one_or_none()

    def get_latest_revision(self, event_id: UUID) -> EventCardDesignRevision | None:
        statement = (
            select(EventCardDesignRevision)
            .where(EventCardDesignRevision.event_id == event_id)
            .order_by(
                EventCardDesignRevision.revision_number.desc(),
                EventCardDesignRevision.id.desc(),
            )
            .limit(1)
        )
        return self._session.execute(statement).scalar_one_or_none()

    def get_revision(self, event_id: UUID, revision_id: UUID) -> EventCardDesignRevision | None:
        return self._session.execute(
            select(EventCardDesignRevision).where(
                EventCardDesignRevision.event_id == event_id,
                EventCardDesignRevision.id == revision_id,
            )
        ).scalar_one_or_none()

    def list_revisions(
        self,
        event_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> list[EventCardDesignRevision]:
        statement = (
            select(EventCardDesignRevision)
            .where(EventCardDesignRevision.event_id == event_id)
            .order_by(
                EventCardDesignRevision.revision_number.desc(),
                EventCardDesignRevision.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.execute(statement).scalars())

    def get_active_approval(self, event_id: UUID) -> EventCardDesignApproval | None:
        return self._session.execute(
            select(EventCardDesignApproval).where(
                EventCardDesignApproval.event_id == event_id,
                EventCardDesignApproval.status == DesignApprovalStatus.ACTIVE,
            )
        ).scalar_one_or_none()

    def get_approval(
        self,
        event_id: UUID,
        approval_id: UUID,
    ) -> EventCardDesignApproval | None:
        return self._session.execute(
            select(EventCardDesignApproval).where(
                EventCardDesignApproval.event_id == event_id,
                EventCardDesignApproval.id == approval_id,
            )
        ).scalar_one_or_none()

    def get_active_design_context(self, event_id: UUID) -> DesignContext | None:
        statement = (
            select(
                EventCardDesignApproval,
                EventCardDesignRevision,
                TemplatePresetVersion,
                TemplatePreset,
                EventPhoto,
            )
            .join(
                EventCardDesignRevision,
                EventCardDesignRevision.id == EventCardDesignApproval.design_revision_id,
            )
            .join(
                TemplatePresetVersion,
                TemplatePresetVersion.id == EventCardDesignRevision.template_version_id,
            )
            .join(TemplatePreset, TemplatePreset.id == TemplatePresetVersion.preset_id)
            .join(EventPhoto, EventPhoto.id == EventCardDesignRevision.event_photo_id)
            .where(
                EventCardDesignApproval.event_id == event_id,
                EventCardDesignApproval.status == DesignApprovalStatus.ACTIVE,
            )
        )
        row = self._session.execute(statement).one_or_none()
        return None if row is None else (row[0], row[1], row[2], row[3], row[4])

    def get_guest_event_for_update(
        self,
        event_id: UUID,
        guest_id: UUID,
    ) -> tuple[Guest, Event] | None:
        statement = (
            select(Guest, Event)
            .join(Event, Event.id == Guest.event_id)
            .where(Guest.id == guest_id, Guest.event_id == event_id)
            .with_for_update(of=Guest)
        )
        row = self._session.execute(statement).one_or_none()
        return None if row is None else (row[0], row[1])

    def get_active_qr(self, event_id: UUID, guest_id: UUID) -> GuestQrCredential | None:
        return self._session.execute(
            select(GuestQrCredential).where(
                GuestQrCredential.event_id == event_id,
                GuestQrCredential.guest_id == guest_id,
                GuestQrCredential.status == QrCredentialStatus.ACTIVE,
            )
        ).scalar_one_or_none()

    def list_eligible_guest_credentials(
        self,
        event_id: UUID,
        *,
        guest_ids: Collection[UUID] | None,
        limit: int,
    ) -> list[tuple[Guest, GuestQrCredential]]:
        statement: Select[tuple[Guest, GuestQrCredential]] = (
            select(Guest, GuestQrCredential)
            .join(
                GuestQrCredential,
                (GuestQrCredential.guest_id == Guest.id)
                & (GuestQrCredential.event_id == Guest.event_id)
                & (GuestQrCredential.status == QrCredentialStatus.ACTIVE),
            )
            .where(Guest.event_id == event_id, Guest.status == GuestStatus.ACTIVE)
            .order_by(Guest.full_name, Guest.id)
            .limit(limit)
            .with_for_update(of=Guest)
        )
        if guest_ids is not None:
            statement = statement.where(Guest.id.in_(guest_ids))
        return [(row[0], row[1]) for row in self._session.execute(statement)]

    def get_by_render_input(self, render_input_sha256: str) -> GeneratedGuestCard | None:
        return self._session.execute(
            select(GeneratedGuestCard).where(
                GeneratedGuestCard.render_input_sha256 == render_input_sha256
            )
        ).scalar_one_or_none()

    def list_by_render_inputs(
        self,
        render_input_sha256s: Collection[str],
    ) -> dict[str, GeneratedGuestCard]:
        if not render_input_sha256s:
            return {}
        cards = self._session.execute(
            select(GeneratedGuestCard).where(
                GeneratedGuestCard.render_input_sha256.in_(render_input_sha256s)
            )
        ).scalars()
        return {card.render_input_sha256: card for card in cards}

    def list_current_for_guests(
        self,
        guest_ids: Collection[UUID],
    ) -> dict[UUID, GeneratedGuestCard]:
        if not guest_ids:
            return {}
        cards = self._session.execute(
            select(GeneratedGuestCard).where(
                GeneratedGuestCard.guest_id.in_(guest_ids),
                GeneratedGuestCard.status == GeneratedCardStatus.CURRENT,
            )
        ).scalars()
        return {card.guest_id: card for card in cards}

    def get_card(self, event_id: UUID, card_id: UUID) -> GeneratedGuestCard | None:
        return self._session.execute(
            select(GeneratedGuestCard).where(
                GeneratedGuestCard.event_id == event_id,
                GeneratedGuestCard.id == card_id,
            )
        ).scalar_one_or_none()

    def list_cards(
        self,
        event_id: UUID,
        *,
        status: GeneratedCardStatus | None,
        limit: int,
        offset: int,
    ) -> list[GeneratedGuestCard]:
        statement = select(GeneratedGuestCard).where(GeneratedGuestCard.event_id == event_id)
        if status is not None:
            statement = statement.where(GeneratedGuestCard.status == status)
        statement = (
            statement.order_by(
                GeneratedGuestCard.generated_at.desc(),
                GeneratedGuestCard.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.execute(statement).scalars())

    def list_cards_by_ids(
        self,
        event_id: UUID,
        card_ids: Collection[UUID],
    ) -> list[GeneratedGuestCard]:
        statement = (
            select(GeneratedGuestCard)
            .where(
                GeneratedGuestCard.event_id == event_id,
                GeneratedGuestCard.id.in_(card_ids),
            )
            .order_by(GeneratedGuestCard.generated_at, GeneratedGuestCard.id)
        )
        return list(self._session.execute(statement).scalars())

    def add(self, *records: object) -> None:
        self._session.add_all(records)

    def flush(self) -> None:
        self._session.flush()

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def refresh_all(self, records: Sequence[object]) -> None:
        for record in records:
            self._session.refresh(record)
