"""Platform-native absolute paths for tests that do not access private storage."""

from pathlib import Path
from tempfile import gettempdir


def synthetic_private_storage_settings() -> dict[str, Path]:
    """Return absolute, nested synthetic paths without creating storage."""
    private_root = Path(gettempdir()).resolve() / "e-wedding-tests" / "private"
    return {
        "PRIVATE_STORAGE_ROOT": private_root,
        "IMPORT_EVIDENCE_STORAGE_ROOT": private_root / "import-evidence",
    }
