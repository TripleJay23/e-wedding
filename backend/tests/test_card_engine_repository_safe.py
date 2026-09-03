"""Repository-safe coverage for the generic card engine and M6 service."""

from __future__ import annotations

import copy
import hashlib
import json
from io import BytesIO
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from PIL import Image

from app.cards import package as package_module
from app.cards.name_policy import CardNamePolicyError, apply_display_name_transform
from app.cards.package import TemplatePackageError, VerifiedTemplateStorage
from app.cards.renderer import CardRenderError, ManifestRenderer
from app.services import cards as card_service_module
from tests import test_card_package_renderer as private_renderer_tests
from tests import test_card_service as private_service_tests
from tests.synthetic_card_package import (
    build_synthetic_package,
    logical_index,
    synthetic_placeholders,
    synthetic_preview,
)


@pytest.fixture
def synthetic_storage(monkeypatch: pytest.MonkeyPatch):
    package = build_synthetic_package()
    preview = synthetic_preview()
    _, index = logical_index(package)
    monkeypatch.setattr(package_module, "EXPECTED_LOGICAL_INDEX", MappingProxyType(index))
    monkeypatch.setattr(package_module, "TEMPLATE_KEY", "synthetic_test_template")
    monkeypatch.setattr(card_service_module, "RAW_PACKAGE_SHA256", package.raw_package_sha256)
    monkeypatch.setattr(
        card_service_module, "LOGICAL_PACKAGE_SHA256", package.logical_package_sha256
    )
    monkeypatch.setattr(card_service_module, "MANIFEST_SHA256", package.manifest_sha256)
    monkeypatch.setattr(
        card_service_module,
        "APPROVED_PREVIEW_SHA256",
        package.approved_preview_sha256,
    )
    monkeypatch.setattr(VerifiedTemplateStorage, "load", lambda self: package)
    monkeypatch.setattr(VerifiedTemplateStorage, "read_approved_preview", lambda self: preview)
    return package, preview


def test_generic_renderer_runs_without_private_assets(synthetic_storage) -> None:
    package, _ = synthetic_storage
    renderer = ManifestRenderer()
    placeholders = synthetic_placeholders()

    first = renderer.render(
        package,
        placeholders,
        photo_fit="COVER",
        photo_focus_x=0.5,
        photo_focus_y=0.5,
        photo_zoom=1.0,
    )
    second = renderer.render(
        package,
        placeholders,
        photo_fit="COVER",
        photo_focus_x=0.5,
        photo_focus_y=0.5,
        photo_zoom=1.0,
    )

    assert first == second
    assert set(renderer.measure_text_layouts(package, placeholders)) == {
        "guest_name",
        "couple_names",
        "date_weekday",
        "date_month",
        "date_day",
        "date_year",
        "event_time",
        "event_venue",
        "event_address",
    }
    with Image.open(BytesIO(first)) as image:
        assert image.size == (1240, 1748)


def test_repository_safe_text_alignment_branches_remain_contained(
    synthetic_storage,
) -> None:
    package, _ = synthetic_storage
    manifest = copy.deepcopy(dict(package.manifest))
    couple_layer = next(layer for layer in manifest["layers"] if layer["id"] == "couple_names")
    couple_layer["content"] = [{"type": "LITERAL", "text": "HARBOR"}]
    couple_layer["style"]["horizontal_alignment"] = "LEFT"
    candidate = copy.copy(package)
    object.__setattr__(candidate, "manifest", MappingProxyType(manifest))

    layouts = ManifestRenderer().measure_text_layouts(candidate, synthetic_placeholders())
    left = layouts["couple_names"]
    right = layouts["date_weekday"]

    assert left.origin_x == left.frame_bounds[0]
    assert left.frame_bounds[0] <= left.ink_bounds[0] <= left.ink_bounds[2]
    assert left.ink_bounds[2] <= left.frame_bounds[2]
    assert right.origin_x + right.advance_width == pytest.approx(right.frame_bounds[2])
    assert right.frame_bounds[0] <= right.ink_bounds[0] <= right.ink_bounds[2]
    assert right.ink_bounds[2] <= right.frame_bounds[2]


@pytest.mark.parametrize(
    ("fit", "focus_x", "focus_y", "zoom"),
    [
        ("COVER", 0.0, 0.0, 1.0),
        ("COVER", 1.0, 1.0, 2.0),
        ("CONTAIN", 0.5, 0.5, 1.0),
    ],
)
def test_repository_safe_photo_controls(
    synthetic_storage,
    fit: str,
    focus_x: float,
    focus_y: float,
    zoom: float,
) -> None:
    package, _ = synthetic_storage
    result = ManifestRenderer().render(
        package,
        synthetic_placeholders(),
        photo_fit=fit,
        photo_focus_x=focus_x,
        photo_focus_y=focus_y,
        photo_zoom=zoom,
    )
    assert result.startswith(b"\x89PNG")


