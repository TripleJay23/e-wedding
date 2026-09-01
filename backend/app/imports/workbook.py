"""Bounded XLSX package inspection, structure detection, and row normalization."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.worksheet.worksheet import Worksheet

from app.models.event import GuestFieldKey
from app.services.guest_validation import (
    GuestFieldError,
    GuestFieldPolicy,
    error,
    guest_identity_fingerprints,
    validate_guest_values,
)

MAX_ZIP_ENTRIES = 2_000
MAX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_ENTRY_BYTES = 20 * 1024 * 1024
MAX_COMPRESSION_RATIO = 250
MAX_WORKSHEET_ROWS = 10_000
MAX_WORKSHEET_COLUMNS = 100
MAX_HEADER_SCAN_ROWS = 100

_REQUIRED_XLSX_PARTS = {
    "[Content_Types].xml",
    "_rels/.rels",
    "xl/workbook.xml",
}
_FORBIDDEN_PREFIXES = (
    "xl/externalLinks/",
    "xl/embeddings/",
    "xl/activeX/",
    "customUI/",
)
_FORBIDDEN_PARTS = {"xl/vbaProject.bin"}
_HEADER_PUNCTUATION = re.compile(r"[^a-z0-9]+")
_FOOTER_TOTAL_TOKENS = frozenset({"total", "totals"})
_FOOTER_SUMMARY_TOKENS = frozenset({"summary"})
_FOOTER_GUEST_TOKENS = frozenset({"guest", "guests"})
_FOOTER_BENIGN_TOKENS = frozenset(
    {
        "count",
        "grand",
        "guest",
        "guests",
        "no",
        "number",
        "of",
        "overall",
        "summary",
        "total",
        "totals",
    }
)

HEADER_ALIASES: dict[GuestFieldKey, frozenset[str]] = {
    GuestFieldKey.FULL_NAME: frozenset({"full name", "guest", "guest name", "name"}),
    GuestFieldKey.PHONE_NUMBER: frozenset(
        {"contact number", "mobile", "mobile number", "phone", "phone number", "telephone"}
    ),
    GuestFieldKey.EMAIL: frozenset({"email", "email address", "e mail"}),
    GuestFieldKey.INVITATION_TYPE: frozenset(
        {"guest type", "invitation", "invitation type", "invite type"}
    ),
    GuestFieldKey.ALLOWED_COMPANIONS: frozenset(
        {
            "allowed companions",
            "companions",
            "number of companions",
            "plus one",
            "plus ones",
        }
    ),
    GuestFieldKey.TABLE_NUMBER: frozenset({"table", "table no", "table number", "table number no"}),
    GuestFieldKey.CATEGORY: frozenset({"category", "group", "guest category"}),
}


class WorkbookRejectedError(ValueError):
    """A bounded client-safe XLSX rejection."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ParsedGuestRow:
    """One actual guest row normalized without retaining unrelated cell data."""

    source_row_number: int
    values: dict[str, str | int | None]
    normalization_errors: list[GuestFieldError]
    validation_errors: list[GuestFieldError]
    identity_fingerprints: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParsedWorkbook:
    """Privacy-minimal deterministic workbook structure and candidate rows."""

    sheet_name: str
    header_row_number: int
    column_mapping: dict[str, int]
    ignored_rows: tuple[dict[str, int | str], ...]
    rows: tuple[ParsedGuestRow, ...]


@dataclass(frozen=True, slots=True)
class _HeaderCandidate:
    worksheet: Worksheet
    row_number: int
    mapping: dict[GuestFieldKey, int]

    @property
    def score(self) -> int:
        return len(self.mapping)


