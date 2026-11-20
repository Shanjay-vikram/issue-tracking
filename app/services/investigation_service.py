from ..extensions import db
from ..models import (Investigation, InvestigationUpdate, ProposedSolution, Status, STAGES, utcnow)
from . import WorkflowError

TEXT_FIELDS = ("problem_description", "observations", "suspected_root_cause", "confirmed_root_cause",
               "corrective_actions", "preventive_actions", "technical_comments", "conclusion")
FIELD_LABELS = {
    "problem_description": "problem description", "observations": "observations",
    "suspected_root_cause": "suspected root cause", "confirmed_root_cause": "confirmed root cause",
    "corrective_actions": "corrective actions", "preventive_actions": "preventive actions",
    "technical_comments": "technical comments", "conclusion": "conclusion", "stage": "stage",
}


def require_editable(ticket):
    if ticket.status not in Status.ENGINEER_EDITABLE:
        raise WorkflowError("This investigation is locked. It is awaiting Management or already approved.")


def save_investigation(problem, data: dict, user):
    """Apply form data, record who changed what, and move the ticket status forward."""
    ticket = problem.ticket
    require_editable(ticket)
    inv = problem.investigation
    changed = []
    new_stage = int(data["stage"])
    if new_stage not in STAGES or new_stage == 7:
        raise WorkflowError("Stage 7 is set automatically when you submit to Management.")
    if new_stage != inv.stage:
        inv.stage = new_stage
        changed.append("stage")
    for field in TEXT_FIELDS:
        value = (data.get(field) or "").strip()
        if value != (getattr(inv, field) or ""):
            setattr(inv, field, value)
            changed.append(field)
    note = (data.get("note") or "").strip()
    if not changed and not note:
        return False
    inv.updated_by_id = user.id
    db.session.add(InvestigationUpdate(
        investigation=inv, user_id=user.id, stage=inv.stage, note=note,
        changed_fields=", ".join(FIELD_LABELS[c] for c in changed)))
    refresh_ticket_status(ticket)
    return True


def refresh_ticket_status(ticket):
    """ASSIGNED -> IN_PROGRESS -> INVESTIGATION as work advances. REWORK stays until resubmission."""
    if ticket.status in (Status.ASSIGNED, Status.IN_PROGRESS, Status.INVESTIGATION):
        top = max((p.stage for p in ticket.problems), default=1)
        ticket.status = Status.INVESTIGATION if top >= 3 else Status.IN_PROGRESS


def touch(problem, user, note):
    """Record an evidence/question change in the investigation history."""
    inv = problem.investigation
    inv.updated_by_id = user.id
    inv.updated_at = utcnow()
    db.session.add(InvestigationUpdate(investigation=inv, user_id=user.id, stage=inv.stage,
                                       note=note, changed_fields=""))
    refresh_ticket_status(problem.ticket)


def add_solution(problem, description, user):
    require_editable(problem.ticket)
    db.session.add(ProposedSolution(problem=problem, description=description, created_by_id=user.id))
    touch(problem, user, "Proposed solution added.")


def completeness_problems(ticket):
    """Return a list of human-readable reasons the ticket cannot go to Management yet."""
    issues = []
    if not ticket.problems:
        return ["Add at least one problem to the ticket."]
    for i, p in enumerate(ticket.problems, 1):
        inv = p.investigation
        tag = f"Problem {i} ({p.title})"
        if inv.stage < 6:
            issues.append(f"{tag}: move the investigation to the Validation stage.")
        if not inv.observations.strip():
            issues.append(f"{tag}: add engineering observations.")
        if not inv.confirmed_root_cause.strip():
            issues.append(f"{tag}: record the confirmed root cause.")
        if not p.solutions:
            issues.append(f"{tag}: add at least one proposed solution.")
    return issues