def test_existing_renderer_failure_scenarios_are_repository_safe(synthetic_storage) -> None:
    private_renderer_tests.test_renderer_helpers_fail_closed_on_invalid_generic_manifest_values()
    for content, message in (
        (b"<!DOCTYPE svg><svg viewBox='0 0 1 1'/>", "safe subset"),
        (b"<svg", "invalid"),
        (b"<rect/>", "root"),
        (b"<svg viewBox='0 0 1'/>", "viewBox"),
        (b"<svg viewBox='0 0 0 1'/>", "viewBox"),
        (b"<svg viewBox='0 0 1 1'><circle/></svg>", "element"),
    ):
        private_renderer_tests.test_svg_subset_rejects_unsafe_or_unsupported_content(
            content, message
        )
    for controls in (
        ("STRETCH", 0.5, 0.5, 1.0, "photo fit"),
        ("COVER", -0.1, 0.5, 1.0, "photo focus"),
        ("COVER", 0.5, 0.5, 3.1, "photo zoom"),
        ("CONTAIN", 0.5, 0.5, 1.1, "CONTAIN"),
    ):
        private_renderer_tests.test_renderer_rejects_controls_outside_manifest_bounds(*controls)


def test_manifest_validation_scenarios_are_repository_safe(synthetic_storage) -> None:
    private_renderer_tests.test_manifest_validator_rejects_every_closed_contract_boundary()
    package, _ = synthetic_storage
    manifest = copy.deepcopy(dict(package.manifest))
    layers = manifest["layers"]
    assert isinstance(layers, list)
    assert isinstance(layers[0], dict)
    layers[0]["frame"] = {"x": 0, "y": 0, "width": 1241, "height": 1}
    with pytest.raises(TemplatePackageError, match="outside"):
        package_module._validate_manifest(manifest)


