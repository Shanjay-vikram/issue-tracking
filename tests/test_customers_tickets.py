from app.extensions import db
from app.models import Customer, Problem, Status, Ticket, User
from conftest import make_ticket

CUST = dict(company_name="Initech", contact_person="Bill L", email="bill@initech.example", phone="+91 98765 43210",
            address="12 Park Lane", country="India")


def test_database_starts_empty(app):
    assert Customer.query.count() == 0 and Ticket.query.count() == 0 and User.query.count() == 0


def test_register_search_edit_toggle_customer(app, world, as_user):
    c = as_user("support")
    assert c.post("/customers/new", data=CUST).status_code == 302
    cust = Customer.query.filter_by(company_name="Initech").one()
    assert cust.customer_code == f"CUST-{cust.id:05d}" and cust.active
    assert b"Initech" in c.get("/customers?q=initech").data
    assert b"Initech" not in c.get("/customers?q=zzz").data
    assert b"Initech" in c.get("/customers?country=india").data
    c.post(f"/customers/{cust.id}/edit", data={**CUST, "company_name": "Initech Global"})
    assert db.session.get(Customer, cust.id).company_name == "Initech Global"
    c.post(f"/customers/{cust.id}/toggle")
    assert not db.session.get(Customer, cust.id).active
    assert b"Initech Global" in c.get("/customers?status=inactive").data
    assert b"Initech Global" not in c.get("/customers?status=active").data


def test_customer_validation(app, world, as_user):
    c = as_user("admin")
    r = c.post("/customers/new", data={**CUST, "email": "not-an-email", "phone": "abc"})
    assert r.status_code == 200 and b"Invalid email" in r.data and b"valid phone" in r.data
    assert Customer.query.filter_by(company_name="Initech").count() == 0


def test_create_ticket_with_first_problem(app, world, as_user):
    c = as_user("support")
    data = dict(customer_id=world["acme"].id, title="Drive overheats", description="Overheats after 10 min", product_name="Drive",
                product_model="D1", serial_number="SN-77", priority="CRITICAL", target_date="2026-12-31",
                assigned_engineer_id=world["engineer"].id, problem_title="Thermal shutdown", problem_description="Shuts down at 70C",
                problem_category="Thermal")
    r = c.post("/tickets/new", data=data)
    assert r.status_code == 302
    t = Ticket.query.one()
    assert t.ticket_no == f"TKT-{t.created_at.year}-{t.id:05d}" and t.status == Status.ASSIGNED
    assert t.assigned_engineer_id == world["engineer"].id and len(t.problems) == 1 and t.problems[0].investigation is not None


def test_ticket_requires_active_customer_and_problem(app, world, as_user):
    world["globex"].active = False
    db.session.commit()
    c = as_user("support")
    base = dict(title="t", description="d", product_name="p", product_model="m", serial_number="s", priority="LOW",
                assigned_engineer_id=0, problem_title="p", problem_description="d", problem_category="Other")
    assert c.post("/tickets/new", data={**base, "customer_id": world["globex"].id}).status_code == 200  # invalid choice
    r = c.post("/tickets/new", data={**base, "customer_id": world["acme"].id, "problem_title": ""})
    assert r.status_code == 200 and b"Add the first problem" in r.data
    assert Ticket.query.count() == 0


def test_multiple_problems_under_one_ticket(app, world, as_user):
    t = make_ticket(world, problems=1, engineer=False)
    c = as_user("support")
    for i in range(3):
        assert c.post(f"/tickets/{t.id}/problems/new", data=dict(title=f"Extra {i}", description="more", category="Firmware")).status_code == 302
    t = db.session.get(Ticket, t.id)
    assert [p.title for p in t.problems] == ["Problem 1", "Extra 0", "Extra 1", "Extra 2"]
    ids = {p.investigation.id for p in t.problems}
    assert len(ids) == 4  # every problem owns its own investigation record
    page = c.get(f"/tickets/{t.id}").get_data(as_text=True)
    assert "Extra 2" in page and "Hardware problems (4)" in page


def test_edit_problem_and_ticket_then_lock(app, world, as_user):
    t = make_ticket(world)
    c = as_user("admin")
    c.post(f"/problems/{t.problems[0].id}/edit", data=dict(title="Renamed", description="d", category="Mechanical"))
    assert db.session.get(Problem, t.problems[0].id).title == "Renamed"
    c.post(f"/tickets/{t.id}/edit", data=dict(customer_id=world["acme"].id, title="New title", description="d", product_name="p",
                                              product_model="m", serial_number="s", priority="LOW"))
    assert db.session.get(Ticket, t.id).title == "New title"
    t.status = Status.PENDING_MANAGEMENT
    db.session.commit()
    assert c.post(f"/tickets/{t.id}/problems/new", data=dict(title="late", description="d", category="Other")).status_code == 302
    assert len(db.session.get(Ticket, t.id).problems) == 2  # blocked while with Management
    assert c.get(f"/tickets/{t.id}/edit").status_code == 302


def test_assignment_rules(app, world, as_user):
    t = make_ticket(world, engineer=False)
    assert t.status == Status.OPEN
    c = as_user("support")
    c.post(f"/tickets/{t.id}/assign", data={"engineer_id": world["engineer"].id})
    assert db.session.get(Ticket, t.id).status == Status.ASSIGNED
    c.post(f"/tickets/{t.id}/assign", data={"engineer_id": world["support"].id})  # not an engineer
    assert db.session.get(Ticket, t.id).assigned_engineer_id == world["engineer"].id
    c.post(f"/tickets/{t.id}/assign", data={"engineer_id": world["engineer2"].id})
    assert db.session.get(Ticket, t.id).assigned_engineer_id == world["engineer2"].id
    t.status = Status.APPROVED
    db.session.commit()
    c.post(f"/tickets/{t.id}/assign", data={"engineer_id": world["engineer"].id})
    assert db.session.get(Ticket, t.id).assigned_engineer_id == world["engineer2"].id


def test_ticket_search_and_filters(app, world, as_user):
    a = make_ticket(world, customer=world["acme"])
    b = make_ticket(world, customer=world["globex"], engineer=False)
    b.priority = "LOW"
    db.session.commit()
    c = as_user("admin")
    assert a.ticket_no in c.get("/tickets?q=SN-1001").get_data(as_text=True)
    only_globex = c.get(f"/tickets?customer={world['globex'].id}").get_data(as_text=True)
    assert b.ticket_no in only_globex and a.ticket_no not in only_globex
    low = c.get("/tickets?priority=LOW").get_data(as_text=True)
    assert b.ticket_no in low and a.ticket_no not in low
    open_ = c.get("/tickets?status=OPEN").get_data(as_text=True)
    assert b.ticket_no in open_ and a.ticket_no not in open_
