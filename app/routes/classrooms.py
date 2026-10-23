import json

from flask import abort, url_for
from flask_login import current_user
from markupsafe import escape

from app.models import AcademicSubject, Assessment, LearningMaterial, LessonPlan
from app.services.enrollment_service import current_enrollment
from app.utils.decorators import permission_for
from app.utils.helpers import linked_children, teacher_assignments
from app.utils.rendering import page


OFFICE_ROLES = {"master_admin", "head_teacher", "secretary"}
CLASSROOM_ROLES = OFFICE_ROLES | {"teacher", "parent", "student"}


def _students_and_enrollments(user):
    if user.role == "student":
        students = [user]
    elif user.role == "parent":
        students = [student for student in linked_children(user.id) if student.is_active_account]
    else:
        return []
    rows = []
    for student in students:
        enrollment = current_enrollment(student.id)
        if enrollment:
            rows.append((student, enrollment))
    return rows


def _assessment_targets(assessment):
    if not assessment.target_student_ids:
        return None
    try:
        return {int(student_id) for student_id in json.loads(assessment.target_student_ids)}
    except (TypeError, ValueError, json.JSONDecodeError):
        return set()


def _visible_classrooms(user):
    subjects = AcademicSubject.query.filter_by(is_active=True).all()
    child_enrollments = _students_and_enrollments(user) if user.role in {"student", "parent"} else []
    if user.role == "teacher":
        assigned = {(class_name.casefold(), subject.casefold())
                    for class_name, subject in teacher_assignments(user)}
        subjects = [row for row in subjects if
                    (row.class_name.casefold(), row.subject.casefold()) in assigned]
    elif user.role in {"student", "parent"}:
        eligible = {
            (enrollment.class_name.casefold(), enrollment.academic_year)
            for _student, enrollment in child_enrollments
        }
        subjects = [row for row in subjects if any(
            row.class_name.casefold() == class_name
            and (not year or not row.academic_year or row.academic_year == year)
            for class_name, year in eligible
        )]

    classrooms = {}
    for subject in subjects:
        key = (subject.class_name, subject.subject)
        classrooms.setdefault(key, {"subjects": [], "students": []})["subjects"].append(subject)
    if user.role in {"student", "parent"}:
        for student, enrollment in child_enrollments:
            for class_name, subject_name in list(classrooms):
                if class_name.casefold() == enrollment.class_name.casefold():
                    classrooms[(class_name, subject_name)]["students"].append(student)

    materials = LearningMaterial.query.order_by(
        LearningMaterial.created_at.desc(), LearningMaterial.id.desc(),
    ).all()
    assessments = Assessment.query.order_by(Assessment.due_at, Assessment.id.desc()).all()
    plans = LessonPlan.query.join(AcademicSubject).order_by(
        LessonPlan.lesson_date.desc(), LessonPlan.id.desc(),
    ).all()

    for (class_name, subject_name), data in classrooms.items():
        class_key, subject_key = class_name.casefold(), subject_name.casefold()
        data["materials"] = [material for material in materials if
            (material.class_name.casefold(), material.subject.casefold()) == (class_key, subject_key)
            and (material.is_published or user.role in OFFICE_ROLES
                 or (user.role == "teacher" and material.teacher_id == user.id))]
        data["assessments"] = []
        for assessment in assessments:
            if ((assessment.class_name.casefold(), assessment.subject.casefold()) != (class_key, subject_key)
                    or (not assessment.is_published and user.role not in OFFICE_ROLES
                        and not (user.role == "teacher" and assessment.teacher_id == user.id))):
                continue
            targets = _assessment_targets(assessment)
            if targets is not None and user.role in {"student", "parent"}:
                accessible_student_ids = {student.id for student in data["students"]}
                if not targets.intersection(accessible_student_ids):
                    continue
            data["assessments"].append(assessment)
        subject_ids = {subject.id for subject in data["subjects"]}
        data["lessons"] = [plan for plan in plans if plan.subject_id in subject_ids and (
            plan.status == "Reviewed" if user.role in {"student", "parent"}
            else user.role in OFFICE_ROLES or plan.teacher_id == user.id
        )]

    return classrooms


def classrooms():
    if current_user.role not in CLASSROOM_ROLES:
        abort(403)
    grouped = _visible_classrooms(current_user)
    cards = []
    for (class_name, subject_name), data in sorted(
        grouped.items(), key=lambda item: (item[0][0].casefold(), item[0][1].casefold()),
    ):
        teachers = {subject.teacher.full_name for subject in data["subjects"] if subject.teacher}
        teacher_label = f" · {escape(', '.join(sorted(teachers)))}" if teachers else ""
        lesson_rows = "".join(
            f"<li><strong>{escape(plan.lesson_date.isoformat())}</strong> · {escape(plan.topic)}</li>"
            for plan in data["lessons"][:4]
        ) or "<li class='muted'>No reviewed lesson plans are available.</li>"
        material_rows = "".join(
            f"<li>{escape(material.title)} <span class='muted'>({escape(material.resource_type)})</span></li>"
            for material in data["materials"][:4]
        ) or "<li class='muted'>No learning resources are available.</li>"
        assessment_rows = "".join(
            f"<li>{escape(assessment.title)} <span class='muted'>· Due {escape(assessment.due_at or 'no deadline')}</span></li>"
            for assessment in data["assessments"][:4]
        ) or "<li class='muted'>No assignments or assessments are available.</li>"
        cards.append(
            "<article class='panel classroom-card'>"
            f"<div class='d-flex justify-content-between flex-wrap gap-2'><div><span class='pill'>{escape(class_name)}</span>"
            f"<h2 class='mt-2'>{escape(subject_name)}</h2><p class='muted'>Official subject assignment{teacher_label}</p></div></div>"
            f"<div class='classroom-columns'><section><h3>Lessons</h3><ul>{lesson_rows}</ul></section>"
            f"<section><h3>Learning materials</h3><ul>{material_rows}</ul></section>"
            f"<section><h3>Assignments</h3><ul>{assessment_rows}</ul></section></div>"
            f"<div class='d-flex flex-wrap gap-2 mt-3'><a class='btn btn-sm btn-outline-success' href='{url_for('learning_materials')}'>Open learning materials</a>"
            f"<a class='btn btn-sm btn-accent' href='{url_for('assessments')}'>Open assessments</a></div></article>"
        )
    empty_message = (
        "No active subject classrooms are available for your current enrollment or assignments."
        if current_user.role in {"student", "parent", "teacher"}
        else "No active subject classrooms are configured yet."
    )
    empty_state = f"<section class='panel muted'>{escape(empty_message)}</section>"
    body = (
        "<section class='panel'><span class='pill'>CLASSROOMS</span><h1 class='mt-2'>Learning <span class='accent'>Classrooms</span></h1>"
        "<p class='muted'>Lessons, learning resources, and published assignments grouped by the school's existing class and subject records.</p></section>"
        f"{''.join(cards) if cards else empty_state}"
        "<style>.classroom-columns{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-top:16px}"
        ".classroom-columns section{min-width:0;border:1px solid var(--border);border-radius:12px;padding:14px}"
        ".classroom-columns h3{font-size:.95rem;font-weight:750}.classroom-columns ul{padding-left:18px;margin:0}"
        ".classroom-columns li{margin:7px 0;overflow-wrap:anywhere}.classroom-card{margin-bottom:16px}</style>"
    )
    return page("Learning Classrooms", body, "teacher")


def register_routes(app):
    app.add_url_rule(
        "/classrooms", endpoint="classrooms",
        view_func=permission_for("classrooms")(classrooms), methods=["GET"],
    )
