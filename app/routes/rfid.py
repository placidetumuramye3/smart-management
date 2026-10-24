import os
import datetime
import json
import hmac
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
    DEFAULT_PRESENT_GRACE_MINUTES,
    LATE_GRACE_SECONDS,
    class_absent_student_names,
    class_attendance_summary,
    clock_minutes,
    classify_morning_status,
    get_present_grace_minutes,
    notify_class_attendance,
    reconcile_daily_absences,
    school_attendance_summary,
    student_class_attendance_summary,
)
from app.services.room_booking import check_in_student_for_current_meetings


def _rfid_device_token_error():
    """Enforce the configured device token while preserving legacy setups without one."""
    expected_token = str(current_app.config.get("RFID_DEVICE_TOKEN", "") or "")
    if not expected_token:
        return None
    supplied_token = request.headers.get("X-Device-Token", "")
    if hmac.compare_digest(supplied_token, expected_token):
        return None
    return jsonify(status="device_unauthorized", message="RFID device token is missing or invalid."), 401


def rfid_class_status():
    """Return a configured class summary or school-wide aggregate to the RFID display."""
    expected_token = str(current_app.config.get("RFID_DEVICE_TOKEN", "") or "")
    allowed_class = str(current_app.config.get("RFID_DEVICE_CLASS", "") or "").strip()
    if not expected_token:
        return jsonify(status="error", message="RFID status display is not configured."), 503
    token_error = _rfid_device_token_error()
    if token_error:
        return token_error

    requested_class = (request.args.get("class") or "").strip()
    if len(requested_class) > 100:
        return jsonify(status="error", message="Class name must be 100 characters or fewer."), 400
    if allowed_class:
        if requested_class and requested_class.casefold() != allowed_class.casefold():
            return jsonify(status="error", message="This device is not authorized for that class."), 403
        class_name = allowed_class
    else:
        if requested_class:
            return jsonify(status="error", message="No class is configured for this device."), 403
        class_name = "All classes"

    now = datetime.datetime.now()
    try:
        absences_recorded = reconcile_daily_absences(now=now)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("RFID class-status reconciliation failed")
        return jsonify(status="error", message="Attendance status is temporarily unavailable."), 503

    today = now.date().isoformat()
    summary = (
        class_attendance_summary(allowed_class, today)
        if allowed_class else school_attendance_summary(today)
    )
    absent_students = class_absent_student_names(allowed_class, today) if allowed_class else []
    return jsonify(
        status="success",
        class_name=class_name,
        class_summary=summary,
        absent_students=absent_students,
        absences_recorded=len(absences_recorded),
        updated_at=now.strftime("%Y-%m-%d %H:%M:%S"),
    )

