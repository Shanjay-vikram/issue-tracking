from datetime import timedelta

from sqlalchemy import func, or_

from ..extensions import db
from ..models import (Customer, Investigation, Problem, Role, Status, Ticket, User, utcnow)
from . import WorkflowError

CUSTOMER_STATUS_LABELS = {
    Status.OPEN: "Received",
    Status.ASSIGNED: "Received",
    Status.IN_PROGRESS: "Under investigation",
    Status.INVESTIGATION: "Under investigation",
    Status.PENDING_MANAGEMENT: "Under investigation",
    Status.REWORK: "Under investigation",
    Status.APPROVED: "Investigation complete",
    Status.CLOSED: "Closed",
}


def customer_status(ticket) -> str:
    """Customers never see internal workflow states such as REWORK."""
    return CUSTOMER_STATUS_LABELS.get(ticket.status, "In progress")


def create_ticket(form_data: dict, problems: list, creator: User) -> Ticket:
    customer = db.session.get(Customer, form_data["customer_id"])
    if customer is None or not customer.active:
        raise WorkflowError("Choose an active customer.")
    ticket = Ticket(ticket_no=Ticket.new_placeholder_no(), created_by=creator, status=Status.OPEN, **form_data)
    db.session.add(ticket)
    db.session.flush()
    ticket.finalize_no()
    for p in problems:
        add_problem(ticket, p["title"], p["description"], p["category"])
    return ticket


def add_problem(ticket: Ticket, title, description, category) -> Problem:
    if ticket.status in (Status.PENDING_MANAGEMENT, Status.APPROVED, Status.CLOSED):
        raise WorkflowError("Problems cannot be added while the ticket is awaiting Management, approved or closed.")
    problem = Problem(ticket=ticket, title=title, description=description, category=category)
    problem.investigation = Investigation(problem_description="")
    db.session.add(problem)
    return problem


def assign_engineer(ticket: Ticket, engineer_id: int):
    if ticket.status in (Status.PENDING_MANAGEMENT, Status.APPROVED, Status.CLOSED):
        raise WorkflowError("This ticket can no longer be reassigned.")
    if engineer_id == 0:
        if ticket.status != Status.OPEN:
            ticket.status = Status.OPEN
        ticket.assigned_engineer_id = None
        return None
    engineer = db.session.get(User, engineer_id)
    if engineer is None or engineer.role != Role.ENGINEER or not engineer.active:
        raise WorkflowError("Choose an active engineer.")
    ticket.assigned_engineer = engineer
    if ticket.status == Status.OPEN:
        ticket.status = Status.ASSIGNED
    return engineer


def close_ticket(ticket: Ticket):
    if ticket.status != Status.APPROVED:
        raise WorkflowError("Only approved tickets can be closed.")
    if ticket.latest_report is None:
        raise WorkflowError("Generate the final report before closing the ticket.")
    ticket.status = Status.CLOSED


def filter_tickets(query, search="", status="", priority="", customer_id=None):
    if search:
        like = f"%{search.strip()}%"
        query = query.join(Customer).filter(or_(
            Ticket.ticket_no.ilike(like), Ticket.title.ilike(like), Ticket.serial_number.ilike(like),
            Ticket.product_name.ilike(like), Customer.company_name.ilike(like)))
    if status in Status.ALL:
        query = query.filter(Ticket.status == status)
    if priority:
        query = query.filter(Ticket.priority == priority)
    if customer_id:
        query = query.filter(Ticket.customer_id == customer_id)
    return query


def dashboard_data(tickets_query, user):
    """Statistics and chart series for the tickets the user is allowed to see."""
    tickets = tickets_query.all()
    counts = {s: 0 for s in Status.ALL}
    for t in tickets:
        counts[t.status] += 1
    stats = {
        "total": len(tickets),
        "open": counts[Status.OPEN] + counts[Status.ASSIGNED],
        "in_progress": counts[Status.IN_PROGRESS] + counts[Status.INVESTIGATION],
        "pending": counts[Status.PENDING_MANAGEMENT],
        "rework": counts[Status.REWORK],
        "approved": counts[Status.APPROVED],
        "closed": counts[Status.CLOSED],
    }
    if user.role == Role.CUSTOMER:
        labels = {}
        for t in tickets:
            lab = customer_status(t)
            labels[lab] = labels.get(lab, 0) + 1
        return {"stats": stats, "status_chart": {"labels": list(labels), "values": list(labels.values())},
                "monthly": monthly_series(tickets), "workload": None, "categories": None}

    status_chart = {"labels": [s.replace("_", " ").title() for s in Status.ALL if counts[s]],
                    "values": [counts[s] for s in Status.ALL if counts[s]]}
    ids = [t.id for t in tickets]
    workload = None
    if user.role != Role.ENGINEER:
        load = {}
        for t in tickets:
            if t.assigned_engineer and t.status in Status.ENGINEER_EDITABLE + (Status.PENDING_MANAGEMENT,):
                load[t.assigned_engineer.full_name] = load.get(t.assigned_engineer.full_name, 0) + 1
        workload = {"labels": list(load), "values": list(load.values())}
    categories = {"labels": [], "values": []}
    if ids:
        rows = (db.session.query(Problem.category, func.count(Problem.id))
                .filter(Problem.ticket_id.in_(ids)).group_by(Problem.category).all())
        categories = {"labels": [r[0] for r in rows], "values": [r[1] for r in rows]}
    return {"stats": stats, "status_chart": status_chart, "monthly": monthly_series(tickets),
            "workload": workload, "categories": categories}


def monthly_series(tickets, months=12):
    now = utcnow()
    keys = []
    year, month = now.year, now.month
    for _ in range(months):
        keys.append((year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    keys.reverse()
    buckets = {k: 0 for k in keys}
    for t in tickets:
        k = (t.created_at.year, t.created_at.month)
        if k in buckets:
            buckets[k] += 1
    return {"labels": [f"{y}-{m:02d}" for y, m in keys], "values": [buckets[k] for k in keys]}
