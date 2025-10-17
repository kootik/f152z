import pytest

from app.utils.sanitizers import sanitize_filename


@pytest.mark.parametrize(
    "input_string, expected_output",
    [
        ("Иванов Иван", "Ivanov_Ivan"),
        ("Special @Chars! 123", "Special__Chars__123"),
        ("  leading_trailing_  ", "leading_trailing"),
        (None, "Unknown"),
        ("", "Unknown"),
        ("___---___", "---"),
    ],
)
def test_sanitize_filename(input_string, expected_output):
    """
    Тестирует функцию sanitize_filename с различными входными данными.
    """
    assert sanitize_filename(input_string) == expected_output
