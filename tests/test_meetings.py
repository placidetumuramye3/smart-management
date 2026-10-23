import datetime

import pytest
from sqlalchemy import create_engine, text

from app.extensions import db
from app.models import (
    MeetingActionItem, MeetingMinutes, MeetingNotificationLog, MessageConversation,
    Setting, StudentGuardian, User,
)
from app.routes.meeting_api import _serialize_meeting
from app.services.meeting_service import (
    can_view_meeting, detect_conflicts, meeting_reminders,
    verify_meeting_audit_chain,
)
from app.services.meeting_workflow import (
    add_meeting_participant, create_action_item, create_meeting, decide_meeting, decide_minutes,
    record_attendance, respond_to_invitation, revoke_guardian_link,
    save_minutes, submit_minutes, update_action, verify_guardian_link,
    revoke_meeting_participant,
)


def make_user(username, role, *, name=None, class_name=None, parent_id=None):
    user = User(
        username=username,
        full_name=name or username.title(),
        password_hash="test-only-hash",
        role=role,
        class_name=class_name,
        parent_id=parent_id,
        is_active_account=True,
    )
    db.session.add(user)
    db.session.flush()
    return user


def meeting_payload(student, approver_id, *, day="2030-05-12", room="Room A"):
    return {
        "title": "Student progress meeting",
        "meeting_type": "parent_teacher",
        "student_id": str(student.id),
        "class_name": student.class_name,
        "scheduled_date": day,
        "start_time": "14:00",
        "end_time": "14:30",
        "location": room,
        "audience_mode": "guardians",
        "minutes_approver_id": str(approver_id),
        "agenda_items": ["Learning progress", "Agreed support"],
        "publish": True,
    }


def login_as(client, user):
    with client.session_transaction() as session:
        session["_user_id"] = str(user.id)
        session["_fresh"] = True


def test_admin_publishes_invitation_and_parent_can_respond(app):
    parent = make_user("parent_one", "parent")
    student = make_user("learner_one", "student", class_name="Class A", parent_id=parent.id)
    admin = make_user("admin_one", "master_admin")

    meeting = create_meeting(admin, meeting_payload(student, admin.id))

    assert meeting.status == "Scheduled"
    assert meeting.approval_status == "approved"
    assert can_view_meeting(parent, meeting)
    participant = next(row for row in meeting.participants if row.user_id == parent.id)
    assert participant.invitation_status == "invited"
    response = respond_to_invitation(parent, meeting, "accepted", "I will attend")
    assert response.invitation_status == "accepted"
    assert response.response_comment == "I will attend"
    valid, _events = verify_meeting_audit_chain()
    assert valid
    thread = MessageConversation.query.filter_by(meeting_id=meeting.id).one()
    from app.routes.messages import _is_participant, _verify_audit_chain
    assert _is_participant(thread.id, parent.id)
    assert _verify_audit_chain()[0]


def test_parent_cannot_open_another_students_meeting_by_guessing_id(app):
    parent = make_user("authorized_parent", "parent")
    outsider = make_user("other_parent", "parent")
    student = make_user("private_student", "student", class_name="Class A", parent_id=parent.id)
    admin = make_user("school_admin", "master_admin")
    meeting = create_meeting(admin, meeting_payload(student, admin.id))

    assert not can_view_meeting(outsider, meeting)
    assert _serialize_meeting(meeting, outsider, detail=True)["student"] is None

    client = app.test_client()
    login_as(client, outsider)
    response = client.get(f"/api/meetings/{meeting.id}")
    assert response.status_code == 403
    assert response.json["ok"] is False, response.get_data(as_text=True)


def test_explicit_guardian_revocation_overrides_legacy_parent_link(app):
    parent = make_user("guardian", "parent")
    student = make_user("linked_student", "student", class_name="Class A", parent_id=parent.id)
    admin = make_user("school_admin", "master_admin")
    meeting = create_meeting(admin, meeting_payload(student, admin.id))

    link = verify_guardian_link(admin, student.id, parent.id, "Mother")
    revoke_guardian_link(admin, link)

    assert student.parent_id is None
    assert not can_view_meeting(parent, meeting)
    assert StudentGuardian.query.filter_by(student_id=student.id, guardian_id=parent.id).first().status == "revoked"