class WorkbookParser:
    """Parse one bounded XLSX upload under an immutable event-field policy."""

    def __init__(self, *, maximum_candidate_rows: int) -> None:
        self._maximum_candidate_rows = maximum_candidate_rows

    def parse(
        self,
        workbook_bytes: bytes,
        policies: list[GuestFieldPolicy],
        event_companion_maximum: int,
    ) -> ParsedWorkbook:
        """Inspect, understand, normalize, and validate a workbook in memory."""
        _inspect_xlsx_package(workbook_bytes)
        try:
            workbook = load_workbook(
                BytesIO(workbook_bytes),
                read_only=False,
                data_only=False,
                keep_links=False,
            )
        except Exception as exc:
            raise WorkbookRejectedError(
                "MALFORMED_WORKBOOK", "workbook could not be read safely"
            ) from exc

        try:
            visible_sheets = [
                sheet for sheet in workbook.worksheets if sheet.sheet_state == "visible"
            ]
            if not visible_sheets:
                raise WorkbookRejectedError(
                    "NO_VISIBLE_WORKSHEET", "workbook has no visible worksheet"
                )
            for sheet in visible_sheets:
                if sheet.max_row > MAX_WORKSHEET_ROWS or sheet.max_column > MAX_WORKSHEET_COLUMNS:
                    raise WorkbookRejectedError(
                        "WORKSHEET_LIMIT_EXCEEDED", "worksheet dimensions exceed import limits"
                    )

            candidate = self._detect_header(visible_sheets, policies)
            return self._parse_sheet(candidate, policies, event_companion_maximum)
        finally:
            workbook.close()

    def _detect_header(
        self,
        worksheets: list[Worksheet],
        policies: list[GuestFieldPolicy],
    ) -> _HeaderCandidate:
        required = {policy.field_key for policy in policies if policy.enabled and policy.required}
        enabled = {policy.field_key for policy in policies if policy.enabled}
        aliases = _enabled_alias_lookup(enabled)
        per_sheet: list[_HeaderCandidate] = []
        best_partial: set[GuestFieldKey] = set()

        for worksheet in worksheets:
            sheet_candidates: list[_HeaderCandidate] = []
            for row_number in range(1, min(worksheet.max_row, MAX_HEADER_SCAN_ROWS) + 1):
                mapping, duplicated = _map_header_row(worksheet[row_number], aliases)
                if len(mapping) > len(best_partial):
                    best_partial = set(mapping)
                if duplicated or not required.issubset(mapping) or len(mapping) < 2:
                    continue
                sheet_candidates.append(_HeaderCandidate(worksheet, row_number, mapping))
            if sheet_candidates:
                best_score = max(item.score for item in sheet_candidates)
                earliest = min(
                    (item for item in sheet_candidates if item.score == best_score),
                    key=lambda item: item.row_number,
                )
                per_sheet.append(earliest)

        if not per_sheet:
            missing = sorted(field.value for field in required - best_partial)
            if missing:
                raise WorkbookRejectedError(
                    "MISSING_REQUIRED_COLUMNS",
                    f"required configured columns are missing: {', '.join(missing)}",
                )
            raise WorkbookRejectedError(
                "NO_GUEST_TABLE", "no unambiguous configured guest table was detected"
            )

        best_score = max(item.score for item in per_sheet)
        finalists = [item for item in per_sheet if item.score == best_score]
        if len(finalists) != 1:
            raise WorkbookRejectedError(
                "AMBIGUOUS_GUEST_TABLE", "multiple visible worksheets contain equal guest tables"
            )
        return finalists[0]

    def _parse_sheet(
        self,
        candidate: _HeaderCandidate,
        policies: list[GuestFieldPolicy],
        event_companion_maximum: int,
    ) -> ParsedWorkbook:
        worksheet = candidate.worksheet
        ignored: list[dict[str, int | str]] = []
        for row_number in range(1, candidate.row_number):
            reason = "BLANK" if _row_is_blank(worksheet[row_number]) else "PRESENTATION"
            ignored.append({"row_number": row_number, "reason": reason})

        parsed_rows: list[ParsedGuestRow] = []
        seen_valid_fingerprints: set[str] = set()
        aliases = _enabled_alias_lookup(set(candidate.mapping))
        merged_ranges = tuple(worksheet.merged_cells.ranges)
        last_populated_row = _last_populated_row(worksheet, candidate.row_number + 1)
        seen_candidate = False

        for row_number in range(candidate.row_number + 1, worksheet.max_row + 1):
            cells = worksheet[row_number]
            reason = _ignored_row_reason(
                cells,
                row_number=row_number,
                selected_mapping=candidate.mapping,
                aliases=aliases,
                merged_ranges=merged_ranges,
                seen_candidate=seen_candidate,
                is_terminal=row_number == last_populated_row,
            )
            if reason is not None:
                ignored.append({"row_number": row_number, "reason": reason})
                continue

            seen_candidate = True
            values, normalization_errors = _normalize_candidate(cells, candidate.mapping, policies)
            validation_errors = validate_guest_values(
                values,
                policies,
                event_companion_maximum,
                prior_errors=normalization_errors,
            )
            fingerprints = guest_identity_fingerprints(values)
            if not validation_errors and set(fingerprints) & seen_valid_fingerprints:
                validation_errors.append(
                    error(None, "DUPLICATE_IN_WORKBOOK", "row duplicates an earlier valid row")
                )
            if not validation_errors:
                seen_valid_fingerprints.update(fingerprints)

            parsed_rows.append(
                ParsedGuestRow(
                    source_row_number=row_number,
                    values=values,
                    normalization_errors=normalization_errors,
                    validation_errors=validation_errors,
                    identity_fingerprints=fingerprints,
                )
            )
            if len(parsed_rows) > self._maximum_candidate_rows:
                raise WorkbookRejectedError(
                    "CANDIDATE_LIMIT_EXCEEDED", "workbook contains too many guest rows"
                )

        if not parsed_rows:
            raise WorkbookRejectedError("NO_GUEST_ROWS", "guest table contains no candidate rows")

        return ParsedWorkbook(
            sheet_name=worksheet.title,
            header_row_number=candidate.row_number,
            column_mapping={field.value: column for field, column in candidate.mapping.items()},
            ignored_rows=tuple(ignored),
            rows=tuple(parsed_rows),
        )


