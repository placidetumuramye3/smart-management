from collections import defaultdict
from datetime import datetime

from app.extensions import db
from app.models import Attendance, User
from app.utils.constants import DEFAULT_LATE_AFTER
from app.utils.helpers import get_setting, guardian_ids_for_student, teacher_assigned_classes
from app.utils.notifications import notify_student_and_parent, notify_users


def classify_morning_status(clock, late_after):
    return "Late" if clock > late_after else "Present"


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


def notify_class_attendance(student, status, day, recorded_at=None):
    """Send one attendance event to the class team and relevant family accounts."""
    class_label = student.class_name or "Unassigned class"
    if status == "Absent":
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

    if status in {"Absent", "Late"}:
        notify_student_and_parent(student, "Attendance", "Attendance alert", detail, "/attendance")


def reconcile_daily_absences(now=None, exclude_student_ids=None):
    """Create daily absence records after the configured morning late deadline.

    The reconciliation is idempotent: students with an attendance record for
    today are left as recorded, including manual permissions and afternoon scans.
    It runs when the attendance page is opened or the RFID endpoint is used.
    """
    now = now or datetime.now()
    late_after = get_setting("late_after", DEFAULT_LATE_AFTER)
    if now.strftime("%H:%M") <= late_after:
        return []

    today = now.date().isoformat()
    excluded = set(exclude_student_ids or ())
    existing_student_ids = {
        row.student_id for row in Attendance.query.filter_by(day=today).all()
    }
    missing_students = User.query.filter_by(role="student").order_by(User.full_name).all()
    absent_by_class = defaultdict(list)

    for student in missing_students:
        if student.id in existing_student_ids or student.id in excluded:
            continue
        deadline_note = f"Automatically marked absent after the {late_after} RFID arrival deadline."
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
            f"{student.full_name} was marked Absent on {today} because no morning RFID arrival was recorded by {late_after}.",
            "/attendance",
        )

    for class_name, students in absent_by_class.items():
        names = ", ".join(student.full_name for student in students)
        body = (
            f"No morning RFID arrival was recorded by {late_after} for {len(students)} student(s) "
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
