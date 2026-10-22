import os
import datetime
import json
from flask import request, redirect, url_for, flash, jsonify, send_file, current_app
from flask_login import login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from openpyxl import Workbook
from app.extensions import db
from app.models import User, Attendance, PendingCard, FeeTransaction, Book, DisciplineCase, SchoolMessage, Grade, TimetableEntry, Setting
from app.utils.constants import *
from app.utils.decorators import role_required
from app.utils.helpers import get_setting, set_setting, attendance_for_student, linked_children, teacher_assigned_classes
from app.utils.rendering import page
from app.utils.decorators import permission_for
from app.services.attendance_service import reconcile_daily_absences
from markupsafe import escape

def attendance():
    if current_user.role not in {"parent", "student"}:
        reconcile_daily_absences()
        db.session.commit()

    teacher_class_keys = {
        name.strip().casefold() for name in teacher_assigned_classes(current_user) if name and name.strip()
    } if current_user.role == "teacher" else set()
    query = Attendance.query.join(User, Attendance.student_id == User.id)
    if current_user.role == "teacher":
        query = query.filter(db.func.lower(User.class_name).in_(teacher_class_keys))
    elif current_user.role in {"discipline", "patron", "matron"}:
        query = query.filter(User.class_name == current_user.class_name)
    elif current_user.role in {"parent", "student"}:
        student_ids = [current_user.id] if current_user.role == "student" else [child.id for child in linked_children(current_user.id)]
        query = query.filter(Attendance.student_id.in_(student_ids))
    q = query.order_by(Attendance.day.desc(), Attendance.id.desc()).all()
    today = datetime.date.today().isoformat()
    today_records = [record for record in q if record.day == today]
    late_records = [record for record in today_records if record.status == "Late"]
    absent_records = [record for record in today_records if record.status == "Absent"]
    rfid_records = [record for record in today_records if record.source == "RFID"]
    teacher_roster_section = ""
    if current_user.role == "teacher":
        roster_query = User.query.filter_by(role="student")
        if teacher_class_keys:
            roster_query = roster_query.filter(db.func.lower(User.class_name).in_(teacher_class_keys))
            class_students = roster_query.order_by(User.class_name, User.full_name).all()
        else:
            class_students = []

        today_by_student = {}
        for record in today_records:
            today_by_student.setdefault(record.student_id, record)

        roster_rows = []
        for student in class_students:
            record = today_by_student.get(student.id)
            if record:
                status_markup = f"<span class='pill'>{escape(record.status)}</span>"
                arrival_time = escape(record.arrival_time or "-")
            else:
                status_markup = "<span class='muted'>Awaiting RFID scan</span>"
                arrival_time = "-"
            roster_rows.append(
                f"<tr><td><a href='/student/profile/{student.id}'>{escape(student.full_name)}</a></td>"
                f"<td>{escape(student.class_name or '-')}</td><td>{status_markup}</td><td>{arrival_time}</td></tr>"
            )

        if not teacher_class_keys:
            roster_content = "<p class='muted mb-0'>No class is assigned to your teacher account yet.</p>"
        else:
            roster_body = "".join(roster_rows) or (
                '<tr><td colspan="4" class="muted">No students are registered in your assigned class(es) yet.</td></tr>'
            )
            roster_content = (
                "<div class='table-responsive'><table class='table align-middle mb-0'>"
                "<thead><tr><th>Student</th><th>Class</th><th>Today's status</th><th>Arrival</th></tr></thead>"
                f"<tbody>{roster_body}</tbody>"
                "</table></div>"
            )
        teacher_roster_section = (
            "<section class='panel'><h2>My Assigned Class Roster</h2>"
            "<p class='muted'>Students appear here automatically when their registration is saved. Attendance is updated from RFID scans and is read-only.</p>"
            f"{roster_content}</section>"
        )
    alert_rows = "".join(
        f"<tr><td>{escape(record.student.full_name if record.student else 'Unknown student')}</td><td>{escape(record.student.class_name if record.student and record.student.class_name else '-')}</td><td><span class='pill'>{escape(record.status)}</span></td><td>{escape(record.arrival_time or '-')}</td><td>{escape(record.afternoon_time or '-')}</td></tr>"
        for record in late_records + absent_records
    )
    rows = []
    for a in q:
        status_cell = f"<span class='pill'>{escape(a.status)}</span>"
        rows.append(f"<tr><td>{a.day}</td><td>{a.student.full_name}</td><td>{a.student.class_name or '-'}</td><td>{status_cell}</td><td>{a.arrival_time or '-'}</td><td>{a.afternoon_time or '-'}</td><td>{a.source}</td></tr>")
    body = f"""<section class="panel"><h1>Attendance <span class="accent">Register</span></h1><p class="muted">Attendance is recorded automatically by RFID scans. Students arriving after the configured late deadline are marked late. Absences are reconciled automatically after the deadline when an RFID scan arrives or staff open this page. Attendance statuses and times are read-only here. Class attendance updates are sent to assigned teachers, matron, patron, discipline staff, and school leadership.</p><div class="grid mb-4"><div class="stat"><div class="stat-value">{len(rfid_records)}</div><div class="stat-label">RFID Scans Today</div></div><div class="stat"><div class="stat-value">{len(late_records)}</div><div class="stat-label">Late Students Today</div></div><div class="stat"><div class="stat-value">{len(absent_records)}</div><div class="stat-label">Absences Today</div></div></div></section>{teacher_roster_section}<section class="panel"><h2>Today's Late Arrivals and Absences</h2><div class="table-responsive"><table class="table align-middle"><thead><tr><th>Student</th><th>Class</th><th>Status</th><th>Arrival</th><th>Departure / Afternoon Scan</th></tr></thead><tbody>{alert_rows or '<tr><td colspan="5" class="muted">No late arrivals or absences recorded today.</td></tr>'}</tbody></table></div><div class="table-responsive"><table class="table align-middle attendance-history"><thead><tr><th>Date</th><th>Student Name</th><th>Class</th><th>Status</th><th>Morning Timestamp</th><th>Afternoon Timestamp</th><th>Source</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="7" class="muted">No attendance records found.</td></tr>'}</tbody></table></div><a class="btn btn-outline-success mt-3" href="/attendance/export" style="border-color:var(--accent);color:var(--text)">Download Attendance Excel</a></section>"""
    return page("Attendance", body, "teacher")

