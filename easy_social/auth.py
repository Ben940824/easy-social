from __future__ import annotations

import json
from urllib.parse import urlencode
from urllib.request import urlopen

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from .extensions import db
from .models import User

bp = Blueprint("auth", __name__, url_prefix="/auth")


def _captcha_is_configured() -> bool:
    return bool(
        current_app.config.get("RECAPTCHA_SITE_KEY")
        and current_app.config.get("RECAPTCHA_SECRET_KEY")
    )


def _verify_recaptcha_token(token: str, remote_ip: str | None = None) -> bool:
    secret = current_app.config.get("RECAPTCHA_SECRET_KEY", "")
    if not secret or not token:
        return False

    payload = {"secret": secret, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    data = urlencode(payload).encode("utf-8")
    try:
        with urlopen(  # nosec B310
            current_app.config["RECAPTCHA_VERIFY_URL"], data=data, timeout=5
        ) as response:
            raw_body = response.read().decode("utf-8")
    except Exception:
        current_app.logger.exception("reCAPTCHA verification request failed.")
        return False

    try:
        verification = json.loads(raw_body)
    except json.JSONDecodeError:
        current_app.logger.warning("reCAPTCHA verification returned invalid JSON.")
        return False

    return bool(verification.get("success"))


def _extract_recaptcha_token() -> str:
    # reCAPTCHA widgets may submit multiple values; prefer the last non-empty value.
    submitted_tokens = [
        token.strip() for token in request.form.getlist("g-recaptcha-response") if token.strip()
    ]
    return submitted_tokens[-1] if submitted_tokens else ""


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("social.feed"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        captcha_token = _extract_recaptcha_token()

        error = None
        if not username or not email or not password:
            error = "Username, email, and password are required."
        elif not _captcha_is_configured():
            error = "CAPTCHA is not configured. Please contact the administrator."
        elif not _verify_recaptcha_token(captcha_token, request.remote_addr):
            error = "CAPTCHA verification failed. Please try again."
        elif len(username) > 40:
            error = "Username must be 40 characters or fewer."
        elif User.query.filter_by(username=username).first():
            error = "That username is already taken."
        elif User.query.filter_by(email=email).first():
            error = "That email is already registered."

        if error:
            flash(error, "error")
        else:
            user = User(username=username, email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(url_for("social.feed"))

    return render_template(
        "auth/register.html",
        recaptcha_site_key=current_app.config.get("RECAPTCHA_SITE_KEY", ""),
    )


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("social.feed"))

    if request.method == "POST":
        username_or_email = request.form.get("username_or_email", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter(
            (User.username == username_or_email)
            | (User.email == username_or_email.lower())
        ).first()

        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for("social.feed"))

        flash("Invalid username/email or password.", "error")

    return render_template("auth/login.html")


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))

