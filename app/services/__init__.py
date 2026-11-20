class WorkflowError(ValueError):
    """Raised when an action is not allowed in the ticket's current state."""
