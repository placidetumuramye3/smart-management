import os
import datetime
import json
from flask import request, redirect, url_for, flash, jsonify, abort, send_file, current_app
from flask_login import login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from openpyxl import Workbook
from app.extensions import db
from app.models import User, Attendance, PendingCard, FeeTransaction, Book, DisciplineCase, SchoolMessage, Grade, TimetableEntry, Setting
from app.utils.constants import *
from app.utils.decorators import role_required
from app.utils.helpers import get_setting, set_setting, attendance_for_student
from app.utils.rendering import page
from app.utils.decorators import permission_for
from markupsafe import escape
from app.utils.notifications import notify_school_admins
from app.services.activity import log_student_activity
from app.services.attendance_service import (
    classify_morning_status,
    notify_class_attendance,
    reconcile_daily_absences,
)
from app.services.room_booking import check_in_student_for_current_meetings

def rfid_login():
    if request.method == "GET":
        return page("RFID Scanner Endpoint", """<section class="panel"><h1>RFID <span class="accent">Scanner API Endpoint</span></h1><p>Connect the RFID reader controller to <code>POST /student/rfid-login</code> with JSON such as <code>{"uid": "A1B2C3D4"}</code> or <code>{"rfid_card_uid": "A1B2C3D4"}</code>.</p><p class="muted">Keep the Student Registration page open while scanning a new card. The reader sends the UID here, and the form fills it automatically. Student scans record daily arrival and late status. After the configured late deadline, students without an attendance record are marked absent when a scan arrives or staff open Attendance. Class attendance alerts go to assigned class staff and school leadership.</p><p>Unassigned cards are added to the pending queue for registration.</p></section>""", "attendance")

    data = request.get_json(silent=True) if request.is_json else request.form
    if not data:
        data = {}
    if not hasattr(data, "get"):
        return jsonify(status="error", message="Expected a JSON object or form fields containing an RFID UID."), 400

    raw_uid = data.get("rfid_card_uid") or data.get("uid") or ""
    if isinstance(raw_uid, (dict, list, bool)):
        return jsonify(status="error", message="RFID UID must be a string or number."), 400
    uid = "".join(str(raw_uid).split()).upper()
    if not uid:
        return jsonify(status="error", message="No RFID UID provided."), 400
    if len(uid) > 100:
        return jsonify(status="error", message="RFID UID exceeds the 100-character limit."), 400

    now = datetime.datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    scan_stamp = now.strftime("%Y-%m-%d %H:%M:%S.%f")
    today = now.date().isoformat()
    clock = now.strftime("%H:%M")

    morning_start = get_setting("morning_start", DEFAULT_MORNING_START)
    late_after = get_setting("late_after", DEFAULT_LATE_AFTER)
    afternoon_start = get_setting("afternoon_start", DEFAULT_AFTERNOON_START)
    afternoon_end = get_setting("afternoon_end", DEFAULT_AFTERNOON_END)

    student = User.query.filter_by(rfid_uid=uid, role="student").first()
    if not student:
        pending = PendingCard.query.filter_by(uid=uid).first()
        if pending and pending.state == "Lost":
            return jsonify(status="lost_card", uid=uid,
                           message="This card was reported lost and is deactivated. Please contact the school office for a replacement."), 403
        if pending and pending.state != "Pending":
            return jsonify(status="card_not_eligible", uid=uid,
                           message="This card is not eligible for student registration."), 403
        if not pending:
            db.session.add(PendingCard(uid=uid, first_seen=stamp, last_seen=scan_stamp))
            db.session.commit()
            notify_school_admins(
                "RFID", "Unrecognized card scan",
                f"Card {uid} is not linked to a student and needs review; it may be unassigned or reported lost.",
                "/students",
            )
            db.session.commit()
        else:
            pending.last_seen = scan_stamp
        reconcile_daily_absences(now=now)
        db.session.commit()
        return jsonify(status="pending_registration", uid=uid,
                       message="Card UID read and queued for student registration."), 202

    meeting_checkins = check_in_student_for_current_meetings(student, now=now)
    row = Attendance.query.filter_by(student_id=student.id, day=today).first()

    if morning_start <= clock < afternoon_start:
        status = classify_morning_status(clock, late_after)
        recorded_arrival = False
        if not row:
            row = Attendance(student_id=student.id, day=today, status=status, arrival_time=stamp, source="RFID")
            db.session.add(row)
            recorded_arrival = True
        elif not row.arrival_time or row.status == "Absent":
            row.arrival_time = stamp
            row.status = status
            row.source = "RFID"
            if row.note and row.note.startswith("Automatically marked absent"):
                row.note = None
            recorded_arrival = True
        if recorded_arrival:
            notify_class_attendance(student, status, today, stamp)
        if clock > late_after:
            reconcile_daily_absences(now=now, exclude_student_ids={student.id})
        db.session.commit()
        return jsonify(status="success", student=student.full_name, attendance_status=row.status,
                       meeting_checkins=meeting_checkins, timestamp=stamp,
                       message=f"{student.full_name}: {row.status} recorded at {stamp}.")

    if afternoon_start <= clock <= afternoon_end:
        if not row:
            row = Attendance(student_id=student.id, day=today, status="Late", source="RFID")
            db.session.add(row)
            notify_class_attendance(student, "Late", today, stamp)
        elif row.status == "Absent":
            row.status = "Late"
            row.source = "RFID"
            if row.note and row.note.startswith("Automatically marked absent"):
                row.note = "Presence confirmed by an afternoon RFID scan; morning arrival was not recorded."
            notify_class_attendance(student, "Late", today, stamp)
        row.afternoon_time = stamp
        reconcile_daily_absences(now=now, exclude_student_ids={student.id})
        db.session.commit()
        return jsonify(status="success", student=student.full_name, attendance_status=row.status,
                       meeting_checkins=meeting_checkins, timestamp=stamp,
                       message=f"{student.full_name}: Afternoon attendance recorded at {stamp}.")

    reconcile_daily_absences(now=now)
    db.session.commit()
    if meeting_checkins:
        return jsonify(status="meeting_checkin", student=student.full_name,
                       meeting_checkins=meeting_checkins, timestamp=stamp,
                       message=f"{student.full_name}: checked in to {meeting_checkins} meeting(s) by RFID.")
    return jsonify(status="outside_window", student=student.full_name,
                   message=f"You late record data: Scan outside allowed windows. Morning: {morning_start}-{late_after}; Afternoon: {afternoon_start}-{afternoon_end}."), 403

