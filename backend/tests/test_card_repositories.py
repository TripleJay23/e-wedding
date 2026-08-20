"""M6 repository query shape, event scoping, batching, and transactions."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from app.models.cards import GeneratedCardStatus
from app.repositories.cards import CardRepository

NOW = datetime(2026, 8, 20, tzinfo=UTC)


class FakeResult:
    def __init__(
        self,
        *,
        scalar: Any = None,
        scalars: list[Any] | None = None,
        rows: list[Any] | None = None,
        one: Any = None,
    ) -> None:
        self._scalar = scalar
        self._scalars = scalars or []
        self._rows = rows or []
        self._one = one

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalars(self) -> list[Any]:
        return self._scalars

    def one_or_none(self) -> Any:
        return self._one

    def __iter__(self):
        return iter(self._rows)


class FakeSession:
    def __init__(self) -> None:
        self.result = FakeResult()
        self.statements: list[Any] = []
        self.added: list[Any] = []
        self.refreshed: list[Any] = []
        self.flushes = 0
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement: Any) -> FakeResult:
        self.statements.append(statement)
        return self.result

    def add_all(self, values: tuple[object, ...]) -> None:
        self.added.extend(values)

    def refresh(self, value: object) -> None:
        self.refreshed.append(value)

    def flush(self) -> None:
        self.flushes += 1

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_template_photo_design_and_approval_queries_are_scoped_and_bounded() -> None:
    session = FakeSession()
    repository = CardRepository(session)  # type: ignore[arg-type]
    event_id = uuid4()
    photo_id = uuid4()
    revision_id = uuid4()
    approval_id = uuid4()
    preset = SimpleNamespace(id=uuid4(), template_key="wedding_floral")
    version = SimpleNamespace(id=uuid4(), preset_id=preset.id, version=10)
    event = SimpleNamespace(id=event_id)
    photo = SimpleNamespace(id=photo_id, event_id=event_id)
    design = SimpleNamespace(id=uuid4(), event_id=event_id)
    revision = SimpleNamespace(id=revision_id, event_id=event_id)
    approval = SimpleNamespace(id=approval_id, event_id=event_id)

    session.result = FakeResult(one=(preset, version))
    assert repository.get_template("wedding_floral", 10) == (preset, version)
    assert "JOIN template_preset_versions" in str(session.statements[-1])
    session.result = FakeResult(one=None)
    assert repository.get_template("missing", 10) is None

    session.result = FakeResult(scalar=preset)
    assert repository.get_preset("wedding_floral") is preset
    session.result = FakeResult(rows=[(preset, version)])
    assert repository.list_templates(limit=10, offset=2) == [(preset, version)]
    assert "LIMIT" in str(session.statements[-1])
    assert "OFFSET" in str(session.statements[-1])

    session.result = FakeResult(scalar=event)
    assert repository.get_event(event_id) is event
    assert repository.get_event(event_id, for_update=True) is event
    assert "FOR UPDATE" in str(session.statements[-1])

    session.result = FakeResult(scalars=[photo])
    assert repository.list_photos(event_id, limit=5, offset=1) == [photo]
    assert "ORDER BY event_photos.created_at DESC" in str(session.statements[-1])
    session.result = FakeResult(scalar=photo)
    assert repository.get_photo(event_id, photo_id) is photo
    assert repository.get_primary_photo(event_id) is photo
    assert "event_photos.is_primary IS true" in str(session.statements[-1])

    session.result = FakeResult(scalar=design)
    assert repository.get_design(event_id) is design
    session.result = FakeResult(scalar=revision)
    assert repository.get_latest_revision(event_id) is revision
    assert "ORDER BY event_card_design_revisions.revision_number DESC" in str(
        session.statements[-1]
    )
    assert repository.get_revision(event_id, revision_id) is revision
    session.result = FakeResult(scalars=[revision])
    assert repository.list_revisions(event_id, limit=10, offset=0) == [revision]

    session.result = FakeResult(scalar=approval)
    assert repository.get_active_approval(event_id) is approval
    assert repository.get_approval(event_id, approval_id) is approval
    session.result = FakeResult(one=(approval, revision, version, preset, photo))
    assert repository.get_active_design_context(event_id) == (
        approval,
        revision,
        version,
        preset,
        photo,
    )
    session.result = FakeResult(one=None)
    assert repository.get_active_design_context(event_id) is None


def test_guest_qr_and_card_queries_are_event_scoped_batched_and_deterministic() -> None:
    session = FakeSession()
    repository = CardRepository(session)  # type: ignore[arg-type]
    event_id = uuid4()
    guest_id = uuid4()
    card_id = uuid4()
    guest = SimpleNamespace(id=guest_id, event_id=event_id, full_name="Synthetic")
    event = SimpleNamespace(id=event_id)
    credential = SimpleNamespace(id=uuid4(), guest_id=guest_id, event_id=event_id)
    card = SimpleNamespace(
        id=card_id,
        event_id=event_id,
        guest_id=guest_id,
        render_input_sha256="a" * 64,
        status=GeneratedCardStatus.CURRENT,
        generated_at=NOW,
    )

    session.result = FakeResult(one=(guest, event))
    assert repository.get_guest_event_for_update(event_id, guest_id) == (guest, event)
    assert "FOR UPDATE" in str(session.statements[-1])
    session.result = FakeResult(one=None)
    assert repository.get_guest_event_for_update(event_id, guest_id) is None

    session.result = FakeResult(scalar=credential)
    assert repository.get_active_qr(event_id, guest_id) is credential
    session.result = FakeResult(rows=[(guest, credential)])
    assert repository.list_eligible_guest_credentials(
        event_id,
        guest_ids=None,
        limit=100,
    ) == [(guest, credential)]
    assert "ORDER BY guests.full_name, guests.id" in str(session.statements[-1])
    assert repository.list_eligible_guest_credentials(
        event_id,
        guest_ids={guest_id},
        limit=1,
    ) == [(guest, credential)]
    assert "guests.id IN" in str(session.statements[-1])

    session.result = FakeResult(scalar=card)
    assert repository.get_by_render_input("a" * 64) is card
    session.result = FakeResult(scalars=[card])
    assert repository.list_by_render_inputs({"a" * 64}) == {"a" * 64: card}
    assert repository.list_current_for_guests({guest_id}) == {guest_id: card}
    assert repository.list_by_render_inputs(set()) == {}
    assert repository.list_current_for_guests(set()) == {}

    session.result = FakeResult(scalar=card)
    assert repository.get_card(event_id, card_id) is card
    session.result = FakeResult(scalars=[card])
    assert repository.list_cards(
        event_id,
        status=None,
        limit=50,
        offset=0,
    ) == [card]
    assert "ORDER BY generated_guest_cards.generated_at DESC" in str(session.statements[-1])
    assert repository.list_cards(
        event_id,
        status=GeneratedCardStatus.CURRENT,
        limit=10,
        offset=2,
    ) == [card]
    assert "generated_guest_cards.status" in str(session.statements[-1])
    assert repository.list_cards_by_ids(event_id, {card_id}) == [card]


def test_repository_delegates_mutations_refresh_and_transactions() -> None:
    session = FakeSession()
    repository = CardRepository(session)  # type: ignore[arg-type]
    first = SimpleNamespace(id=uuid4())
    second = SimpleNamespace(id=uuid4())

    repository.add(first, second)
    repository.flush()
    repository.commit()
    repository.rollback()
    repository.refresh_all([first, second])

    assert session.added == [first, second]
    assert session.refreshed == [first, second]
    assert (session.flushes, session.commits, session.rollbacks) == (1, 1, 1)
