from collections import defaultdict
from datetime import datetime

from app.extensions import db
from app.models import Attendance, User
from app.utils.constants import DEFAULT_LATE_AFTER
from app.utils.helpers import get_setting, guardian_ids_for_student, teacher_assigned_classes
from app.utils.notifications import notify_student_and_parent, notify_users


DEFAULT_PRESENT_GRACE_MINUTES = 5
MAX_PRESENT_GRACE_MINUTES = 14
LATE_GRACE_SECONDS = 15 * 60


def clock_minutes(value):
    """Parse a stored or submitted clock value as minutes after midnight."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("A clock time is required.")
    for time_format in ("%H:%M", "%H:%M:%S", "%I:%M %p"):
        try:
            parsed = datetime.strptime(text.upper(), time_format)
            return parsed.hour * 60 + parsed.minute
        except ValueError:
            continue
    raise ValueError(f"Invalid clock time: {value!r}")


def clock_seconds(value):
    """Parse a clock value to seconds after midnight, preserving scan seconds."""
    text = str(value or "").strip()
    for time_format in ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p"):
        try:
            parsed = datetime.strptime(text.upper(), time_format)
            return parsed.hour * 3600 + parsed.minute * 60 + parsed.second
        except ValueError:
            continue
    raise ValueError(f"Invalid clock time: {value!r}")


def normalize_clock(value):
    """Return a valid clock value in the HTML time-input format HH:MM."""
    minutes = clock_minutes(value)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def get_present_grace_minutes():
    """Read the configured Present grace, using a safe default for older installs."""
    value = get_setting("present_grace_minutes", DEFAULT_PRESENT_GRACE_MINUTES)
    try:
        minutes = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError("Present grace must be a whole number of minutes.")
    if not 0 <= minutes <= MAX_PRESENT_GRACE_MINUTES:
        raise ValueError(
            f"Present grace must be between 0 and {MAX_PRESENT_GRACE_MINUTES} minutes."
        )
    return minutes


def classify_morning_status(clock, late_after, grace_minutes=None):
    """Classify a scan against the configured allowed-time end and grace periods."""
    if grace_minutes is None:
        grace_minutes = DEFAULT_PRESENT_GRACE_MINUTES
    if not 0 <= int(grace_minutes) <= MAX_PRESENT_GRACE_MINUTES:
        raise ValueError(
            f"Present grace must be between 0 and {MAX_PRESENT_GRACE_MINUTES} minutes."
        )
    seconds_after_end = clock_seconds(clock) - clock_seconds(late_after)
    if seconds_after_end <= int(grace_minutes) * 60:
        return "Present"
    if seconds_after_end <= LATE_GRACE_SECONDS:
        return "Late"
    return "Absent"


def _attendance_summary_for_students(students, day=None):
    """Return attendance counts and roster percentages for a set of students."""
    day = day or datetime.now().date().isoformat()
    student_ids = {student.id for student in students}
    records = []
    if student_ids:
        records = Attendance.query.filter(
            Attendance.day == day,
            Attendance.student_id.in_(student_ids),
        ).order_by(Attendance.id.asc()).all()

    status_by_student = {}
    for record in records:
        status_by_student.setdefault(record.student_id, record.status.strip().casefold())

    total = len(student_ids)
    present = sum(status == "present" for status in status_by_student.values())
    late = sum(status == "late" for status in status_by_student.values())
    absent = sum(status == "absent" for status in status_by_student.values())
    unmarked = max(0, total - present - late - absent)

    def percentage(count):
        return round(count * 100 / total) if total else 0

    return {
        "day": day,
        "total_students": total,
        "present_count": present,
        "late_count": late,
        "absent_count": absent,
        "unmarked_count": unmarked,
        "present_percent": percentage(present),
        "late_percent": percentage(late),
        "absent_percent": percentage(absent),
    }


def class_attendance_summary(class_name, day=None):
    """Return counts and roster percentages for one class and attendance day."""
    class_label = (class_name or "").strip()
    students = []
    if class_label:
        students = User.query.filter(
            User.role == "student",
            db.or_(User.is_active_account.is_(True), User.is_active_account.is_(None)),
            db.func.lower(db.func.trim(User.class_name)) == class_label.casefold(),
        ).all()
    return _attendance_summary_for_students(students, day)


def school_attendance_summary(day=None):
    """Return aggregate attendance counts for every enrolled student."""
    students = User.query.filter(
        User.role == "student",
        db.or_(User.is_active_account.is_(True), User.is_active_account.is_(None)),
    ).all()
    return _attendance_summary_for_students(students, day)


def student_class_attendance_summary(student, day=None):
    """Summarize the exact class section associated with a scanned student's card."""
    fields = (
        ("department", student.department or student.trade),
        ("trade", student.trade),
        ("level", student.level),
        ("class_name", student.class_name),
        ("class_section", student.class_section),
    )
    query = User.query.filter(
        User.role == "student",
        db.or_(User.is_active_account.is_(True), User.is_active_account.is_(None)),
    )
    for field_name, value in fields:
        column = getattr(User, field_name)
        normalized_value = (value or "").strip()
        if normalized_value:
            query = query.filter(
                db.func.lower(db.func.trim(column)) == normalized_value.casefold()
            )
        else:
            query = query.filter(db.or_(column.is_(None), db.func.trim(column) == ""))
    return _attendance_summary_for_students(query.all(), day)


