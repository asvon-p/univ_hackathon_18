from urllib.parse import parse_qs, urlsplit

import pytest

from frontend.recommendation_navigation import safe_back, with_schedule


@pytest.mark.parametrize(
    "value",
    [
        "https://evil.invalid/recommend",
        "//evil.invalid/recommend",
        "javascript:alert(1)",
        "/recommend/../profile",
        "/\\evil.invalid/recommend",
        "\r\n/recommend",
        "http://[broken",
        "/recommend?" + "a=1&" * 513,
    ],
)
def test_return_destination_cannot_leave_recommendation(value):
    assert safe_back(value) == "/recommend"


def test_preserves_multi_select_and_encoded_text_without_forwarding_unrelated_fields():
    result = with_schedule(
        "/recommend?required=a&required=b&styles=casual&styles=minimal&style_text=A%26B%2B+C&temp=0&token=do-not-forward&schedule_id=old&schedule_version=1",
        "new",
        2,
    )
    assert parse_qs(urlsplit(result).query) == {
        "required": ["a", "b"],
        "styles": ["casual", "minimal"],
        "style_text": ["A&B+ C"],
        "temp": ["0"],
        "schedule_id": ["new"],
        "schedule_version": ["2"],
    }
