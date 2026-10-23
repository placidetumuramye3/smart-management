from app.extensions import db
from app.models import Appointment, Setting, StudentGuardian, User
from app.utils.helpers import guardian_can_access_student, linked_children


def make_user(username, role, *, parent_id=None, sdms_code=None):
    user = User(
        username=username,
        full_name=username.replace("_", " ").title(),
        password_hash="test-hash",
        role=role,
        parent_id=parent_id,
        sdms_code=sdms_code,
        is_active_account=True,
    )
    db.session.add(user)
    db.session.flush()
    return user


def login_as(client, user):
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True


def add_verified_link(student, guardian, *, status="verified"):
    link = StudentGuardian(
        student_id=student.id,
        guardian_id=guardian.id,
        relationship="Parent / Guardian",
        status=status,
        verified_at="2026-10-23 12:00:00",
    )
    db.session.add(link)
    db.session.flush()
    return link


def test_multiple_guardians_share_access_and_revoked_links_are_excluded(app):
    primary = make_user("primary_parent", "parent")
    secondary = make_user("secondary_parent", "parent")
    student = make_user("learner", "student", parent_id=primary.id)
    primary_link = add_verified_link(student, primary)
    secondary_link = add_verified_link(student, secondary)
    db.session.commit()

    assert guardian_can_access_student(primary.id, student.id)
    assert guardian_can_access_student(secondary.id, student.id)
    assert {child.id for child in linked_children(secondary.id)} == {student.id}

    secondary_link.status = "revoked"
    db.session.commit()

    assert not guardian_can_access_student(secondary.id, student.id)
    assert guardian_can_access_student(primary.id, student.id)
    assert primary_link.status == "verified"


def test_parent_signup_creates_explicit_verified_guardian_links(app):
    student_a = make_user("learner_a", "student", sdms_code="SDMS-A1B2C3D4")
    student_b = make_user("learner_b", "student", sdms_code="SDMS-E5F6A7B8")
    school = Setting(key="school_name", value="Test School")
    db.session.add(school)
    db.session.commit()

    response = app.test_client().post("/register", data={
        "username": "family_account",
        "full_name": "Family Account",
        "password": "secure-test-password",
        "school_name": "Test School",
        "sdms_codes": f"{student_a.sdms_code}, {student_b.sdms_code}",
    })

    assert response.status_code == 302
    guardian = User.query.filter_by(username="family_account").one()
    links = StudentGuardian.query.filter_by(guardian_id=guardian.id).all()
    assert {link.student_id for link in links} == {student_a.id, student_b.id}
    assert all(link.status == "verified" for link in links)
    assert all(link.verified_at for link in links)
    assert guardian_can_access_student(guardian.id, student_a.id)
    assert guardian_can_access_student(guardian.id, student_b.id)


def test_secondary_verified_guardian_can_view_child_meetings(app):
    requester = make_user("requester", "parent")
    other_guardian = make_user("other_guardian", "parent")
    secretary = make_user("school_secretary", "secretary")
    student = make_user("learner", "student", parent_id=requester.id)
    add_verified_link(student, requester)
    secondary_link = add_verified_link(student, other_guardian)
    appointment = Appointment(
        visitor_name=requester.full_name,
        student_id=student.id,
        purpose="Learning progress review",
        scheduled_date="2030-05-12",
        scheduled_time="14:00",
        status="Scheduled",
        created_by=secretary.id,
        parent_id=requester.id,
    )
    db.session.add(appointment)
    db.session.commit()

    client = app.test_client()
    login_as(client, other_guardian)
    response = client.get("/appointments")

    assert response.status_code == 200
    assert b"Learning progress review" in response.data

    secondary_link.status = "revoked"
    db.session.commit()
    revoked_response = client.get("/appointments")
    assert revoked_response.status_code == 200
    assert b"Learning progress review" not in revoked_response.data
