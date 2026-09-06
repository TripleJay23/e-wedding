"""Central M3 event lifecycle, guest policy, and manual guest rules."""

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from app.models.event import (
    DEFAULT_GUEST_FIELD_CONFIGURATION,
    Event,
    EventGuestField,
    EventStatus,
    GuestFieldKey,
)
from app.models.guest import Guest
from app.repositories.events import EventRepository
from app.repositories.guests import GuestRepository
from app.services.guest_validation import GuestFieldPolicy, validate_guest_values


class M3ResourceNotFoundError(LookupError):
    """Raised when an event-scoped M3 resource does not exist."""


class M3ConflictError(ValueError):
    """Raised when a requested mutation conflicts with persisted M3 state."""


class EventLifecycle:
    """Single authoritative event lifecycle policy."""

    _allowed_transitions: ClassVar[Mapping[EventStatus, frozenset[EventStatus]]] = {
        EventStatus.DRAFT: frozenset({EventStatus.ACTIVE, EventStatus.CANCELLED}),
        EventStatus.ACTIVE: frozenset({EventStatus.COMPLETED, EventStatus.CANCELLED}),
        EventStatus.COMPLETED: frozenset(),
        EventStatus.CANCELLED: frozenset(),
    }
    _mutable_states: ClassVar[frozenset[EventStatus]] = frozenset(
        {EventStatus.DRAFT, EventStatus.ACTIVE}
    )

    @classmethod
    def require_transition(cls, current: EventStatus, target: EventStatus) -> None:
        """Permit an idempotent status request or one documented forward transition."""
        if target != current and target not in cls._allowed_transitions[current]:
            raise M3ConflictError(f"event cannot transition from {current.value} to {target.value}")

    @classmethod
    def require_mutable(cls, event: Event) -> None:
        """Keep completed and cancelled event state immutable."""
        if event.status not in cls._mutable_states:
            raise M3ConflictError(f"{event.status.value} events are immutable")


