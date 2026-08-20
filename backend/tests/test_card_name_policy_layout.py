"""Wedding Floral V10 display-name policy and text-layout regression tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.cards.name_policy import couple_display_name, guest_display_name
from app.cards.package import VerifiedTemplateStorage
from app.cards.renderer import ManifestRenderer, TextLayout

PRIVATE_TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "storage" / "private" / "templates"
PRIVATE_PACKAGE = (
    PRIVATE_TEMPLATE_ROOT / "wedding_floral" / "v10" / "wedding_floral_v10.template-package.zip"
)
PRIVATE_PREVIEW = (
    PRIVATE_TEMPLATE_ROOT / "wedding_floral" / "v10" / "wedding_floral_v10_default_preview.png"
)


@pytest.mark.parametrize(
    ("full_name", "expected"),
    [
        ("Jordan Jeremiah Josephat", "Jordan"),
        ("Jessica Rose Example", "Jessica"),
        ("  Dr.   Mary-Jane  Rose Smith  ", "Mary-Jane"),
    ],
)
def test_couple_display_name_uses_first_non_honorific_token(
    full_name: str,
    expected: str,
) -> None:
    assert couple_display_name(full_name) == expected


@pytest.mark.parametrize(
    ("full_name", "expected"),
    [
        ("Freddy John Mushi", "Freddy Mushi"),
        ("Asha Mohamed Ali", "Asha Ali"),
        ("John Mwakalinga", "John Mwakalinga"),
        ("Neema", "Neema"),
        ("Mr. John Michael Mushi", "John Mushi"),
        ("Mrs. Neema Asha Mushi", "Neema Mushi"),
        ("ms Neema Asha Mushi", "Neema Mushi"),
        ("MISS Neema", "Neema"),
        ("Dr Asha Mariam Ali", "Asha Ali"),
        ("Prof Asha Mariam Ali", "Asha Ali"),
        ("Mary-Jane Rose Smith", "Mary-Jane Smith"),
        ("O'Neil", "O'Neil"),
        ("  pRoF.   Asha   Mariam   O'Neil-Smith  ", "Asha O'Neil-Smith"),
    ],
)
def test_guest_display_name_uses_first_and_final_non_honorific_tokens(
    full_name: str,
    expected: str,
) -> None:
    assert guest_display_name(full_name) == expected


def _placeholders(
    couple_names: tuple[str, str] = (
        "Jordan Jeremiah Josephat",
        "Jessica Rose Example",
    ),
    guest_name: str = "Synthetic Permanent Card Guest",
    *,
    venue: str = "Synthetic Hall",
    address: str = "123 Synthetic Road",
) -> dict[str, object]:
    event_datetime = datetime(2027, 9, 29, 23, 45, tzinfo=UTC)
    return {
        "event.address": address,
        "event.couple_names": list(couple_names),
        "event.date": event_datetime,
        "event.time": event_datetime,
        "event.venue": venue,
        "guest.full_name": guest_name,
    }


def _layouts(placeholders: dict[str, object]) -> dict[str, TextLayout]:
    if not PRIVATE_PACKAGE.is_file() or not PRIVATE_PREVIEW.is_file():
        pytest.skip("exact ignored Wedding Floral V10 recovery is not present")
    package = VerifiedTemplateStorage(PRIVATE_TEMPLATE_ROOT).load()
    return dict(ManifestRenderer().measure_text_layouts(package, placeholders))


def _assert_contained(layout: TextLayout) -> None:
    frame_left, frame_top, frame_right, frame_bottom = layout.frame_bounds
    ink_left, ink_top, ink_right, ink_bottom = layout.ink_bounds
    assert frame_left <= ink_left <= ink_right <= frame_right
    assert frame_top <= ink_top <= ink_bottom <= frame_bottom


@pytest.mark.parametrize(
    ("couple_names", "expected", "expected_font_size"),
    [
        (
            ("Jordan Jeremiah Josephat", "Jessica Rose Example"),
            "Jordan & Jessica",
            158,
        ),
        (
            ("Alexandria Catherine Example", "Bartholomew James Example"),
            "Alexandria & Bartholomew",
            110,
        ),
        (("Jo Example", "Li Example"), "Jo & Li", 158),
    ],
)
def test_couple_script_baseline_and_bounds_match_the_v10_region(
    couple_names: tuple[str, str],
    expected: str,
    expected_font_size: int,
) -> None:
    layouts = _layouts(_placeholders(couple_names=couple_names))
    heading = layouts["invitation_heading"]
    couple = layouts["couple_names"]

    assert couple.text == expected
    assert couple.font_size == expected_font_size
    assert 70 <= couple.font_size <= 158
    assert couple.ink_bounds[1] >= 875
    assert couple.baseline_y >= 980
    assert heading.ink_bounds[3] < couple.ink_bounds[1]
    assert couple.ink_bounds[3] < 1073
    _assert_contained(heading)
    _assert_contained(couple)


@pytest.mark.parametrize(
    ("guest_name", "display_name"),
    [
        ("Freddy John Mushi", "FREDDY MUSHI"),
        ("Synthetic Permanent Card Guest", "SYNTHETIC GUEST"),
    ],
)
def test_guest_display_policy_preserves_invitation_typography_hierarchy(
    guest_name: str,
    display_name: str,
) -> None:
    layouts = _layouts(_placeholders(guest_name=guest_name))
    first_line = layouts["invitation_first_line"]
    second_line = layouts["invitation_second_line"]

    assert first_line.text == f"INVITE YOU {display_name} TO JOIN"
    assert first_line.font_size == 38
    assert second_line.font_size == 40
    assert second_line.font_size - first_line.font_size <= 2
    assert first_line.ink_bounds[3] < second_line.ink_bounds[1]
    _assert_contained(first_line)
    _assert_contained(second_line)


def test_long_event_text_remains_bounded_after_name_layout_correction() -> None:
    layouts = _layouts(
        _placeholders(
            venue="A Relatively Long Synthetic Wedding Venue",
            address="A Very Long Synthetic Address For Regression",
        )
    )

    expected_ranges = {
        "date_weekday": (32, 40),
        "date_month": (32, 40),
        "date_day": (96, 110),
        "date_year": (34, 40),
        "event_time": (32, 40),
        "event_venue": (26, 38),
        "event_address": (26, 38),
    }
    for layer_id, (minimum, maximum) in expected_ranges.items():
        layout = layouts[layer_id]
        assert minimum <= layout.font_size <= maximum
        _assert_contained(layout)
