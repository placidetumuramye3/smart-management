from io import BytesIO

import datetime

from flask import abort, request, send_file, redirect, url_for, flash
from flask_login import current_user
from markupsafe import escape
from openpyxl import Workbook

from app.extensions import db
from app.models import AdmissionApplicant, Attendance, PendingCard, User, ActivityLog, Appointment, SchoolMessage
from app.utils.decorators import permission_for
from app.utils.helpers import now_string
from app.utils.notifications import notify_users
from app.utils.rendering import page


REPORT_TYPES = {"students", "admissions", "attendance", "cards"}
SUMMARY_PERIODS = {"daily", "weekly", "monthly", "yearly"}


def _summary_window(period, today):
    if period == "daily":
        start = today
    elif period == "weekly":
        start = today - datetime.timedelta(days=today.weekday())
    elif period == "monthly":
        start = today.replace(day=1)
    else:
        start = today.replace(month=1, day=1)
    return start, today


def _office_summary(period, start, end):
    start_iso, end_iso = start.isoformat(), end.isoformat()
    activity_start = datetime.datetime.combine(start, datetime.time.min)
    activity_end = datetime.datetime.combine(end + datetime.timedelta(days=1), datetime.time.min)
    attendance_total = Attendance.query.filter(Attendance.day >= start_iso, Attendance.day <= end_iso).count()
    late_total = Attendance.query.filter(Attendance.day >= start_iso, Attendance.day <= end_iso, Attendance.status == "Late").count()
    applicants = AdmissionApplicant.query.filter(
        AdmissionApplicant.applied_at >= activity_start,
        AdmissionApplicant.applied_at < activity_end,
    ).count()
    student_added = ActivityLog.query.filter(
        ActivityLog.created_at >= activity_start,
        ActivityLog.created_at < activity_end,
        ActivityLog.action.in_({"Student registered", "Admission converted"}),
    ).count()
    student_removed = ActivityLog.query.filter(
        ActivityLog.created_at >= activity_start,
        ActivityLog.created_at < activity_end,
        ActivityLog.action == "Student removed",
    ).count()
    appointments = Appointment.query.filter(
        Appointment.scheduled_date >= start_iso,
        Appointment.scheduled_date <= end_iso,
    ).count()
    return {
        "period": period,
        "start": start_iso,
        "end": end_iso,
        "attendance": attendance_total,
        "late": late_total,
        "admissions": applicants,
        "students_added": student_added,
        "students_removed": student_removed,
        "appointments": appointments,
    }


def _summary_text(summary):
    return (
        f"School office {summary['period']} summary ({summary['start']} to {summary['end']}): "
        f"{summary['attendance']} attendance records, {summary['late']} late arrivals, "
        f"{summary['admissions']} new applicants, {summary['students_added']} students added, "
        f"{summary['students_removed']} students removed, and {summary['appointments']} appointments."
    )


def send_summary():
    period = request.form.get("period", "daily")
    if period not in SUMMARY_PERIODS:
        abort(400)
    start, end = _summary_window(period, datetime.date.today())
    summary = _office_summary(period, start, end)
    recipients = User.query.filter(User.role.in_({"master_admin", "head_teacher"})).all()
    timestamp = now_string()
    body = _summary_text(summary)
    for recipient in recipients:
        db.session.add(SchoolMessage(
            sender_id=current_user.id,
            sender_name=current_user.full_name,
            sender_role=current_user.role,
            recipient_id=recipient.id,
            subject=f"School {period.title()} Office Summary",
            body=body,
            created_at=timestamp,
        ))
    notify_users({recipient.id for recipient in recipients}, "Office Summary", f"School {period.title()} Office Summary", body, "/reports?type=summary")
    db.session.commit()
    flash("Summary sent to the Master Admin and Head Teacher.", "success")
    return redirect(url_for("reports", type="summary", period=period))


