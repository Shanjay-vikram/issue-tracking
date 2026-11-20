import pytest

from app.models import Role, Status
from conftest import make_ticket

ALL = ["admin", "support", "engineer", "manager", "cust_acme"]


@pytest.mark.parametrize("url,allowed", [
    ("/customers", {"admin", "support"}),
    ("/customers/new", {"admin", "support"}),
    ("/users", {"admin"}),
    ("/users/new", {"admin"}),
    ("/audit", {"admin"}),
    ("/tickets/new", {"admin", "support"}),
    ("/dashboard", set(ALL)),
    ("/tickets", set(ALL)),
])
def test_role_matrix_for_pages(app, world, as_user, url, allowed):
    for username in ALL:
        status = as_user(username).get(url).status_code
        assert (status == 200) == (username in allowed), f"{username} {url} -> {status}"
        if username not in allowed:
            assert status == 403


def test_backend_blocks_forbidden_writes(app, world, as_user):
    t = make_ticket(world)
    pid = t.problems[0].id
    cust = dict(company_name="X", contact_person="Y", email="a@b.co", phone="12345678", address="a", country="c")
    assert as_user("engineer").post("/customers/new", data=cust).status_code == 403
    assert as_user("cust_acme").post("/customers/new", data=cust).status_code == 403
    assert as_user("manager").post("/customers/new", data=cust).status_code == 403
    assert as_user("cust_acme").post(f"/tickets/{t.id}/assign", data={"engineer_id": world["engineer2"].id}).status_code == 403
    assert as_user("engineer").post(f"/tickets/{t.id}/assign", data={"engineer_id": world["engineer2"].id}).status_code == 403
    assert as_user("support").post(f"/tickets/{t.id}/decision", data={"decision": "APPROVED"}).status_code == 403
    assert as_user("admin").post(f"/tickets/{t.id}/decision", data={"decision": "APPROVED"}).status_code == 403
    assert as_user("support").post(f"/tickets/{t.id}/submit").status_code == 403
    assert as_user("admin").post(f"/tickets/{t.id}/submit").status_code == 403
    assert as_user("engineer").post(f"/tickets/{t.id}/report").status_code == 403
    assert as_user("cust_acme").post(f"/tickets/{t.id}/report").status_code == 403
    assert as_user("support").post(f"/problems/{pid}/investigation", data={"stage": 2}).status_code == 403
    assert as_user("admin").post(f"/problems/{pid}/solutions", data={"description": "x"}).status_code == 403
    assert as_user("support").post("/users/new", data={"username": "x"}).status_code == 403


def test_engineer_sees_only_assigned_tickets(app, world, as_user):
    mine = make_ticket(world)
    theirs = make_ticket(world, engineer=False)
    c = as_user("engineer")
    assert c.get(f"/tickets/{mine.id}").status_code == 200
    assert c.get(f"/tickets/{theirs.id}").status_code == 404
    assert c.get(f"/problems/{theirs.problems[0].id}").status_code == 404
    other = as_user("engineer2")
    assert other.get(f"/tickets/{mine.id}").status_code == 404
    assert other.post(f"/problems/{mine.problems[0].id}/investigation", data={"stage": 2}).status_code == 404
    listing = c.get("/tickets").get_data(as_text=True)
    assert mine.ticket_no in listing and theirs.ticket_no not in listing


def test_customer_is_isolated_to_own_tickets(app, world, as_user):
    mine = make_ticket(world, customer=world["acme"])
    other = make_ticket(world, customer=world["globex"])
    c = as_user("cust_acme")
    assert c.get(f"/tickets/{mine.id}").status_code == 200
    assert c.get(f"/tickets/{other.id}").status_code == 404
    listing = c.get("/tickets").get_data(as_text=True)
    assert mine.ticket_no in listing and other.ticket_no not in listing
    # engineering records are never available to customers, even on their own tickets
    assert c.get(f"/problems/{mine.problems[0].id}").status_code == 403
    assert c.get(f"/problems/{other.problems[0].id}").status_code == 404


def test_unknown_ids_return_404_not_500(app, world, as_user):
    c = as_user("admin")
    for url in ("/tickets/9999", "/problems/9999", "/files/9999/download", "/reports/9999/download", "/graphs/9999/data"):
        assert c.get(url).status_code == 404


def test_deactivated_user_session_is_rejected(app, world, as_user):
    from app.extensions import db
    c = as_user("support")
    world["support"].active = False
    db.session.commit()
    assert c.get("/dashboard").status_code == 302


def test_admin_user_management_rules(app, world, as_user):
    c = as_user("admin")
    r = c.post("/users/new", data=dict(username="newbie", full_name="New Bie", email="newbie@example.com", role=Role.ENGINEER,
                                       customer_id=0, password="Passw0rd1234"))
    assert r.status_code == 302
    # customer logins need a customer
    r = c.post("/users/new", data=dict(username="cc", full_name="C C", email="cc@example.com", role=Role.CUSTOMER, customer_id=0, password="Passw0rd1234"))
    assert r.status_code == 200 and b"linked to a customer" in r.data
    # duplicate email
    r = c.post("/users/new", data=dict(username="other", full_name="O O", email="newbie@example.com", role=Role.SUPPORT, customer_id=0, password="Passw0rd1234"))
    assert b"already used" in r.data
    # cannot deactivate yourself
    c.post(f"/users/{world['admin'].id}/toggle")
    assert as_user("admin").get("/dashboard").status_code == 200
    # deactivate another user
    assert c.post(f"/users/{world['support'].id}/toggle").status_code == 302
    from conftest import login
    assert login(app.test_client(), "support").status_code == 200
