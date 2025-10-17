# app/utils/sanitizers.py
import unidecode


def sanitize_filename(name_part: str) -> str:
    """Sanitize string for safe filename usage."""
    if not name_part or not isinstance(name_part, str):
        return "Unknown"

    # Transliterate to ASCII
    name_part = unidecode.unidecode(name_part)

    # Replace invalid characters
    name_part = "".join(c if c.isalnum() or c in ["_", "-"] else "_" for c in name_part)

    # Clean up leading/trailing underscores
    return name_part.strip("_") or "Unknown"
