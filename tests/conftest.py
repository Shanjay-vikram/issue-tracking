import io
import re

import openpyxl
import pytest

from app import create_app
from app.extensions import db
from app.models import Customer, Role, Ticket, User
from app.services import ticket_service

PASSWORD = "Sup3rSecret!pass"


@pytest.fixture(autouse=True)
def fast_password_hashing(monkeypatch):
    """Production uses scrypt; tests use a cheap PBKDF2 so the suite stays fast."""
    import app.models as models
    from werkzeug.security import generate_password_hash
    monkeypatch.setattr(models, "generate_password_hash", lambda pw: generate_password_hash(pw, method="pbkdf2:sha256:1000"))


@pytest.fixture()
def app():
    app = create_app("testing")

    @app.before_request
    def _fresh_login_state():
        # The fixture keeps one app context open, so g would otherwise leak the cached user between requests.
        from flask import g
        g.pop("_login_user", None)

    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def make_customer(name="Acme Robotics", **kw):
    c = Customer(customer_code=Customer.new_placeholder_code(), company_name=name, contact_person="Pat Contact",
                 email="pat@example.com", phone="+1 555 0101", address="1 Test Road", country="Testland", active=True, **kw)
    db.session.add(c)
    db.session.flush()
    c.finalize_code()
    return c


def make_user(role, username=None, customer=None, active=True):
    username = username or role.lower()
    u = User(username=username, full_name=f"{username.title()} Person", email=f"{username}@example.com",
             role=role, customer_id=customer.id if customer else None, active=active)
    u.set_password(PASSWORD)
    db.session.add(u)
    db.session.flush()
    return u


@pytest.fixture()
def world(app):
    """One user per role, two customers each with a login."""
    acme, globex = make_customer("Acme Robotics"), make_customer("Globex Systems")
    w = dict(
        acme=acme, globex=globex,
        admin=make_user(Role.ADMIN), support=make_user(Role.SUPPORT),
        engineer=make_user(Role.ENGINEER), engineer2=make_user(Role.ENGINEER, "engineer2"),
        manager=make_user(Role.MANAGEMENT, "manager"),
        cust_acme=make_user(Role.CUSTOMER, "cust_acme", acme), cust_globex=make_user(Role.CUSTOMER, "cust_globex", globex),
    )
    db.session.commit()
    return w


def login(client, username, password=PASSWORD):
    return client.post("/login", data={"username": username, "password": password})


@pytest.fixture()
def as_user(app):
    """as_user('engineer') -> logged-in test client."""
    def factory(username):
        client = app.test_client()
        resp = login(client, username)
        assert resp.status_code == 302, f"login failed for {username}"
        return client
    return factory


def make_ticket(world, customer=None, engineer=True, problems=2, status=None):
    ticket = ticket_service.create_ticket(
        dict(customer_id=(customer or world["acme"]).id, title="Controller board resets", description="Resets under load",
             product_name="Servo Drive", product_model="SD-200", serial_number="SN-1001", priority="HIGH", target_date=None),
        [dict(title=f"Problem {i + 1}", description=f"Fault {i + 1} description", category="Electrical") for i in range(problems)],
        world["support"])
    if engineer:
        ticket_service.assign_engineer(ticket, world["engineer"].id)
    if status:
        ticket.status = status
    db.session.commit()
    return ticket


def fill_investigation(ticket, stage=6):
    """Make every problem complete enough to submit."""
    from app.models import ProposedSolution
    for p in ticket.problems:
        inv = p.investigation
        inv.stage = stage
        inv.observations = f"Observed brown-out on rail for {p.title}"
        inv.confirmed_root_cause = f"Capacitor C12 undersized for {p.title}"
        inv.corrective_actions = "Replace C12 with 470uF part"
        inv.preventive_actions = "Add inrush test to production"
        inv.conclusion = "Fix validated on bench"
        db.session.add(ProposedSolution(problem=p, description="Swap C12", created_by_id=ticket.assigned_engineer_id))
    db.session.commit()


CSV_TEXT = "time,voltage,current,label\n" + "\n".join(f"{i},{3.3 + (i % 5) * 0.01:.2f},{0.5 + i * 0.01:.2f},run{i}" for i in range(30)) + "\n"


def csv_bytes():
    return CSV_TEXT.encode()


def xlsx_bytes():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["t", "temp"])
    for i in range(10):
        ws.append([i, 20 + i * 1.5])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


PNG_BYTES = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
             b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82")


def upload(client, problem_id, filename, content, kind="data", description=""):
    return client.post(f"/problems/{problem_id}/files", data={"kind": kind, "description": description,
                       "file": (io.BytesIO(content), filename)}, content_type="multipart/form-data")


def csrf_token(html):
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html).group(1)