def test_multiple_verified_guardians_can_receive_the_same_student_invitation(app):
    first_parent = make_user("first_guardian", "parent")
    second_parent = make_user("second_guardian", "parent")
    student = make_user("shared_student", "student", class_name="Class A", parent_id=first_parent.id)
    admin = make_user("school_admin", "master_admin")
    verify_guardian_link(admin, student.id, second_parent.id, "Father")

    meeting = create_meeting(admin, meeting_payload(student, admin.id))

    parent_ids = {row.user_id for row in meeting.participants if row.user.role == "parent"}
    assert parent_ids == {first_parent.id, second_parent.id}
    assert can_view_meeting(first_parent, meeting)
    assert can_view_meeting(second_parent, meeting)


def test_added_invitee_gets_audited_thread_access_and_revocation_removes_it(app):
    first_parent = make_user("first_guardian", "parent")
    second_parent = make_user("second_guardian", "parent")
    student = make_user("shared_student", "student", class_name="Class A", parent_id=first_parent.id)
    admin = make_user("school_admin", "master_admin")
    verify_guardian_link(admin, student.id, second_parent.id, "Father")
    payload = meeting_payload(student, admin.id)
    payload["audience_mode"] = "selected"
    payload["participant_ids"] = [str(first_parent.id)]
    meeting = create_meeting(admin, payload)

    added = add_meeting_participant(admin, meeting, second_parent.id)
    assert can_view_meeting(second_parent, meeting)
    thread = MessageConversation.query.filter_by(meeting_id=meeting.id).one()
    from app.routes.messages import _is_participant, _verify_audit_chain
    assert _is_participant(thread.id, second_parent.id)
    assert _verify_audit_chain()[0]

    revoke_meeting_participant(admin, meeting, added.id)
    assert not can_view_meeting(second_parent, meeting)
    assert not _is_participant(thread.id, second_parent.id)
    assert _verify_audit_chain()[0]


def test_school_meeting_approval_can_be_configured(app):
    parent = make_user("parent", "parent")
    student = make_user("student", "student", class_name="Class D", parent_id=parent.id)
    teacher = make_user("teacher", "teacher", class_name="Class D")
    head = make_user("head", "head_teacher")
    db.session.add(Setting(key="meeting_approval_required", value="false"))
    db.session.flush()

    meeting = create_meeting(teacher, meeting_payload(student, head.id))

    assert meeting.status == "Scheduled"
    assert meeting.approval_status == "approved"


def test_action_deadline_reminders_are_deduplicated(app):
    admin = make_user("admin", "master_admin")
    meeting = create_meeting(admin, {
        "title": "Action reminder meeting", "meeting_type": "general",
        "scheduled_date": "2030-07-12", "start_time": "09:00", "end_time": "10:00",
        "audience_mode": "selected", "participant_ids": [], "publish": True,
    })
    due = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    action = MeetingActionItem(
        meeting_id=meeting.id, title="Prepare parent summary", assignee_id=admin.id,
        assigned_by_id=admin.id, deadline=due, status="Not Started",
        created_at="2030-01-01 00:00:00", updated_at="2030-01-01 00:00:00",
    )
    db.session.add(action)
    db.session.commit()

    first_count = meeting_reminders()
    second_count = meeting_reminders()

    assert first_count == 1
    assert second_count == 0
    assert MeetingNotificationLog.query.filter_by(
        meeting_id=meeting.id, user_id=admin.id,
        dedupe_key=f"action-deadline:{action.id}:{due}",
    ).count() == 1


def test_meeting_and_appointment_scheduling_conflicts_are_detected(app):
    parent = make_user("parent", "parent")
    student = make_user("student", "student", class_name="Class A", parent_id=parent.id)
    admin = make_user("admin", "master_admin")
    first = create_meeting(admin, meeting_payload(student, admin.id))

    conflicts = detect_conflicts(
        first.scheduled_date, "14:15", "14:45", "room a",
        {row.user_id for row in first.participants},
    )

    assert conflicts
    assert "Room" in conflicts[0][1] or "participants" in conflicts[0][1]


def test_only_designated_approver_can_publish_meeting_and_minutes(app):
    parent = make_user("parent", "parent")
    student = make_user("student", "student", class_name="Class B", parent_id=parent.id)
    teacher = make_user("teacher", "teacher", class_name="Class B")
    head = make_user("head", "head_teacher")
    other_admin = make_user("other_admin", "master_admin")
    payload = meeting_payload(student, head.id)

    meeting = create_meeting(teacher, payload)
    assert meeting.status == "Pending approval"
    with pytest.raises(PermissionError):
        decide_meeting(other_admin, meeting, "approved")
    decide_meeting(head, meeting, "approved")
    assert meeting.status == "Scheduled"

    save_minutes(teacher, meeting, "Discussion and agreed support plan.")
    submit_minutes(teacher, meeting)
    assert MeetingMinutes.query.filter_by(meeting_id=meeting.id).first().status == "pending_approval"
    with pytest.raises(PermissionError):
        decide_minutes(other_admin, meeting, "approved")
    decide_minutes(head, meeting, "approved")
    assert meeting.minutes.status == "approved"
    assert _serialize_meeting(meeting, parent, detail=True)["minutes"]["content"].startswith("Discussion")


