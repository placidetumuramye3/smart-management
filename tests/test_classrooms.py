import datetime
import json

from app.extensions import db
from app.models import (
    AcademicSubject, Assessment, LearningMaterial, LessonPlan, StudentGuardian, User,
)
from app.services.enrollment_service import create_initial_enrollment


def make_user(username, role, *, class_name=None, parent_id=None):
    user = User(
        username=username,
        full_name=username.replace("_", " ").title(),
        password_hash="test-hash",
        role=role,
        class_name=class_name,
        parent_id=parent_id,
        academic_year="2026-2027" if role == "student" else None,
        is_active_account=True,
    )
    db.session.add(user)
    db.session.flush()
    return user


def login_as(client, user):
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True


def test_student_classroom_shows_only_current_enrollment_and_published_work(app):
    secretary = make_user("secretary", "secretary")
    teacher = make_user("teacher", "teacher")
    student = make_user("learner", "student", class_name="L3 Software Development")
    other_student = make_user("other_learner", "student", class_name="L3 Software Development")
    create_initial_enrollment(secretary, student, "L3 Software Development", "2026-2027")
    create_initial_enrollment(secretary, other_student, "L3 Software Development", "2026-2027")
    subject = AcademicSubject(
        trade="ICT and Multimedia", level="L3", class_name=student.class_name,
        subject="Programming", teacher_id=teacher.id, academic_year="2026-2027",
        is_active=True,
    )
    db.session.add(subject)
    db.session.flush()
    db.session.add_all([
        LessonPlan(subject_id=subject.id, teacher_id=teacher.id,
                   lesson_date=datetime.date(2026, 10, 20), topic="Loops",
                   objectives="Use repetition", status="Reviewed"),
        LessonPlan(subject_id=subject.id, teacher_id=teacher.id,
                   lesson_date=datetime.date(2026, 10, 21), topic="Draft topic",
                   objectives="Internal review", status="Pending"),
        LearningMaterial(teacher_id=teacher.id, title="Loop notes", class_name=student.class_name,
                         subject="Programming", is_published=True, created_at="2026-10-20 12:00:00"),
        LearningMaterial(teacher_id=teacher.id, title="Draft notes", class_name=student.class_name,
                         subject="Programming", is_published=False, created_at="2026-10-21 12:00:00"),
        Assessment(teacher_id=teacher.id, title="Practice quiz", assessment_type="Quiz",
                   class_name=student.class_name, subject="Programming", term="Term 1",
                   due_at="2026-10-30 12:00:00", max_score=10, is_published=True,
                   created_at="2026-10-20 12:00:00"),
        Assessment(teacher_id=teacher.id, title="Other student's assignment", assessment_type="Homework",
                   class_name=student.class_name, target_student_ids=json.dumps([other_student.id]),
                   subject="Programming", term="Term 1", max_score=10, is_published=True,
                   created_at="2026-10-21 12:00:00"),
    ])
    db.session.commit()

    client = app.test_client()
    login_as(client, student)
    response = client.get("/classrooms")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Programming" in body
    assert "Loops" in body
    assert "Loop notes" in body
    assert "Practice quiz" in body
    assert "Draft topic" not in body
    assert "Draft notes" not in body
    assert "Other student's assignment" not in body


def test_parent_classroom_requires_a_verified_child_enrollment(app):
    parent = make_user("parent", "parent")
    student = make_user("learner", "student", class_name="L3 Software Development")
    secretary = make_user("secretary", "secretary")
    teacher = make_user("teacher", "teacher")
    create_initial_enrollment(secretary, student, "L3 Software Development", "2026-2027")
    subject = AcademicSubject(
        trade="ICT and Multimedia", level="L3", class_name=student.class_name,
        subject="Programming", teacher_id=teacher.id, academic_year="2026-2027",
        is_active=True,
    )
    db.session.add(subject)
    resource = LearningMaterial(
        teacher_id=teacher.id, title="Guardian Resource", class_name=student.class_name,
        subject="Programming", is_published=True, created_at="2026-10-20 12:00:00",
    )
    db.session.add(resource)
    db.session.commit()

    client = app.test_client()
    login_as(client, parent)
    response = client.get("/classrooms")
    assert response.status_code == 200
    assert "Programming" not in response.get_data(as_text=True)
    unlinked_materials = client.get("/learning-materials")
    assert unlinked_materials.status_code == 200
    assert b"Guardian Resource" not in unlinked_materials.data

    db.session.add(StudentGuardian(
        student_id=student.id, guardian_id=parent.id, relationship="Parent / Guardian",
        status="verified", verified_at="2026-10-23 12:00:00",
    ))
    db.session.commit()
    linked_response = client.get("/classrooms")
    assert "Programming" in linked_response.get_data(as_text=True)
    linked_materials = client.get("/learning-materials")
    assert linked_materials.status_code == 200
    assert b"Guardian Resource" in linked_materials.data


def test_teacher_classroom_list_is_limited_to_assigned_subjects(app):
    teacher = make_user("teacher", "teacher")
    other_teacher = make_user("other_teacher", "teacher")
    db.session.add_all([
        AcademicSubject(trade="ICT and Multimedia", level="L3",
                        class_name="L3 Software Development", subject="Programming",
                        teacher_id=teacher.id, academic_year="2026-2027", is_active=True),
        AcademicSubject(trade="ICT and Multimedia", level="L4",
                        class_name="L4 Software Development A", subject="Networks",
                        teacher_id=other_teacher.id, academic_year="2026-2027", is_active=True),
    ])
    db.session.commit()

    client = app.test_client()
    login_as(client, teacher)
    response = client.get("/classrooms")

    assert response.status_code == 200
    assert b"Programming" in response.data
    assert b"Networks" not in response.data
