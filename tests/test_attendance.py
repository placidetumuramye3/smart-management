from datetime import date, datetime as DateTime
from types import SimpleNamespace

import pytest

from app.extensions import db
from app.models import Attendance, User, UserNotification
from app.routes import rfid as rfid_routes
from app.services.attendance_service import (
    class_attendance_summary,
    classify_morning_status,
    clock_minutes,
    reconcile_daily_absences,
)
from app.utils.helpers import set_setting


def add_student(username, full_name, class_name):
    student = User(username=username, full_name=full_name, role="student", class_name=class_name)
    student.set_password("test-password")
    db.session.add(student)
    db.session.flush()
    return student

def test_attendance_classification():
    assert classify_morning_status("06:45", "08:00") == "Present"
    assert classify_morning_status("07:30", "08:00") == "Present"
    assert classify_morning_status("08:01", "08:00") == "Present"
    assert classify_morning_status("08:05:00", "08:00") == "Present"
    assert classify_morning_status("08:05:01", "08:00") == "Late"
    assert classify_morning_status("08:15:00", "08:00") == "Late"
    assert classify_morning_status("08:15:01", "08:00") == "Absent"
    assert classify_morning_status("7:30 AM", "08:00 AM") == "Present"


def test_clock_settings_are_compared_as_time_values():
    assert clock_minutes("6:30") == 390
    assert clock_minutes("06:30:00") == 390
    assert clock_minutes("8:00 AM") == 480


@pytest.mark.parametrize(
    ("scan_clock", "expected"),
    [("07:30", "Present"), ("08:05", "Present"), ("08:06", "Late")],
)
def test_rfid_scan_uses_configured_cutoff(app, monkeypatch, scan_clock, expected):
    hour, minute = (int(part) for part in scan_clock.split(":"))

    class FrozenDateTime(DateTime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 24, hour, minute)

    monkeypatch.setattr(rfid_routes, "datetime", SimpleNamespace(datetime=FrozenDateTime))
    with app.app_context():
        student = add_student("scan_student", "Scan Student", "Class 7A")
        student.rfid_uid = "EARLY-LATE-UID"
        set_setting("morning_start", "06:30")
        set_setting("late_after", "08:00")
        set_setting("afternoon_start", "16:00")
        set_setting("afternoon_end", "16:30")

    response = app.test_client().post("/student/rfid-login", json={"uid": "EARLY-LATE-UID"})

    assert response.status_code == 200
    assert response.get_json()["attendance_status"] == expected
    assert response.get_json()["server_time"] == scan_clock
    assert response.get_json()["late_after"] == "08:00"


def test_late_rfid_scan_queues_in_app_alerts_for_student_family_and_staff(app, monkeypatch):
    class FrozenDateTime(DateTime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 24, 8, 2)

    monkeypatch.setattr(rfid_routes, "datetime", SimpleNamespace(datetime=FrozenDateTime))
    with app.app_context():
        parent = User(
            username="late_student_parent",
            full_name="Late Student Parent",
            password_hash="test-hash",
            role="parent",
            is_active_account=True,
        )
        teacher = User(
            username="late_class_teacher",
            full_name="Late Class Teacher",
            password_hash="test-hash",
            role="teacher",
            class_name="Class 7A",
            is_active_account=True,
        )
        admin = User(
            username="late_school_admin",
            full_name="Late School Admin",
            password_hash="test-hash",
            role="master_admin",
            is_active_account=True,
        )
        db.session.add_all([parent, teacher, admin])
        db.session.flush()
        student = add_student("late_scan_student", "Late Scan Student", "Class 7A")
        student.parent_id = parent.id
        student.rfid_uid = "LATE-NOTIFICATION-UID"
        set_setting("morning_start", "06:30")
        set_setting("late_after", "08:00")
        set_setting("afternoon_start", "16:00")
        set_setting("afternoon_end", "16:30")
        recipient_ids = {parent.id, teacher.id, admin.id, student.id}

    response = app.test_client().post(
        "/student/rfid-login",
        json={"uid": "LATE-NOTIFICATION-UID"},
    )

    assert response.status_code == 200
    assert response.get_json()["attendance_status"] == "Late"
    with app.app_context():
        notifications = UserNotification.query.filter(
            UserNotification.user_id.in_(recipient_ids),
            UserNotification.category == "Attendance",
        ).all()
        assert {item.user_id for item in notifications} == recipient_ids
        assert all("Late" in item.title or "Late" in item.body for item in notifications)


