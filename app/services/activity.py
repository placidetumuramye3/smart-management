from app.extensions import db
from app.models import ActivityLog


def log_student_activity(actor, student, action, details):
    db.session.add(ActivityLog(
        actor_id=actor.id if actor else None,
        actor_name=actor.full_name if actor else "System",
        student_id=student.id if student else None,
        student_name=student.full_name if student else "Unknown student",
        action=action,
        details=details,
    ))
