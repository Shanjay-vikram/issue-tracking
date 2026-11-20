import os
import subprocess
import sys

import pytest

from app import create_app
from app.config import database_url
from app.extensions import db
from app.models import Role, Status, User
from conftest import csv_bytes, fill_investigation, make_ticket, upload

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_production_requires_secret_key(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    from app import config
    monkeypatch.setattr(config.ProductionConfig, "SECRET_KEY", None)
    with pytest.raises(RuntimeError):
        create_app("production")


def test_production_cookie_flags(monkeypatch):
    from app import config
    monkeypatch.setattr(config.ProductionConfig, "SECRET_KEY", "x" * 40)
    app = create_app("production")
    assert app.config["SESSION_COOKIE_SECURE"] and app.config["SESSION_COOKIE_HTTPONLY"] and app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


def test_database_url_normalisation(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host:5432/db")
    assert database_url() == "postgresql://u:p@host:5432/db"
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host/db")
    assert database_url() == "postgresql://u:p@host/db"
    monkeypatch.delenv("DATABASE_URL")
    assert database_url().startswith("sqlite:///")


def test_healthz_is_public(app):
    r = app.test_client().get("/healthz")
    assert r.status_code == 200 and r.get_json() == {"status": "ok"}


def test_every_parameterless_page_renders_for_every_role(app, world, as_user):
    rules = [r for r in app.url_map.iter_rules() if "GET" in r.methods and not r.arguments and r.endpoint != "static"]
    assert len(rules) >= 8
    for username in ("admin", "support", "engineer", "manager", "cust_acme"):
        c = as_user(username)
        for rule in rules:
            if rule.rule in ("/login",):
                continue
            assert c.get(rule.rule).status_code in (200, 302, 403), (username, rule.rule)


def test_every_route_without_login_never_errors(app, world):
    c = app.test_client()
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static" or rule.rule in ("/login", "/", "/healthz"):
            continue
        url = rule.rule
        for arg in rule.arguments:
            url = url.replace(f"<int:{arg}>", "1")
        for method in sorted(rule.methods & {"GET", "POST"}):
            r = c.open(url, method=method)
            assert r.status_code in (302, 401, 403, 405), (method, url, r.status_code)


def test_full_page_rendering_with_populated_ticket(app, world, as_user):
    t = make_ticket(world)
    p = t.problems[0]
    e = as_user("engineer")
    upload(e, p.id, "run.csv", csv_bytes())
    from app.models import TestFile
    f = TestFile.query.one()
    e.post(f"/problems/{p.id}/graphs", data=dict(test_file_id=f.id, title="g", chart_type="line", x_column="time", y_column="voltage", explanation="e"))
    e.post(f"/problems/{p.id}/questions", data=dict(question="q?", justification="j"))
    for who in ("admin", "support", "engineer", "manager"):
        c = as_user(who)
        for url in (f"/tickets/{t.id}", f"/problems/{p.id}", f"/problems/{t.problems[1].id}", f"/tickets/{t.id}/edit" if who in ("admin", "support") else "/dashboard"):
            assert c.get(url).status_code == 200, (who, url)
    # editable view exposes forms, read-only views do not
    assert b"graph-builder" in e.get(f"/problems/{p.id}").data
    assert b"graph-builder" not in as_user("manager").get(f"/problems/{p.id}").data
    assert b"Submit to Management" in e.get(f"/tickets/{t.id}").data
    assert b"Submit to Management" not in as_user("support").get(f"/tickets/{t.id}").data
    assert b"Assignment" in as_user("support").get(f"/tickets/{t.id}").data


def test_dashboard_is_scoped_by_role(app, world, as_user):
    a = make_ticket(world, customer=world["acme"])
    make_ticket(world, customer=world["globex"], engineer=False)
    make_ticket(world, customer=world["globex"], engineer=False)
    from app.services.ticket_service import dashboard_data
    from app.permissions import visible_tickets_query
    assert dashboard_data(visible_tickets_query(world["admin"]), world["admin"])["stats"]["total"] == 3
    assert dashboard_data(visible_tickets_query(world["cust_acme"]), world["cust_acme"])["stats"]["total"] == 1
    assert dashboard_data(visible_tickets_query(world["cust_globex"]), world["cust_globex"])["stats"]["total"] == 2
    eng = dashboard_data(visible_tickets_query(world["engineer"]), world["engineer"])
    assert eng["stats"]["total"] == 1 and eng["workload"] is None
    full = dashboard_data(visible_tickets_query(world["manager"]), world["manager"])
    assert full["workload"]["labels"] == ["Engineer Person"] and sum(full["categories"]["values"]) == 6
    assert len(full["monthly"]["values"]) == 12 and sum(full["monthly"]["values"]) == 3
    assert as_user("cust_acme").get("/dashboard").status_code == 200
    page = as_user("manager").get("/dashboard").get_data(as_text=True)
    assert "chart-status" in page and "Pending Management" in page


def test_dashboard_lists_tickets_waiting_for_management(app, world, as_user):
    t = make_ticket(world)
    fill_investigation(t)
    as_user("engineer").post(f"/tickets/{t.id}/submit")
    assert b"Waiting for your decision" in as_user("manager").get("/dashboard").data
    assert b"Waiting for your decision" not in as_user("support").get("/dashboard").data


def test_error_pages(app, world, as_user):
    assert b"Page not found" in as_user("admin").get("/nope").data
    r = as_user("support").get("/users")
    assert r.status_code == 403 and b"have access" in r.data


def test_create_admin_script_and_migrations(tmp_path):
    db_file = tmp_path / "t.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{db_file}", "APP_ENV": "development", "FLASK_APP": "run.py",
           "CREATE_ADMIN_PASSWORD": "A-long-passw0rd-123"}
    env.pop("SECRET_KEY", None)
    run = lambda *args: subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert run(sys.executable, "-m", "flask", "db", "upgrade").returncode == 0
    base = [sys.executable, "create_admin.py", "--from-env", "--username", "boss", "--full-name", "Big Boss", "--email", "boss@example.com"]
    r = run(*base)
    assert r.returncode == 0 and "created" in r.stdout
    again = run(*base)
    assert again.returncode == 0 and "already exists" in again.stdout
    import sqlite3
    rows = sqlite3.connect(db_file).execute("select username, role, password_hash from users").fetchall()
    assert len(rows) == 1 and rows[0][:2] == ("boss", "ADMIN") and "A-long-passw0rd-123" not in rows[0][2]
    weak = run(*[a if a != "boss" else "boss2" for a in base], )  # admin exists, so nothing happens
    assert "already exists" in weak.stdout


def test_create_admin_rejects_weak_password(tmp_path):
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'w.db'}", "APP_ENV": "development", "FLASK_APP": "run.py",
           "CREATE_ADMIN_PASSWORD": "short1"}
    subprocess.run([sys.executable, "-m", "flask", "db", "upgrade"], cwd=ROOT, env=env, capture_output=True, timeout=120)
    r = subprocess.run([sys.executable, "create_admin.py", "--from-env", "--username", "boss", "--full-name", "B B", "--email", "b@example.com"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode != 0 and "at least 12" in r.stderr


def test_seed_script_refuses_production(tmp_path):
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 's.db'}", "APP_ENV": "production", "SECRET_KEY": "k" * 40}
    r = subprocess.run([sys.executable, "seed_dev.py"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode != 0 and "Refusing" in r.stderr