def test_absence_reconciliation_saves_absence_and_queues_in_app_alerts(app):
    with app.app_context():
        parent = User(
            username="absent_student_parent",
            full_name="Absent Student Parent",
            password_hash="test-hash",
            role="parent",
            is_active_account=True,
        )
        teacher = User(
            username="absent_class_teacher",
            full_name="Absent Class Teacher",
            password_hash="test-hash",
            role="teacher",
            class_name="Class 7A",
            is_active_account=True,
        )
        admin = User(
            username="absent_school_admin",
            full_name="Absent School Admin",
            password_hash="test-hash",
            role="master_admin",
            is_active_account=True,
        )
        db.session.add_all([parent, teacher, admin])
        db.session.flush()
        student = add_student("absent_scan_student", "Absent Scan Student", "Class 7A")
        student.parent_id = parent.id
        recipient_ids = {parent.id, teacher.id, admin.id, student.id}
        set_setting("late_after", "08:00")

        absentees = reconcile_daily_absences(now=DateTime(2026, 10, 24, 8, 15, 1))
        db.session.commit()

        assert [item.id for item in absentees] == [student.id]
        attendance = Attendance.query.filter_by(
            student_id=student.id,
            day="2026-10-24",
        ).one()
        assert attendance.status == "Absent"
        notifications = UserNotification.query.filter(
            UserNotification.user_id.in_(recipient_ids),
            UserNotification.category == "Attendance",
        ).all()
        assert {item.user_id for item in notifications} == recipient_ids
        assert all("Absent" in item.title or "Absent" in item.body for item in notifications)


def test_rfid_rescan_corrects_todays_late_record_after_cutoff_change(app, monkeypatch):
    class FrozenDateTime(DateTime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 24, 7, 50)

    monkeypatch.setattr(rfid_routes, "datetime", SimpleNamespace(datetime=FrozenDateTime))
    with app.app_context():
        student = add_student("corrected_student", "Corrected Student", "Class 7A")
        student.rfid_uid = "CORRECTED-UID"
        db.session.add(Attendance(
            student=student,
            day="2026-10-24",
            status="Late",
            arrival_time="2026-10-24 07:30:00",
            source="RFID",
        ))
        set_setting("morning_start", "06:30")
        set_setting("late_after", "08:00")
        set_setting("afternoon_start", "16:00")
        set_setting("afternoon_end", "16:30")

    response = app.test_client().post("/student/rfid-login", json={"uid": "CORRECTED-UID"})

    assert response.status_code == 200
    assert response.get_json()["attendance_status"] == "Present"


def test_rfid_class_status_requires_device_token(app):
    app.config["RFID_DEVICE_TOKEN"] = "test-device-secret"
    app.config["RFID_DEVICE_CLASS"] = "Class 7A"
    response = app.test_client().get("/student/rfid-status?class=Class%207A")
    assert response.status_code == 401


def test_rfid_scan_requires_the_configured_device_token(app):
    app.config["RFID_DEVICE_TOKEN"] = "test-device-secret"
    client = app.test_client()

    rejected = client.post("/student/rfid-login", json={"uid": "TEST-UID"})
    assert rejected.status_code == 401
    assert rejected.get_json()["status"] == "device_unauthorized"

    accepted = client.post(
        "/student/rfid-login",
        json={"uid": "TEST-UID"},
        headers={"X-Device-Token": "test-device-secret"},
    )
    assert accepted.status_code == 202
    assert accepted.get_json()["status"] == "pending_registration"


def test_rfid_class_status_rejects_other_classes(app):
    app.config["RFID_DEVICE_TOKEN"] = "test-device-secret"
    app.config["RFID_DEVICE_CLASS"] = "Class 7A"
    response = app.test_client().get(
        "/student/rfid-status?class=Class%207B",
        headers={"X-Device-Token": "test-device-secret"},
    )
    assert response.status_code == 403


