from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from easy_social import create_app
from easy_social import auth as auth_module
from easy_social.extensions import db


@pytest.fixture()
def app():
    with tempfile.TemporaryDirectory() as temp_dir:
        app = create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test",
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "UPLOAD_FOLDER": str(Path(temp_dir) / "uploads"),
                "MEDIA_STORAGE_BACKEND": "local",
                "WTF_CSRF_ENABLED": False,
                "RECAPTCHA_SITE_KEY": "test-site-key",
                "RECAPTCHA_SECRET_KEY": "test-secret-key",
            }
        )
        with app.app_context():
            db.create_all()
        yield app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def mock_recaptcha_verification(monkeypatch):
    class _MockResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"success": True}).encode("utf-8")

    def _fake_urlopen(*args, **kwargs):
        return _MockResponse()

    monkeypatch.setattr(auth_module, "urlopen", _fake_urlopen)


def register(client, username: str, email: str | None = None, password: str = "password"):
    return client.post(
        "/auth/register",
        data={
            "username": username,
            "email": email or f"{username}@example.com",
            "password": password,
            "g-recaptcha-response": "test-captcha-token",
        },
        follow_redirects=True,
    )


def login(client, username_or_email: str, password: str = "password"):
    return client.post(
        "/auth/login",
        data={"username_or_email": username_or_email, "password": password},
        follow_redirects=True,
    )


def logout(client):
    return client.post("/auth/logout", follow_redirects=True)
