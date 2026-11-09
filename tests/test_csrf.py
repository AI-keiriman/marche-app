import pytest


def test_CSRFトークンの無いPOSTは400(app):
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    response = client.post(
        "/login", data={"email": "a@example.com", "password": "password"}
    )
    assert response.status_code == 400


@pytest.mark.parametrize(
    "url",
    [
        "/cart/add/1",
        "/cart/update/1",
        "/orders/",
        "/producer/products/1/toggle",
        "/producer/products/1/delete",
    ],
)
def test_値を変えるPOSTはCSRFトークンが無いと400(app, url):
    app.config["WTF_CSRF_ENABLED"] = True
    response = app.test_client().post(url, data={"quantity": "1"})
    assert response.status_code == 400