class EventService:
    """Coordinate transactions and all M3 domain validation."""

    def __init__(
        self,
        events: EventRepository,
        guests: GuestRepository,
    ) -> None:
        self._events = events
        self._guests = guests

    def create_event(self, *, administrator_id: UUID, values: Mapping[str, Any]) -> Event:
        """Create one DRAFT event and its complete default configuration atomically."""
        event = Event(
            id=uuid4(),
            created_by=administrator_id,
            status=EventStatus.DRAFT,
            **values,
        )
        fields = [
            EventGuestField(
                id=uuid4(),
                event_id=event.id,
                field_key=field_key,
                enabled=enabled,
                required=required,
                sort_order=sort_order,
            )
            for sort_order, (field_key, enabled, required) in enumerate(
                DEFAULT_GUEST_FIELD_CONFIGURATION
            )
        ]
        self._events.add(event)
        self._events.add_guest_fields(fields)
        self._commit(self._events)
        return event

    def list_events(
        self,
        *,
        event_status: EventStatus | None,
        limit: int,
        offset: int,
    ) -> list[Event]:
        return self._events.list(event_status=event_status, limit=limit, offset=offset)

    def get_event(self, event_id: UUID) -> Event:
        return self._require_event(event_id)

    def update_event(self, event_id: UUID, values: Mapping[str, Any]) -> Event:
        """Update event data or transition status under one centralized policy."""
        event = self._require_event(event_id)
        requested_status = values.get("status", event.status)
        assert isinstance(requested_status, EventStatus)
        EventLifecycle.require_transition(event.status, requested_status)

        substantive_changes = {
            key: value
            for key, value in values.items()
            if key != "status" and getattr(event, key) != value
        }
        status_changes = requested_status != event.status
        if substantive_changes:
            EventLifecycle.require_mutable(event)

        requested_maximum = substantive_changes.get("max_companions_per_invitation")
        if isinstance(requested_maximum, int):
            current_guest_maximum = self._events.maximum_guest_companions(event.id)
            if current_guest_maximum is not None and current_guest_maximum > requested_maximum:
                raise M3ConflictError(
                    "event companion maximum cannot be lower than an existing guest allowance"
                )

        for key, value in substantive_changes.items():
            setattr(event, key, value)
        if status_changes:
            event.status = requested_status
        if substantive_changes or status_changes:
            self._commit(self._events)
        return event

    def get_guest_fields(self, event_id: UUID) -> list[EventGuestField]:
        self._require_event(event_id)
        fields = self._events.get_guest_fields(event_id)
        if len(fields) != len(DEFAULT_GUEST_FIELD_CONFIGURATION):
            raise M3ConflictError("event guest-field configuration is incomplete")
        return fields

    def replace_guest_fields(
        self,
        event_id: UUID,
        configuration: Sequence[tuple[GuestFieldKey, bool, bool]],
    ) -> list[EventGuestField]:
        """Replace enabled/required rules while preserving the canonical field set."""
        event = self._require_event(event_id)
        EventLifecycle.require_mutable(event)
        self._validate_configuration(configuration)

        fields = self._events.get_guest_fields(event_id)
        by_key = {field.field_key: field for field in fields}
        if set(by_key) != {item[0] for item in DEFAULT_GUEST_FIELD_CONFIGURATION}:
            raise M3ConflictError("event guest-field configuration is incomplete")
        for field_key, enabled, required in configuration:
            field = by_key[field_key]
            field.enabled = enabled
            field.required = required
        self._commit(self._events)
        return sorted(fields, key=lambda field: field.sort_order)

    def create_guest(self, event_id: UUID, values: Mapping[str, Any]) -> Guest:
        """Create a manual guest only when event policy and field rules allow it."""
        event = self._require_event(event_id)
        EventLifecycle.require_mutable(event)
        fields = self._require_guest_fields(event_id)
        self._validate_guest_values(values, fields, event.max_companions_per_invitation)

        guest = Guest(id=uuid4(), event_id=event.id, **values)
        self._guests.add(guest)
        self._commit(self._guests)
        return guest

    def list_guests(self, event_id: UUID, *, limit: int, offset: int) -> list[Guest]:
        self._require_event(event_id)
        return self._guests.list_for_event(event_id, limit=limit, offset=offset)

    def get_guest(self, event_id: UUID, guest_id: UUID) -> Guest:
        guest = self._guests.get_for_event(event_id, guest_id)
        if guest is None:
            raise M3ResourceNotFoundError("guest not found")
        return guest

    def update_guest(
        self,
        event_id: UUID,
        guest_id: UUID,
        values: Mapping[str, Any],
    ) -> Guest:
        """Apply a manual guest update under current event configuration and policy."""
        event = self._require_event(event_id)
        EventLifecycle.require_mutable(event)
        guest = self._guests.get_for_event_for_update(event_id, guest_id)
        if guest is None:
            raise M3ResourceNotFoundError("guest not found")
        requested_allowance = values.get("allowed_companions", guest.allowed_companions)
        if (
            requested_allowance != guest.allowed_companions
            and self._guests.has_companion_entitlement(event_id, guest_id)
        ):
            raise M3ConflictError("guest companion allowance is locked after admission")
        merged = {
            field_key.value: getattr(guest, field_key.value)
            for field_key, _, _ in DEFAULT_GUEST_FIELD_CONFIGURATION
        }
        merged.update(
            {
                key: value
                for key, value in values.items()
                if key in {item[0].value for item in DEFAULT_GUEST_FIELD_CONFIGURATION}
            }
        )
        fields = self._require_guest_fields(event_id)
        self._validate_guest_values(merged, fields, event.max_companions_per_invitation)

        changed = False
        for key, value in values.items():
            if getattr(guest, key) != value:
                setattr(guest, key, value)
                changed = True
        if changed:
            self._commit(self._guests)
        return guest

    def _require_event(self, event_id: UUID) -> Event:
        event = self._events.get(event_id)
        if event is None:
            raise M3ResourceNotFoundError("event not found")
        return event

    def _require_guest_fields(self, event_id: UUID) -> list[EventGuestField]:
        fields = self._events.get_guest_fields(event_id)
        if len(fields) != len(DEFAULT_GUEST_FIELD_CONFIGURATION):
            raise M3ConflictError("event guest-field configuration is incomplete")
        return fields

    @staticmethod
    def _validate_configuration(
        configuration: Sequence[tuple[GuestFieldKey, bool, bool]],
    ) -> None:
        expected = {item[0] for item in DEFAULT_GUEST_FIELD_CONFIGURATION}
        keys = [field_key for field_key, _, _ in configuration]
        if len(keys) != len(expected) or set(keys) != expected:
            raise M3ConflictError("configuration must contain every canonical guest field")
        for field_key, enabled, required in configuration:
            if required and not enabled:
                raise M3ConflictError("a required guest field must be enabled")
            if field_key in {GuestFieldKey.FULL_NAME, GuestFieldKey.INVITATION_TYPE} and (
                not enabled or not required
            ):
                raise M3ConflictError(f"{field_key.value} must remain enabled and required")

    @staticmethod
    def _validate_guest_values(
        values: Mapping[str, Any],
        fields: Sequence[EventGuestField],
        event_companion_maximum: int,
    ) -> None:
        policies = [
            GuestFieldPolicy(field.field_key, field.enabled, field.required) for field in fields
        ]
        errors = validate_guest_values(dict(values), policies, event_companion_maximum)
        if not errors:
            return
        first = errors[0]
        field_name = first["field"]
        if first["code"] == "REQUIRED" and field_name is not None:
            raise M3ConflictError(f"{field_name} is required for this event")
        if first["code"] == "FIELD_DISABLED" and field_name is not None:
            raise M3ConflictError(f"{field_name} is disabled for this event")
        if first["code"] == "INVALID_COMPANION_COUNT":
            raise M3ConflictError("allowed companions must be an integer")
        if first["code"] == "COMPANION_LIMIT_EXCEEDED":
            raise M3ConflictError("allowed companions must be within the event companion policy")
        raise M3ConflictError(first["message"])

    @staticmethod
    def _commit(repository: EventRepository | GuestRepository) -> None:
        try:
            repository.commit()
        except IntegrityError as exc:
            repository.rollback()
            raise M3ConflictError("M3 persistence constraint rejected the request") from exc