def test_rfid_class_status_records_and_lists_absences_for_configured_class(app):
    app.config["RFID_DEVICE_TOKEN"] = "test-device-secret"
    app.config["RFID_DEVICE_CLASS"] = "Class 7A"
    with app.app_context():
        add_student("student_a", "Student A", "Class 7A")
        add_student("student_b", "Student B", "Class 7A")
        add_student("student_c", "Student C", "Class 7B")
        set_setting("late_after", "00:00")

    response = app.test_client().get(
        "/student/rfid-status?class=Class%207A",
        headers={"X-Device-Token": "test-device-secret"},
    )

    assert response.status_code == 200
    result = response.get_json()
    assert result["class_name"] == "Class 7A"
    assert result["absent_students"] == ["Student A", "Student B"]
    assert result["class_summary"]["absent_count"] == 2
    assert result["class_summary"]["absent_percent"] == 100
    assert result["absences_recorded"] == 3
    with app.app_context():
        assert Attendance.query.filter_by(day=date.today().isoformat(), status="Absent").count() == 3


def test_class_attendance_percentages_use_the_roster_as_denominator(app):
    with app.app_context():
        students = [
            add_student("present_student", "Present Student", "Class 7A"),
            add_student("late_student", "Late Student", "Class 7A"),
            add_student("absent_student", "Absent Student", "Class 7A"),
        ]
        for student, status in zip(students, ("Present", "Late", "Absent")):
            db.session.add(Attendance(
                student_id=student.id,
                day=date.today().isoformat(),
                status=status,
                source="Test",
            ))
        db.session.flush()

        summary = class_attendance_summary("Class 7A", date.today().isoformat())

    assert summary["total_students"] == 3
    assert summary["present_percent"] == 33
    assert summary["late_percent"] == 33
    assert summary["absent_percent"] == 33
    assert summary["unmarked_count"] == 0


def test_attendance_register_shows_scan_datetime_for_present_rfid_students(app):
    today = date.today().isoformat()
    with app.app_context():
        student = add_student("timestamp_student", "Timestamp Student", "Class 7A")
        db.session.add(Attendance(
            student_id=student.id,
            day=today,
            status="Present",
            arrival_time="10:51:28",  # Legacy rows may contain only the scan clock.
            source="RFID",
        ))
        admin = User(
            username="attendance_viewer",
            full_name="Attendance Viewer",
            password_hash="test-hash",
            role="master_admin",
            is_active_account=True,
        )
        db.session.add(admin)
        set_setting("late_after", "23:59")
        db.session.commit()
        admin_id = admin.id

    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(admin_id)
        session["_fresh"] = True

    response = client.get("/attendance")

    assert response.status_code == 200
    assert b"Today's Attendance Details" in response.data
    assert f"{today} 10:51:28".encode() in response.data
    assert b"Timestamp Student" in response.data
    assert b"Present" in response.data


def test_attendance_totals_are_grouped_by_department_level_and_class(app):
    today = date.today().isoformat()
    with app.app_context():
        present_student = add_student("summary_present", "Summary Present", "L4 Fashion Design")
        present_student.department = "Creative Arts"
        present_student.trade = "Fashion Design"
        present_student.level = "L4"
        present_student.class_section = "A"
        late_student = add_student("summary_late", "Summary Late", "L4 Fashion Design")
        late_student.department = "Creative Arts"
        late_student.trade = "Fashion Design"
        late_student.level = "L4"
        late_student.class_section = "B"
        db.session.add_all([
            Attendance(student_id=present_student.id, day=today, status="Present", source="RFID"),
            Attendance(student_id=late_student.id, day=today, status="Late", source="RFID"),
        ])
        admin = User(
            username="attendance_summary_admin",
            full_name="Attendance Summary Admin",
            password_hash="test-hash",
            role="master_admin",
            is_active_account=True,
        )
        db.session.add(admin)
        set_setting("late_after", "23:59")
        db.session.commit()
        admin_id = admin.id

    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(admin_id)
        session["_fresh"] = True

    response = client.get("/attendance")

    assert response.status_code == 200
    assert b"Attendance Totals by Department, Trade, Level, Class and Section" in response.data
    assert b"<th>Department</th><th>Trade</th><th>Level</th><th>Class</th><th>Class Section</th>" in response.data
    assert (
        b"<td>Creative Arts</td><td>Fashion Design</td><td>All levels</td>"
        b"<td>All classes</td><td>All sections</td><td>2</td><td>1</td><td>1</td>"
        b"<td>0</td><td>0</td><td>0</td>"
    ) in response.data
    assert (
        b"<td>Creative Arts</td><td>Fashion Design</td><td>L4</td>"
        b"<td>L4 Fashion Design</td><td>All sections</td><td>2</td><td>1</td><td>1</td>"
        b"<td>0</td><td>0</td><td>0</td>"
    ) in response.data
    assert (
        b"<td>Creative Arts</td><td>Fashion Design</td><td>L4</td>"
        b"<td>L4 Fashion Design</td><td>A</td>"
    ) in response.data