def card_lookup():
    uid = request.args.get("uid", "").strip().replace(" ", "").upper()
    result = ""
    if uid:
        student = User.query.filter_by(rfid_uid=uid, role="student").first()
        if student:
            result = f"<div class='alert alert-success mt-3'><strong>{escape(student.full_name)}</strong><div>{escape(student.class_name or 'No class')} · {escape(student.trade or 'No trade')}</div><a class='btn btn-sm btn-accent mt-2' href='/student/profile/{student.id}'>Open Student Profile</a></div>"
        else:
            result = f"<div class='alert alert-warning mt-3'>No student is linked to card <code>{escape(uid)}</code>.</div>"
    body = f"""<section class='panel'><h1>Card <span class='accent'>Lookup</span></h1><p class='muted'>Scan or tap a card to find its linked student profile.</p><form method='get' class='row g-2'><div class='col-md-8'><label class='form-label' for='card-uid'>Card UID</label><input id='card-uid' name='uid' class='form-control' value='{escape(uid)}' autocomplete='off' autofocus required></div><div class='col-md-4 d-flex align-items-end'><button class='btn btn-accent w-100'>Look Up Card</button></div></form>{result}</section>"""
    return page("Card Lookup", body, "attendance")

def report_lost_card():
    if request.method == "POST":
        student = db.session.get(User, request.form.get("student_id", type=int))
        if not student or student.role != "student" or not student.rfid_uid:
            abort(400)
        lost_uid = student.rfid_uid
        student.rfid_uid = None
        pending = PendingCard.query.filter_by(uid=lost_uid).first()
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if pending:
            pending.state = "Lost"
        else:
            db.session.add(PendingCard(uid=lost_uid, first_seen=timestamp, state="Lost"))
        notify_school_admins(
            "RFID", "Student card reported lost",
            f"{student.full_name}'s card {lost_uid} was deactivated. A replacement card needs to be assigned.",
            "/students",
        )
        log_student_activity(current_user, student, "RFID card reported lost", f"Card {lost_uid} deactivated and marked lost.")
        db.session.commit()
        flash("Lost card deactivated and school staff notified.", "success")
        return redirect(url_for("report_lost_card"))
    students = User.query.filter(User.role == "student", User.rfid_uid.is_not(None)).order_by(User.full_name).all()
    options = "".join(f"<option value='{student.id}'>{escape(student.full_name)} · {escape(student.class_name or 'No class')} · {escape(student.rfid_uid)}</option>" for student in students)
    body = f"<section class='panel'><h1>Report a <span class='accent'>Lost Card</span></h1><p class='muted'>Reporting deactivates the card UID on the student's account and alerts school administrators.</p><form method='post' class='row g-3'><div class='col-md-8'><label class='form-label'>Student and assigned card</label><select class='form-select' name='student_id' required><option value=''>Choose student</option>{options}</select></div><div class='col-md-4 d-flex align-items-end'><button class='btn btn-danger'>Deactivate Lost Card</button></div></form>{'<p class="muted mt-3">No assigned student cards are available to report.</p>' if not options else ''}</section>"
    return page("Report Lost Card", body, "master_admin")

def register_routes(app):
    app.add_url_rule('/student/rfid-login', endpoint='rfid_login', view_func=rfid_login, methods=['GET', 'POST'])
    app.add_url_rule('/rfid/card-lookup', endpoint='card_lookup', view_func=permission_for('card_lookup')(card_lookup), methods=['GET'])
    app.add_url_rule('/rfid/report-lost', endpoint='report_lost_card', view_func=permission_for('report_lost_card')(report_lost_card), methods=['GET', 'POST'])