def class_absent_student_names(class_name, day=None):
    """Return only absent student names for the class status display."""
    class_label = (class_name or "").strip()
    if not class_label:
        return []
    day = day or datetime.now().date().isoformat()
    rows = (
        Attendance.query.join(User, Attendance.student_id == User.id)
        .filter(
            Attendance.day == day,
            db.func.lower(db.func.trim(Attendance.status)) == "absent",
            User.role == "student",
            db.or_(User.is_active_account.is_(True), User.is_active_account.is_(None)),
            db.func.lower(db.func.trim(User.class_name)) == class_label.casefold(),
        )
        .order_by(User.full_name.asc())
        .all()
    )
    return [row.student.full_name for row in rows]


def attendance_staff_ids_for_class(class_name):
    """Find school leaders and staff responsible for the student's class."""
    staff_ids = {
        user.id for user in User.query.filter(
            User.role.in_({"master_admin", "head_teacher", "secretary"})
        ).all()
    }

    class_key = (class_name or "").strip().casefold()
    staff_query = User.query.filter(User.role.in_({"matron", "patron", "discipline"}))
    if class_key:
        staff_query = staff_query.filter(
            db.or_(
                User.class_name.is_(None),
                User.class_name == "",
                db.func.lower(User.class_name) == class_key,
            )
        )
    else:
        staff_query = staff_query.filter(
            db.or_(User.class_name.is_(None), User.class_name == "")
        )
    staff_ids.update(user.id for user in staff_query.all())

    if class_key:
        for teacher in User.query.filter_by(role="teacher").all():
            assigned = {name.casefold() for name in teacher_assigned_classes(teacher)}
            if class_key in assigned:
                staff_ids.add(teacher.id)
    return staff_ids


def notify_class_attendance(student, status, day, recorded_at=None, session="morning"):
    """Notify class staff, the student, and verified guardians of a recorded scan."""
    class_label = student.class_name or "Unassigned class"
    if session == "afternoon":
        title = f"Afternoon scan recorded: {student.full_name}"
        detail = f"{student.full_name} ({class_label}) had an afternoon RFID scan on {day}."
        if recorded_at:
            detail += f" Time recorded: {recorded_at}."
        detail += f" Morning attendance remains {status}."
    elif status == "Absent":
        title = f"Absence recorded: {student.full_name}"
    elif status == "Late":
        title = f"Late arrival: {student.full_name}"
    elif status == "Present":
        title = f"Arrival recorded: {student.full_name}"
    else:
        title = f"Attendance updated: {student.full_name}"

    detail = f"{student.full_name} ({class_label}) was marked {status} on {day}."
    if recorded_at:
        detail += f" Time recorded: {recorded_at}."
    notify_users(attendance_staff_ids_for_class(student.class_name),
                 "Attendance", title, detail, "/attendance")
    notify_student_and_parent(student, "Attendance", title, detail, "/attendance")


def reconcile_daily_absences(now=None, exclude_student_ids=None):
    """Create absences after the 15-minute morning late-arrival grace period.

    The reconciliation is idempotent: students with an attendance record for
    today are left as recorded, including manual permissions and afternoon scans.
    It runs when the attendance page is opened, the RFID endpoint is used, or
    the scheduled ``reconcile-daily-attendance`` Flask command is invoked.
    """
    now = now or datetime.now()
    late_after = get_setting("late_after", DEFAULT_LATE_AFTER)
    try:
        allowed_end_seconds = clock_seconds(late_after)
        current_seconds = now.hour * 3600 + now.minute * 60 + now.second
        if current_seconds <= allowed_end_seconds + LATE_GRACE_SECONDS:
            return []
    except ValueError:
        # Invalid legacy settings must not create false all-school absences.
        return []

    today = now.date().isoformat()
    excluded = set(exclude_student_ids or ())
    existing_student_ids = {
        row.student_id for row in Attendance.query.filter_by(day=today).all()
    }
    missing_students = User.query.filter(
        User.role == "student",
        db.or_(User.is_active_account.is_(True), User.is_active_account.is_(None)),
    ).order_by(User.full_name).all()
    absent_by_class = defaultdict(list)

    for student in missing_students:
        if student.id in existing_student_ids or student.id in excluded:
            continue
        deadline_note = (
            f"Automatically marked absent after the 15-minute RFID late-arrival grace period "
            f"following {late_after}."
        )
        db.session.add(Attendance(
            student_id=student.id,
            day=today,
            status="Absent",
            source="System",
            note=deadline_note,
        ))
        absent_by_class[student.class_name or "Unassigned class"].append(student)
        notify_ids = {student.id, *guardian_ids_for_student(student.id)}
        notify_users(
            notify_ids,
            "Attendance",
            "Absence alert",
            f"{student.full_name} was marked Absent on {today} because no morning RFID arrival "
            f"was recorded within 15 minutes after {late_after}.",
            "/attendance",
        )

    for class_name, students in absent_by_class.items():
        names = ", ".join(student.full_name for student in students)
        body = (
            f"No morning RFID arrival was recorded within 15 minutes after {late_after} "
            f"for {len(students)} student(s) "
            f"in {class_name} on {today}: {names}."
        )
        notify_users(
            attendance_staff_ids_for_class(None if class_name == "Unassigned class" else class_name),
            "Attendance",
            f"{class_name}: {len(students)} absence(s)",
            body,
            "/attendance",
        )

    return [student for students in absent_by_class.values() for student in students]
