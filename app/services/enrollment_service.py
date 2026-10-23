import datetime

from app.extensions import db
from app.models import ActivityLog, StudentEnrollment, User
from app.utils.constants import CLASS_TRADE_LEVEL
from app.utils.helpers import now_string


ENROLLMENT_STAFF_ROLES = {"master_admin", "head_teacher", "secretary"}
ENROLLMENT_STATUSES = {"active", "transferred", "withdrawn", "graduated"}


def current_enrollment(student_id):
    return StudentEnrollment.query.filter_by(student_id=student_id, status="active").order_by(
        StudentEnrollment.id.desc(),
    ).first()


def _require_staff(actor):
    if not actor or actor.role not in ENROLLMENT_STAFF_ROLES:
        raise PermissionError("Only school leadership or the secretary can change student enrollment.")


def _student(student):
    if not student or student.role != "student":
        raise ValueError("Choose a valid student account.")


def _class_details(class_name):
    class_name = str(class_name or "").strip()
    details = CLASS_TRADE_LEVEL.get(class_name)
    if not details:
        raise ValueError("Choose a class from the school's configured programme and level list.")
    trade, level = details
    return class_name, trade, level


def _academic_year(value):
    value = str(value or "").strip()
    parts = value.split("-")
    if (len(parts) != 2 or any(len(part) != 4 or not part.isdigit() for part in parts)
            or int(parts[1]) != int(parts[0]) + 1):
        raise ValueError("Enter an academic year such as 2026-2027.")
    return value


def _date(value):
    value = str(value or "").strip()
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        raise ValueError("Enter an effective date in YYYY-MM-DD format.")
    return value


def _log(actor, student, action, details):
    db.session.add(ActivityLog(
        actor_id=actor.id, actor_name=actor.full_name,
        student_id=student.id, student_name=student.full_name,
        action=action, details=details,
    ))


def create_initial_enrollment(actor, student, class_name, academic_year, start_date=None):
    """Create the initial record after registration of the canonical User row."""
    _require_staff(actor)
    _student(student)
    class_name, trade, level = _class_details(class_name)
    academic_year = _academic_year(academic_year)
    effective_date = _date(start_date or datetime.date.today().isoformat())
    active = current_enrollment(student.id)
    if active:
        if (active.class_name, active.academic_year) == (class_name, academic_year):
            return active
        raise ValueError("This student already has an active enrollment. Use the transfer workflow.")

    student.class_name = class_name
    student.trade = trade
    student.level = level
    student.academic_year = academic_year
    record = StudentEnrollment(
        student_id=student.id, class_name=class_name, trade=trade, level=level,
        academic_year=academic_year, start_date=effective_date, status="active",
        recorded_by_id=actor.id, created_at=now_string(),
    )
    db.session.add(record)
    _log(actor, student, "Student enrollment created",
         f"Initial enrollment in {class_name} for {academic_year}, effective {effective_date}.")
    return record


def transfer_student(actor, student, class_name, academic_year, effective_date, reason=""):
    _require_staff(actor)
    _student(student)
    class_name, trade, level = _class_details(class_name)
    academic_year = _academic_year(academic_year)
    effective_date = _date(effective_date)
    reason = str(reason or "").strip()
    if len(reason) > 500:
        raise ValueError("Transfer reason must be 500 characters or fewer.")
    current = current_enrollment(student.id)
    if not current:
        raise ValueError("This student has no active enrollment to transfer.")
    if (current.class_name, current.academic_year) == (class_name, academic_year):
        raise ValueError("The student is already enrolled in that class and academic year.")
    if current.start_date and effective_date < current.start_date:
        raise ValueError("A transfer cannot begin before the current enrollment start date.")

    current.status = "transferred"
    current.end_date = effective_date
    current.reason = reason or f"Transferred to {class_name}."
    db.session.flush()
    new_record = StudentEnrollment(
        student_id=student.id, class_name=class_name, trade=trade, level=level,
        academic_year=academic_year, start_date=effective_date, status="active",
        reason=reason or "Transfer", recorded_by_id=actor.id, created_at=now_string(),
    )
    student.class_name = class_name
    student.trade = trade
    student.level = level
    student.academic_year = academic_year
    db.session.add(new_record)
    _log(actor, student, "Student transferred",
         f"Enrollment changed from {current.class_name or 'Unassigned'} to {class_name}, "
         f"effective {effective_date}. {reason}".strip())
    return new_record


def close_enrollment(actor, student, status, effective_date, reason=""):
    _require_staff(actor)
    _student(student)
    if status not in {"withdrawn", "graduated"}:
        raise ValueError("Choose withdrawal or graduation to close an enrollment.")
    effective_date = _date(effective_date)
    reason = str(reason or "").strip()
    if len(reason) > 500:
        raise ValueError("The reason must be 500 characters or fewer.")
    current = current_enrollment(student.id)
    if not current:
        raise ValueError("This student has no active enrollment to close.")
    if current.start_date and effective_date < current.start_date:
        raise ValueError("The effective date cannot be before the current enrollment start date.")

    current.status = status
    current.end_date = effective_date
    current.reason = reason or status.title()
    student.class_name = None
    db.session.add(ActivityLog(
        actor_id=actor.id, actor_name=actor.full_name,
        student_id=student.id, student_name=student.full_name,
        action=f"Student {status}",
        details=f"Enrollment closed effective {effective_date}. {reason}".strip(),
    ))
    return current
