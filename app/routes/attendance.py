import os
import datetime
import json
import re
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
from app.services.attendance_service import (
    LATE_GRACE_SECONDS,
    clock_minutes,
    normalize_clock,
    reconcile_daily_absences,
)
from markupsafe import escape

ATTENDANCE_LEVEL_GROUPS = {
    "L3-S4": {"L3", "S4"},
    "L4-S5": {"L4", "S5"},
    "L5-S6": {"L5", "S6"},
}


def _scan_datetime(day, value):
    """Return a full date and time for both current and legacy time-only rows."""
    if not value:
        return "-"
    value = str(value).strip()
    if len(value) >= 10 and value[4:5] == "-" and value[7:8] == "-":
        return value
    return f"{day} {value}"


def _student_level(student):
    class_name = (student.class_name or "").strip()
    inferred_level = CLASS_TRADE_LEVEL.get(class_name, (None, None))[1]
    return (student.level or inferred_level or "").strip()


def attendance():
    if current_user.role not in {"parent", "student"}:
        reconcile_daily_absences()
        db.session.commit()

    selected_level_group = request.args.get("level_group", "ALL").strip().upper()
    if selected_level_group != "ALL" and selected_level_group not in ATTENDANCE_LEVEL_GROUPS:
        selected_level_group = "ALL"

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
    students_query = User.query.filter(
        User.role == "student",
        db.or_(User.is_active_account.is_(True), User.is_active_account.is_(None)),
    )
    if current_user.role == "teacher":
        students_query = students_query.filter(db.func.lower(User.class_name).in_(teacher_class_keys))
    elif current_user.role in {"discipline", "patron", "matron"}:
        students_query = students_query.filter(User.class_name == current_user.class_name)
    elif current_user.role == "student":
        students_query = students_query.filter(User.id == current_user.id)
    elif current_user.role == "parent":
        child_ids = [child.id for child in linked_children(current_user.id)]
        students_query = students_query.filter(User.id.in_(child_ids))
    scoped_students = students_query.order_by(User.full_name).all()

    if selected_level_group != "ALL":
        selected_levels = {level.casefold() for level in ATTENDANCE_LEVEL_GROUPS[selected_level_group]}
        scoped_students = [
            student for student in scoped_students
            if _student_level(student).casefold() in selected_levels
        ]

    filter_fields = (
        ("department", "Department", lambda student: (student.department or student.trade or "").strip()),
        ("trade", "Trade", lambda student: (student.trade or "").strip()),
        ("level", "Level", _student_level),
        ("class_name", "Class", lambda student: (student.class_name or "").strip()),
        ("class_section", "Class Section", lambda student: (student.class_section or "").strip()),
    )
    no_value = "__not_specified__"
    selected_filters = {}
    filter_selects = []
    for field_name, label, value_for in filter_fields:
        values = {value_for(student) for student in scoped_students}
        has_unspecified = "" in values
        values.discard("")
        raw_value = request.args.get(field_name, "").strip()
        valid_values = values | ({no_value} if has_unspecified else set())
        selected_value = raw_value if raw_value in valid_values else ""
        selected_filters[field_name] = selected_value
        options = [f"<option value=''{ ' selected' if not selected_value else ''}>All {escape(label.lower())}s</option>"]
        if has_unspecified:
            options.append(
                f"<option value='{no_value}'{' selected' if selected_value == no_value else ''}>Not specified</option>"
            )
        options.extend(
            f"<option value='{escape(value)}'{' selected' if selected_value == value else ''}>{escape(value)}</option>"
            for value in sorted(values, key=str.casefold)
        )
        filter_selects.append(
            f"<div class='col-md-2'><label class='form-label' for='attendance-{field_name}'>{escape(label)}</label>"
            f"<select class='form-select' id='attendance-{field_name}' name='{field_name}'>{''.join(options)}</select></div>"
        )

    scoped_students = [
        student for student in scoped_students
        if all(
            not selected_filters[field_name]
            or (no_value if not value_for(student) else value_for(student)) == selected_filters[field_name]
            for field_name, _label, value_for in filter_fields
        )
    ]
    scoped_student_ids = {student.id for student in scoped_students}
    q = [record for record in q if record.student_id in scoped_student_ids]
    today = datetime.date.today().isoformat()
    today_records = [record for record in q if record.day == today]
    late_records = [record for record in today_records if record.status == "Late"]
    absent_records = [record for record in today_records if record.status == "Absent"]
    rfid_records = [record for record in today_records if record.source == "RFID"]

    level_group_labels = {
        "ALL": "All levels",
        "L3-S4": "L3 and S4 (all departments and classes)",
        "L4-S5": "L4 and S5 (all departments and classes)",
        "L5-S6": "L5 and S6 (all departments and classes)",
    }
    level_group_options = "".join(
        f"<option value='{value}'{' selected' if selected_level_group == value else ''}>{escape(label)}</option>"
        for value, label in level_group_labels.items()
    )
    attendance_filter = (
        "<form method='get' action='/attendance' class='row g-2 align-items-end mb-3'>"
        "<div class='col-md-2'><label class='form-label' for='attendance-level-group'>Level group</label>"
        f"<select class='form-select' id='attendance-level-group' name='level_group'>{level_group_options}</select></div>"
        f"{''.join(filter_selects)}"
        "<div class='col-auto'><button class='btn btn-accent' type='submit'>View Attendance</button></div>"
        "<div class='col-auto'><a class='btn btn-outline-secondary' href='/attendance'>Clear filter</a></div></form>"
    )

    status_by_student = {}
    for record in today_records:
        status_by_student.setdefault(record.student_id, record.status.strip().casefold())

    totals_by_category = {}
    for student in scoped_students:
        class_name = (student.class_name or "").strip() or "Not specified"
        inferred_trade, inferred_level = CLASS_TRADE_LEVEL.get(class_name, (None, None))
        department = (student.department or student.trade or "").strip() or "Not specified"
        trade = (student.trade or inferred_trade or "").strip() or "Not specified"
        level = (student.level or inferred_level or "").strip() or "Not specified"
        section = (student.class_section or "").strip() or "Not specified"
        status = status_by_student.get(student.id, "unmarked")
        status_key = {
            "present": "present",
            "late": "late",
            "absent": "absent",
            "permission": "permission",
            "excused": "permission",
        }.get(status, "unmarked")

        for category_key in (
            (department, None, None, None, None),
            (department, trade, None, None, None),
            (department, trade, level, None, None),
            (department, trade, level, class_name, None),
            (department, trade, level, class_name, section),
        ):
            counts = totals_by_category.setdefault(category_key, {
                "enrolled": 0,
                "present": 0,
                "late": 0,
                "absent": 0,
                "permission": 0,
                "unmarked": 0,
            })
            counts["enrolled"] += 1
            counts[status_key] += 1

    def totals_row(department, trade, level, class_name, section, counts, emphasis=False):
        row_class = " class='fw-semibold'" if emphasis else ""
        return (
            f"<tr{row_class}><td>{escape(department)}</td><td>{escape(trade)}</td>"
            f"<td>{escape(level)}</td><td>{escape(class_name)}</td><td>{escape(section)}</td>"
            f"<td>{counts['enrolled']}</td>"
            f"<td>{counts['present']}</td><td>{counts['late']}</td>"
            f"<td>{counts['absent']}</td><td>{counts['permission']}</td>"
            f"<td>{counts['unmarked']}</td></tr>"
        )

    attendance_total_rows = []
    departments = sorted({key[0] for key in totals_by_category}, key=str.casefold)
    for department in departments:
        attendance_total_rows.append(totals_row(
            department, "All trades", "All levels", "All classes", "All sections",
            totals_by_category[(department, None, None, None, None)],
            emphasis=True,
        ))
        trades = sorted(
            {key[1] for key in totals_by_category if key[0] == department and key[1] is not None},
            key=str.casefold,
        )
        for trade in trades:
            attendance_total_rows.append(totals_row(
                department, trade, "All levels", "All classes", "All sections",
                totals_by_category[(department, trade, None, None, None)],
                emphasis=True,
            ))
            levels = sorted(
                {
                    key[2] for key in totals_by_category
                    if key[0] == department and key[1] == trade and key[2] is not None
                },
                key=str.casefold,
            )
            for level in levels:
                attendance_total_rows.append(totals_row(
                    department, trade, level, "All classes", "All sections",
                    totals_by_category[(department, trade, level, None, None)],
                    emphasis=True,
                ))
                classes = sorted(
                    {
                        key[3] for key in totals_by_category
                        if key[0] == department and key[1] == trade and key[2] == level and key[3] is not None
                    },
                    key=str.casefold,
                )
                for class_name in classes:
                    attendance_total_rows.append(totals_row(
                        department, trade, level, class_name, "All sections",
                        totals_by_category[(department, trade, level, class_name, None)],
                        emphasis=True,
                    ))
                    sections = sorted(
                        {
                            key[4] for key in totals_by_category
                            if key[0] == department and key[1] == trade and key[2] == level
                            and key[3] == class_name and key[4] is not None
                        },
                        key=str.casefold,
                    )
                    for section in sections:
                        attendance_total_rows.append(totals_row(
                            department, trade, level, class_name, section,
                            totals_by_category[(department, trade, level, class_name, section)],
                        ))
    if departments:
        grand_total = {
            status: sum(totals_by_category[(department, None, None, None, None)][status]
                        for department in departments)
            for status in ("enrolled", "present", "late", "absent", "permission", "unmarked")
        }
        attendance_total_rows.append(totals_row(
            "All departments", "All trades", "All levels", "All classes", "All sections",
            grand_total,
            emphasis=True,
        ))

    teacher_roster_section = ""
    if current_user.role == "teacher":
        class_students = scoped_students

        today_by_student = {}
        for record in today_records:
            today_by_student.setdefault(record.student_id, record)

        roster_rows = []
        for student in class_students:
            record = today_by_student.get(student.id)
            if record:
                status_markup = f"<span class='pill'>{escape(record.status)}</span>"
                arrival_time = escape(_scan_datetime(record.day, record.arrival_time))
            else:
                status_markup = "<span class='muted'>Awaiting RFID scan</span>"
                arrival_time = "-"
            roster_rows.append(
                f"<tr><td><a href='/student/profile/{student.id}'>{escape(student.full_name)}</a></td>"
                f"<td>{escape(student.class_name or '-')} / {escape(student.class_section or '-')}</td>"
                f"<td>{status_markup}</td><td>{arrival_time}</td></tr>"
            )

        if not teacher_class_keys:
            roster_content = "<p class='muted mb-0'>No class is assigned to your teacher account yet.</p>"
        else:
            roster_body = "".join(roster_rows) or (
                '<tr><td colspan="4" class="muted">No students are registered in your assigned class(es) yet.</td></tr>'
            )
            roster_content = (
                "<div class='table-responsive'><table class='table align-middle mb-0'>"
                "<thead><tr><th>Student</th><th>Class</th><th>Today's status</th><th>Morning Scan Date/Time</th></tr></thead>"
                f"<tbody>{roster_body}</tbody>"
                "</table></div>"
            )
        teacher_roster_section = (
            "<section class='panel'><h2>My Assigned Class Roster</h2>"
            "<p class='muted'>Students appear here automatically when their registration is saved. RFID scan date and time are shown using the server clock. Attendance is read-only.</p>"
            f"{roster_content}</section>"
        )
    today_rows = "".join(
        f"<tr><td>{escape(record.student.full_name if record.student else 'Unknown student')}</td>"
        f"<td>{escape((record.student.department or record.student.trade or '-') if record.student else '-')}</td>"
        f"<td>{escape(record.student.trade if record.student else '-')}</td>"
        f"<td>{escape(record.student.level if record.student else '-')}</td>"
        f"<td>{escape(record.student.class_name if record.student and record.student.class_name else '-')}</td>"
        f"<td>{escape(record.student.class_section if record.student and record.student.class_section else '-')}</td>"
        f"<td><span class='pill'>{escape(record.status)}</span></td>"
        f"<td>{escape(_scan_datetime(record.day, record.arrival_time))}</td>"
        f"<td>{escape(_scan_datetime(record.day, record.afternoon_time))}</td>"
        f"<td>{escape(record.source or '-')}</td></tr>"
        for record in today_records
    )
    rows = []
    for a in q:
        status_cell = f"<span class='pill'>{escape(a.status)}</span>"
        rows.append(
            f"<tr><td>{escape(a.day)}</td>"
            f"<td>{escape(a.student.full_name if a.student else 'Unknown student')}</td>"
            f"<td>{escape((a.student.department or a.student.trade or '-') if a.student else '-')}</td>"
            f"<td>{escape(a.student.trade if a.student else '-')}</td>"
            f"<td>{escape(a.student.level if a.student else '-')}</td>"
            f"<td>{escape(a.student.class_name if a.student and a.student.class_name else '-')}</td>"
            f"<td>{escape(a.student.class_section if a.student and a.student.class_section else '-')}</td>"
            f"<td>{status_cell}</td><td>{escape(_scan_datetime(a.day, a.arrival_time))}</td>"
            f"<td>{escape(_scan_datetime(a.day, a.afternoon_time))}</td><td>{escape(a.source or '-')}</td></tr>"
        )
    body = f"""<section class="panel"><h1>Attendance <span class="accent">Register</span></h1><p class="muted">Attendance is recorded automatically by RFID scans using the server clock. Scans through one minute after the allowed time ends are Present; scans more than one minute and up to 15 minutes after are Late. Students with no scan by the end of the 15-minute grace period are marked Absent when the ESP32 polls, staff open this page, or the scheduled attendance reconciliation command runs. Scans after the grace period are denied; the separately configured afternoon scan window remains available. Attendance statuses and times are read-only here. Class attendance updates are sent to assigned teachers, matron, patron, discipline staff, and school leadership.</p><div class="grid mb-4"><div class="stat"><div class="stat-value">{len(rfid_records)}</div><div class="stat-label">RFID Scans Today</div></div><div class="stat"><div class="stat-value">{len(late_records)}</div><div class="stat-label">Late Students Today</div></div><div class="stat"><div class="stat-value">{len(absent_records)}</div><div class="stat-label">Absences Today</div></div></div></section>{teacher_roster_section}<section class="panel"><h2>Attendance Totals by Department / Trade and Class</h2><p class="muted">Showing {escape(level_group_labels[selected_level_group])} for {today}. Totals follow the registered student structure: Trade / Department → Level → Class. Students without a recorded status are counted as Not marked.</p>{attendance_filter}<div class="table-responsive"><table class="table align-middle"><thead><tr><th>Department / Trade</th><th>Level</th><th>Class</th><th>Enrolled</th><th>Present</th><th>Late</th><th>Absent</th><th>Permission</th><th>Not marked</th></tr></thead><tbody>{''.join(attendance_total_rows) or '<tr><td colspan="9" class="muted">No students are registered in this attendance scope.</td></tr>'}</tbody></table></div></section><section class="panel"><h2>Today's Attendance Details</h2><div class="table-responsive"><table class="table align-middle"><thead><tr><th>Student</th><th>Class</th><th>Status</th><th>Morning Scan Date/Time</th><th>Afternoon Scan Date/Time</th><th>Source</th></tr></thead><tbody>{today_rows or '<tr><td colspan="6" class="muted">No attendance records recorded today.</td></tr>'}</tbody></table></div><h2 class="mt-4">Attendance History</h2><div class="table-responsive"><table class="table align-middle attendance-history"><thead><tr><th>Date</th><th>Student Name</th><th>Class</th><th>Status</th><th>Morning Scan Date/Time</th><th>Afternoon Scan Date/Time</th><th>Source</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="7" class="muted">No attendance records found.</td></tr>'}</tbody></table></div><a class="btn btn-outline-success mt-3" href="/attendance/export" style="border-color:var(--accent);color:var(--text)">Download Attendance Excel</a></section>"""
    body = body.replace(
        "Trade / Department â†’ Level â†’ Class.",
        "Department â†’ Trade â†’ Level â†’ Class â†’ Section.",
    )
    body = body.replace(
        "Attendance Totals by Department / Trade and Class",
        "Attendance Totals by Department, Trade, Level, Class and Section",
    )
    body = re.sub(
        r"Totals follow the registered student structure:[^<]*?Students without",
        "Totals follow the registered student structure: Department / Trade / Level / Class / Section. Students without",
        body,
        count=1,
    )
    body = body.replace(
        "<th>Department / Trade</th><th>Level</th><th>Class</th>",
        "<th>Department</th><th>Trade</th><th>Level</th><th>Class</th><th>Class Section</th>",
    ).replace('colspan="9" class="muted">No students are registered',
              'colspan="11" class="muted">No students are registered')
    body = body.replace(
        "<th>Student</th><th>Class</th><th>Status</th><th>Morning Scan Date/Time</th>",
        "<th>Student</th><th>Department</th><th>Trade</th><th>Level</th><th>Class</th><th>Section</th>"
        "<th>Status</th><th>Morning Scan Date/Time</th>",
    ).replace('colspan="6" class="muted">No attendance records recorded today',
              'colspan="10" class="muted">No attendance records recorded today')
    body = body.replace(
        "<th>Date</th><th>Student Name</th><th>Class</th><th>Status</th><th>Morning Scan Date/Time</th>",
        "<th>Date</th><th>Student Name</th><th>Department</th><th>Trade</th><th>Level</th>"
        "<th>Class</th><th>Section</th><th>Status</th><th>Morning Scan Date/Time</th>",
    ).replace('colspan="7" class="muted">No attendance records found',
              'colspan="11" class="muted">No attendance records found')
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
            vals = {key: normalize_clock(value) for key, value in vals.items()}
            if not (
                clock_minutes(vals["morning_start"]) <= clock_minutes(vals["late_after"])
                and clock_minutes(vals["late_after"]) + (LATE_GRACE_SECONDS // 60)
                < clock_minutes(vals["afternoon_start"])
                <= clock_minutes(vals["afternoon_end"])
            ):
                raise ValueError()
            for k, v in vals.items():
                set_setting(k, v)
            flash(
                f"Attendance windows saved: Present through one minute after {vals['late_after']}; "
                f"Late through 15 minutes after; then Absent if no scan.",
                "success",
            )
            return redirect(url_for("attendance_times"))
        except ValueError:
            flash("Use valid 24-hour times ensuring: Morning start ≤ allowed-time end, the 15-minute late period ends before Afternoon start, and Afternoon start ≤ Afternoon end.", "danger")
    values = {}
    for key, default in keys.items():
        try:
            values[key] = normalize_clock(get_setting(key, default))
        except ValueError:
            values[key] = normalize_clock(default)
    body = f"""<section class="panel"><h1>Attendance <span class="accent">Time Settings</span></h1><p class="muted">The server's local clock is used for every RFID scan. Scans through one minute after the allowed time ends are Present. Scans more than one minute and up to 15 minutes after are Late. If there is no scan by the end of those 15 minutes, the student is marked Absent. Scans after the 15-minute grace are denied, except during the separately configured Afternoon Start to Afternoon End scan window. Set Afternoon Start later than the end of the grace period.</p><form method="post" class="row g-3">
<div class="col-md-6"><label>Morning Start</label><input class="form-control" type="time" name="morning_start" value="{values['morning_start']}" required></div>
<div class="col-md-6"><label>Allowed Time Ends</label><input class="form-control" type="time" name="late_after" value="{values['late_after']}" required></div>
<div class="col-md-6"><label>Afternoon Start</label><input class="form-control" type="time" name="afternoon_start" value="{values['afternoon_start']}" required></div>
<div class="col-md-6"><label>Afternoon End</label><input class="form-control" type="time" name="afternoon_end" value="{values['afternoon_end']}" required></div>
<div class="col-12"><button class="btn btn-accent">Save Time Windows</button></div></form></section>"""
    return page("Attendance Times", body, "teacher")

def export_attendance():
    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance"
    ws.append([
        "Date", "Student Name", "Department", "Trade", "Level", "Class", "Class Section",
        "Status", "Morning Scan Date/Time", "Afternoon Scan Date/Time", "Source",
    ])
    query = Attendance.query.join(User, Attendance.student_id == User.id)
    if current_user.role == "teacher":
        query = query.filter(db.func.lower(User.class_name).in_({name.casefold() for name in teacher_assigned_classes(current_user)}))
    elif current_user.role in {"discipline", "patron", "matron"}:
        query = query.filter(User.class_name == current_user.class_name)
    for r in query.order_by(Attendance.day.desc()).all():
        ws.append([
            r.day,
            r.student.full_name if r.student else "",
            (r.student.department or r.student.trade or "") if r.student else "",
            r.student.trade if r.student else "",
            r.student.level if r.student else "",
            r.student.class_name if r.student else "",
            r.student.class_section if r.student else "",
            r.status,
            _scan_datetime(r.day, r.arrival_time) if r.arrival_time else "",
            _scan_datetime(r.day, r.afternoon_time) if r.afternoon_time else "",
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