def test_attendance_and_action_ownership_are_enforced(app):
    parent = make_user("parent", "parent")
    student = make_user("student", "student", class_name="Class C", parent_id=parent.id)
    teacher = make_user("teacher", "teacher", class_name="Class C")
    head = make_user("head", "head_teacher")
    meeting = create_meeting(teacher, meeting_payload(student, head.id))
    decide_meeting(head, meeting, "approved")
    save_minutes(teacher, meeting, "Decision: provide weekly learning support.")
    submit_minutes(teacher, meeting)
    decide_minutes(head, meeting, "approved")

    parent_participant = next(row for row in meeting.participants if row.user_id == parent.id)
    attendance = record_attendance(
        teacher, meeting, parent_participant.id, "Present", "Authorized representative", "Aunt",
    )
    assert attendance.status == "Present"
    assert attendance.verified_by_id == teacher.id

    action = create_action_item(teacher, meeting, {
        "title": "Weekly learning check-in",
        "assignee_id": str(teacher.id),
        "deadline": "2030-05-30",
        "is_parent_visible": "yes",
    })
    update_action(teacher, action, {
        "status": "Completed", "assignee_id": str(teacher.id),
        "deadline": action.deadline, "completion_evidence": "Four sessions recorded.",
    })
    assert action.status == "Completed"
    assert action.completion_evidence == "Four sessions recorded."

    with pytest.raises(PermissionError):
        update_action(parent, action, {"status": "Completed"})
    hidden_action = MeetingActionItem(
        meeting_id=meeting.id, title="Staff-only review", assignee_id=teacher.id,
        assigned_by_id=teacher.id, deadline="2030-05-30", status="Not Started",
        is_parent_visible=False, created_at="2030-01-01 00:00:00", updated_at="2030-01-01 00:00:00",
    )
    db.session.add(hidden_action)
    db.session.flush()
    with pytest.raises(PermissionError):
        from app.services.meeting_workflow import add_action_comment
        add_action_comment(parent, hidden_action, "Trying to post on a hidden action", True)


def test_api_csrf_and_report_exports(app):
    admin = make_user("api_admin", "master_admin")
    client = app.test_client()
    login_as(client, admin)
    payload = {
        "title": "School planning meeting", "meeting_type": "general",
        "scheduled_date": "2030-06-02", "start_time": "09:00", "end_time": "10:00",
        "audience_mode": "selected", "participant_ids": [], "publish": True,
    }

    denied = client.post("/api/meetings", json=payload)
    assert denied.status_code == 400
    csrf = client.get("/api/meetings/csrf").json["data"]["csrf_token"]
    created = client.post("/api/meetings", json=payload, headers={"X-CSRF-Token": csrf})
    assert created.status_code == 201
    meeting_id = created.json["data"]["id"]
    assert client.get("/api/meetings?page=1&per_page=10").json["meta"]["total"] == 1
    assert client.get("/meetings/reports.xlsx").mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    pdf = client.get("/meetings/reports.pdf")
    assert pdf.mimetype == "application/pdf"
    assert pdf.data.startswith(b"%PDF-1.4")
    assert client.get(f"/api/meetings/{meeting_id}").json["data"]["title"] == payload["title"]


def test_meeting_migration_seeds_legacy_guardians_and_is_idempotent(app):
    from migrations.versions.v20261023_02_add_meeting_workflows import upgrade

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE user (id INTEGER PRIMARY KEY, role VARCHAR(30), parent_id INTEGER, guardian_relationship VARCHAR(80))"))
        connection.execute(text("INSERT INTO user (id, role) VALUES (1, 'parent')"))
        connection.execute(text("INSERT INTO user (id, role, parent_id, guardian_relationship) VALUES (2, 'student', 1, 'Guardian')"))
        upgrade(connection)
        upgrade(connection)
        seeded = connection.execute(text("SELECT student_id, guardian_id, relationship, status FROM student_guardian")).all()

    assert [tuple(row) for row in seeded] == [(2, 1, "Guardian", "verified")]