def test_attendance_filters_department_trade_level_class_and_section(app):
    today = date.today().isoformat()
    with app.app_context():
        selected = add_student("filtered_student", "Filtered Student", "L4 Software Development A")
        selected.department = "Technology"
        selected.trade = "ICT and Multimedia"
        selected.level = "L4"
        selected.class_section = "A"
        other = add_student("other_filter_student", "Other Filter Student", "L4 Fashion Design")
        other.department = "Creative Arts"
        other.trade = "Fashion Design"
        other.level = "L4"
        other.class_section = "B"
        db.session.add_all([
            Attendance(student_id=selected.id, day=today, status="Present", source="RFID"),
            Attendance(student_id=other.id, day=today, status="Absent", source="System"),
            User(username="placement_filter_admin", full_name="Placement Filter Admin",
                 password_hash="test-hash", role="master_admin", is_active_account=True),
        ])
        set_setting("late_after", "23:59")
        db.session.commit()
        admin_id = User.query.filter_by(username="placement_filter_admin").one().id

    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(admin_id)
        session["_fresh"] = True

    response = client.get(
        "/attendance?department=Technology&trade=ICT+and+Multimedia&level=L4&"
        "class_name=L4+Software+Development+A&class_section=A"
    )

    assert response.status_code == 200
    assert b"Filtered Student" in response.data
    assert b"Other Filter Student" not in response.data
    assert b"Technology" in response.data
    assert b"Class Section" in response.data


def test_teacher_attendance_totals_only_include_assigned_classes(app):
    today = date.today().isoformat()
    with app.app_context():
        assigned_student = add_student("assigned_student", "Assigned Student", "L4 Fashion Design")
        assigned_student.trade = "Fashion Design"
        assigned_student.level = "L4"
        private_student = add_student("private_student", "Private Student", "L4 Software Development A")
        private_student.trade = "ICT and Multimedia"
        private_student.level = "L4"
        teacher = User(
            username="class_teacher",
            full_name="Class Teacher",
            password_hash="test-hash",
            role="teacher",
            class_name="L4 Fashion Design",
            is_active_account=True,
        )
        db.session.add_all([
            Attendance(student_id=assigned_student.id, day=today, status="Present", source="RFID"),
            Attendance(student_id=private_student.id, day=today, status="Absent", source="System"),
            teacher,
        ])
        set_setting("late_after", "23:59")
        db.session.commit()
        teacher_id = teacher.id

    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(teacher_id)
        session["_fresh"] = True

    response = client.get("/attendance")

    assert response.status_code == 200
    assert b"Assigned Student" in response.data
    assert b"Private Student" not in response.data
    assert b"ICT and Multimedia" not in response.data
    assert b"L4 Software Development A" not in response.data


def test_attendance_level_group_filter_shows_l3_and_s4_together(app):
    today = date.today().isoformat()
    with app.app_context():
        level_3_student = add_student("level_3_student", "Level 3 Student", "L3 Fashion Design")
        level_3_student.trade = "Fashion Design"
        level_3_student.level = "L3"
        level_s4_student = add_student("level_s4_student", "Level S4 Student", "S4 Professional Accountant")
        level_s4_student.trade = "Professional Accountant"
        level_s4_student.level = "S4"
        level_4_student = add_student("level_4_student", "Level 4 Student", "L4 Fashion Design")
        level_4_student.trade = "Fashion Design"
        level_4_student.level = "L4"
        admin = User(
            username="level_filter_admin",
            full_name="Level Filter Admin",
            password_hash="test-hash",
            role="master_admin",
            is_active_account=True,
        )
        db.session.add_all([
            Attendance(student_id=level_3_student.id, day=today, status="Present", source="RFID"),
            Attendance(student_id=level_s4_student.id, day=today, status="Late", source="RFID"),
            Attendance(student_id=level_4_student.id, day=today, status="Absent", source="System"),
            admin,
        ])
        set_setting("late_after", "23:59")
        db.session.commit()
        admin_id = admin.id

    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(admin_id)
        session["_fresh"] = True

    response = client.get("/attendance?level_group=L3-S4")

    assert response.status_code == 200
    assert b"<option value='L3-S4' selected>" in response.data
    assert b"Level 3 Student" in response.data
    assert b"Level S4 Student" in response.data
    assert b"Level 4 Student" not in response.data