def attendance_times():
    keys = {
        "morning_start": DEFAULT_MORNING_START,
        "late_after": DEFAULT_LATE_AFTER,
        "afternoon_start": DEFAULT_AFTERNOON_START,
        "afternoon_end": DEFAULT_AFTERNOON_END,
    }
    if request.method == "POST":
        vals = {k: request.form.get(k, "").strip() for k in keys}
        try:
            for v in vals.values():
                datetime.datetime.strptime(v, "%H:%M")
            if not (vals["morning_start"] <= vals["late_after"] < vals["afternoon_start"] <= vals["afternoon_end"]):
                raise ValueError()
            for k, v in vals.items():
                set_setting(k, v)
            flash("Attendance time windows updated.", "success")
            return redirect(url_for("attendance_times"))
        except ValueError:
            flash("Use valid 24-hour times ensuring: Morning start ≤ Late deadline < Afternoon start ≤ Afternoon end.", "danger")
    values = {k: get_setting(k, v) for k, v in keys.items()}
    body = f"""<section class="panel"><h1>Attendance <span class="accent">Time Settings</span></h1><p class="muted">Teachers and Admin can configure allowed daily RFID scanning windows.</p><form method="post" class="row g-3">
<div class="col-md-6"><label>Morning Start</label><input class="form-control" type="time" name="morning_start" value="{values['morning_start']}" required></div>
<div class="col-md-6"><label>Late Deadline</label><input class="form-control" type="time" name="late_after" value="{values['late_after']}" required></div>
<div class="col-md-6"><label>Afternoon Start</label><input class="form-control" type="time" name="afternoon_start" value="{values['afternoon_start']}" required></div>
<div class="col-md-6"><label>Afternoon End</label><input class="form-control" type="time" name="afternoon_end" value="{values['afternoon_end']}" required></div>
<div class="col-12"><button class="btn btn-accent">Save Time Windows</button></div></form></section>"""
    return page("Attendance Times", body, "teacher")

def export_attendance():
    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance"
    ws.append(["Date", "Student Name", "Class", "Status", "Morning Time", "Afternoon Time", "Source"])
    query = Attendance.query.join(User, Attendance.student_id == User.id)
    if current_user.role == "teacher":
        query = query.filter(db.func.lower(User.class_name).in_({name.casefold() for name in teacher_assigned_classes(current_user)}))
    elif current_user.role in {"discipline", "patron", "matron"}:
        query = query.filter(User.class_name == current_user.class_name)
    for r in query.order_by(Attendance.day.desc()).all():
        ws.append([
            r.day,
            r.student.full_name if r.student else "",
            r.student.class_name if r.student else "",
            r.status,
            r.arrival_time or "",
            r.afternoon_time or "",
            r.source or ""
        ])
    import tempfile
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    wb.save(tmp.name)
    tmp.close()
    return send_file(tmp.name, as_attachment=True, download_name=f"attendance_{datetime.date.today().isoformat()}.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

def register_routes(app):
    app.add_url_rule('/attendance', endpoint='attendance', view_func=permission_for('attendance')(attendance), methods=['GET'])
    app.add_url_rule('/attendance-times', endpoint='attendance_times', view_func=permission_for('attendance_times')(attendance_times), methods=['GET', 'POST'])
    app.add_url_rule('/attendance/export', endpoint='export_attendance', view_func=permission_for('export_attendance')(export_attendance), methods=['GET'])
