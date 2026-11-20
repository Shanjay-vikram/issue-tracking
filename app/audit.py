from flask import has_request_context, request
from flask_login import current_user

from .extensions import db
from .models import AuditLog


def log(action, entity_type="", entity_id=None, details="", user=None):
    """Add an audit entry to the current session. The caller commits."""
    user = user or (current_user if has_request_context() and current_user.is_authenticated else None)
    entry = AuditLog(
        user_id=user.id if user is not None else None,
        username=user.username if user is not None else "anonymous",
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=(details or "")[:500],
        ip_address=(request.remote_addr or "")[:64] if has_request_context() else "",
    )
    db.session.add(entry)
    return entry
