"""Opt-in disposable PostgreSQL M3 acceptance and constraint verification."""

import asyncio
import os
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.cli import disposable_alembic
from app.core.security import AuthenticationSecurity
from app.core.settings import Settings
from app.db.runtime import DatabaseRuntime
from app.main import create_app
from app.models.administrator import Administrator
from app.models.event import Event
from app.models.guest import Guest
from app.repositories.administrators import AdministratorRepository
from app.repositories.events import EventRepository
from app.repositories.guests import GuestRepository
from app.services.admin_provisioning import AdministratorProvisioningService
from app.services.events import EventService

DISPOSABLE_URL = os.getenv("WEDDING_DISPOSABLE_DATABASE_URL")
pytestmark = [
    pytest.mark.postgresql,
    pytest.mark.skipif(
        DISPOSABLE_URL is None,
        reason="WEDDING_DISPOSABLE_DATABASE_URL is not explicitly configured",
    ),
]


def configure_test_environment(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Select only the explicitly supplied guarded disposable database."""
    assert DISPOSABLE_URL is not None
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", DISPOSABLE_URL)
    monkeypatch.setenv("JWT_SECRET", "synthetic-jwt-signing-secret-at-least-32-bytes")
    monkeypatch.setenv("JWT_ISSUER", "synthetic-issuer")
    monkeypatch.setenv("JWT_AUDIENCE", "synthetic-audience")
    return Settings()


async def authenticated_m3_round_trip(
    settings: Settings,
    runtime: DatabaseRuntime,
) -> tuple[dict[str, Any], str]:
    """Exercise actual auth reload plus the primary M3 protected routes."""
    app = create_app(
        settings,
        database_runtime=runtime,
        authentication_security=AuthenticationSecurity.from_settings(settings),
    )
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://synthetic.test") as client,
    ):
        login = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "synthetic administrator password"},
        )
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created = await client.post(
            "/api/v1/events",
            headers=headers,
            json={
                "event_name": "Synthetic PostgreSQL Wedding",
                "bride_name": "Amina",
                "groom_name": "Baraka",
                "event_datetime": "2027-06-12T14:30:00+03:00",
                "venue": "Synthetic Hall",
                "max_companions_per_invitation": 2,
            },
        )
        assert created.status_code == 201, created.text
        event_body = created.json()
        event_id = event_body["id"]

        listed = await client.get("/api/v1/events?limit=10&offset=0", headers=headers)
        detail = await client.get(f"/api/v1/events/{event_id}", headers=headers)
        guest_fields = await client.get(
            f"/api/v1/events/{event_id}/guest-fields",
            headers=headers,
        )
        assert listed.status_code == detail.status_code == guest_fields.status_code == 200
        assert len(guest_fields.json()["fields"]) == 7
        assert listed.json()["items"][0]["id"] == event_id

        for companions in (0, 1, 2):
            guest = await client.post(
                f"/api/v1/events/{event_id}/guests",
                headers=headers,
                json={
                    "full_name": f"Synthetic Guest {companions}",
                    "invitation_type": "Family",
                    "allowed_companions": companions,
                },
            )
            assert guest.status_code == 201, guest.text
            assert guest.json()["allowed_companions"] == companions

        activated = await client.patch(
            f"/api/v1/events/{event_id}",
            headers=headers,
            json={"status": "ACTIVE"},
        )
        assert activated.status_code == 200
        assert activated.json()["status"] == "ACTIVE"
        return event_body, event_id


def test_m3_schema_authenticated_api_constraints_and_query_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = configure_test_environment(monkeypatch)
    assert disposable_alembic.main(["upgrade", "head"]) == 0
    assert disposable_alembic.main(["check"]) == 0

    engine = create_engine(DISPOSABLE_URL, pool_pre_ping=True)
    runtime = DatabaseRuntime(settings)
    administrator_id = None
    event_id = None
    try:
        security = AuthenticationSecurity.from_settings(settings)
        with Session(engine, expire_on_commit=False) as session:
            administrator = AdministratorProvisioningService(
                AdministratorRepository(session),
                security.passwords,
            ).provision("admin", "synthetic administrator password")
            administrator_id = administrator.id

        event_body, event_id_text = asyncio.run(authenticated_m3_round_trip(settings, runtime))
        event_id = UUID(event_id_text)
        assert event_body["created_by"] == str(administrator_id)
        assert event_body["max_companions_per_invitation"] == 2

        inspector = inspect(engine)
        assert {"events", "event_guest_fields", "guests", "users"} <= set(
            inspector.get_table_names()
        )
        assert {index["name"] for index in inspector.get_indexes("events")} >= {
            "ix_events_event_datetime_id",
            "ix_events_status_event_datetime_id",
        }
        assert {index["name"] for index in inspector.get_indexes("guests")} >= {
            "ix_guests_event_full_name_id"
        }
        assert {constraint["name"] for constraint in inspector.get_check_constraints("events")} >= {
            "ck_events_max_companions_bounded",
            "ck_events_status",
        }
        assert {constraint["name"] for constraint in inspector.get_check_constraints("guests")} >= {
            "ck_guests_allowed_companions_bounded",
            "ck_guests_status",
        }
        with engine.connect() as connection:
            trigger_names = set(
                connection.execute(
                    text(
                        "SELECT trigger_name FROM information_schema.triggers "
                        "WHERE event_object_table IN ('events', 'guests')"
                    )
                ).scalars()
            )
        assert {"trg_events_companion_policy", "trg_guests_companion_policy"} <= trigger_names

        assert administrator_id is not None
        with Session(engine) as session:
            invalid_creator = Event(
                event_name="Invalid Creator Wedding",
                bride_name="Synthetic",
                groom_name="Synthetic",
                event_datetime=datetime(2027, 1, 1, tzinfo=UTC),
                venue="Synthetic Hall",
                max_companions_per_invitation=2,
                created_by=uuid4(),
            )
            session.add(invalid_creator)
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            too_large_policy = Event(
                event_name="Invalid Policy Wedding",
                bride_name="Synthetic",
                groom_name="Synthetic",
                event_datetime=datetime(2027, 1, 1, tzinfo=UTC),
                venue="Synthetic Hall",
                max_companions_per_invitation=3,
                created_by=administrator_id,
            )
            session.add(too_large_policy)
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

            restricted_event = Event(
                event_name="Restricted Policy Wedding",
                bride_name="Synthetic",
                groom_name="Synthetic",
                event_datetime=datetime(2027, 1, 2, tzinfo=UTC),
                venue="Synthetic Hall",
                max_companions_per_invitation=1,
                created_by=administrator_id,
            )
            session.add(restricted_event)
            session.commit()
            session.add(
                Guest(
                    event_id=restricted_event.id,
                    full_name="Cross Policy Guest",
                    invitation_type="Family",
                    allowed_companions=2,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

        with Session(engine) as session:
            for invalid_allowance in (-1, 3):
                persisted_event = session.get(Event, event_id)
                assert persisted_event is not None
                invalid_guest = Guest(
                    event_id=persisted_event.id,
                    full_name="Invalid Guest",
                    invitation_type="Family",
                    allowed_companions=invalid_allowance,
                )
                session.add(invalid_guest)
                with pytest.raises(IntegrityError):
                    session.commit()
                session.rollback()

            persisted_event = session.get(Event, event_id)
            assert persisted_event is not None
            persisted_event.max_companions_per_invitation = 1
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()

        statements: list[str] = []

        def capture_statement(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: bool,
        ) -> None:
            statements.append(statement)

        sqlalchemy_event.listen(engine, "before_cursor_execute", capture_statement)
        try:
            with Session(engine, expire_on_commit=False) as session:
                service = EventService(EventRepository(session), GuestRepository(session))
                statements.clear()
                listed_events = service.list_events(event_status=None, limit=50, offset=0)
                assert event_id in {listed_event.id for listed_event in listed_events}
                assert len(statements) == 1

            with Session(engine, expire_on_commit=False) as session:
                service = EventService(EventRepository(session), GuestRepository(session))
                statements.clear()
                service.get_event(event_id)
                assert len(statements) == 1

            with Session(engine, expire_on_commit=False) as session:
                service = EventService(EventRepository(session), GuestRepository(session))
                statements.clear()
                assert len(service.list_guests(event_id, limit=50, offset=0)) == 3
                assert len(statements) == 2
        finally:
            sqlalchemy_event.remove(engine, "before_cursor_execute", capture_statement)
    finally:
        runtime.close()
        with Session(engine) as session:
            if administrator_id is not None:
                for persisted_event in session.execute(
                    select(Event).where(Event.created_by == administrator_id)
                ).scalars():
                    session.delete(persisted_event)
                session.flush()
                administrator = session.get(Administrator, administrator_id)
                if administrator is not None:
                    session.delete(administrator)
            session.commit()
        engine.dispose()