def test_strict_loader_accepts_only_runtime_synthetic_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = build_synthetic_package()
    resources, index = logical_index(package)
    root = tmp_path / "templates"
    package_path = root / "synthetic" / "v1" / "package.zip"
    preview_path = root / "synthetic" / "v1" / "preview.png"
    package_path.parent.mkdir(parents=True)
    with ZipFile(package_path, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in resources.items():
            archive.writestr(name, content)
    preview_path.write_bytes(synthetic_preview())

    monkeypatch.setattr(package_module, "PACKAGE_STORAGE_KEY", "synthetic/v1/package.zip")
    monkeypatch.setattr(package_module, "PREVIEW_STORAGE_KEY", "synthetic/v1/preview.png")
    monkeypatch.setattr(package_module, "TEMPLATE_KEY", "synthetic_test_template")
    monkeypatch.setattr(package_module, "EXPECTED_LOGICAL_INDEX", MappingProxyType(index))
    monkeypatch.setattr(
        package_module,
        "RAW_PACKAGE_SHA256",
        hashlib.sha256(package_path.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        package_module,
        "APPROVED_PREVIEW_SHA256",
        hashlib.sha256(preview_path.read_bytes()).hexdigest(),
    )
    package_module._load_verified_package.cache_clear()
    storage = VerifiedTemplateStorage(root)

    loaded = storage.load()
    assert storage.load() is loaded
    assert storage.read_approved_preview() == preview_path.read_bytes()

    package_module._load_verified_package.cache_clear()
    package_path.write_bytes(b"not-a-zip")
    monkeypatch.setattr(
        package_module,
        "RAW_PACKAGE_SHA256",
        hashlib.sha256(package_path.read_bytes()).hexdigest(),
    )
    with pytest.raises(TemplatePackageError, match="readable ZIP"):
        storage.load()
    with pytest.raises(TemplatePackageError, match="storage key"):
        storage._resolve("../outside")


@pytest.mark.parametrize(
    ("value", "transform", "message"),
    [
        ("single string", "FIRST_NAME_EACH", "ordered name list"),
        (["Valid Name", 1], "FIRST_NAME_EACH", "invalid name"),
        ([], "FIRST_NAME_EACH", "invalid name"),
        (1, "FIRST_AND_LAST", "requires text"),
        ("Dr.", "FIRST_AND_LAST", "no name tokens"),
        ("Name", "UNKNOWN", "unsupported"),
    ],
)
def test_display_name_transform_rejects_invalid_inputs(
    value: object, transform: str, message: str
) -> None:
    with pytest.raises(CardNamePolicyError, match=message):
        apply_display_name_transform(value, transform)


def test_m6_service_scenarios_run_with_synthetic_package(
    tmp_path: Path,
    synthetic_storage,
) -> None:
    _, preview = synthetic_storage
    private_service_tests.test_permanent_generation_uses_protected_qr_is_idempotent_and_regenerates(
        tmp_path / "permanent"
    )
    _exercise_repository_safe_service_lifecycle(tmp_path / "lifecycle", preview)
    private_service_tests.test_missing_inactive_superseded_and_cross_event_inputs_are_rejected(
        tmp_path / "missing"
    )
    private_service_tests.test_database_failure_cleans_new_card_and_photo_artifacts(
        tmp_path / "persistence"
    )
    private_service_tests.test_bounded_template_photo_and_design_conflicts_are_explicit(
        tmp_path / "conflicts"
    )
    private_service_tests.test_generation_bulk_storage_integrity_and_terminal_event_errors(
        tmp_path / "terminal"
    )
    private_service_tests.test_registration_and_commit_integrity_conflicts_fail_closed(
        tmp_path / "registration"
    )


def _exercise_repository_safe_service_lifecycle(tmp_path: Path, preview: bytes) -> None:
    actor_id = uuid4()
    repository = private_service_tests.FakeCardRepository()
    bridge = private_service_tests.FakeQrImageBridge()
    service = private_service_tests._service(tmp_path, repository, bridge)
    event = private_service_tests._event(actor_id)
    repository.events[event.id] = event
    photo, revision, approval = private_service_tests._prepare_approved_design(
        service, repository, event, actor_id
    )

    assert service.register_verified_template(administrator_id=actor_id)[1].version == 10
    assert len(service.get_template("wedding_floral", 10)[1].logical_package_sha256) == 64
    assert len(service.list_templates(limit=10, offset=0)) == 1
    assert service.list_event_photos(event.id, limit=10, offset=0) == [photo]
    assert service.read_event_photo(event.id, photo.id).startswith(b"\x89PNG")
    assert service.get_latest_design_revision(event.id) is revision
    assert service.list_design_revisions(event.id, limit=10, offset=0) == [revision]
    assert (
        service.approve_design_revision(event.id, revision.id, administrator_id=actor_id)
        is approval
    )
    assert service.render_design_preview(event.id, revision.id).startswith(b"\x89PNG")
    assert service.read_approved_preview() == preview

    guests = [
        private_service_tests._guest(event.id, "Synthetic Guest One"),
        private_service_tests._guest(event.id, "Synthetic Guest Two"),
    ]
    for index, guest in enumerate(guests):
        repository.guests[guest.id] = guest
        payload = chr(ord("C") + index) * 32
        credential, image = private_service_tests._credential(event.id, guest.id, actor_id, payload)
        repository.credentials[guest.id] = credential
        bridge.images[credential.id] = image
    cards = service.generate_event_cards(
        event.id,
        administrator_id=actor_id,
        guest_ids=[guest.id for guest in guests],
        limit=100,
    )
    assert len(cards) == 2
    assert (
        service.list_cards(
            event.id,
            status=private_service_tests.GeneratedCardStatus.CURRENT,
            limit=100,
            offset=0,
        )
        == cards
    )
    export = service.export_cards(event.id, [card.id for card in cards])
    with ZipFile(BytesIO(export)) as archive:
        assert archive.namelist() == [f"card-{card.id}.png" for card in cards]

    revoked = service.revoke_design_approval(event.id, approval.id, administrator_id=actor_id)
    assert revoked.status is private_service_tests.DesignApprovalStatus.REVOKED


def test_renderer_rejects_invalid_layer_contracts(synthetic_storage) -> None:
    package, _ = synthetic_storage
    placeholders = synthetic_placeholders()

    def render_with(mutator) -> None:
        manifest = copy.deepcopy(dict(package.manifest))
        mutator(manifest)
        candidate = copy.copy(package)
        object.__setattr__(candidate, "manifest", MappingProxyType(manifest))
        ManifestRenderer().render(
            candidate,
            placeholders,
            photo_fit="COVER",
            photo_focus_x=0.5,
            photo_focus_y=0.5,
            photo_zoom=1.0,
        )

    mutations = (
        (lambda manifest: manifest["canvas"].update(background={"type": "IMAGE"}), "background"),
        (lambda manifest: manifest.update(layers={}), "layers"),
        (lambda manifest: manifest["layers"][0].update(type="UNKNOWN"), "unsupported"),
        (lambda manifest: manifest["layers"][3].update(shape="CIRCLE"), "shape"),
        (lambda manifest: manifest["layers"][4].update(style="DASHED"), "line style"),
        (lambda manifest: manifest["layers"][5].update(opacity=2), "opacity"),
        (
            lambda manifest: manifest["assets"]["fill"].update(kind="UNKNOWN"),
            "asset kind",
        ),
        (
            lambda manifest: manifest["layers"][8]["style"].update(vertical_alignment="TOP"),
            "vertical alignment",
        ),
        (
            lambda manifest: manifest["layers"][8]["fitting"].update(character_limit=1),
            "character limit",
        ),
    )
    for mutate, message in mutations:
        with pytest.raises(CardRenderError, match=message):
            render_with(mutate)

    no_text = copy.deepcopy(dict(package.manifest))
    no_text["layers"] = {}
    candidate = copy.copy(package)
    object.__setattr__(candidate, "manifest", MappingProxyType(no_text))
    with pytest.raises(CardRenderError, match="layers"):
        ManifestRenderer().measure_text_layouts(candidate, placeholders)


def test_manifest_json_is_synthetic_and_contains_no_private_values(synthetic_storage) -> None:
    package, _ = synthetic_storage
    encoded = json.dumps(dict(package.manifest), sort_keys=True)
    assert package.manifest["template_key"] == "synthetic_test_template"
    assert "bottom_left_floral" not in encoded
