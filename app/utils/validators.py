# app/utils/validators.py

import re
from typing import Any, Dict, List, Tuple

# --- ПЕРЕНЕСИТЕ ЭТИ КОНСТАНТЫ ИЗ СТАРОГО app.py СЮДА ---
MAX_SESSION_ID_LENGTH = 128
SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
MAX_EVENT_TYPE_LENGTH = 64
# ---------------------------------------------------------


def validate_session_id(session_id: str) -> bool:
    """Checks if the session ID format is valid."""
    if not session_id or not isinstance(session_id, str):
        return False
    # Теперь эта функция снова "видит" константы
    return len(session_id) <= MAX_SESSION_ID_LENGTH and bool(
        SESSION_ID_PATTERN.match(session_id)
    )


def validate_event_type(event_type: str) -> bool:
    """Checks if the event type format is valid."""
    if not event_type or not isinstance(event_type, str):
        return False
    return len(event_type) <= MAX_EVENT_TYPE_LENGTH


def validate_json_data(
    data: Dict[str, Any], required_fields: List[str]
) -> Tuple[bool, str]:
    """Validate JSON data for required fields."""
    if not data or not isinstance(data, dict):
        return False, "Invalid or missing data"

    missing = [f for f in required_fields if f not in data]
    if missing:
        return False, f"Missing required fields: {', '.join(missing)}"

    return True, ""