def reports():
    report_type = request.args.get("type", "students")
    if report_type == "summary":
        period = request.args.get("period", "daily")
        if period not in SUMMARY_PERIODS:
            abort(400)
        start, end = _summary_window(period, datetime.date.today())
        summary = _office_summary(period, start, end)
        period_options = "".join(
            f"<option value='{value}'{' selected' if value == period else ''}>{label}</option>"
            for value, label in (("daily", "Today"), ("weekly", "This week"), ("monthly", "This month"), ("yearly", "This year"))
        )
        metrics = (
            ("Attendance Records", summary["attendance"]), ("Late Arrivals", summary["late"]),
            ("New Applicants", summary["admissions"]), ("Students Added", summary["students_added"]),
            ("Students Removed", summary["students_removed"]), ("Appointments", summary["appointments"]),
        )
        cards = "".join(f"<div class='stat'><div class='stat-value'>{value}</div><div class='stat-label'>{label}</div></div>" for label, value in metrics)
        body = f"""<section class='panel'><h1>Office Summary</h1><p class='muted'>Review daily, weekly, monthly, or yearly activity and send the summary to school leadership.</p><form method='get' class='row g-3 align-items-end'><input type='hidden' name='type' value='summary'><div class='col-md-4'><label class='form-label'>Reporting period</label><select class='form-select' name='period'>{period_options}</select></div><div class='col-md-4'><button class='btn btn-accent'>View Summary</button></div></form><p class='mt-3 mb-0'><strong>Dates:</strong> {summary['start']} – {summary['end']}</p></section><section class='grid mb-4'>{cards}</section><section class='panel'><h2>Share with leadership</h2><p class='muted'>Sends this summary to all Master Admin and Head Teacher accounts as an inbox message and notification.</p><form method='post' action='/reports/send-summary'><input type='hidden' name='period' value='{period}'><button class='btn btn-accent'>Send Summary</button></form><p class='small muted mt-3 mb-0'>Student additions and removals are counted from activity records available in the system.</p></section>"""
        return page("Office Summary", body, "master_admin")
    if report_type not in REPORT_TYPES:
        abort(400)
    class_name = request.args.get("class_name", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    for value in (date_from, date_to):
        if value:
            try:
                from datetime import date
                date.fromisoformat(value)
            except ValueError:
                abort(400)
    if date_from and date_to and date_from > date_to:
        abort(400)

    if report_type == "students":
        headers = ["Student", "SDMS Code", "Department", "Trade", "Level", "Class", "Class Section", "RFID Card"]
        query = User.query.filter_by(role="student")
        if class_name:
            query = query.filter_by(class_name=class_name)
        rows = [[s.full_name, s.sdms_code or "", s.department or s.trade or "", s.trade or "", s.level or "", s.class_name or "", s.class_section or "", s.rfid_uid or "Not assigned"] for s in query.order_by(User.full_name).all()]
        title = "Student Report"
    elif report_type == "admissions":
        headers = ["Applicant", "Guardian", "Phone", "Email", "Department", "Trade", "Level", "Requested Class", "Class Section", "Documents", "Verified", "Status", "Applied"]
        query = AdmissionApplicant.query
        if class_name:
            query = query.filter_by(desired_class=class_name)
        applications = query.order_by(AdmissionApplicant.applied_at.desc()).all()
        if date_from:
            applications = [item for item in applications if item.applied_at.strftime("%Y-%m-%d") >= date_from]
        if date_to:
            applications = [item for item in applications if item.applied_at.strftime("%Y-%m-%d") <= date_to]
        rows = [[a.full_name, a.guardian_name or "", a.phone or "", a.email or "", a.desired_department or a.desired_trade, a.desired_trade, a.desired_level, a.desired_class, a.desired_section or "", a.documents_submitted or "", "Yes" if a.documents_verified else "No", a.status, a.applied_at.strftime("%Y-%m-%d")] for a in applications]
        title = "Admissions Report"
    elif report_type == "attendance":
        headers = ["Date", "Student", "Department", "Trade", "Level", "Class", "Class Section", "Status", "Arrival", "Afternoon Scan", "Source"]
        query = Attendance.query.join(User, Attendance.student_id == User.id)
        if class_name:
            query = query.filter(User.class_name == class_name)
        if date_from:
            query = query.filter(Attendance.day >= date_from)
        if date_to:
            query = query.filter(Attendance.day <= date_to)
        records = query.order_by(Attendance.day.desc(), Attendance.id.desc()).all()
        rows = [[a.day, a.student.full_name if a.student else "Unknown", (a.student.department or a.student.trade or "") if a.student else "", a.student.trade or "" if a.student else "", a.student.level or "" if a.student else "", a.student.class_name or "" if a.student else "", a.student.class_section or "" if a.student else "", a.status, a.arrival_time or "", a.afternoon_time or "", a.source or ""] for a in records]
        title = "Attendance Report"
    else:
        headers = ["Student / Card", "Class", "UID", "Card Status", "First Seen"]
        student_query = User.query.filter_by(role="student")
        if class_name:
            student_query = student_query.filter_by(class_name=class_name)
        students = student_query.order_by(User.full_name).all()
        rows = [[student.full_name, student.class_name or "", student.rfid_uid or "", "Assigned" if student.rfid_uid else "No card", ""] for student in students]
        pending = PendingCard.query.order_by(PendingCard.first_seen.desc()).all()
        for card in pending:
            if class_name:
                continue
            if date_from and card.first_seen[:10] < date_from:
                continue
            if date_to and card.first_seen[:10] > date_to:
                continue
            rows.append(["", "", card.uid, "Reported lost" if card.state == "Lost" else "Pending registration", card.first_seen])
        title = "RFID Card Report"

    if request.args.get("format") == "xlsx":
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = title[:31]
        worksheet.append(headers)
        for row in rows:
            worksheet.append(row)
        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=f"{report_type}_report.xlsx", mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    classes = sorted({student.class_name for student in User.query.filter_by(role="student").all() if student.class_name})
    type_options = "".join(
        f"<option value='{key}'{' selected' if report_type == key else ''}>{label}</option>"
        for key, label in (("students", "Students"), ("admissions", "Admissions"), ("attendance", "Attendance"), ("cards", "RFID / Cards"))
    )
    class_options = "".join(
        f"<option value='{escape(value)}'{' selected' if value == class_name else ''}>{escape(value)}</option>"
        for value in classes
    )
    result_rows = "".join("<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in row) + "</tr>" for row in rows)
    download_query = request.args.to_dict()
    download_query["format"] = "xlsx"
    from urllib.parse import urlencode
    download_url = f"/reports?{urlencode(download_query)}"
    heading = "<h1>Reports</h1><p class='muted'>Generate student, admission, attendance, and RFID card reports.</p><a class='btn btn-outline-success' href='/reports?type=summary'>Office summaries</a>"
    if current_user.role not in {"master_admin", "head_teacher", "secretary"}:
        heading = "<h1>Reports</h1><p class='muted'>View authorized school reports.</p>"
    filters = f"""<section class='panel'>{heading}<form method='get' class='row g-3'><div class='col-md-3'><label class='form-label'>Report</label><select class='form-select' name='type'>{type_options}</select></div><div class='col-md-3'><label class='form-label'>Class</label><select class='form-select' name='class_name'><option value=''>All classes</option>{class_options}</select></div><div class='col-md-3'><label class='form-label'>From date</label><input class='form-control' type='date' name='date_from' value='{escape(date_from)}'></div><div class='col-md-3'><label class='form-label'>To date</label><input class='form-control' type='date' name='date_to' value='{escape(date_to)}'></div><div class='col-12'><button class='btn btn-accent'>Generate Report</button></div></form></section>"""
    body = filters + f"<section class='panel'><div class='d-flex justify-content-between align-items-center flex-wrap gap-2'><h2>{title} · {len(rows)} records</h2><a class='btn btn-outline-success' href='{escape(download_url)}'>Download Excel</a></div><div class='table-responsive'><table class='table align-middle'><thead><tr>{''.join(f'<th>{escape(header)}</th>' for header in headers)}</tr></thead><tbody>{result_rows or f'<tr><td colspan="{len(headers)}" class="muted">No matching records found.</td></tr>'}</tbody></table></div></section>"
    return page("Reports", body, "master_admin")


def register_routes(app):
    app.add_url_rule("/reports", endpoint="reports", view_func=permission_for("reports")(reports), methods=["GET"])
    app.add_url_rule("/reports/send-summary", endpoint="send_summary", view_func=permission_for("reports")(send_summary), methods=["POST"])
