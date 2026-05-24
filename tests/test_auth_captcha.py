from __future__ import annotations

import json

import pytest
from werkzeug.datastructures import MultiDict

from easy_social import auth as auth_module
from easy_social.models import User


class _MockResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


@pytest.mark.unit
def test_extract_recaptcha_token_prefers_last_non_empty_value(app):
    with app.test_request_context(
        "/auth/register",
        method="POST",
        data=MultiDict(
            [
            ("g-recaptcha-response", ""),
            ("g-recaptcha-response", "first-token"),
            ("g-recaptcha-response", "  "),
            ("g-recaptcha-response", "last-token"),
            ]
        ),
    ):
        assert auth_module._extract_recaptcha_token() == "last-token"


@pytest.mark.unit
def test_verify_recaptcha_token_returns_true_on_success_payload(app, monkeypatch):
    def _fake_urlopen(*args, **kwargs):
        return _MockResponse({"success": True})

    monkeypatch.setattr(auth_module, "urlopen", _fake_urlopen)

    with app.app_context():
        assert auth_module._verify_recaptcha_token("valid-token", "127.0.0.1")


@pytest.mark.unit
def test_verify_recaptcha_token_returns_false_when_provider_fails(app, monkeypatch):
    def _fake_urlopen(*args, **kwargs):
        return _MockResponse({"success": False})

    monkeypatch.setattr(auth_module, "urlopen", _fake_urlopen)

    with app.app_context():
        assert not auth_module._verify_recaptcha_token("invalid-token", "127.0.0.1")


@pytest.mark.unit
def test_verify_recaptcha_token_returns_false_on_invalid_json(app, monkeypatch):
    class _InvalidJsonResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"not-json"

    def _fake_urlopen(*args, **kwargs):
        return _InvalidJsonResponse()

    monkeypatch.setattr(auth_module, "urlopen", _fake_urlopen)

    with app.app_context():
        assert not auth_module._verify_recaptcha_token("token", "127.0.0.1")


@pytest.mark.integration
def test_register_requires_valid_captcha_token(client, app, monkeypatch):
    def _fake_urlopen(*args, **kwargs):
        return _MockResponse({"success": False})

    monkeypatch.setattr(auth_module, "urlopen", _fake_urlopen)

    response = client.post(
        "/auth/register",
        data={
            "username": "captcha_fail",
            "email": "captcha_fail@example.com",
            "password": "password",
            "g-recaptcha-response": "bad-token",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"CAPTCHA verification failed. Please try again." in response.data
    with app.app_context():
        assert User.query.filter_by(username="captcha_fail").first() is None


@pytest.mark.integration
def test_register_succeeds_with_valid_captcha_token(client, app, monkeypatch):
    def _fake_urlopen(*args, **kwargs):
        return _MockResponse({"success": True})

    monkeypatch.setattr(auth_module, "urlopen", _fake_urlopen)

    response = client.post(
        "/auth/register",
        data={
            "username": "captcha_pass",
            "email": "captcha_pass@example.com",
            "password": "password",
            "g-recaptcha-response": "good-token",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Feed" in response.data
    with app.app_context():
        assert User.query.filter_by(username="captcha_pass").first() is not None
