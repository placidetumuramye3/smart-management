from functools import wraps
from flask import abort
from flask_login import login_required, current_user
from app.utils.constants import ROLE_ACCESS

def permission_for(endpoint):
    """Protect a route using the role matrix; unknown routes fail closed."""
    roles = ROLE_ACCESS.get(endpoint, set())
    return role_required(*roles)

def role_required(*roles):
    def decorator(fn):
        @wraps(fn)
        @login_required
        def wrapped(*args, **kwargs):
            if current_user.role not in roles:
                abort(403)
            return fn(*args, **kwargs)
        return wrapped
    return decorator
