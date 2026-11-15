"""Submit the real creation form, then follow its read-only result URL."""

import re
from urllib.parse import parse_qsl, urlencode


def submit_recommendation(ui, params, *, request_key=None):
    form = ui.get("/recommend")
    assert form.status_code == 200, form.text[:300]
    key = request_key or re.search(r'name="request_key" value="([^"]+)"', form.text)[1]
    values = parse_qsl(params, keep_blank_values=True) if isinstance(params, str) else params
    encoded = urlencode(values, doseq=True) + "&request_key=" + key
    response = ui.post(
        "/recommend", content=encoded, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    if response.status_code == 303:
        assert response.headers["location"].startswith("/recommend/result/")
        return ui.get(response.headers["location"])
    return response
