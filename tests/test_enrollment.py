import pytest
from sqlalchemy import create_engine, text

from app.extensions import db
from app.models import StudentEnrollment, User
from app.services.enrollment_service import (
    close_enrollment, create_initial_enrollment, current_enrollment, transfer_student,
)


def make_user(username, role, *, class_name=None, trade=None, level=None, academic_year=None):
    user = User(
        username=username, full_name=username.replace("_", " ").title(),
        password_hash="test-hash", role=role, class_name=class_name,
        trade=trade, level=level, academic_year=academic_year,
        is_active_account=True,
    )
    db.session.add(user)
    db.session.flush()
    return user


def login_as(client, user):
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True


def test_transfer_appends_history_and_updates_current_class(app):
    secretary = make_user("secretary", "secretary")
    student = make_user("learner", "student")
    initial = create_initial_enrollment(
        secretary, student, "L3 Software Development", "2025-2026", "2025-09-01",
    )
    db.session.commit()

    moved = transfer_student(
        secretary, student, "L4 Software Development A", "2026-2027",
        "2026-09-01", "Promoted after annual review.",
    )
    db.session.commit()

    assert initial.status == "transferred"
    assert initial.end_date == "2026-09-01"
    assert moved.status == "active"
    assert moved.class_name == student.class_name == "L4 Software Development A"
    assert student.academic_year == "2026-2027"
    assert StudentEnrollment.query.filter_by(student_id=student.id, status="active").count() == 1


def test_withdrawal_closes_current_enrollment(app):
    head = make_user("head", "head_teacher")
    student = make_user("learner", "student")
    current = create_initial_enrollment(head, student, "L3 Software Development", "2025-2026", "2025-09-01")
    db.session.commit()

    closed = close_enrollment(head, student, "withdrawn", "2026-01-20", "Family relocation.")
    db.session.commit()

    assert closed.id == current.id
    assert closed.status == "withdrawn"
    assert closed.end_date == "2026-01-20"
    assert current_enrollment(student.id) is None
    assert student.class_name is None


def test_invalid_staff_and_class_cannot_change_enrollment(app):
    teacher = make_user("teacher", "teacher")
    student = make_user("learner", "student")

    with pytest.raises(PermissionError):
        create_initial_enrollment(teacher, student, "L3 Software Development", "2025-2026")
    secretary = make_user("secretary", "secretary")
    with pytest.raises(ValueError):
        create_initial_enrollment(secretary, student, "Unconfigured Class", "2025-2026")


def test_enrollment_page_is_role_scoped_and_transfer_requires_csrf(app):
    head = make_user("head", "head_teacher")
    student = make_user("learner", "student")
    create_initial_enrollment(head, student, "L3 Software Development", "2025-2026", "2025-09-01")
    db.session.commit()

    staff_client = app.test_client()
    login_as(staff_client, head)
    page = staff_client.get(f"/students/{student.id}/enrollment")
    assert page.status_code == 200, page.get_data(as_text=True)
    with staff_client.session_transaction() as session:
        csrf_token = session["student_enrollment_csrf"]
    changed = staff_client.post(f"/students/{student.id}/enrollment", data={
        "csrf_token": csrf_token,
        "operation": "transfer",
        "class_name": "L4 Software Development A",
        "academic_year": "2026-2027",
        "effective_date": "2026-09-01",
        "reason": "Promotion",
    })
    assert changed.status_code == 302
    assert current_enrollment(student.id).class_name == "L4 Software Development A"

    rejected = staff_client.post(f"/students/{student.id}/enrollment", data={
        "operation": "withdrawn", "effective_date": "2026-10-01",
    })
    assert rejected.status_code == 400


def test_parent_cannot_read_enrollment_history(app):
    parent = make_user("parent", "parent")
    student = make_user("learner", "student")
    client = app.test_client()
    login_as(client, parent)

    assert client.get(f"/students/{student.id}/enrollment").status_code == 403


def test_migration_imports_existing_placements_once():
    from migrations.versions.v20261023_03_add_student_enrollment_history import upgrade

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE user (
                id INTEGER PRIMARY KEY, role VARCHAR(30), class_name VARCHAR(100),
                trade VARCHAR(100), level VARCHAR(50), academic_year VARCHAR(9)
            )
        """))
        connection.execute(text("""
            INSERT INTO user (id, role, class_name, trade, level, academic_year)
            VALUES (1, 'student', 'L3 Software Development', 'ICT and Multimedia', 'L3', '2025-2026')
        """))
        upgrade(connection)
        upgrade(connection)
        rows = connection.execute(text(
            "SELECT student_id, class_name, academic_year, start_date, status FROM student_enrollment"
        )).all()

    assert [tuple(row) for row in rows] == [
        (1, "L3 Software Development", "2025-2026", None, "active"),
    ]
