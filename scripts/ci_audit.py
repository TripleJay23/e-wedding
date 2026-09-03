"""Fail-closed repository and distribution privacy audits for local use and CI."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tarfile
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
MAX_TEXT_BYTES = 2 * 1024 * 1024
EXPECTED_ARCHIVE_PREFIX = "e_wedding_backend-"

ALLOWED_ENV_FILES = {
    ".env.example",
    ".env.staging.example",
    "backend/.env.example",
}
DELETED_GOVERNANCE_FILES = {
    "MASTER_BOOTSTRAP_PROMPT.md",
    "docs/LegacyRecovery.md",
    "docs/LegacyRecoveryReport.md",
    "docs/RebuildDecisionChecklist.md",
}
PRIVATE_EXTENSIONS = {
    ".csv",
    ".db",
    ".dump",
    ".heic",
    ".jpeg",
    ".jpg",
    ".key",
    ".ods",
    ".otf",
    ".p12",
    ".pem",
    ".pfx",
    ".pdf",
    ".png",
    ".sql",
    ".sqlite",
    ".sqlite3",
    ".svg",
    ".tsv",
    ".ttf",
    ".webp",
    ".woff",
    ".woff2",
    ".xls",
    ".xlsb",
    ".xlsm",
    ".xlsx",
    ".zip",
}
PRIVATE_PATH_PARTS = {
    "backups",
    "generated-cards",
    "pgdata",
    "postgres-data",
    "private",
    "private-storage",
    "uploads",
}
WORKSPACE_SKIP_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".test-tmp",
    ".tox",
    ".uv-cache",
    ".uv-python",
    ".venv",
    "__pycache__",
    "dist",
    "htmlcov",
}

STRONG_SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"sk-[A-Za-z0-9]{20,}"),
    re.compile(rb"xox[baprs]-[A-Za-z0-9-]{10,}"),
)
DATABASE_URL_PATTERN = re.compile(
    rb"postgresql(?:\+[a-z0-9_]+)?://[^\s:/@]+:([^\s/@]+)@",
    re.IGNORECASE,
)
EMAIL_PATTERN = re.compile(
    rb"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})"
)
PHONE_PATTERN = re.compile(rb"(?<![0-9A-Za-z])\+[1-9]\d(?:[ ()-]*\d){7,14}")


@dataclass(frozen=True, order=True)
class Finding:
    category: str
    path: str


def _git(*args: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ("git", *args),
        cwd=ROOT,
        input=input_bytes,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {message}")
    return result.stdout


def _normalize_path(raw_path: str) -> str:
    normalized = raw_path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _allowed_public_asset(path: str) -> bool:
    normalized = _normalize_path(path).lower()
    return normalized.startswith(("assets/mockups/", "assets/public/", "docs/"))


def _allowed_sample_data(path: str) -> bool:
    return _normalize_path(path).lower().startswith("assets/sample_excel/")


def _private_path_reason(
    path: str, *, allow_archive_env_example: bool = False
) -> str | None:
    normalized = _normalize_path(path)
    lower = normalized.lower()
    pure = PurePosixPath(lower)
    name = pure.name
    suffix = pure.suffix

    if lower in ALLOWED_ENV_FILES or (
        allow_archive_env_example and name == ".env.example"
    ):
        return None
    if name == ".env" or name.startswith(".env."):
        return "environment file"
    if any(part in PRIVATE_PATH_PARTS for part in pure.parts):
        return "private storage path"
    if suffix in {".xlsx", ".xls", ".xlsm", ".xlsb", ".ods", ".csv", ".tsv"}:
        if _allowed_sample_data(normalized):
            return None
        return "workbook or tabular data"
    if suffix in PRIVATE_EXTENSIONS:
        if _allowed_public_asset(normalized):
            return None
        return "private or generated binary"
    if name.endswith((".bak", ".tmp", ".temp", ".swp", ".swo")):
        return "temporary backup"
    return None


def _content_findings(path: str, content: bytes) -> set[Finding]:
    findings: set[Finding] = set()
    if len(content) > MAX_TEXT_BYTES or b"\x00" in content[:8192]:
        return findings

    if any(pattern.search(content) for pattern in STRONG_SECRET_PATTERNS):
        findings.add(Finding("strong-secret", path))

    for match in DATABASE_URL_PATTERN.finditer(content):
        password = match.group(1).lower()
        if (
            b"synthetic" not in password
            and b"change_me" not in password
            and b"${" not in password
        ):
            findings.add(Finding("strong-secret", path))
            break

    for match in EMAIL_PATTERN.finditer(content):
        domain = match.group(2).decode("ascii", errors="ignore").lower()
        if not domain.endswith(".test"):
            findings.add(Finding("non-synthetic-email", path))
            break

    for match in PHONE_PATTERN.finditer(content):
        digits = re.sub(rb"\D", b"", match.group(0))
        normalized = f"/{_normalize_path(path).lower()}"
        if "/tests/" not in normalized or b"00000" not in digits:
            findings.add(Finding("non-synthetic-phone", path))
            break

    stale_product = ("e-card-system" + "-v2").encode()
    stale_display = ("E-Card System" + " V2").encode()
    if stale_product in content.lower() or stale_display.lower() in content.lower():
        findings.add(Finding("stale-branding", path))

    return findings


def _current_repository_files() -> dict[str, bytes]:
    output = _git("ls-files", "--cached", "--others", "--exclude-standard", "-z")
    files: dict[str, bytes] = {}
    for raw_path in output.split(b"\0"):
        if not raw_path:
            continue
        path = raw_path.decode("utf-8", errors="surrogateescape")
        absolute = ROOT / path
        if absolute.is_file():
            files[_normalize_path(path)] = absolute.read_bytes()
    return files


def _historical_blobs() -> Iterable[tuple[set[str], bytes]]:
    objects: dict[str, set[str]] = {}
    for line in (
        _git("rev-list", "--objects", "--all")
        .decode("utf-8", errors="surrogateescape")
        .splitlines()
    ):
        object_id, separator, path = line.partition(" ")
        if separator and path:
            objects.setdefault(object_id, set()).add(_normalize_path(path))

    process = subprocess.Popen(
        ("git", "cat-file", "--batch"),
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    try:
        for object_id, paths in objects.items():
            process.stdin.write(f"{object_id}\n".encode())
            process.stdin.flush()
            header = process.stdout.readline().decode("ascii", errors="replace").strip()
            fields = header.split()
            if len(fields) != 3:
                raise RuntimeError("git cat-file returned an invalid object header")
            _, object_type, size_text = fields
            size = int(size_text)
            content = process.stdout.read(size)
            terminator = process.stdout.read(1)
            if len(content) != size or terminator != b"\n":
                raise RuntimeError("git cat-file returned a truncated object")
            if object_type == "blob":
                yield paths, content
    finally:
        process.stdin.close()
        process.wait(timeout=30)
        if process.returncode != 0:
            assert process.stderr is not None
            message = process.stderr.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"git cat-file failed: {message}")


def audit_repository() -> set[Finding]:
    findings: set[Finding] = set()
    current_files = _current_repository_files()

    for path, content in current_files.items():
        if reason := _private_path_reason(path):
            findings.add(Finding(f"private-file:{reason}", path))
        if path in DELETED_GOVERNANCE_FILES:
            findings.add(Finding("deleted-governance-file-reintroduced", path))
        findings.update(_content_findings(path, content))

    for paths, content in _historical_blobs():
        for path in paths:
            if reason := _private_path_reason(path):
                findings.add(Finding(f"private-history:{reason}", path))
        if any(pattern.search(content) for pattern in STRONG_SECRET_PATTERNS):
            findings.add(Finding("strong-secret-history", "<historical blob>"))
        for match in DATABASE_URL_PATTERN.finditer(content):
            password = match.group(1).lower()
            if (
                b"synthetic" not in password
                and b"change_me" not in password
                and b"${" not in password
            ):
                findings.add(Finding("strong-secret-history", "<historical blob>"))
                break

    return findings


def audit_workspace() -> set[Finding]:
    findings: set[Finding] = set()
    if "WEDDING_STAGING_BOOTSTRAP_DATABASE_URL" in os.environ:
        findings.add(Finding("staging-operator-environment", "<process environment>"))

    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        normalized = _normalize_path(relative.as_posix())
        parts = PurePosixPath(normalized.lower()).parts
        if any(part in WORKSPACE_SKIP_PARTS for part in parts):
            continue
        if path.is_dir():
            if any(part in PRIVATE_PATH_PARTS for part in parts):
                findings.add(Finding("private-workspace-directory", normalized))
            continue
        if reason := _private_path_reason(normalized):
            findings.add(Finding(f"private-workspace:{reason}", normalized))
    return findings


def _archive_entries(path: Path) -> Iterable[tuple[str, bytes]]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                yield info.filename, archive.read(info)
        return

    if path.name.endswith(".tar.gz"):
        with tarfile.open(path, mode="r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise RuntimeError(
                        f"could not read archive member from {path.name}"
                    )
                yield member.name, extracted.read()
        return

    raise RuntimeError(f"unsupported archive type: {path.name}")


def _archive_logical_path(path: str) -> str:
    normalized = _normalize_path(path)
    parts = PurePosixPath(normalized).parts
    if parts and parts[0].startswith(EXPECTED_ARCHIVE_PREFIX):
        return PurePosixPath(*parts[1:]).as_posix()
    return normalized


def audit_archives(raw_paths: list[str]) -> set[Finding]:
    findings: set[Finding] = set()
    paths = [Path(raw_path) for raw_path in raw_paths]
    wheels = [path for path in paths if path.suffix == ".whl"]
    sdists = [path for path in paths if path.name.endswith(".tar.gz")]
    if len(paths) != 2 or len(wheels) != 1 or len(sdists) != 1:
        findings.add(Finding("archive-set", "expected exactly one wheel and one sdist"))

    for path in paths:
        if not path.is_file() or not path.name.startswith(EXPECTED_ARCHIVE_PREFIX):
            findings.add(Finding("archive-name", path.name))
            continue
        try:
            entries = list(_archive_entries(path))
        except (OSError, tarfile.TarError, zipfile.BadZipFile, RuntimeError):
            findings.add(Finding("archive-readable", path.name))
            continue
        if not entries:
            findings.add(Finding("archive-empty", path.name))
            continue

        for member_path, content in entries:
            pure = PurePosixPath(member_path.replace("\\", "/"))
            if pure.is_absolute() or ".." in pure.parts:
                findings.add(Finding("archive-path-traversal", f"{path.name}:<member>"))
                continue
            logical_path = _archive_logical_path(member_path)
            if reason := _private_path_reason(
                logical_path, allow_archive_env_example=True
            ):
                findings.add(
                    Finding(f"archive-private:{reason}", f"{path.name}:{logical_path}")
                )
            if logical_path in DELETED_GOVERNANCE_FILES:
                findings.add(
                    Finding(
                        "archive-deleted-governance-file", f"{path.name}:{logical_path}"
                    )
                )
            for finding in _content_findings(logical_path, content):
                findings.add(
                    Finding(
                        f"archive-{finding.category}", f"{path.name}:{logical_path}"
                    )
                )

    return findings


def _report(findings: set[Finding], success_lines: tuple[str, ...]) -> int:
    if findings:
        for finding in sorted(findings):
            print(f"FAIL [{finding.category}] {finding.path}")
        print(f"audit result: FAIL ({len(findings)} finding(s))")
        return 1
    for line in success_lines:
        print(f"{line}: PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "repository", help="audit current files and complete Git history"
    )
    subparsers.add_parser(
        "workspace", help="assert the checkout contains no private resources"
    )
    archives_parser = subparsers.add_parser(
        "archives", help="audit one wheel and one sdist"
    )
    archives_parser.add_argument("paths", nargs="+")
    args = parser.parse_args(argv)

    try:
        if args.command == "repository":
            return _report(
                audit_repository(),
                (
                    "private-file audit",
                    "strong-secret audit",
                    "PII/synthetic-data audit",
                    "branding/governance audit",
                ),
            )
        if args.command == "workspace":
            return _report(
                audit_workspace(),
                ("CI private-asset boundary", "staging-operator environment boundary"),
            )
        return _report(audit_archives(args.paths), ("wheel/sdist archive hygiene",))
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"audit infrastructure failure: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