def _inspect_xlsx_package(workbook_bytes: bytes) -> None:
    if not workbook_bytes.startswith(b"PK"):
        raise WorkbookRejectedError("UNSUPPORTED_WORKBOOK", "upload is not an XLSX package")
    try:
        with ZipFile(BytesIO(workbook_bytes)) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_ZIP_ENTRIES:
                raise WorkbookRejectedError(
                    "UNSAFE_XLSX_PACKAGE", "XLSX package entry count is unsafe"
                )

            names: set[str] = set()
            total_uncompressed = 0
            for entry in entries:
                normalized_name = entry.filename.replace("\\", "/")
                path = PurePosixPath(normalized_name)
                if (
                    normalized_name in names
                    or path.is_absolute()
                    or ".." in path.parts
                    or any(part == "" for part in path.parts)
                    or entry.flag_bits & 0x1
                ):
                    raise WorkbookRejectedError(
                        "UNSAFE_XLSX_PACKAGE", "XLSX package contains an unsafe entry"
                    )
                names.add(normalized_name)
                total_uncompressed += entry.file_size
                ratio = entry.file_size / max(entry.compress_size, 1)
                if (
                    entry.file_size > MAX_ENTRY_BYTES
                    or total_uncompressed > MAX_UNCOMPRESSED_BYTES
                    or ratio > MAX_COMPRESSION_RATIO
                ):
                    raise WorkbookRejectedError(
                        "UNSAFE_XLSX_PACKAGE", "XLSX package expansion exceeds safety limits"
                    )

            if not _REQUIRED_XLSX_PARTS.issubset(names) or not any(
                name.startswith("xl/worksheets/sheet") and name.endswith(".xml") for name in names
            ):
                raise WorkbookRejectedError(
                    "MALFORMED_WORKBOOK", "XLSX package is missing required workbook parts"
                )
            if names & _FORBIDDEN_PARTS or any(
                name.startswith(prefix) for name in names for prefix in _FORBIDDEN_PREFIXES
            ):
                raise WorkbookRejectedError(
                    "UNSAFE_XLSX_PACKAGE", "XLSX package contains unsupported active content"
                )
    except BadZipFile as exc:
        raise WorkbookRejectedError(
            "MALFORMED_WORKBOOK", "workbook could not be read safely"
        ) from exc


def _enabled_alias_lookup(enabled: set[GuestFieldKey]) -> dict[str, GuestFieldKey]:
    lookup: dict[str, GuestFieldKey] = {}
    for field_key in enabled:
        aliases = set(HEADER_ALIASES[field_key]) | {
            normalize_header(field_key.value),
            normalize_header(field_key.value.replace("_", " ")),
        }
        for alias in aliases:
            existing = lookup.get(alias)
            if existing is not None and existing is not field_key:
                raise RuntimeError("configured header aliases are ambiguous")
            lookup[alias] = field_key
    return lookup


