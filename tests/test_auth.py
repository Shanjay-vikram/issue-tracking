from app.extensions import db
from app.models import AuditLog, User
from conftest import PASSWORD, csrf_token, login


def test_login_success_and_dashboard(app, world):
    c = app.test_client()
    r = login(c, "admin")
    assert r.status_code == 302 and r.headers["Location"].endswith("/dashboard")
    assert c.get("/dashboard").status_code == 200


def test_wrong_password_and_unknown_user_give_same_message(app, world):
    c = app.test_client()
    a = c.post("/login", data={"username": "admin", "password": "nope"})
    b = c.post("/login", data={"username": "ghost", "password": "nope"})
    assert a.status_code == b.status_code == 200
    assert b"Incorrect username or password." in a.data and b"Incorrect username or password." in b.data


def test_passwords_are_hashed(app, world):
    hash_ = User.query.filter_by(username="admin").one().password_hash
    assert PASSWORD not in hash_ and hash_.startswith(("scrypt:", "pbkdf2:"))


def test_account_locks_after_repeated_failures(app, world):
    c = app.test_client()
    for _ in range(3):
        c.post("/login", data={"username": "support", "password": "bad"})
    r = login(c, "support")  # correct password, but locked
    assert r.status_code == 200 and b"Too many failed attempts" in r.data
    assert AuditLog.query.filter_by(action="login.lockout").count() == 1


def test_inactive_user_cannot_login(app, world):
    world["support"].active = False
    db.session.commit()
    assert login(app.test_client(), "support").status_code == 200


def test_logout_requires_post_and_ends_session(app, as_user):
    from conftest import make_user
    # no world fixture needed beyond a user
    from app.models import Role
    make_user(Role.ADMIN)
    db.session.commit()
    c = as_user("admin")
    assert c.get("/logout").status_code == 405
    assert c.post("/logout").status_code == 302
    assert c.get("/dashboard").status_code == 302


def test_open_redirect_is_blocked(app, world):
    for target in ("https://evil.example/x", "//evil.example", "javascript:alert(1)"):
        r = app.test_client().post(f"/login?next={target}", data={"username": "admin", "password": PASSWORD})
        assert r.status_code == 302 and "evil" not in r.headers["Location"] and r.headers["Location"].endswith("/dashboard")
    r = app.test_client().post("/login?next=/tickets", data={"username": "admin", "password": PASSWORD})
    assert r.headers["Location"].endswith("/tickets")


def test_protected_pages_redirect_when_anonymous(app, world):
    c = app.test_client()
    for url in ("/dashboard", "/tickets", "/customers", "/users", "/audit", "/profile", "/tickets/1", "/problems/1"):
        r = c.get(url)
        assert r.status_code == 302 and "/login" in r.headers["Location"], url


def test_change_password(app, world, as_user):
    c = as_user("support")
    assert b"Current password is incorrect" in c.post("/profile", data={"current_password": "x", "new_password": "NewPassw0rd99", "confirm": "NewPassw0rd99"}).data
    c.post("/profile", data={"current_password": PASSWORD, "new_password": "NewPassw0rd99", "confirm": "NewPassw0rd99"})
    assert login(app.test_client(), "support", "NewPassw0rd99").status_code == 302
    r = c.post("/profile", data={"current_password": "NewPassw0rd99", "new_password": "short", "confirm": "short"})
    assert b"at least 10" in r.data


def test_csrf_is_enforced_when_enabled(app, world):
    app.config["WTF_CSRF_ENABLED"] = True
    c = app.test_client()
    assert c.post("/login", data={"username": "admin", "password": PASSWORD}).status_code == 400
    token = csrf_token(c.get("/login").get_data(as_text=True))
    r = c.post("/login", data={"username": "admin", "password": PASSWORD, "csrf_token": token})
    assert r.status_code == 302
    # state-changing POSTs outside WTForms are protected too
    assert c.post("/logout").status_code == 400


def test_security_headers_and_session_cookie(app, world):
    r = app.test_client().get("/login")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "DENY"
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["Cache-Control"] == "no-store"
