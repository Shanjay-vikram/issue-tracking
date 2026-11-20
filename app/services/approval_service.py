from ..extensions import db
from ..models import ManagementApproval, Role, Status
from . import WorkflowError
from .. import audit
from .investigation_service import completeness_problems


def submit_to_management(ticket, user):
    if user.role != Role.ENGINEER or ticket.assigned_engineer_id != user.id:
        raise WorkflowError("Only the assigned engineer can submit this ticket.")
    if ticket.status not in Status.ENGINEER_EDITABLE:
        raise WorkflowError("This ticket cannot be submitted in its current status.")
    issues = completeness_problems(ticket)
    if issues:
        raise WorkflowError("Complete these items first: " + " ".join(issues))
    for p in ticket.problems:
        p.investigation.stage = 7
        p.investigation.locked = True
    ticket.status = Status.PENDING_MANAGEMENT
    audit.log("ticket.submitted", "Ticket", ticket.id, f"{ticket.ticket_no} submitted to Management")


def decide(ticket, user, decision: str, comments: str):
    if user.role != Role.MANAGEMENT:
        raise WorkflowError("Only Management can approve or return investigations.")
    if ticket.status != Status.PENDING_MANAGEMENT:
        raise WorkflowError("This ticket is not waiting for a Management decision.")
    comments = (comments or "").strip()
    if decision not in ("APPROVED", "RETURNED"):
        raise WorkflowError("Unknown decision.")
    if decision == "RETURNED" and len(comments) < 5:
        raise WorkflowError("Explain what needs rework (at least a short comment is required).")
    db.session.add(ManagementApproval(ticket=ticket, decision=decision, comments=comments, decided_by_id=user.id))
    if decision == "APPROVED":
        ticket.status = Status.APPROVED
        for p in ticket.problems:
            p.investigation.locked = True
    else:
        ticket.status = Status.REWORK
        for p in ticket.problems:
            p.investigation.locked = False
            p.investigation.stage = 6  # back to Validation so the engineer can continue
    audit.log(f"approval.{decision.lower()}", "Ticket", ticket.id, f"{ticket.ticket_no}: {comments[:300]}")