def normalize_header(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("&", " and ")
    return " ".join(_HEADER_PUNCTUATION.sub(" ", normalized).split())


def _map_header_row(
    cells: tuple[Cell, ...],
    aliases: dict[str, GuestFieldKey],
) -> tuple[dict[GuestFieldKey, int], bool]:
    mapping: dict[GuestFieldKey, int] = {}
    duplicated = False
    for cell in cells:
        field_key = aliases.get(normalize_header(cell.value))
        if field_key is None:
            continue
        if field_key in mapping:
            duplicated = True
        else:
            mapping[field_key] = cell.column
    return mapping, duplicated


def _ignored_row_reason(
    cells: tuple[Cell, ...],
    *,
    row_number: int,
    selected_mapping: dict[GuestFieldKey, int],
    aliases: dict[str, GuestFieldKey],
    merged_ranges: tuple[Any, ...],
    seen_candidate: bool,
    is_terminal: bool,
) -> str | None:
    populated = [cell for cell in cells if cell.value is not None and cell.value != ""]
    if not populated:
        return "BLANK"

    header_mapping, duplicated = _map_header_row(cells, aliases)
    if not duplicated and set(header_mapping) == set(selected_mapping):
        return "REPEATED_HEADER"

    if len(populated) == 1 and any(
        merged.min_row <= row_number <= merged.max_row
        and merged.min_col <= populated[0].column <= merged.max_col
        and merged.max_col > merged.min_col
        for merged in merged_ranges
    ):
        return "PRESENTATION"

    if seen_candidate and is_terminal and _is_sparse_aggregate_footer(populated, selected_mapping):
        return "FOOTER"
    return None


def _row_is_blank(cells: tuple[Cell, ...]) -> bool:
    return all(cell.value is None or cell.value == "" for cell in cells)


def _last_populated_row(worksheet: Worksheet, first_row: int) -> int | None:
    for row_number in range(worksheet.max_row, first_row - 1, -1):
        if not _row_is_blank(worksheet[row_number]):
            return row_number
    return None


def _is_sparse_aggregate_footer(
    populated: list[Cell],
    selected_mapping: dict[GuestFieldKey, int],
) -> bool:
    """Recognize one aggregate formula plus one deterministic summary label."""
    if len(populated) != 2:
        return False

    aggregate_column = selected_mapping.get(GuestFieldKey.ALLOWED_COMPANIONS)
    if aggregate_column is None:
        return False
    aggregate_cells = [cell for cell in populated if cell.column == aggregate_column]
    if len(aggregate_cells) != 1 or not _cell_is_formula(aggregate_cells[0]):
        return False

    summary_cells = [cell for cell in populated if cell is not aggregate_cells[0]]
    return len(summary_cells) == 1 and _is_structural_footer_label(summary_cells[0].value)


def _is_structural_footer_label(value: Any) -> bool:
    """Recognize only compositional labels made entirely from benign summary tokens."""
    tokens = tuple(normalize_header(value).split())
    if not tokens or not set(tokens).issubset(_FOOTER_BENIGN_TOKENS):
        return False
    if set(tokens) & (_FOOTER_TOTAL_TOKENS | _FOOTER_SUMMARY_TOKENS):
        return True
    return "count" in tokens and bool(set(tokens) & _FOOTER_GUEST_TOKENS)


def _cell_is_formula(cell: Cell) -> bool:
    return cell.data_type == "f" or (isinstance(cell.value, str) and cell.value.startswith("="))


def _normalize_candidate(
    cells: tuple[Cell, ...],
    mapping: dict[GuestFieldKey, int],
    policies: list[GuestFieldPolicy],
) -> tuple[dict[str, str | int | None], list[GuestFieldError]]:
    values: dict[str, str | int | None] = {}
    errors: list[GuestFieldError] = []
    cells_by_column = {cell.column: cell for cell in cells}

    for policy in policies:
        field_key = policy.field_key
        if not policy.enabled:
            values[field_key.value] = 0 if field_key is GuestFieldKey.ALLOWED_COMPANIONS else None
            continue
        column = mapping.get(field_key)
        if column is None:
            values[field_key.value] = 0 if field_key is GuestFieldKey.ALLOWED_COMPANIONS else None
            continue
        cell = cells_by_column[column]
        value, field_error = _normalize_cell(field_key, cell)
        values[field_key.value] = value
        if field_error is not None:
            errors.append(field_error)

    return values, errors


def _normalize_cell(
    field_key: GuestFieldKey,
    cell: Cell,
) -> tuple[str | int | None, GuestFieldError | None]:
    value = cell.value
    if value is None or value == "":
        default = 0 if field_key is GuestFieldKey.ALLOWED_COMPANIONS else None
        return default, None
    if _cell_is_formula(cell):
        return None, error(
            field_key, "FORMULA_NOT_ALLOWED", "formulas are not allowed in guest rows"
        )
    if isinstance(value, (bool, date, datetime, time)):
        return None, error(field_key, "UNSUPPORTED_VALUE_TYPE", "cell value type is unsupported")

    if field_key is GuestFieldKey.ALLOWED_COMPANIONS:
        if isinstance(value, int):
            return value, None
        if isinstance(value, float) and math.isfinite(value) and value.is_integer():
            return int(value), None
        return None, error(
            field_key,
            "INVALID_COMPANION_COUNT",
            "allowed companions must be an integer",
        )

    if field_key is GuestFieldKey.TABLE_NUMBER and isinstance(value, (int, float)):
        if isinstance(value, int):
            return str(value), None
        if math.isfinite(value) and value.is_integer():
            return str(int(value)), None
        return None, error(
            field_key,
            "LOSSY_VALUE_NOT_ALLOWED",
            "table number must be losslessly representable as text",
        )

    if not isinstance(value, str):
        return None, error(
            field_key,
            "LOSSY_VALUE_NOT_ALLOWED",
            "text fields must use lossless text cells",
        )
    normalized = value.strip()
    if not normalized:
        return None, None
    if field_key is GuestFieldKey.EMAIL:
        normalized = normalized.casefold()
    return normalized, None
