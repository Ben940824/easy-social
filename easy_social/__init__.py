from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask
from sqlalchemy.pool import NullPool

from .extensions import db, login_manager, migrate
from .media import media_url
from .models import User


def _validate_database_url(database_url: str) -> None:
    if not database_url:
        return
    if "[YOUR-PASSWORD]" in database_url:
        raise ValueError(
            "DATABASE_URL still contains [YOUR-PASSWORD]. Replace it with the "
            "Supabase database password from Project Settings > Database."
        )

    parsed = urlsplit(database_url)
    if not parsed.hostname or not parsed.hostname.endswith(".pooler.supabase.com"):
        return

    if parsed.username == "postgres":
        raise ValueError(
            "Supabase pooler DATABASE_URL must use username "
            "postgres.<project-ref>, not postgres. Copy the transaction pooler "
            "connection string from Supabase Project Settings > Database."
        )
    if not parsed.password:
        raise ValueError(
            "Supabase pooler DATABASE_URL is missing a password. Use the "
            "database password from Supabase Project Settings > Database."
        )


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        return ""
    _validate_database_url(database_url)
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def _engine_options(database_url: str) -> dict:
    if not database_url.startswith("postgresql"):
        return {}
    return {
        "pool_pre_ping": True,
        "poolclass": NullPool,
        "connect_args": {"prepare_threshold": None},
    }


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    database_url = _database_url()
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-key"),
        SQLALCHEMY_DATABASE_URI=database_url
        or f"sqlite:///{Path(app.instance_path) / 'easy_social.sqlite'}",
        SQLALCHEMY_ENGINE_OPTIONS=_engine_options(database_url),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        UPLOAD_FOLDER=str(Path(app.root_path) / "static" / "uploads"),
        MAX_CONTENT_LENGTH=50 * 1024 * 1024,
        MEDIA_STORAGE_BACKEND=os.environ.get("MEDIA_STORAGE_BACKEND", "local"),
        SUPABASE_URL=os.environ.get("SUPABASE_URL"),
        SUPABASE_SERVICE_ROLE_KEY=os.environ.get("SUPABASE_SERVICE_ROLE_KEY"),
        SUPABASE_STORAGE_BUCKET=os.environ.get("SUPABASE_STORAGE_BUCKET", "easy-social-media"),
        RECAPTCHA_SITE_KEY=os.environ.get("RECAPTCHA_SITE_KEY", ""),
        RECAPTCHA_SECRET_KEY=os.environ.get("RECAPTCHA_SECRET_KEY", ""),
        RECAPTCHA_VERIFY_URL="https://www.google.com/recaptcha/api/siteverify",
    )

    if test_config:
        app.config.update(test_config)
        if "SQLALCHEMY_ENGINE_OPTIONS" not in test_config:
            app.config["SQLALCHEMY_ENGINE_OPTIONS"] = _engine_options(
                app.config["SQLALCHEMY_DATABASE_URI"]
            )

    if app.config["SQLALCHEMY_DATABASE_URI"].startswith("sqlite:///"):
        Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    if app.config["MEDIA_STORAGE_BACKEND"] == "local":
        Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id: str) -> User | None:
        return db.session.get(User, int(user_id))

    from .auth import bp as auth_bp
    from .social import bp as social_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(social_bp)
    app.jinja_env.globals["media_url"] = media_url

    @app.cli.command("init-db")
    def init_db_command() -> None:
        db.create_all()
        print("Initialized the database.")

    return app
