"""Synthetic-only M4 XLSX security, structure, normalization, and validation tests."""

from __future__ import annotations

from datetime import date, time
from io import BytesIO
from math import inf, nan
from time import perf_counter
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook

from app.imports.workbook import WorkbookParser, WorkbookRejectedError, _normalize_cell
from app.models.event import DEFAULT_GUEST_FIELD_CONFIGURATION, GuestFieldKey
from app.services.guest_validation import GuestFieldPolicy


def default_policies() -> list[GuestFieldPolicy]:
    return [
        GuestFieldPolicy(field_key, enabled, required)
        for field_key, enabled, required in DEFAULT_GUEST_FIELD_CONFIGURATION
    ]


def save_workbook(workbook: Workbook) -> bytes:
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


def simple_workbook(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    for row in rows:
        worksheet.append(row)
    return save_workbook(workbook)


def parse(content: bytes, *, policies: list[GuestFieldPolicy] | None = None):
    return WorkbookParser(maximum_candidate_rows=5_000).parse(
        content,
        policies or default_policies(),
        2,
    )


def error_codes(parsed_row) -> set[str]:
    return {item["code"] for item in parsed_row.validation_errors}


def test_reordered_extra_columns_and_human_formatting_are_understood() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = "Guest List"
    worksheet.merge_cells("A1:H1")
    worksheet["A1"] = "Synthetic Wedding Guest Register"
    worksheet.merge_cells("A2:H2")
    worksheet["A2"] = "Please complete configured fields"
    worksheet.append([])
    worksheet.append(
        [
            "Unmanaged Notes",
            "Invitation Type",
            "Email Address",
            "Guest Name",
            "Table No.",
            "Phone",
            "Category",
            "Plus Ones",
        ]
    )
    worksheet.append(
        [
            "ignored",
            "Family",
            "SYNTHETIC.ONE@EXAMPLE.TEST",
            "Synthetic Guest One",
            "T-1",
            "+255 700 000 001",
            "Friends",
            2,
        ]
    )
    worksheet.append([])
    worksheet.merge_cells("A7:H7")
    worksheet["A7"] = "Synthetic Family Section"
    worksheet.append(
        [
            "Unmanaged Notes",
            "Invitation Type",
            "Email Address",
            "Guest Name",
            "Table No.",
            "Phone",
            "Category",
            "Plus Ones",
        ]
    )
    worksheet.append(
        [
            None,
            "Individual",
            None,
            "Synthetic Guest Two",
            None,
            "+255700000002",
            None,
            0,
        ]
    )
    worksheet.append(["Total Guests", None, None, None, None, None, None, "=COUNTA(D5:D9)"])

    result = parse(save_workbook(workbook))

    assert result.sheet_name == "Guest List"
    assert result.header_row_number == 4
    assert result.column_mapping == {
        "invitation_type": 2,
        "email": 3,
        "full_name": 4,
        "table_number": 5,
        "phone_number": 6,
        "category": 7,
        "allowed_companions": 8,
    }
    assert [row.source_row_number for row in result.rows] == [5, 9]
    assert result.rows[0].values["email"] == "synthetic.one@example.test"
    assert result.rows[0].validation_errors == []
    assert result.rows[1].validation_errors == []
    assert {item["reason"] for item in result.ignored_rows} >= {
        "PRESENTATION",
        "BLANK",
        "REPEATED_HEADER",
        "FOOTER",
    }


def test_unknown_only_row_is_preserved_as_invalid_not_discarded() -> None:
    result = parse(
        simple_workbook(
            [
                ["Full Name", "Invitation Type", "Unmanaged Notes"],
                ["Synthetic Valid", "Family", None],
                [None, None, "This row still represents unresolved workbook data"],
            ]
        )
    )

    assert len(result.rows) == 2
    assert result.rows[1].source_row_number == 3
    assert error_codes(result.rows[1]) == {"REQUIRED"}


def test_formula_guest_cells_fail_but_independently_structural_footer_is_ignored() -> None:
    result = parse(
        simple_workbook(
            [
                ["Full Name", "Invitation Type", "Allowed Companions"],
                ["Synthetic Formula Guest", '=UPPER("family")', 0],
                [None, "Closing constellation reckoning", "=COUNTA(A2:A2)"],
            ]
        )
    )

    assert len(result.rows) == 1
    assert "FORMULA_NOT_ALLOWED" in error_codes(result.rows[0])
    assert any(
        item["row_number"] == 3 and item["reason"] == "FOOTER" for item in result.ignored_rows
    )


@pytest.mark.parametrize(
    ("table_number", "expected"),
    [
        (1, "1"),
        (1.0, "1"),
        (12, "12"),
        (12.0, "12"),
        ("12", "12"),
        ("A1", "A1"),
        ("Table 7", "Table 7"),
    ],
)
def test_table_number_losslessly_normalizes_integral_numbers_and_preserves_text(
    table_number: object,
    expected: str,
) -> None:
    result = parse(
        simple_workbook(
            [
                ["Full Name", "Invitation Type", "Table Number"],
                ["Synthetic Table Guest", "Family", table_number],
            ]
        )
    )

    assert result.rows[0].values["table_number"] == expected
    assert result.rows[0].validation_errors == []


@pytest.mark.parametrize("table_number", [12.5, True])
def test_table_number_rejects_ambiguous_or_unsafe_excel_scalars(table_number: object) -> None:
    result = parse(
        simple_workbook(
            [
                ["Full Name", "Invitation Type", "Table Number"],
                ["Synthetic Unsafe Table", "Family", table_number],
            ]
        )
    )

    assert error_codes(result.rows[0]) & {
        "LOSSY_VALUE_NOT_ALLOWED",
        "UNSUPPORTED_VALUE_TYPE",
    }


@pytest.mark.parametrize("table_number", [nan, inf, -inf])
def test_table_number_rejects_non_finite_numeric_scalars(table_number: float) -> None:
    synthetic_cell = SimpleNamespace(value=table_number, data_type="n")

    value, field_error = _normalize_cell(GuestFieldKey.TABLE_NUMBER, synthetic_cell)  # type: ignore[arg-type]

    assert value is None
    assert field_error is not None
    assert field_error["code"] == "LOSSY_VALUE_NOT_ALLOWED"


@pytest.mark.parametrize(
    ("table_number", "expected_code"),
    [
        (date(2027, 1, 2), "UNSUPPORTED_VALUE_TYPE"),
        (time(14, 30), "UNSUPPORTED_VALUE_TYPE"),
        (complex(12, 1), "LOSSY_VALUE_NOT_ALLOWED"),
        (object(), "LOSSY_VALUE_NOT_ALLOWED"),
    ],
)
def test_table_number_rejects_date_time_and_unsupported_values(
    table_number: object,
    expected_code: str,
) -> None:
    synthetic_cell = SimpleNamespace(value=table_number, data_type="n")

    value, field_error = _normalize_cell(GuestFieldKey.TABLE_NUMBER, synthetic_cell)  # type: ignore[arg-type]

    assert value is None
    assert field_error is not None
    assert field_error["code"] == expected_code


def test_table_number_formula_in_genuine_guest_row_is_rejected() -> None:
    result = parse(
        simple_workbook(
            [
                ["Full Name", "Invitation Type", "Table Number"],
                ["Synthetic Formula Table", "Family", "=SUM(6,6)"],
            ]
        )
    )

    assert result.rows[0].values["table_number"] is None
    assert "FORMULA_NOT_ALLOWED" in error_codes(result.rows[0])


def test_numeric_phone_remains_rejected_when_excel_formatting_is_unrecoverable() -> None:
    result = parse(
        simple_workbook(
            [
                ["Full Name", "Invitation Type", "Phone Number", "Table Number"],
                ["Synthetic Numeric Phone", "Family", 255700000012, 12],
            ]
        )
    )

    assert result.rows[0].values["table_number"] == "12"
    assert result.rows[0].values["phone_number"] is None
    assert "LOSSY_VALUE_NOT_ALLOWED" in error_codes(result.rows[0])


@pytest.mark.parametrize(
    ("label", "formula"),
    [
        ("Attendance constellation reckoning", "=SUM(D2:D2)"),
        ("Closing roster constellation", "=COUNT(D2:D2)"),
        ("Gathering census ledger", "=COUNTA(B2:B2)"),
    ],
)
def test_terminal_sparse_aggregate_footer_is_independent_of_label_vocabulary(
    label: str,
    formula: str,
) -> None:
    result = parse(
        simple_workbook(
            [
                [
                    "Unmanaged Notes",
                    "Guest Name",
                    "Invitation Type",
                    "Allowed Companions",
                ],
                [None, "Synthetic Guest", "Family", 1],
                [label, None, None, formula],
            ]
        )
    )

    assert [row.source_row_number for row in result.rows] == [2]
    assert any(
        item["row_number"] == 3 and item["reason"] == "FOOTER" for item in result.ignored_rows
    )


def test_summary_like_text_without_aggregate_formula_remains_candidate() -> None:
    result = parse(
        simple_workbook(
            [
                ["Unmanaged Notes", "Guest Name", "Invitation Type"],
                [None, "Synthetic Guest", "Family"],
                ["Total Guests", None, None, 1],
            ]
        )
    )

    assert [row.source_row_number for row in result.rows] == [2, 3]
    assert "REQUIRED" in error_codes(result.rows[1])


def test_terminal_footer_label_in_mapped_invitation_type_is_structural() -> None:
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.merge_cells("A1:E1")
    worksheet["A1"] = "Synthetic Wedding Guest Register"
    worksheet.merge_cells("A2:E2")
    worksheet["A2"] = "Synthetic Import Preview"
    worksheet.append([])
    worksheet.append(
        [
            "Allowed Companions",
            "Full Name",
            "Unmanaged Notes",
            "Invitation Type",
            "Category",
        ]
    )
    worksheet.append([1, "Synthetic Guest One", None, "Family", "Friends"])
    worksheet.append([0, "Synthetic Guest Two", None, "Individual", None])
    worksheet.append(["=SUM(A5:A6)", None, None, "Grand Total", None])

    result = parse(save_workbook(workbook))

    assert [row.source_row_number for row in result.rows] == [5, 6]
    assert [item["reason"] for item in result.ignored_rows].count("PRESENTATION") == 2
    assert [item["reason"] for item in result.ignored_rows].count("BLANK") == 1
    assert [item["reason"] for item in result.ignored_rows].count("FOOTER") == 1


def test_terminal_unknown_footer_label_in_mapped_invitation_type_is_structural() -> None:
    result = parse(
        simple_workbook(
            [
                ["Allowed Companions", "Full Name", "Invitation Type"],
                [1, "Synthetic Guest", "Family"],
                ["=SUM(A2:A2)", None, "Uncatalogued constellation reckoning"],
            ]
        )
    )

    assert [row.source_row_number for row in result.rows] == [2]
    assert result.ignored_rows[-1] == {"row_number": 3, "reason": "FOOTER"}


def test_unknown_footer_wording_does_not_require_fuzzy_matching() -> None:
    result = parse(
        simple_workbook(
            [
                ["Allowed Companions", "Full Name", "Invitation Type"],
                [1, "Synthetic Guest", "Family"],
                ["=SUM(A2:A2)", None, "Approximate guest totals note"],
            ]
        )
    )

    assert [row.source_row_number for row in result.rows] == [2]
    assert result.ignored_rows[-1] == {"row_number": 3, "reason": "FOOTER"}


@pytest.mark.parametrize(
    "formula",
    [
        "=UPPER(A2)",
        "=SUM(A1:A1)",
        "=SUM(B2:B2)",
        "=SUM(A2:B2)",
        "=SUM(A2:A3)",
        "=SUM(AAAAAA2:AAAAAA2)",
    ],
)
def test_terminal_unrelated_or_nonpreceding_formula_remains_candidate(formula: str) -> None:
    result = parse(
        simple_workbook(
            [
                ["Allowed Companions", "Full Name", "Invitation Type"],
                [1, "Synthetic Guest", "Family"],
                [formula, None, "Closing constellation reckoning"],
            ]
        )
    )

    assert [row.source_row_number for row in result.rows] == [2, 3]
    assert "FORMULA_NOT_ALLOWED" in error_codes(result.rows[1])


@pytest.mark.parametrize(
    ("guest_field_header", "guest_evidence"),
    [
        ("Phone Number", "+255700000099"),
        ("Email", "evidence@example.test"),
        ("Table Number", "T-9"),
        ("Category", "Friends"),
    ],
)
def test_terminal_summary_label_with_guest_field_evidence_remains_candidate(
    guest_field_header: str,
    guest_evidence: str,
) -> None:
    result = parse(
        simple_workbook(
            [
                [
                    "Allowed Companions",
                    "Full Name",
                    "Invitation Type",
                    guest_field_header,
                ],
                [1, "Synthetic Guest", "Family", None],
                ["=SUM(A2:A2)", None, "Grand Total", guest_evidence],
            ]
        )
    )

    assert [row.source_row_number for row in result.rows] == [2, 3]
    assert "FORMULA_NOT_ALLOWED" in error_codes(result.rows[1])


@pytest.mark.parametrize(
    "footer_row",
    [
        ["=SUM(A2:A2)", None, "Family", None],
        ["=SUM(A2:A2)", "Synthetic Formula Guest", None, None],
        ["=SUM(A2:A2)", "Total Anderson", None, None],
        ["=SUM(A2:A2)", None, "Grand Total", "T-9"],
        ["=SUM(A2:A2)", None, "Grand Total", "Meaningful guest category"],
    ],
)
def test_terminal_formula_rows_with_meaningful_guest_evidence_remain_candidates(
    footer_row: list[object],
) -> None:
    result = parse(
        simple_workbook(
            [
                ["Allowed Companions", "Full Name", "Invitation Type", "Category"],
                [1, "Synthetic Guest", "Family", "Friends"],
                footer_row,
            ]
        )
    )

    assert [row.source_row_number for row in result.rows] == [2, 3]
    assert "FORMULA_NOT_ALLOWED" in error_codes(result.rows[1])


@pytest.mark.parametrize(
    ("guest_field_header", "guest_evidence", "expected_error"),
    [
        ("Phone Number", "+25512", "INVALID_PHONE"),
        ("Email", "incomplete@", "INVALID_EMAIL"),
    ],
)
def test_terminal_malformed_but_guest_like_evidence_remains_candidate(
    guest_field_header: str,
    guest_evidence: str,
    expected_error: str,
) -> None:
    result = parse(
        simple_workbook(
            [
                [
                    "Allowed Companions",
                    "Full Name",
                    "Invitation Type",
                    guest_field_header,
                ],
                [1, "Synthetic Guest", "Family", None],
                ["=SUM(A2:A2)", None, None, guest_evidence],
            ]
        )
    )

    assert [row.source_row_number for row in result.rows] == [2, 3]
    assert {"FORMULA_NOT_ALLOWED", expected_error} <= error_codes(result.rows[1])


def test_terminal_malformed_full_name_remains_candidate() -> None:
    result = parse(
        simple_workbook(
            [
                ["Allowed Companions", "Full Name", "Invitation Type"],
                [1, "Synthetic Guest", "Family"],
                ["=SUM(A2:A2)", "X" * 161, None],
            ]
        )
    )

    assert [row.source_row_number for row in result.rows] == [2, 3]
    assert {"FORMULA_NOT_ALLOWED", "INVALID_TEXT"} <= error_codes(result.rows[1])


def test_non_terminal_footer_signature_remains_candidate() -> None:
    result = parse(
        simple_workbook(
            [
                ["Allowed Companions", "Full Name", "Invitation Type"],
                [1, "Synthetic Guest One", "Family"],
                ["=SUM(A2:A2)", None, "Grand Total"],
                [0, "Synthetic Guest Two", "Individual"],
            ]
        )
    )

    assert [row.source_row_number for row in result.rows] == [2, 3, 4]
    assert "FORMULA_NOT_ALLOWED" in error_codes(result.rows[1])


def test_incomplete_genuine_guest_at_end_remains_candidate() -> None:
    result = parse(
        simple_workbook(
            [
                ["Allowed Companions", "Full Name", "Invitation Type"],
                [1, "Synthetic Guest", "Family"],
                [0, "Synthetic Needs Correction", None],
            ]
        )
    )

    assert [row.source_row_number for row in result.rows] == [2, 3]
    assert "REQUIRED" in error_codes(result.rows[1])


def test_table_and_companion_fields_keep_explicit_semantics() -> None:
    result = parse(
        simple_workbook(
            [
                ["Full Name", "Invitation Type", "Allowed Companions", "Table Number"],
                ["Synthetic Semantic Guest", "Family", 2, 20],
            ]
        )
    )

    assert result.rows[0].values["allowed_companions"] == 2
    assert result.rows[0].values["table_number"] == "20"
    assert result.rows[0].validation_errors == []


def test_sparse_or_formula_guest_evidence_is_never_hidden_as_footer() -> None:
    result = parse(
        simple_workbook(
            [
                [
                    "Unmanaged Notes",
                    "Guest Name",
                    "Invitation Type",
                    "Allowed Companions",
                    "Category",
                ],
                [None, "Formula Guest", "Family", "=SUM(1,1)", None],
                [None, "Missing Type Guest", None, 0, None],
                ["Aggregate Before Guest", None, None, "=SUM(D2:D3)", None],
                [None, None, None, 0, "Incomplete guest evidence"],
            ]
        )
    )

    assert [row.source_row_number for row in result.rows] == [2, 3, 4, 5]
    assert "FORMULA_NOT_ALLOWED" in error_codes(result.rows[0])
    assert "REQUIRED" in error_codes(result.rows[1])
    assert "FORMULA_NOT_ALLOWED" in error_codes(result.rows[2])
    assert "REQUIRED" in error_codes(result.rows[3])


@pytest.mark.parametrize("companions", [0, 1, 2])
def test_companion_values_within_system_and_event_ceiling_are_valid(companions: int) -> None:
    result = parse(
        simple_workbook(
            [
                ["Full Name", "Invitation Type", "Allowed Companions"],
                [f"Synthetic Guest {companions}", "Family", companions],
            ]
        )
    )
    assert result.rows[0].validation_errors == []


@pytest.mark.parametrize("companions", [-1, 3, 1.5])
def test_invalid_companion_values_remain_visible(companions: float) -> None:
    result = parse(
        simple_workbook(
            [
                ["Full Name", "Invitation Type", "Allowed Companions"],
                ["Synthetic Invalid Companion", "Family", companions],
            ]
        )
    )
    assert error_codes(result.rows[0]) & {
        "INVALID_COMPANION_COUNT",
        "COMPANION_LIMIT_EXCEEDED",
    }


def test_invalid_phone_email_invitation_and_lossy_numeric_text_are_actionable() -> None:
    result = parse(
        simple_workbook(
            [
                ["Full Name", "Invitation Type", "Phone", "Email"],
                ["Synthetic Phone", "Family", "12-3", None],
                ["Synthetic Email", "Family", "+255700000010", "not-an-email"],
                ["Synthetic Type", "X" * 65, "+255700000011", None],
                ["Synthetic Numeric Phone", "Family", 255700000012, None],
            ]
        )
    )

    assert "INVALID_PHONE" in error_codes(result.rows[0])
    assert "INVALID_EMAIL" in error_codes(result.rows[1])
    assert "INVALID_INVITATION_TYPE" in error_codes(result.rows[2])
    assert "LOSSY_VALUE_NOT_ALLOWED" in error_codes(result.rows[3])


def test_duplicate_inside_workbook_marks_later_valid_identity() -> None:
    result = parse(
        simple_workbook(
            [
                ["Full Name", "Invitation Type", "Email"],
                ["Synthetic First", "Family", "same@example.test"],
                ["Synthetic Second", "Individual", "SAME@example.test"],
            ]
        )
    )
    assert result.rows[0].validation_errors == []
    assert error_codes(result.rows[1]) == {"DUPLICATE_IN_WORKBOOK"}


def test_optional_missing_columns_default_without_becoming_structural_failure() -> None:
    result = parse(
        simple_workbook(
            [
                ["Name", "Invite Type"],
                ["Synthetic Minimal", "Individual"],
            ]
        )
    )
    assert result.rows[0].values["allowed_companions"] == 0
    assert result.rows[0].values["email"] is None
    assert result.rows[0].validation_errors == []


def test_missing_required_configured_column_fails_closed() -> None:
    with pytest.raises(WorkbookRejectedError) as exc_info:
        parse(simple_workbook([["Full Name", "Phone"], ["Synthetic", "+255700000001"]]))
    assert exc_info.value.code == "MISSING_REQUIRED_COLUMNS"
    assert "invitation_type" in str(exc_info.value)


def test_equal_guest_tables_on_two_visible_sheets_are_ambiguous() -> None:
    workbook = Workbook()
    first = workbook.active
    assert first is not None
    first.append(["Full Name", "Invitation Type"])
    first.append(["Synthetic One", "Family"])
    second = workbook.create_sheet("Second")
    second.append(["Full Name", "Invitation Type"])
    second.append(["Synthetic Two", "Family"])

    with pytest.raises(WorkbookRejectedError) as exc_info:
        parse(save_workbook(workbook))
    assert exc_info.value.code == "AMBIGUOUS_GUEST_TABLE"


def test_hidden_equal_sheet_does_not_make_visible_selection_ambiguous() -> None:
    workbook = Workbook()
    first = workbook.active
    assert first is not None
    first.append(["Full Name", "Invitation Type"])
    first.append(["Synthetic One", "Family"])
    hidden = workbook.create_sheet("Hidden")
    hidden.sheet_state = "hidden"
    hidden.append(["Full Name", "Invitation Type"])
    hidden.append(["Synthetic Two", "Family"])

    result = parse(save_workbook(workbook))
    assert [row.values["full_name"] for row in result.rows] == ["Synthetic One"]


@pytest.mark.parametrize(
    ("content", "expected_code"),
    [
        (b"not a workbook", "UNSUPPORTED_WORKBOOK"),
        (b"PK malformed", "MALFORMED_WORKBOOK"),
    ],
)
def test_unsupported_and_corrupt_workbooks_fail_safely(
    content: bytes,
    expected_code: str,
) -> None:
    with pytest.raises(WorkbookRejectedError) as exc_info:
        parse(content)
    assert exc_info.value.code == expected_code


def test_xlsx_path_traversal_entry_is_rejected_before_parser() -> None:
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("_rels/.rels", "<Relationships/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
        archive.writestr("xl/worksheets/sheet1.xml", "<worksheet/>")
        archive.writestr("../escape.xml", "private")

    with pytest.raises(WorkbookRejectedError) as exc_info:
        parse(stream.getvalue())
    assert exc_info.value.code == "UNSAFE_XLSX_PACKAGE"


def test_corrupt_xlsx_xml_is_converted_to_bounded_rejection() -> None:
    valid = simple_workbook([["Full Name", "Invitation Type"], ["Synthetic", "Family"]])
    source = ZipFile(BytesIO(valid))
    stream = BytesIO()
    with source, ZipFile(stream, "w", ZIP_DEFLATED) as target:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                content = b"<worksheet><broken>"
            target.writestr(item, content)

    with pytest.raises(WorkbookRejectedError) as exc_info:
        parse(stream.getvalue())
    assert exc_info.value.code == "MALFORMED_WORKBOOK"


def test_candidate_limit_is_enforced_after_structure_detection() -> None:
    content = simple_workbook(
        [
            ["Full Name", "Invitation Type"],
            ["Synthetic One", "Family"],
            ["Synthetic Two", "Family"],
        ]
    )
    parser = WorkbookParser(maximum_candidate_rows=1)
    with pytest.raises(WorkbookRejectedError) as exc_info:
        parser.parse(content, default_policies(), 2)
    assert exc_info.value.code == "CANDIDATE_LIMIT_EXCEEDED"


def test_one_thousand_row_synthetic_parse_is_bounded() -> None:
    rows: list[list[object]] = [["Full Name", "Invitation Type", "Phone"]]
    rows.extend(
        [f"Synthetic Guest {index}", "Individual", f"+255700{index:06d}"] for index in range(1_000)
    )
    content = simple_workbook(rows)

    started = perf_counter()
    result = parse(content)
    duration = perf_counter() - started

    assert len(result.rows) == 1_000
    assert duration < 10
