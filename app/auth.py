from urllib.parse import urljoin, urlparse

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from . import audit
from .extensions import db
from .forms import ChangePasswordForm, LoginForm
from .models import User, utcnow
from datetime import timedelta

bp = Blueprint("auth", __name__)

# Verifying against a dummy hash keeps response time similar for unknown usernames.
_DUMMY_HASH = "scrypt:32768:8:1$dummy$" + "0" * 128


def is_safe_next(target):
    if not target:
        return False
    ref, test = urlparse(request.host_url), urlparse(urljoin(request.host_url, target))
    return test.scheme in ("http", "https") and ref.netloc == test.netloc and target.startswith("/") and not target.startswith("//")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        user = User.query.filter(db.func.lower(User.username) == username.lower()).first()
        generic = "Incorrect username or password."
        if user is None:
            from werkzeug.security import check_password_hash
            check_password_hash(_DUMMY_HASH, form.password.data)
            audit.log("login.failed", "User", None, f"unknown user '{username[:40]}'")
            db.session.commit()
            flash(generic, "danger")
        elif user.is_locked:
            audit.log("login.locked", "User", user.id, "login attempt while locked", user=user)
            db.session.commit()
            flash("Too many failed attempts. Try again in a few minutes.", "danger")
        elif not user.check_password(form.password.data) or not user.active:
            user.failed_attempts += 1
            if user.failed_attempts >= current_app.config["LOGIN_MAX_ATTEMPTS"]:
                user.locked_until = utcnow() + timedelta(minutes=current_app.config["LOGIN_LOCK_MINUTES"])
                user.failed_attempts = 0
                audit.log("login.lockout", "User", user.id, "account temporarily locked", user=user)
            else:
                audit.log("login.failed", "User", user.id, "wrong password or inactive", user=user)
            db.session.commit()
            flash(generic, "danger")
        else:
            user.failed_attempts = 0
            user.locked_until = None
            user.last_login = utcnow()
            login_user(user)
            audit.log("login.success", "User", user.id, "", user=user)
            db.session.commit()
            nxt = request.args.get("next")
            return redirect(nxt if is_safe_next(nxt) else url_for("main.dashboard"))
    return render_template("login.html", form=form)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    audit.log("logout", "User", current_user.id)
    db.session.commit()
    logout_user()
    flash("You have signed out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            form.current_password.errors.append("Current password is incorrect.")
        else:
            current_user.set_password(form.new_password.data)
            audit.log("password.changed", "User", current_user.id)
            db.session.commit()
            flash("Password changed.", "success")
            return redirect(url_for("auth.profile"))
    return render_template("profile.html", form=form)
