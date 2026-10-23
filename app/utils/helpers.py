import datetime
import secrets
from app.extensions import db
from app.models import Setting, User, Attendance, AcademicSubject, TimetableEntry, StudentGuardian

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
    student_ids = verified_student_ids(user_id)
    if not student_ids:
        return []
    return User.query.filter(User.id.in_(student_ids), User.role == "student").order_by(User.full_name).all()


def verified_student_ids(guardian_id):
    """Return verified linked students, respecting explicit guardian revocations."""
    explicit = StudentGuardian.query.filter_by(guardian_id=guardian_id).all()
    by_student = {link.student_id: link.status for link in explicit}
    result = {student_id for student_id, status in by_student.items() if status == "verified"}
    legacy = User.query.filter_by(role="student", parent_id=guardian_id).all()
    result.update(student.id for student in legacy if student.id not in by_student)
    return result


def guardian_ids_for_student(student_id):
    """Return verified guardian accounts plus a legacy parent if not explicitly revoked."""
    explicit = StudentGuardian.query.filter_by(student_id=student_id).all()
    by_guardian = {link.guardian_id: link.status for link in explicit}
    result = set()
    for link in explicit:
        if link.status == "verified" and link.guardian and link.guardian.role == "parent":
            result.add(link.guardian_id)
    student = db.session.get(User, student_id)
    if student and student.parent_id and student.parent_id not in by_guardian:
        parent = db.session.get(User, student.parent_id)
        if parent and parent.role == "parent":
            result.add(parent.id)
    return result


def guardian_can_access_student(guardian_id, student_id):
    return student_id in verified_student_ids(guardian_id)

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
