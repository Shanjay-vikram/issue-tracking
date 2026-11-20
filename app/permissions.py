"""Backend authorization. Every route calls one of these; hiding links is never the only control."""
from functools import wraps

from flask import abort
from flask_login import current_user, login_required

from .extensions import db
from .models import Role, Status, Ticket


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def visible_tickets_query(user):
    """Tickets the user may see at all."""
    query = Ticket.query
    if user.role in (Role.ADMIN, Role.SUPPORT, Role.MANAGEMENT):
        return query
    if user.role == Role.ENGINEER:
        return query.filter(Ticket.assigned_engineer_id == user.id)
    if user.role == Role.CUSTOMER and user.customer_id:
        return query.filter(Ticket.customer_id == user.customer_id)
    return query.filter(Ticket.id == -1)


def can_view_ticket(user, ticket) -> bool:
    if user.role in (Role.ADMIN, Role.SUPPORT, Role.MANAGEMENT):
        return True
    if user.role == Role.ENGINEER:
        return ticket.assigned_engineer_id == user.id
    if user.role == Role.CUSTOMER:
        return bool(user.customer_id) and ticket.customer_id == user.customer_id
    return False


def get_ticket_or_404(ticket_id, user):
    """Object-level check. Returns 404 (not 403) so IDs of other tenants cannot be probed."""
    ticket = db.session.get(Ticket, ticket_id)
    if ticket is None or not can_view_ticket(user, ticket):
        abort(404)
    return ticket


def can_edit_investigation(user, ticket) -> bool:
    return (user.role == Role.ENGINEER
            and ticket.assigned_engineer_id == user.id
            and ticket.status in Status.ENGINEER_EDITABLE)


def can_customer_download(ticket) -> bool:
    return ticket.status in (Status.APPROVED, Status.CLOSED) and ticket.latest_report is not None