def rfid_login():
    if request.method == "GET":
        try:
            grace_minutes = get_present_grace_minutes()
        except ValueError:
            grace_minutes = DEFAULT_PRESENT_GRACE_MINUTES
        return page("RFID Scanner Endpoint", f"""<section class="panel"><h1>RFID <span class="accent">Scanner API Endpoint</span></h1><p>Connect the RFID reader controller to <code>POST /student/rfid-login</code> with JSON such as <code>{{"uid": "A1B2C3D4"}}</code> or <code>{{"rfid_card_uid": "A1B2C3D4"}}</code>.</p><p class="muted">The server clock and saved Scan Times settings determine each result; the ESP32 sends the card UID. Scans through the configured {grace_minutes}-minute Present grace after the allowed time ends are Present. Later scans through 15 minutes after the deadline are Late. After 15 minutes, the endpoint returns Access Denied and keeps the student Absent; the configured afternoon scan window remains available. The ESP32's protected status poll, staff opening Attendance, or the scheduled attendance reconciliation command queues in-app alerts for newly absent students, guardians, and authorized staff. Late scans queue alerts immediately. Parent email and SMS alerts also require configured providers.</p><p>Configure the same RFID_DEVICE_TOKEN in the server environment and ESP32 sketch to authenticate requests. Leave RFID_DEVICE_CLASS blank to let each scanned card use its registered student's class and show aggregate school attendance totals on the idle LCD. Set RFID_DEVICE_CLASS only when the reader should be restricted to one class. Unassigned cards are added to the pending queue for registration.</p></section>""", "attendance")

    token_error = _rfid_device_token_error()
    if token_error:
        return token_error

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
    scan_clock = now.strftime("%H:%M:%S")

    morning_start = get_setting("morning_start", DEFAULT_MORNING_START)
    late_after = get_setting("late_after", DEFAULT_LATE_AFTER)
    afternoon_start = get_setting("afternoon_start", DEFAULT_AFTERNOON_START)
    afternoon_end = get_setting("afternoon_end", DEFAULT_AFTERNOON_END)
    try:
        morning_start_minute = clock_minutes(morning_start)
        late_after_minute = clock_minutes(late_after)
        afternoon_start_minute = clock_minutes(afternoon_start)
        afternoon_end_minute = clock_minutes(afternoon_end)
        configured_present_grace = get_present_grace_minutes()
    except ValueError:
        current_app.logger.exception("Invalid saved RFID attendance time setting")
        return jsonify(
            status="error",
            message="Attendance times or Present grace are invalid. Ask the school administrator to save the time settings again.",
        ), 503
    current_seconds = now.hour * 3600 + now.minute * 60 + now.second
    allowed_end_seconds = late_after_minute * 60
    late_grace_end_seconds = allowed_end_seconds + LATE_GRACE_SECONDS
    if not (
        morning_start_minute <= late_after_minute
        and late_grace_end_seconds < afternoon_start_minute * 60
        and afternoon_start_minute <= afternoon_end_minute
    ):
        return jsonify(
            status="error",
            message="Attendance times must leave the full 15-minute late period before Afternoon Start.",
        ), 503
    morning_start = f"{morning_start_minute // 60:02d}:{morning_start_minute % 60:02d}"
    late_after = f"{late_after_minute // 60:02d}:{late_after_minute % 60:02d}"
    afternoon_start = f"{afternoon_start_minute // 60:02d}:{afternoon_start_minute % 60:02d}"
    afternoon_end = f"{afternoon_end_minute // 60:02d}:{afternoon_end_minute % 60:02d}"

    student = User.query.filter_by(rfid_uid=uid, role="student").first()
    if student and student.is_active_account is False:
        return jsonify(
            status="student_inactive",
            student=student.full_name,
            attendance_status="Inactive",
            timestamp=stamp,
            message="This student's account is archived. Contact the school office.",
        ), 403
    allowed_class = str(current_app.config.get("RFID_DEVICE_CLASS", "") or "").strip()
    if student and allowed_class and (student.class_name or "").strip().casefold() != allowed_class.casefold():
        return jsonify(
            status="class_not_allowed",
            message="This RFID reader is not authorized for the student's class.",
        ), 403
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
    in_afternoon_window = (
        afternoon_start_minute * 60 <= current_seconds <= afternoon_end_minute * 60
    )

    if current_seconds > late_grace_end_seconds and not in_afternoon_window:
        # Keep any meeting check-in above, but reject this as a school arrival
        # scan once the configured late-arrival grace period has expired.
        reconcile_daily_absences(now=now)
        db.session.commit()
        late_window_end = late_grace_end_seconds
        late_window_end_text = (
            f"{late_window_end // 3600:02d}:"
            f"{(late_window_end % 3600) // 60:02d}"
        )
        return jsonify(
            status="access_denied",
            student=student.full_name,
            attendance_status=row.status if row else "Absent",
            class_name=student.class_name or "Unassigned",
            timestamp=stamp,
            allowed_time_ends=late_after,
            late_window_ends=late_window_end_text,
            present_grace_minutes=configured_present_grace,
            meeting_checkins=meeting_checkins,
            message=(
                f"Access denied: the RFID arrival window ended at {late_window_end_text}. "
                "The 15-minute late-arrival grace period has expired."
            ),
        ), 403

    if morning_start_minute * 60 <= current_seconds < afternoon_start_minute * 60:
        status = classify_morning_status(scan_clock, late_after, configured_present_grace)
        recorded_arrival = False
        should_notify = True
        notification_time = stamp
        if not row:
            row = Attendance(student_id=student.id, day=today, status=status, arrival_time=stamp, source="RFID")
            db.session.add(row)
            recorded_arrival = True
        elif row.status == "Absent" and status == "Absent":
            # Record the first late RFID arrival without changing the Absent
            # status or sending the same absence alert again on repeat scans.
            if not row.arrival_time:
                row.arrival_time = stamp
                row.source = "RFID"
                if row.note and row.note.startswith("Automatically marked absent"):
                    row.note = f"RFID scan recorded at {stamp}, after the 15-minute late-arrival grace period; attendance remains Absent."
                recorded_arrival = True
            should_notify = False
        elif row.status == "Absent" or not row.arrival_time:
            row.arrival_time = stamp
            row.status = status
            row.source = "RFID"
            if row.note and row.note.startswith("Automatically marked absent"):
                row.note = None
            recorded_arrival = True
        elif row.source == "RFID" and row.arrival_time:
            # If an administrator corrected today's allowed-time end, re-evaluate the
            # original RFID arrival time when the student scans again.
            saved_clock = row.arrival_time.rsplit(" ", 1)[-1][:8]
            try:
                corrected_status = classify_morning_status(
                    saved_clock, late_after, configured_present_grace
                )
            except ValueError:
                corrected_status = row.status
            if row.status != corrected_status:
                row.status = corrected_status
                recorded_arrival = True
                notification_time = row.arrival_time
        if recorded_arrival and should_notify:
            notify_class_attendance(student, row.status, today, notification_time)
        if current_seconds > late_grace_end_seconds:
            reconcile_daily_absences(now=now, exclude_student_ids={student.id})
        db.session.commit()
        return jsonify(status="success", student=student.full_name, attendance_status=row.status,
                       class_name=student.class_name or "Unassigned",
                       class_summary=student_class_attendance_summary(student, today),
                       server_time=clock, morning_start=morning_start,
                       late_after=late_after,
                       present_grace_minutes=configured_present_grace,
                       afternoon_start=afternoon_start,
                       afternoon_end=afternoon_end,
                       meeting_checkins=meeting_checkins, timestamp=stamp,
                       message=f"{student.full_name}: {row.status} recorded at {stamp}.")

    if in_afternoon_window:
        new_afternoon_scan = not row or not row.afternoon_time
        if not row:
            row = Attendance(
                student_id=student.id,
                day=today,
                status="Absent",
                source="RFID",
                note="Afternoon RFID scan recorded; attendance remains Absent because the 15-minute morning late-arrival grace period ended.",
            )
            db.session.add(row)
        elif row.status == "Absent":
            if row.note and row.note.startswith("Automatically marked absent"):
                row.note = f"Afternoon RFID scan recorded at {stamp}; attendance remains Absent because the 15-minute morning late-arrival grace period ended."
            row.source = "RFID"
        row.afternoon_time = stamp
        if new_afternoon_scan:
            notify_class_attendance(student, row.status, today, stamp, session="afternoon")
        reconcile_daily_absences(now=now, exclude_student_ids={student.id})
        db.session.commit()
        return jsonify(status="success", student=student.full_name, attendance_status=row.status,
                       class_name=student.class_name or "Unassigned",
                       class_summary=student_class_attendance_summary(student, today),
                       server_time=clock, morning_start=morning_start,
                       late_after=late_after,
                       present_grace_minutes=configured_present_grace,
                       afternoon_start=afternoon_start,
                       afternoon_end=afternoon_end,
                       meeting_checkins=meeting_checkins, timestamp=stamp,
                       message=f"{student.full_name}: Afternoon attendance recorded at {stamp}.")

    reconcile_daily_absences(now=now)
    db.session.commit()
    class_summary = student_class_attendance_summary(student, today)
    if meeting_checkins:
        return jsonify(status="meeting_checkin", student=student.full_name,
                       class_name=student.class_name or "Unassigned",
                       class_summary=class_summary,
                       meeting_checkins=meeting_checkins, timestamp=stamp,
                       message=f"{student.full_name}: checked in to {meeting_checkins} meeting(s) by RFID.")
    return jsonify(status="outside_window", student=student.full_name,
                   class_name=student.class_name or "Unassigned",
                   class_summary=class_summary,
                   server_time=clock, morning_start=morning_start,
                   late_after=late_after, present_grace_minutes=configured_present_grace,
                   afternoon_start=afternoon_start,
                   afternoon_end=afternoon_end,
                   message=f"Scan at server time {clock} is outside the allowed windows. Morning: {morning_start}-{afternoon_start}; afternoon: {afternoon_start}-{afternoon_end}."), 403

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
    app.add_url_rule('/student/rfid-status', endpoint='rfid_class_status', view_func=rfid_class_status, methods=['GET'])
    app.add_url_rule('/rfid/card-lookup', endpoint='card_lookup', view_func=permission_for('card_lookup')(card_lookup), methods=['GET'])
    app.add_url_rule('/rfid/report-lost', endpoint='report_lost_card', view_func=permission_for('report_lost_card')(report_lost_card), methods=['GET', 'POST'])
