import datetime
import secrets
from app.extensions import db
from app.models import Setting, User, Attendance, AcademicSubject, TimetableEntry

def now_string():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def get_setting(key, default):
    row = Setting.query.filter_by(key=key).first()
    return row.value if row else default

def set_setting(key, value):
    row = Setting.query.filter_by(key=key).first()
    if row:
        row.value = value
    else:
        db.session.add(Setting(key=key, value=value))
    db.session.commit()

def linked_children(user_id):
    return User.query.filter_by(parent_id=user_id, role="student").all()

def generate_sdms_code():
    """Return an unused, human-readable student SDMS code."""
    while True:
        code = "SDMS-" + secrets.token_hex(4).upper()
        if not User.query.filter_by(sdms_code=code).first():
            return code

def attendance_for_student(student_id):
    return Attendance.query.filter_by(student_id=student_id).order_by(Attendance.day.desc()).all()

def teacher_assignments(teacher):
    """Return assigned (class, subject) pairs for a teacher, with timetable fallback."""
    subjects = AcademicSubject.query.filter_by(teacher_id=teacher.id, is_active=True).all()
    assignments = {(item.class_name, item.subject.strip()) for item in subjects}
    names = {teacher.full_name.strip().casefold(), teacher.username.strip().casefold()}
    assignments.update({
        (entry.class_name, entry.subject.strip())
        for entry in TimetableEntry.query.all()
        if entry.teacher_name and entry.teacher_name.strip().casefold() in names
    })
    return assignments

def teacher_assigned_classes(teacher):
    classes = {class_name for class_name, _ in teacher_assignments(teacher)}
    if teacher.class_name:
        classes.add(teacher.class_name)
    return classes

def teacher_can_access_class(teacher, class_name):
    return bool(class_name) and class_name.casefold() in {
        assigned_class.casefold() for assigned_class in teacher_assigned_classes(teacher)
    }
