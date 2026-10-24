import os
import datetime
import json
import re
import secrets
from io import BytesIO
from flask import request, redirect, url_for, flash, jsonify, abort, send_file, current_app, make_response, session
from flask_login import login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from openpyxl import Workbook
from app.extensions import db
from app.models import User, Attendance, PendingCard, FeeTransaction, Book, BookLoan, DisciplineCase, SchoolMessage, Grade, TimetableEntry, DayScheduleEntry, Setting, StudentGuardian
from app.utils.constants import *
from app.utils.decorators import role_required
from app.utils.helpers import get_setting, set_setting, attendance_for_student, generate_sdms_code, teacher_assigned_classes, teacher_can_access_class, now_string, guardian_can_access_student
from app.utils.rendering import page
from app.utils.decorators import permission_for
from markupsafe import escape
from app.services.activity import log_student_activity

def student_profile(student_id=None):
    if current_user.role == "student":
        student = current_user
    elif student_id is not None:
        student = db.session.get(User, student_id)
    else:
        abort(404)
    if not student or student.role != "student":
        abort(404)

    if current_user.role == "parent":
        if not guardian_can_access_student(current_user.id, student.id):
            abort(403)
    elif current_user.role in {"student"}:
        if student.id != current_user.id:
            abort(403)
    elif current_user.role == "teacher":
        if not teacher_can_access_class(current_user, student.class_name):
            abort(403)
    elif current_user.role in {"discipline", "patron", "matron"}:
        if student.class_name != current_user.class_name:
            abort(403)

    if request.method == "POST":
        if current_user.role != "student" or student.id != current_user.id:
            abort(403)
        uploaded = request.files.get("profile_photo")
        if not uploaded or not uploaded.filename:
            flash("Choose a profile photo to upload.", "danger")
        else:
            filename = secure_filename(uploaded.filename)
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext not in ALLOWED_LOGO_EXTENSIONS:
                flash("Profile photo must be PNG, JPG, JPEG, or WEBP.", "danger")
            else:
                os.makedirs(current_app.config["UPLOAD_FOLDER"], exist_ok=True)
                stored_name = f"student_{student.id}_{secrets.token_hex(6)}.{ext}"
                uploaded.save(os.path.join(current_app.config["UPLOAD_FOLDER"], stored_name))
                student.profile_photo = stored_name
                log_student_activity(current_user, student, "Student profile updated", "Profile photo changed.")
                db.session.commit()
                flash("Profile photo updated.", "success")
                return redirect(url_for("student_profile"))

    photo = url_for("student_photo", student_id=student.id) if student.profile_photo else ""
    photo_style = "width:132px;height:132px;object-fit:cover;border-radius:50%;border:4px solid var(--border);background:var(--page)"
    photo_markup = f'<img src="{photo}" alt="Profile photo of {student.full_name}" style="{photo_style}">' if photo else f'<div class="d-grid place-items-center" style="{photo_style};display:grid;place-items:center;font-size:3rem;color:var(--muted)"><i class="bi bi-person"></i></div>'
    motto = get_setting("school_motto", "").strip()
    scan_records = attendance_for_student(student.id)
    scan_rows = "".join(f"<tr><td>{record.day}</td><td><span class='pill'>{record.status}</span></td><td>{record.arrival_time or '-'}</td><td>{record.afternoon_time or '-'}</td><td>{record.source or 'Manual'}</td></tr>" for record in scan_records)
    timetable_entries = TimetableEntry.query.filter_by(class_name=student.class_name).order_by(TimetableEntry.day_of_week, TimetableEntry.time_slot).all() if student.class_name else []
    timetable_rows = "".join(f"<tr><td>{entry.day_of_week}</td><td>{entry.time_slot or '-'}</td><td>{entry.subject}</td><td>{entry.teacher_name or '-'}</td></tr>" for entry in timetable_entries)
    day_schedule_query = DayScheduleEntry.query.filter(DayScheduleEntry.is_published.is_(True), DayScheduleEntry.schedule_date >= datetime.date.today())
    day_schedule_query = day_schedule_query.filter(db.or_(DayScheduleEntry.class_name.is_(None), DayScheduleEntry.class_name == student.class_name))
    day_schedule_entries = day_schedule_query.order_by(DayScheduleEntry.schedule_date, DayScheduleEntry.start_time).all()
    day_schedule_rows = "".join(f"<tr><td>{entry.schedule_date.isoformat()}</td><td>{entry.start_time or '-'}{f' - {entry.end_time}' if entry.end_time else ''}</td><td>{entry.activity}</td><td>{entry.details or '-'}</td></tr>" for entry in day_schedule_entries)
    book_loans = BookLoan.query.filter_by(student_id=student.id).order_by(BookLoan.borrowed_date.desc(), BookLoan.id.desc()).all()
    book_loan_rows = "".join(f"<tr><td>{escape(loan.book.title if loan.book else '-')}</td><td>{loan.borrowed_date.isoformat()}</td><td>{loan.due_date.isoformat()}</td><td>{loan.returned_date.isoformat() if loan.returned_date else '-'}</td><td>{'Returned' if loan.returned_date else 'Overdue' if loan.due_date < datetime.date.today() else 'Borrowed'}</td></tr>" for loan in book_loans)
    discipline_cases = DisciplineCase.query.filter_by(student_id=student.id, is_published=True).order_by(DisciplineCase.id.desc()).all()
    discipline_rows = "".join(f"<tr><td>{escape(case.created_at)}</td><td>{escape(case.record_type or 'Concern')}</td><td>{escape(case.details)}</td><td>{escape(case.action_taken or '-')}</td><td>{escape(case.remarks or '-')}</td></tr>" for case in discipline_cases)
    attendance_counts = {status: sum(1 for record in scan_records if record.status == status) for status in ("Present", "Absent", "Late")}
    attendance_summary = "".join(f"<div class='stat'><div class='stat-value'>{attendance_counts[status]}</div><div class='stat-label'>{status} days</div></div>" for status in ("Present", "Absent", "Late"))
    upload_form = """<form method="post" enctype="multipart/form-data" class="mt-3"><label class="form-label">Update profile photo</label><input class="form-control" type="file" name="profile_photo" accept=".png,.jpg,.jpeg,.webp" required><button class="btn btn-accent mt-2">Save Photo</button></form>""" if current_user.role == "student" else ""
    body = f"""<section class="panel"><div class="d-flex align-items-center gap-4 flex-wrap">{photo_markup}<div><span class="pill">STUDENT PROFILE</span><h1 class="mt-2">{student.full_name}</h1><p class="muted mb-0">Admission Number (SDMS Code): <code>{escape(student.sdms_code or 'Not assigned')}</code></p></div></div>{upload_form}</section>
<section class="panel"><h2>Student Information</h2><div class="table-responsive"><table class="table"><tr><th>Name</th><td>{escape(student.full_name)}</td></tr><tr><th>Date of Birth</th><td>{escape(student.date_of_birth.isoformat() if student.date_of_birth else '-')}</td></tr><tr><th>Gender</th><td>{escape(student.gender or '-')}</td></tr><tr><th>Admission Number (SDMS Code)</th><td>{escape(student.sdms_code or '-')}</td></tr><tr><th>RFID Card UID</th><td>{escape(student.rfid_uid or '-')}</td></tr><tr><th>Class</th><td>{escape(student.class_name or '-')}</td></tr><tr><th>Academic Year</th><td>{escape(student.academic_year or '-')}</td></tr><tr><th>Level</th><td>{escape(student.level or '-')}</td></tr><tr><th>Trade</th><td>{escape(student.trade or '-')}</td></tr><tr><th>Guardian</th><td>{escape(student.guardian_name or '-')} ({escape(student.guardian_relationship or '-')})</td></tr><tr><th>Guardian Phone</th><td>{escape(student.guardian_phone or '-')}</td></tr><tr><th>Home Address</th><td>{escape(student.address or '-')}</td></tr></table></div></section>
<section class="panel"><h2>Academic Placement</h2><div class="table-responsive"><table class="table"><tr><th>Department</th><td>{escape(student.department or student.trade or '-')}</td></tr><tr><th>Trade</th><td>{escape(student.trade or '-')}</td></tr><tr><th>Level</th><td>{escape(student.level or '-')}</td></tr><tr><th>Class</th><td>{escape(student.class_name or '-')}</td></tr><tr><th>Class Section</th><td>{escape(student.class_section or '-')}</td></tr></table></div></section>
<section class="panel"><h2>Academic Timetable</h2><p class="muted">The class schedule for {student.class_name or 'this student'}.</p><div class="table-responsive"><table class="table"><tr><th>Day</th><th>Time Slot</th><th>Subject</th><th>Teacher</th></tr>{timetable_rows or '<tr><td colspan="4" class="muted">No timetable has been published for this class yet.</td></tr>'}</table></div></section>
<section class="panel"><h2>Published Day Schedule</h2><p class="muted">Daily program and activities for this child.</p><div class="table-responsive"><table class="table"><tr><th>Date</th><th>Time</th><th>Activity</th><th>Details</th></tr>{day_schedule_rows or '<tr><td colspan="4" class="muted">No published activities are available.</td></tr>'}</table></div></section>
<section class="panel"><h2>Library Loans</h2><div class="table-responsive"><table class="table"><tr><th>Book</th><th>Borrowed</th><th>Due Date</th><th>Returned</th><th>Status</th></tr>{book_loan_rows or '<tr><td colspan="5" class="muted">No book loans are recorded.</td></tr>'}</table></div></section>
<section class="panel"><h2>Published Behavior Reports</h2><div class="table-responsive"><table class="table"><tr><th>Date</th><th>Type</th><th>Details</th><th>Action Taken</th><th>Remarks</th></tr>{discipline_rows or '<tr><td colspan="5" class="muted">No published behavior reports are available.</td></tr>'}</table></div></section>
<section class="panel"><h2>Attendance Summary</h2><div class="grid">{attendance_summary}</div></section>
<section class="panel"><h2>Attendance History & RFID Scan Times</h2><p class="muted">Daily status, school arrival time, and afternoon attendance for this student.</p><div class="table-responsive"><table class="table"><tr><th>Date</th><th>Attendance</th><th>Arrival Time</th><th>Afternoon Attendance</th><th>Source</th></tr>{scan_rows or '<tr><td colspan="5" class="muted">No attendance history is available for this student.</td></tr>'}</table></div></section>
<section class="panel"><h2>School Information</h2><p><strong>{get_setting('school_name', 'Lycée de Muhura')}</strong></p>{f'<p class="muted">{motto}</p>' if motto else ''}<img src="/school-logo" alt="School logo" onerror="this.style.display='none'" style="max-width:110px;max-height:90px;object-fit:contain"></section>"""
    if current_user.role in {"master_admin", "secretary"}:
        actions = (
            f"<section class='panel d-flex gap-2 flex-wrap'><a class='btn btn-outline-primary' href='{url_for('student_edit', student_id=student.id)}'>Edit student</a>"
        )
        if student.is_active_account is not False:
            actions += (
                f"<form method='post' action='{url_for('students')}' onsubmit=\"return confirm('Delete this student from active records? Attendance and academic history will be retained, and the student can be restored.')\">"
                f"<input type='hidden' name='action' value='archive_student'><input type='hidden' name='student_id' value='{student.id}'>"
                "<button class='btn btn-outline-danger' type='submit'>Delete student</button></form>"
            )
        else:
            actions += (
                f"<form method='post' action='{url_for('students')}'><input type='hidden' name='action' value='restore_student'>"
                f"<input type='hidden' name='student_id' value='{student.id}'><button class='btn btn-outline-success' type='submit'>Restore student</button></form>"
            )
        body = actions + "</section>" + body
    return page("Student Profile", body, "student")

def student_photo(student_id):
    student = db.session.get(User, student_id)
    if not student or student.role != "student" or not student.profile_photo:
        abort(404)
    if current_user.role == "student" and current_user.id != student.id:
        abort(403)
    if current_user.role == "parent" and not guardian_can_access_student(current_user.id, student.id):
        abort(403)
    if current_user.role == "teacher" and not teacher_can_access_class(current_user, student.class_name):
        abort(403)
    if current_user.role in {"discipline", "patron", "matron"} and student.class_name != current_user.class_name:
        abort(403)
    photo_path = os.path.join(current_app.config["UPLOAD_FOLDER"], student.profile_photo)
    if not os.path.isfile(photo_path):
        abort(404)
    return send_file(photo_path)


def student_edit(student_id):
    student = db.session.get(User, student_id)
    if not student or student.role != "student":
        abort(404)

    form = {
        "full_name": student.full_name or "",
        "username": student.username or "",
        "email": student.email or "",
        "phone": student.phone or "",
        "date_of_birth": student.date_of_birth.isoformat() if student.date_of_birth else "",
        "gender": student.gender or "",
        "academic_year": student.academic_year or "",
        "department": student.department or student.trade or "",
        "trade": student.trade or "",
        "level": student.level or "",
        "class_name": student.class_name or "",
        "class_section": student.class_section or "",
        "guardian_name": student.guardian_name or "",
        "guardian_relationship": student.guardian_relationship or "",
        "guardian_phone": student.guardian_phone or "",
        "address": student.address or "",
        "rfid_uid": student.rfid_uid or "",
    }
    if request.method == "POST":
        for field in form:
            form[field] = request.form.get(field, "").strip()
        form["rfid_uid"] = "".join(form["rfid_uid"].split()).upper()
        try:
            date_of_birth = datetime.date.fromisoformat(form["date_of_birth"]) if form["date_of_birth"] else None
        except ValueError:
            date_of_birth = None
        academic_year_parts = form["academic_year"].split("-")
        valid_academic_year = False
        if len(academic_year_parts) == 2 and all(
            part.isdigit() and len(part) == 4 for part in academic_year_parts
        ):
            valid_academic_year = int(academic_year_parts[1]) == int(academic_year_parts[0]) + 1
        guardian_phone_digits = re.sub(r"\D", "", form["guardian_phone"])
        valid_guardian_phone = not form["guardian_phone"] or bool(
            re.fullmatch(r"\+?[0-9 ()-]{6,39}", form["guardian_phone"])
            and len(guardian_phone_digits) >= 7
        )
        other_students = User.query.filter(
            User.role == "student",
            User.class_name == form["class_name"],
            User.id != student.id,
        ).all() if form["class_name"] else []
        placement_changed = (
            form["department"] != (student.department or student.trade or "")
            or form["trade"] != (student.trade or "")
            or form["level"] != (student.level or "")
            or form["class_name"] != (student.class_name or "")
            or form["class_section"] != (student.class_section or "")
        )
        class_scope_conflict = placement_changed and any(
            (other.department or other.trade) != form["department"]
            or other.trade != form["trade"]
            or other.level != form["level"]
            for other in other_students
        )
        current_placement_unchanged = (
            form["class_name"] == student.class_name
            and form["trade"] == student.trade
            and form["level"] == student.level
        )
        class_matches_program = (
            CLASS_TRADE_LEVEL.get(form["class_name"]) == (form["trade"], form["level"])
            or current_placement_unchanged
        )

        error = None
        if not form["full_name"] or len(form["full_name"]) > 160 or not form["username"] or len(form["username"]) > 80:
            error = "Enter a student name and username within the allowed lengths."
        elif not form["department"] or len(form["department"]) > 100 or len(form["class_section"]) > 50:
            error = "Enter a department of up to 100 characters and a section of up to 50 characters."
        elif form["trade"] not in PROGRAM_LEVELS or form["level"] not in PROGRAM_LEVELS.get(form["trade"], []):
            error = "Choose a valid trade and level."
        elif not form["class_name"] or len(form["class_name"]) > 100 or not class_matches_program:
            error = "Choose a class that matches the selected trade and level."
        elif class_scope_conflict:
            error = "That class name is already assigned to a different department, trade, or level."
        elif not form["academic_year"] or len(form["academic_year"]) > 9 or not valid_academic_year:
            error = "Enter a valid academic year, such as 2026-2027."
        elif not date_of_birth or date_of_birth > datetime.date.today():
            error = "Enter a valid date of birth that is not in the future."
        elif form["gender"] not in {"Female", "Male", "Other"}:
            error = "Choose a valid gender."
        elif len(form["email"]) > 160 or (form["email"] and "@" not in form["email"]):
            error = "Enter a valid email address."
        elif len(form["phone"]) > 40 or len(form["guardian_name"]) > 160 or len(form["guardian_relationship"]) > 80:
            error = "Contact details exceed the allowed length."
        elif len(form["guardian_phone"]) > 40 or not valid_guardian_phone or len(form["address"]) > 500:
            error = "Enter a valid guardian phone number and an address within 500 characters."
        elif User.query.filter(User.username == form["username"], User.id != student.id).first():
            error = "That username is already in use."
        elif form["email"] and User.query.filter(User.email == form["email"], User.id != student.id).first():
            error = "That email address is already in use."
        elif form["rfid_uid"] and User.query.filter(User.rfid_uid == form["rfid_uid"], User.id != student.id).first():
            error = "That RFID card is assigned to another account."

        if error:
            flash(error, "danger")
        else:
            changed_fields = [field for field, value in form.items() if value != str(getattr(student, field) or "")]
            student.full_name = form["full_name"]
            student.username = form["username"]
            student.email = form["email"] or None
            student.phone = form["phone"] or None
            student.date_of_birth = date_of_birth
            student.gender = form["gender"]
            student.academic_year = form["academic_year"]
            student.department = form["department"]
            student.trade = form["trade"]
            student.level = form["level"]
            student.class_name = form["class_name"]
            student.class_section = form["class_section"] or None
            student.guardian_name = form["guardian_name"] or None
            student.guardian_relationship = form["guardian_relationship"] or None
            student.guardian_phone = form["guardian_phone"] or None
            student.address = form["address"] or None
            student.rfid_uid = form["rfid_uid"] or None
            log_student_activity(
                current_user,
                student,
                "Student record updated",
                f"Edited fields: {', '.join(changed_fields) or 'student details'}.",
            )
            db.session.commit()
            flash(f"Student details saved for {student.full_name}.", "success")
            return redirect(url_for("student_profile_by_id", student_id=student.id))

    class_names = sorted(set(CLASS_TRADE_LEVEL) | ({student.class_name} if student.class_name else set()))
    trade_options = "".join(f"<option value='{escape(value)}'>" for value in TRADES)
    level_options = "".join(
        f"<option value='{escape(level)}'>" for levels in PROGRAM_LEVELS.values() for level in levels
    )
    class_options = "".join(f"<option value='{escape(value)}'>" for value in class_names)
    department_options = "".join(
        f"<option value='{escape(value)}'>"
        for value in sorted(
            {user.department or user.trade for user in User.query.filter_by(role="student").all() if user.department or user.trade},
            key=str.casefold,
        )
    )
    fields = (
        ("full_name", "Full name", "text", True), ("username", "Username", "text", True),
        ("email", "Student email", "email", False), ("phone", "Student phone", "tel", False),
        ("date_of_birth", "Date of birth", "date", True), ("academic_year", "Academic year", "text", True),
        ("department", "Department", "text", True), ("trade", "Trade", "text", True),
        ("level", "Level", "text", True), ("class_name", "Class", "text", True),
        ("class_section", "Class section", "text", False), ("guardian_name", "Guardian name", "text", False),
        ("guardian_relationship", "Guardian relationship", "text", False),
        ("guardian_phone", "Guardian phone", "tel", False), ("address", "Home address", "text", False),
        ("rfid_uid", "RFID card UID", "text", False),
    )
    controls = []
    datalists = {
        "department": "student-edit-departments",
        "trade": "student-edit-trades",
        "level": "student-edit-levels",
        "class_name": "student-edit-classes",
    }
    for field, label, input_type, required in fields:
        list_attr = f" list='{datalists[field]}'" if field in datalists else ""
        required_attr = " required" if required else ""
        controls.append(
            f"<div class='col-md-6'><label class='form-label' for='edit-{field}'>{escape(label)}</label>"
            f"<input class='form-control' id='edit-{field}' name='{field}' type='{input_type}' "
            f"value='{escape(form[field])}' maxlength='500'{list_attr}{required_attr}></div>"
        )
    gender_options = "".join(
        f"<option value='{value}'{' selected' if form['gender'] == value else ''}>{value}</option>"
        for value in ("", "Female", "Male", "Other")
    )
    body = (
        "<section class='panel'><h1>Edit student</h1>"
        f"<p class='muted'>Update the registered details for {escape(student.full_name)}. Changes are recorded in the student activity history.</p>"
        "<form method='post' class='row g-3'>"
        + "".join(controls)
        + f"<div class='col-md-6'><label class='form-label' for='edit-gender'>Gender</label><select class='form-select' id='edit-gender' name='gender' required>{gender_options}</select></div>"
        + "<datalist id='student-edit-departments'>" + department_options + "</datalist>"
        + "<datalist id='student-edit-trades'>" + trade_options + "</datalist>"
        + "<datalist id='student-edit-levels'>" + level_options + "</datalist>"
        + "<datalist id='student-edit-classes'>" + class_options + "</datalist>"
        + "<div class='col-12 d-flex gap-2 flex-wrap'><button class='btn btn-accent' type='submit'>Save changes</button>"
        + f"<a class='btn btn-outline-secondary' href='{url_for('student_profile_by_id', student_id=student.id)}'>Cancel</a></div></form></section>"
    )
    return page("Edit Student", body, "students")


def students():
    if request.method == "POST" and current_user.role not in {"master_admin", "secretary"}:
        abort(403)
    can_register = current_user.role in {"master_admin", "secretary"}
    if request.method == "POST" and request.form.get("action") in {"archive_student", "restore_student"}:
        student = db.session.get(User, request.form.get("student_id", type=int))
        if not student or student.role != "student":
            abort(404)
        should_archive = request.form.get("action") == "archive_student"
        student.is_active_account = not should_archive
        log_student_activity(
            current_user,
            student,
            "Student removed" if should_archive else "Student restored",
            "Student account archived; attendance and academic history retained."
            if should_archive else "Student account re-enabled.",
        )
        db.session.commit()
        flash(
            f"{student.full_name} was {'deleted from active records' if should_archive else 'restored'}. "
            "Existing school records were retained.",
            "success",
        )
        return redirect(url_for("students", status="archived" if should_archive else "active"))
    if request.method == "POST" and request.form.get("action") == "delete_pending_card":
        uid = "".join(request.form.get("uid", "").split()).upper()
        pending_card = PendingCard.query.filter_by(uid=uid, state="Pending").first()
        if not pending_card:
            abort(404)
        db.session.delete(pending_card)
        db.session.commit()
        flash(f"Pending RFID card {uid} was deleted.", "success")
        return redirect(url_for("students"))
    if request.method == "POST" and request.form.get("action") == "update_placement":
        student = db.session.get(User, request.form.get("student_id", type=int))
        if not student or student.role != "student":
            abort(404)
        department = request.form.get("department", "").strip()
        class_section = request.form.get("class_section", "").strip()
        if not department or len(department) > 100 or len(class_section) > 50:
            flash("Enter a department of 1 to 100 characters and a section of up to 50 characters.", "danger")
        else:
            student.department = department
            student.class_section = class_section or None
            log_student_activity(
                current_user, student, "Student placement updated",
                f"Department set to {department}; section set to {class_section or 'Not specified'}.",
            )
            db.session.commit()
            flash(f"Department and section updated for {student.full_name}.", "success")
        return redirect(url_for("students"))

    today = datetime.date.today()
    school_year_start = today.year if today.month >= 9 else today.year - 1
    default_academic_year = f"{school_year_start}-{school_year_start + 1}"
    form_values = {
        "full_name": "", "username": "", "uid": "", "date_of_birth": "",
        "gender": "", "academic_year": default_academic_year, "guardian_name": "",
        "guardian_relationship": "", "guardian_phone": "", "address": "",
        "class_name": "", "department": "", "trade": "", "level": "",
        "class_section": "", "parent_id": "",
    }
    if request.method == "POST":
        for field in form_values:
            form_values[field] = request.form.get(field, "").strip()
        full_name = form_values["full_name"]
        username = form_values["username"]
        password = request.form.get("password", "")
        uid = form_values["uid"].replace(" ", "").upper() or None
        form_values["uid"] = uid or ""
        raw_date_of_birth = form_values["date_of_birth"]
        try:
            date_of_birth = datetime.date.fromisoformat(raw_date_of_birth)
        except ValueError:
            date_of_birth = None
        gender = form_values["gender"]
        academic_year = form_values["academic_year"]
        academic_year_parts = academic_year.split("-")
        valid_academic_year = False
        if len(academic_year_parts) == 2 and all(
            part.isdigit() and len(part) == 4 for part in academic_year_parts
        ):
            valid_academic_year = int(academic_year_parts[1]) == int(academic_year_parts[0]) + 1
        guardian_phone = form_values["guardian_phone"]
        phone_digits = re.sub(r"\D", "", guardian_phone)
        valid_guardian_phone = bool(
            re.fullmatch(r"\+?[0-9 ()-]{6,39}", guardian_phone)
            and len(phone_digits) >= 7
        )
        class_name = request.form.get("class_name", "").strip()
        form_values["class_name"] = class_name
        department = form_values["department"]
        level = form_values["level"]
        trade = form_values["trade"]
        class_section = form_values["class_section"]
        parent_id = form_values["parent_id"] or None
        parent_record = db.session.get(User, int(parent_id)) if parent_id and parent_id.isdigit() else None
        existing_class_students = User.query.filter_by(role="student", class_name=class_name).all() if class_name else []
        class_scope_conflict = any(
            student.trade != trade or student.level != level
            or (student.department or student.trade) != department
            for student in existing_class_students
        )

        if not uid:
            flash("Tap the student's RFID card or select a pending card before registering.", "danger")
        elif not full_name or not username or len(password) < 8:
            flash("Full name, username, and password (8+ chars) are required.", "danger")
        elif len(full_name) > 160 or len(username) > 80:
            flash("Student name or username exceeds the allowed length.", "danger")
        elif (not raw_date_of_birth or not gender or not academic_year or not department
              or not form_values["guardian_name"] or not form_values["guardian_relationship"]
              or not guardian_phone or not form_values["address"]):
            flash("Date of birth, gender, academic year, department, guardian name and relationship, guardian phone, and address are required.", "danger")
        elif not date_of_birth or date_of_birth > today:
            flash("Enter a valid date of birth that is not in the future.", "danger")
        elif gender not in {"Female", "Male", "Other"}:
            flash("Choose a valid gender.", "danger")
        elif not valid_academic_year:
            flash("Enter a valid academic year, such as 2026-2027.", "danger")
        elif (len(department) > 100 or len(class_section) > 50 or len(form_values["guardian_name"]) > 160
              or len(form_values["guardian_relationship"]) > 80 or len(guardian_phone) > 40
              or len(form_values["address"]) > 500):
            flash("Department, section, guardian details, phone, or address exceed the allowed length.", "danger")
        elif not valid_guardian_phone:
            flash("Enter a valid guardian phone number with at least 7 digits.", "danger")
        elif trade not in PROGRAM_LEVELS or level not in PROGRAM_LEVELS.get(trade, []):
            flash("Choose a valid programme, level, and class name.", "danger")
        elif CLASS_TRADE_LEVEL.get(class_name) != (trade, level):
            flash("Choose a class from the selected programme and level.", "danger")
        elif class_scope_conflict:
            flash("That class name is already used by a different trade or level. Choose a unique class name.", "danger")
        elif User.query.filter_by(username=username).first():
            flash("Username already exists.", "danger")
        elif uid and User.query.filter_by(rfid_uid=uid).first():
            flash("RFID UID already assigned to another student.", "danger")
        elif not PendingCard.query.filter_by(uid=uid, state="Pending").first():
            flash("This RFID card is not eligible for registration. Scan an unassigned card that is not lost or already assigned.", "danger")
        elif parent_id and (not parent_record or parent_record.role != "parent"):
            flash("Choose a valid existing parent account or leave that field blank.", "danger")
        elif request.form.get("review_confirmed") != "yes":
            flash("Review the student details and confirm registration before saving.", "danger")
        else:
            admission_number = generate_sdms_code()
            s = User(username=username, full_name=full_name, role="student", rfid_uid=uid,
                     class_name=class_name, class_section=class_section or None,
                     department=department, level=level, trade=trade, sdms_code=admission_number,
                     date_of_birth=date_of_birth, gender=gender, academic_year=academic_year,
                     guardian_name=form_values["guardian_name"],
                     guardian_relationship=form_values["guardian_relationship"],
                     guardian_phone=guardian_phone, address=form_values["address"],
                     parent_id=parent_record.id if parent_id else None)
            s.set_password(password)
            db.session.add(s)
            db.session.flush()
            if parent_record:
                db.session.add(StudentGuardian(
                    student_id=s.id, guardian_id=parent_record.id,
                    relationship=form_values["guardian_relationship"], status="verified",
                    verified_by_id=current_user.id, verified_at=now_string(),
                ))
            log_student_activity(current_user, s, "Student registration confirmed", f"Student account created in {department} / {trade} / {level} / {class_name} / {class_section or 'no section'} with admission number {admission_number}.")
            if uid:
                PendingCard.query.filter_by(uid=uid).delete()
                log_student_activity(current_user, s, "RFID card assigned", f"Card {uid} assigned to the student.")
            db.session.commit()
            return redirect(url_for("student_registration_confirmation", student_id=s.id))

    requested_status = request.args.get("status", "active").strip().lower()
    if requested_status not in {"active", "archived", "all"}:
        requested_status = "active"
    if not can_register:
        requested_status = "active"
    student_query = User.query.filter_by(role="student")
    if requested_status == "active":
        student_query = student_query.filter(
            db.or_(User.is_active_account.is_(True), User.is_active_account.is_(None))
        )
    elif requested_status == "archived":
        student_query = student_query.filter(User.is_active_account.is_(False))
    if current_user.role == "teacher":
        student_query = student_query.filter(db.func.lower(User.class_name).in_({name.casefold() for name in teacher_assigned_classes(current_user)}))
    elif current_user.role in {"discipline", "patron", "matron"}:
        student_query = student_query.filter_by(class_name=current_user.class_name)
    all_students = student_query.order_by(User.full_name).all()
    pending = PendingCard.query.filter_by(state="Pending").order_by(PendingCard.id.desc()).all()
    parents = User.query.filter_by(role="parent").all()

    safe_html = lambda value: str(escape(value)).replace("{", "&#123;").replace("}", "&#125;")
    student_row_items = []
    student_card_items = []
    for student in all_students:
        profile_url = url_for("student_profile_by_id", student_id=student.id)
        student_is_active = student.is_active_account is not False
        student_state = "Active" if student_is_active else "Archived"
        student_actions = (
            f"<div class='d-flex gap-1 flex-wrap'><a class='btn btn-sm btn-outline-secondary' href='{profile_url}'>View</a>"
        )
        if can_register:
            student_actions += (
                f"<a class='btn btn-sm btn-outline-primary' href='{url_for('student_edit', student_id=student.id)}'>Edit</a>"
            )
            if student_is_active:
                student_actions += (
                    f"<form method='post' action='{url_for('students')}' onsubmit=\"return confirm('Delete this student from active records? Their school history will be retained and they can be restored.')\">"
                    f"<input type='hidden' name='action' value='archive_student'><input type='hidden' name='student_id' value='{student.id}'>"
                    "<button class='btn btn-sm btn-outline-danger' type='submit'>Delete</button></form>"
                )
            else:
                student_actions += (
                    f"<form method='post' action='{url_for('students')}'><input type='hidden' name='action' value='restore_student'>"
                    f"<input type='hidden' name='student_id' value='{student.id}'><button class='btn btn-sm btn-outline-success' type='submit'>Restore</button></form>"
                )
        student_actions += "</div>"
        placement_editor = (
            f"<form method='post' class='d-flex flex-wrap gap-1'><input type='hidden' name='action' value='update_placement'>"
            f"<input type='hidden' name='student_id' value='{student.id}'><input class='form-control form-control-sm' "
            f"name='department' value='{safe_html(student.department or student.trade or '')}' maxlength='100' "
            f"aria-label='Department for {safe_html(student.full_name)}' required>"
            f"<input class='form-control form-control-sm' name='class_section' value='{safe_html(student.class_section or '')}' "
            f"maxlength='50' placeholder='Section' aria-label='Class section for {safe_html(student.full_name)}'>"
            "<button class='btn btn-sm btn-outline-success' type='submit'>Save</button></form>"
            if can_register else
            f"{safe_html(student.department or student.trade or '-')} / {safe_html(student.class_section or '-')}"
        )
        student_row_items.append(
            f"<tr><td><a href='{profile_url}'>{safe_html(student.full_name)}</a><br><span class='pill'>{student_state}</span></td>"
            f"<td><code>{safe_html(student.sdms_code or '-')}</code></td><td>{safe_html(student.class_name or '-')}</td>"
            f"<td>{safe_html(student.department or student.trade or '-')}</td><td>{safe_html(student.trade or '-')}</td>"
            f"<td>{safe_html(student.level or '-')}</td><td>{safe_html(student.class_section or '-')}</td>"
            f"<td>{placement_editor}</td>"
            f"<td><code>{safe_html(student.rfid_uid or 'Not Assigned')}</code></td>"
            f"<td>{safe_html(student.parent.full_name if student.parent else 'None')}</td><td>{student_actions}</td></tr>"
        )
        student_card_items.append(
            f"<article class='registered-student-card'><div class='registered-student-card-head'>"
            f"<a href='{profile_url}'>{safe_html(student.full_name)}</a><span class='pill'>{student_state}</span>"
            f"<span><b>Admission No.</b> <code>{safe_html(student.sdms_code or '-')}</code></span></div>"
            f"<dl><div><dt>Department</dt><dd>{safe_html(student.department or student.trade or '-')}</dd></div>"
            f"<div><dt>Trade</dt><dd>{safe_html(student.trade or '-')}</dd></div>"
            f"<div><dt>Level</dt><dd>{safe_html(student.level or '-')}</dd></div>"
            f"<div><dt>Class</dt><dd>{safe_html(student.class_name or '-')}</dd></div>"
            f"<div><dt>Class Section</dt><dd>{safe_html(student.class_section or '-')}</dd></div>"
            f"<div><dt>Update Department / Section</dt><dd>{placement_editor}</dd></div>"
            f"<div><dt>Actions</dt><dd>{student_actions}</dd></div>"
            f"<div><dt>RFID UID</dt><dd><code>{safe_html(student.rfid_uid or 'Not Assigned')}</code></dd></div>"
            f"<div><dt>Parent</dt><dd>{safe_html(student.parent.full_name if student.parent else 'None')}</dd></div></dl></article>"
        )
    student_rows = "".join(student_row_items)
    student_cards = "".join(student_card_items)
    pending_rows = "".join(
        f"<tr data-pending-uid='{escape(p.uid)}' data-first-seen='{escape(p.first_seen or '')}' data-last-seen='{escape(p.last_seen or '')}'><td><code>{escape(p.uid)}</code></td>"
        f"<td>{escape(p.last_seen or p.first_seen or '')}</td><td class='d-flex gap-1 flex-wrap'>"
        f"<button class='btn btn-sm btn-outline-primary' type='button' data-use-uid='{escape(p.uid)}' title='Use this card and edit the new student details'>Edit</button>"
        f"<button class='btn btn-sm btn-outline-secondary' type='button' data-view-uid='{escape(p.uid)}'>View</button>"
        f"<form method='post' action='{url_for('students')}' onsubmit=\"return confirm('Delete this pending RFID card?')\">"
        f"<input type='hidden' name='action' value='delete_pending_card'><input type='hidden' name='uid' value='{escape(p.uid)}'>"
        "<button class='btn btn-sm btn-outline-danger' type='submit'>Delete</button></form></td></tr>"
        for p in pending
    )
    parent_opts = "".join(
        f'<option value="{p.id}"{" selected" if str(p.id) == form_values["parent_id"] else ""}>{escape(p.full_name)}</option>'
        for p in parents
    )
    trade_options = "".join(
        f'<option value="{escape(trade)}"{" selected" if trade == form_values["trade"] else ""}>{escape(trade)}</option>'
        for trade in TRADES
    )
    gender_options = "".join(
        f'<option value="{gender}"{" selected" if gender == form_values["gender"] else ""}>{gender}</option>'
        for gender in ("Female", "Male", "Other")
    )
    class_name_options = "".join(
        f'<option value="{escape(class_name)}" data-trade="{escape(class_trade)}" data-level="{escape(class_level)}"{" selected" if class_name == form_values["class_name"] else ""}>{escape(class_name)}</option>'
        for class_name, (class_trade, class_level) in CLASS_TRADE_LEVEL.items()
    )
    level_options = "".join(
        f'<option value="{escape(level)}" data-trade="{escape(trade)}"{" selected" if level == form_values["level"] else ""}>{escape(level)}</option>'
        for trade, levels in PROGRAM_LEVELS.items() for level in levels
    )
    known_departments = sorted(
        {student.department or student.trade for student in all_students if student.department or student.trade},
        key=str.casefold,
    )
    department_options = "".join(f'<option value="{escape(value)}">' for value in known_departments)
    pending_section = f"""<section class="panel"><div class="student-registration-header"><h1>Student <span class="accent">Registration</span></h1><p class="muted">Tap an unassigned card on the connected RFID reader while this page is open. The UID is read automatically, fills the form, and is required to save. You can also select a card from the pending list.</p><a class="btn btn-sm btn-outline-secondary mt-3" href="#registered-students">View existing students</a></div><h3 class="mt-4 pending-section-title">Pending Scanned RFID Cards</h3><div class='table-responsive'><table class="table pending-cards-table"><thead><tr><th>UID</th><th>Last Scanned</th><th>Action</th></tr></thead><tbody id='pending-rfid-rows'>{pending_rows or '<tr data-empty-row><td colspan="3" class="muted">No unregistered RFID cards are pending.</td></tr>'}</tbody></table></div><dialog id='pending-rfid-details' class='student-review-dialog'><h2>Pending RFID Card</h2><dl class='student-review-details'><dt>UID</dt><dd id='pending-card-view-uid'></dd><dt>First scanned</dt><dd id='pending-card-view-first'></dd><dt>Last scanned</dt><dd id='pending-card-view-last'></dd></dl><div class='student-review-actions'><button type='button' class='btn btn-outline-secondary' data-close-pending-card>Close</button></div></dialog></section>""" if can_register else ""
    registration_form = f"""<section class="panel"><h2>Register New Student</h2><form id="student-registration-form" method="post" class="row g-3 student-registration-form">
<div class="col-md-6"><label class="form-label">Student Full Name</label><input name="full_name" maxlength="160" class="form-control" value="{escape(form_values['full_name'])}" required></div>
<div class="col-md-3"><label class="form-label">Date of Birth</label><input name="date_of_birth" type="date" max="{today.isoformat()}" class="form-control" value="{escape(form_values['date_of_birth'])}" required></div>
<div class="col-md-3"><label class="form-label">Gender</label><select name="gender" class="form-select" required><option value="">Choose gender...</option>{gender_options}</select></div>
<div class="col-md-4"><label class="form-label">Username</label><input name="username" maxlength="80" class="form-control" value="{escape(form_values['username'])}" required></div>
<div class="col-md-4"><label class="form-label">Temporary Password (8+ chars)</label><input name="password" type="password" minlength="8" class="form-control" required></div>
<div class="col-md-6"><label class="form-label" for="uid">RFID Card UID</label><input id="uid" name="uid" class="form-control" value="{escape(form_values['uid'])}" placeholder="Waiting for card scan" autocomplete="off" required readonly><small id="rfid-scan-status" class="form-text muted" aria-live="polite">Waiting for the connected reader. The UID fills automatically when you scan a card.</small></div>
<div class="col-md-4"><label class="form-label">Department</label><input name="department" list="student-departments" maxlength="100" class="form-control" value="{escape(form_values['department'])}" required><datalist id="student-departments">{department_options}</datalist></div>
<div class="col-md-4"><label class="form-label">Trade</label><select name="trade" class="form-select trade-select" required><option value="">Choose trade...</option>{trade_options}</select></div>
<div class="col-md-4"><label class="form-label">Level</label><select name="level" class="form-select level-select" required disabled><option value="">Choose trade first...</option>{level_options}</select></div>
<div class="col-md-4"><label class="form-label">Class</label><select id="class-choice" class="form-select" required disabled><option value="">Choose programme and level first...</option>{class_name_options}</select><input type="hidden" name="class_name" id="class-name" value="{escape(form_values['class_name'])}" data-initial-class="{escape(form_values['class_name'])}"></div>
<div class="col-md-4"><label class="form-label">Class Section</label><input name="class_section" maxlength="50" class="form-control" value="{escape(form_values['class_section'])}" placeholder="A, B, Morning (optional)"></div>
<div class="col-md-4"><label class="form-label">Academic Year</label><input name="academic_year" maxlength="9" pattern="[0-9]{{4}}-[0-9]{{4}}" placeholder="2026-2027" class="form-control" value="{escape(form_values['academic_year'])}" required></div>
<div class="col-md-4"><label class="form-label">Guardian Full Name</label><input name="guardian_name" maxlength="160" class="form-control" value="{escape(form_values['guardian_name'])}" required></div>
<div class="col-md-4"><label class="form-label">Relationship to Student</label><input name="guardian_relationship" maxlength="80" class="form-control" value="{escape(form_values['guardian_relationship'])}" placeholder="Parent, aunt, uncle..." required></div>
<div class="col-md-4"><label class="form-label">Guardian Phone Number</label><input name="guardian_phone" type="tel" maxlength="40" pattern="\\+?[0-9 ()-]{{6,39}}" class="form-control" value="{escape(form_values['guardian_phone'])}" required></div>
<div class="col-12"><label class="form-label">Home Address</label><textarea name="address" maxlength="500" rows="2" class="form-control" required>{escape(form_values['address'])}</textarea></div>
<div class="col-md-6"><label class="form-label">Link Parent (Optional)</label><select name="parent_id" class="form-select"><option value="">Choose parent...</option>{parent_opts}</select></div>
<input type="hidden" name="review_confirmed" value="">
<div class="col-12"><button class="btn btn-accent" type="submit">Review Student Details</button></div></form>
<dialog id="student-review-dialog" class="student-review-dialog" aria-labelledby="student-review-title"><h2 id="student-review-title">Review Student Registration</h2><p class="muted">Check the details below. Confirm to create the student record and assign this RFID card. A unique admission number will be generated automatically.</p><dl id="student-review-details" class="student-review-details"></dl><p class="muted small">The temporary password is set but is not displayed here.</p><div class="student-review-actions"><button type="button" class="btn btn-outline-secondary" id="student-review-back">Back to Edit</button><button type="button" class="btn btn-accent" id="student-review-confirm">Confirm Registration</button></div></dialog></section>""" if can_register else ""

    status_filters = (
        "<nav class='d-flex gap-2 flex-wrap mb-3' aria-label='Student account status'>"
        f"<a class='btn btn-sm {'btn-accent' if requested_status == 'active' else 'btn-outline-secondary'}' href='{url_for('students', status='active')}'>Active</a>"
        f"<a class='btn btn-sm {'btn-accent' if requested_status == 'archived' else 'btn-outline-secondary'}' href='{url_for('students', status='archived')}'>Archived</a>"
        f"<a class='btn btn-sm {'btn-accent' if requested_status == 'all' else 'btn-outline-secondary'}' href='{url_for('students', status='all')}'>All</a></nav>"
        if can_register else ""
    )
    body = f"""<style>.student-registration-header{{margin-bottom:20px}}.student-registration-header h1{{font-size:clamp(1.5rem,2.4vw,2rem)}}.student-registration-header p{{max-width:850px;font-size:1rem;line-height:1.5;margin-bottom:0}}.pending-section-title{{font-size:1.25rem!important}}.pending-cards-table{{width:100%;margin-bottom:0}}.student-registration-form{{row-gap:1.25rem!important}}.student-registration-form .form-label{{display:block;font-weight:650;margin-bottom:.45rem}}.student-registration-form .form-control,.student-registration-form .form-select{{min-height:48px}}.student-registration-form h2{{margin-bottom:.25rem}}.student-review-dialog{{width:min(620px,calc(100vw - 28px));max-height:85vh;overflow:auto;border:1px solid var(--border);border-radius:16px;padding:22px;background:var(--card);color:var(--text)}}.student-review-dialog::backdrop{{background:rgba(5,10,15,.68)}}.student-review-dialog h2{{font-size:1.3rem;margin-bottom:8px}}.student-review-details{{display:grid;grid-template-columns:minmax(120px,.4fr) minmax(0,1fr);gap:8px 16px;margin:18px 0}}.student-review-details dt{{font-weight:700;color:var(--muted)}}.student-review-details dd{{margin:0;overflow-wrap:anywhere;white-space:pre-wrap}}.student-review-actions{{display:flex;justify-content:flex-end;gap:10px;flex-wrap:wrap}}@media(max-width:480px){{.student-review-dialog{{padding:16px}}.student-review-details{{grid-template-columns:1fr;gap:2px 0}}.student-review-details dd{{margin-bottom:8px}}}}</style>{status_filters}{pending_section}
{registration_form}
<section class="panel registered-students-panel" id="registered-students"><h2>Registered Students</h2><p class="muted">View the full profile, edit student details, or delete the account from active records while retaining attendance and academic history.</p><div class="table-responsive registered-students-table"><table class="table"><thead><tr><th>Name / Status</th><th>Admission Number</th><th>Class</th><th>Department</th><th>Trade</th><th>Level</th><th>Class Section</th><th>Update Department / Section</th><th>RFID UID</th><th>Parent</th><th>Actions</th></tr></thead><tbody>{student_rows or '<tr><td colspan="11" class="muted">No students match this status.</td></tr>'}</tbody></table></div><div class="registered-student-cards">{student_cards or '<p class="muted mb-0">No students match this status.</p>'}</div></section>"""
    body = """<style>
.student-registration-picker{position:relative;min-width:0;width:100%}
.student-registration-picker-source{display:none!important}
.student-registration-picker-input{width:100%;min-width:0;text-overflow:ellipsis;white-space:nowrap}
.student-registration-picker-options{position:absolute;inset:calc(100% + 4px) 0 auto;z-index:1300;max-height:min(16rem,45vh);overflow:auto;padding:4px;border:1px solid var(--border);border-radius:10px;background:var(--card);box-shadow:0 12px 28px rgba(0,0,0,.24)}
.student-registration-picker-options[hidden],.student-registration-picker-option[hidden]{display:none!important}
.student-registration-picker-option{width:100%;padding:8px 10px;border-radius:7px;color:var(--text);overflow-wrap:anywhere;white-space:normal;cursor:pointer}
.student-registration-picker-option:hover,.student-registration-picker-option[aria-selected="true"]{background:color-mix(in srgb,var(--green-700) 12%,var(--card))}
</style>""" + body
    body += """<script>(()=>{
const form=document.querySelector('.student-registration-form');
if(!form)return;
const controls=new Set();
let nextId=0;
function enhance(select){
if(select.dataset.mobilePicker)return;
select.dataset.mobilePicker='true';
const wrapper=document.createElement('div');wrapper.className='student-registration-picker';
const input=document.createElement('input');input.type='text';input.className='form-control student-registration-picker-input';input.setAttribute('role','combobox');input.setAttribute('aria-autocomplete','list');input.setAttribute('aria-haspopup','listbox');input.setAttribute('aria-expanded','false');input.autocomplete='off';
const list=document.createElement('div');list.className='student-registration-picker-options';list.setAttribute('role','listbox');list.hidden=true;
const baseId=select.id||'student-registration-select-'+(++nextId);select.id=baseId;input.id=baseId+'-picker';list.id=baseId+'-options';input.setAttribute('aria-controls',list.id);
const label=select.previousElementSibling&&select.previousElementSibling.tagName==='LABEL'?select.previousElementSibling:null;
if(label)label.htmlFor=input.id;else input.setAttribute('aria-label','Choose an option');
input.required=select.required;select.required=false;
select.classList.add('student-registration-picker-source');select.tabIndex=-1;
select.parentNode.insertBefore(wrapper,select);wrapper.append(input,list,select);
let activeIndex=-1;
function visibleItems(){return [...list.querySelectorAll('.student-registration-picker-option')].filter(item=>!item.hidden)}
function close(){list.hidden=true;activeIndex=-1;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');list.querySelectorAll('.student-registration-picker-option').forEach(item=>item.setAttribute('aria-selected','false'))}
function renderOptions(query=''){
const normalized=query.trim().toLocaleLowerCase();
const optionNodes=[...select.options].map((option,index)=>{
const item=document.createElement('div');item.className='student-registration-picker-option';item.id=baseId+'-option-'+index;item.setAttribute('role','option');item.setAttribute('aria-selected','false');item.dataset.value=option.value;item.dataset.label=option.textContent.trim();item.textContent=option.textContent.trim();item.hidden=!!normalized&&!item.dataset.label.toLocaleLowerCase().includes(normalized);return item;
});
list.replaceChildren(...optionNodes);list.hidden=false;input.setAttribute('aria-expanded','true');activeIndex=-1;
}
function sync(){
input.disabled=select.disabled;
const selectedOption=select.selectedOptions[0];
input.value=select.value&&selectedOption?selectedOption.textContent.trim():'';
input.placeholder=select.options[0]?select.options[0].textContent.trim():'Choose an option';
input.setCustomValidity(input.required&&!select.value?'Choose an option from the suggestions.':'');
renderOptions('');close();
}
function choose(item){select.value=item.dataset.value;select.dispatchEvent(new Event('change',{bubbles:true}));input.value=item.dataset.label;close()}
input.addEventListener('focus',()=>{if(select.value)input.select();renderOptions(select.value?'':input.value);controls.forEach(control=>{if(control.wrapper!==wrapper)control.close()})});
input.addEventListener('click',()=>{if(list.hidden)renderOptions(select.value?'':input.value)});
input.addEventListener('input',()=>{
const query=input.value;
if(select.value){select.value='';select.dispatchEvent(new Event('change',{bubbles:true}))}
if(input.required)input.setCustomValidity('Choose an option from the suggestions.');
input.value=query;renderOptions(query);
});
input.addEventListener('keydown',event=>{
if(event.key==='ArrowDown'||event.key==='ArrowUp'){
event.preventDefault();if(list.hidden)renderOptions(input.value);const visible=visibleItems();if(!visible.length)return;
activeIndex=activeIndex<0?(event.key==='ArrowDown'?0:visible.length-1):(activeIndex+(event.key==='ArrowDown'?1:-1)+visible.length)%visible.length;
list.querySelectorAll('.student-registration-picker-option').forEach(item=>item.setAttribute('aria-selected','false'));
const active=visible[activeIndex];active.setAttribute('aria-selected','true');input.setAttribute('aria-activedescendant',active.id);active.scrollIntoView({block:'nearest'});return;
}
if(event.key==='Enter'&&!list.hidden){event.preventDefault();if(activeIndex>=0)choose(visibleItems()[activeIndex]);return}
if(event.key==='Escape')close();
});
list.addEventListener('click',event=>{const item=event.target.closest('.student-registration-picker-option');if(item&&list.contains(item))choose(item)});
select.addEventListener('change',sync);
const observer=new MutationObserver(sync);observer.observe(select,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled']});
const control={wrapper,close};controls.add(control);sync();
}
form.querySelectorAll('select').forEach(enhance);
document.addEventListener('pointerdown',event=>controls.forEach(control=>{if(!control.wrapper.contains(event.target))control.close()}));
})();</script>"""
    body += """<script>(()=>{
const rows=document.getElementById('pending-rfid-rows');
const uidInput=document.getElementById('uid');
const status=document.getElementById('rfid-scan-status');
if(!rows||!uidInput||!status)return;
const known=new Map([...rows.querySelectorAll('tr[data-pending-uid]')].map(row=>[row.dataset.pendingUid,row.dataset.lastSeen||'']));
let polling=false;
function makeRow(card){
const row=document.createElement('tr');row.dataset.pendingUid=card.uid;row.dataset.firstSeen=card.first_seen||'';row.dataset.lastSeen=card.last_seen||'';
const uidCell=document.createElement('td');const code=document.createElement('code');code.textContent=card.uid;uidCell.append(code);
const timeCell=document.createElement('td');timeCell.textContent=card.last_seen||card.first_seen||'';
const actionCell=document.createElement('td');actionCell.className='d-flex gap-1 flex-wrap';
const editButton=document.createElement('button');editButton.type='button';editButton.className='btn btn-sm btn-outline-primary';editButton.dataset.useUid=card.uid;editButton.title='Use this card and edit the new student details';editButton.textContent='Edit';
const viewButton=document.createElement('button');viewButton.type='button';viewButton.className='btn btn-sm btn-outline-secondary';viewButton.dataset.viewUid=card.uid;viewButton.textContent='View';
const deleteForm=document.createElement('form');deleteForm.method='post';deleteForm.action='/students';deleteForm.addEventListener('submit',event=>{if(!window.confirm('Delete this pending RFID card?'))event.preventDefault()});
const action=document.createElement('input');action.type='hidden';action.name='action';action.value='delete_pending_card';
const uid=document.createElement('input');uid.type='hidden';uid.name='uid';uid.value=card.uid;
const deleteButton=document.createElement('button');deleteButton.type='submit';deleteButton.className='btn btn-sm btn-outline-danger';deleteButton.textContent='Delete';
deleteForm.append(action,uid,deleteButton);actionCell.append(editButton,viewButton,deleteForm);
row.append(uidCell,timeCell,actionCell);return row;
}
rows.addEventListener('click',event=>{
const editButton=event.target.closest('button[data-use-uid]');
if(editButton){
uidInput.value=editButton.dataset.useUid;status.textContent='Card '+editButton.dataset.useUid+' selected. Enter or edit the student details below.';
const registrationForm=document.getElementById('student-registration-form');registrationForm.scrollIntoView({behavior:'smooth',block:'start'});
registrationForm.elements.namedItem('full_name').focus({preventScroll:true});return;
}
const viewButton=event.target.closest('button[data-view-uid]');
if(viewButton){
const row=viewButton.closest('tr'),dialog=document.getElementById('pending-rfid-details');
document.getElementById('pending-card-view-uid').textContent=row.dataset.pendingUid||'';
document.getElementById('pending-card-view-first').textContent=row.dataset.firstSeen||'Not recorded';
document.getElementById('pending-card-view-last').textContent=row.dataset.lastSeen||'Not recorded';
if(typeof dialog.showModal==='function')dialog.showModal();else window.alert('Pending RFID card: '+(row.dataset.pendingUid||''));
}
});
document.getElementById('pending-rfid-details').addEventListener('click',event=>{if(event.target.closest('[data-close-pending-card]'))event.currentTarget.close()});
async function refreshPendingCards(){
if(polling)return;polling=true;
try{
const response=await fetch('/students/pending-rfid-cards',{cache:'no-store',headers:{'Accept':'application/json'}});
if(!response.ok)return;
const data=await response.json();const cards=Array.isArray(data.cards)?data.cards:[];
const current=new Set(cards.map(card=>card.uid));
for(const uid of known.keys())if(!current.has(uid))known.delete(uid);
const fresh=cards.filter(card=>!known.has(card.uid)||(known.get(card.uid)||'')!==(card.last_seen||''));
for(const card of cards)known.set(card.uid,card.last_seen||'');
rows.replaceChildren(...(cards.length?cards.map(makeRow):[(()=>{const row=document.createElement('tr');const cell=document.createElement('td');cell.colSpan=3;cell.className='muted';cell.textContent='No unregistered RFID cards are pending.';row.append(cell);return row})()]));
if(fresh.length){
const latest=fresh.reduce((current,card)=>String(card.last_seen||card.first_seen||'').localeCompare(String(current.last_seen||current.first_seen||''))>0?card:current);
uidInput.value=latest.uid;status.textContent='Card '+latest.uid+' read by the RFID reader and ready to assign to this student.';
}
}catch(error){/* Keep the form usable if the live refresh is temporarily unavailable. */}
finally{polling=false;}
}
refreshPendingCards();window.setInterval(refreshPendingCards,2000);
})();</script>"""
    body += """<script>(()=>{
document.addEventListener('DOMContentLoaded',()=>setTimeout(()=>{
const field=document.getElementById('class-name'),choice=document.getElementById('class-choice');
if(!field||!choice)return;
const initial=field.dataset.initialClass||'';if(!initial)return;
if([...choice.options].some(option=>option.value===initial)){choice.value=initial;choice.dispatchEvent(new Event('change',{bubbles:true}));}
else{choice.value='';field.value='';}
},0));
})();</script>"""
    body += """<script>(()=>{
const form=document.querySelector('.student-registration-form');
const dialog=document.getElementById('student-review-dialog');
const details=document.getElementById('student-review-details');
if(!form||!dialog||!details)return;
const confirmed=form.elements.namedItem('review_confirmed');
const value=name=>{
const field=form.elements.namedItem(name);
if(!field)return '—';
if(field.tagName==='SELECT')return field.value?(field.selectedOptions[0]?.textContent.trim()||field.value):'—';
return String(field.value||'').trim()||'—';
};
const rows=[
['Student full name','full_name'],['Date of birth','date_of_birth'],['Gender','gender'],
['Username','username'],['RFID card UID','uid'],['Department','department'],['Trade','trade'],
['Level','level'],['Class','class_name'],['Class section','class_section'],
['Academic year','academic_year'],['Guardian full name','guardian_name'],
['Relationship to student','guardian_relationship'],['Guardian phone','guardian_phone'],
['Home address','address'],['Linked parent','parent_id']
];
function renderReview(){
details.replaceChildren();
for(const [label,name] of rows){
const term=document.createElement('dt');term.textContent=label;
const description=document.createElement('dd');
description.textContent=name==='parent_id'?(()=>{const field=form.elements.namedItem(name);return field&&field.value?(field.selectedOptions[0]?.textContent.trim()||'—'):'None';})():value(name);
details.append(term,description);
}
}
form.addEventListener('submit',event=>{
if(confirmed.value==='yes')return;
event.preventDefault();renderReview();
if(typeof dialog.showModal==='function')dialog.showModal();
else if(window.confirm('Confirm registration for '+value('full_name')+' and assign RFID card '+value('uid')+'?')){
confirmed.value='yes';HTMLFormElement.prototype.submit.call(form);
}
});
document.getElementById('student-review-back').addEventListener('click',()=>dialog.close());
document.getElementById('student-review-confirm').addEventListener('click',()=>{
confirmed.value='yes';dialog.close();HTMLFormElement.prototype.submit.call(form);
});
})();</script>"""
    body += """<style>
.registered-student-cards{display:none}
.registered-student-card{min-width:0;padding:12px;border:1px solid var(--border);border-radius:11px;background:var(--card);overflow-wrap:anywhere}
.registered-student-card-head{display:grid;gap:5px;padding-bottom:9px;border-bottom:1px solid var(--border)}
.registered-student-card-head>a{font-weight:750;overflow-wrap:anywhere}
.registered-student-card-head>span{font-size:.78rem;color:var(--muted)}
.registered-student-card dl{display:grid;gap:7px;margin:10px 0 0}
.registered-student-card dl>div{display:grid;grid-template-columns:minmax(58px,.55fr) minmax(0,1fr);gap:8px;align-items:start}
.registered-student-card dt{font-size:.75rem;font-weight:700;color:var(--muted)}
.registered-student-card dd{min-width:0;margin:0;font-size:.8rem;overflow-wrap:anywhere}
.registered-student-card code{white-space:normal;overflow-wrap:anywhere}
@media(max-width:600px){.registered-students-table{display:none}.registered-student-cards{display:grid;gap:9px}.registered-students-panel{min-width:0}.registered-student-card{padding:10px}}
</style>"""
    return page("Students", body, "teacher")


def _registration_document_html(student, artifact, auto_print=False):
    school_name = escape(get_setting("school_name", "School"))
    admission_number = escape(student.sdms_code or "Not assigned")
    full_name = escape(student.full_name)
    if artifact == "registration-details":
        rows = [
            ("Admission number", admission_number),
            ("Student full name", full_name),
            ("Username", student.username),
            ("Date of birth", student.date_of_birth.isoformat() if student.date_of_birth else "-"),
            ("Gender", student.gender or "-"),
            ("Academic year", student.academic_year or "-"),
            ("Department", student.department or student.trade or "-"),
            ("Trade", student.trade or "-"),
            ("Level", student.level or "-"),
            ("Class", student.class_name or "-"),
            ("Class section", student.class_section or "-"),
            ("Guardian", student.guardian_name or "-"),
            ("Relationship to student", student.guardian_relationship or "-"),
            ("Guardian phone", student.guardian_phone or "-"),
            ("Home address", student.address or "-"),
            ("RFID card UID", student.rfid_uid or "-"),
            ("Linked parent account", student.parent.full_name if student.parent else "None"),
        ]
        detail_rows = "".join(
            f"<tr><th>{escape(label)}</th><td>{escape(value)}</td></tr>"
            for label, value in rows
        )
        title = "Student Registration Details"
        content = f"<header><p class='eyebrow'>{school_name}</p><h1>Student Registration Details</h1><p>Admission number: <strong>{admission_number}</strong></p></header><table>{detail_rows}</table>"
        print_rules = "@page{margin:16mm}"
        file_suffix = "registration_details"
    elif artifact == "card-label":
        uid = escape(student.rfid_uid or "No RFID card assigned")
        title = "RFID Card Label"
        content = f"<article class='card-label'><div class='school'>{school_name}</div><div class='student-name'>{full_name}</div><div class='label-caption'>ADMISSION NUMBER</div><div class='admission-number'>{admission_number}</div><div class='label-caption'>RFID CARD UID</div><div class='rfid-uid'>{uid}</div></article>"
        print_rules = "@page{size:85.6mm 54mm;margin:0}"
        file_suffix = "rfid_card_label"
    else:
        abort(404)

    print_script = "<script>window.addEventListener('load',()=>setTimeout(()=>window.print(),250));</script>" if auto_print else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>
*{{box-sizing:border-box}}body{{margin:0;padding:24px;background:#f3f5f7;color:#17212b;font:15px/1.5 Arial,sans-serif}}.toolbar{{max-width:820px;margin:0 auto 16px;display:flex;gap:8px;align-items:center}}.toolbar button{{padding:9px 14px;border:0;border-radius:7px;background:#137c5b;color:white;font-weight:700;cursor:pointer}}.document{{max-width:820px;margin:auto;padding:28px;background:white;border:1px solid #d9e0e5;border-radius:12px}}header{{margin-bottom:20px;border-bottom:2px solid #137c5b;padding-bottom:16px}}h1{{margin:3px 0 8px;font-size:24px}}.eyebrow{{margin:0;color:#137c5b;font-weight:700;text-transform:uppercase;letter-spacing:.07em}}table{{width:100%;border-collapse:collapse}}th,td{{padding:9px 10px;border-bottom:1px solid #dfe5e9;text-align:left;vertical-align:top;overflow-wrap:anywhere}}th{{width:36%;color:#50606c}}.card-label{{width:85.6mm;height:54mm;margin:30px auto;padding:5mm 6mm;border:1px solid #334155;border-radius:3mm;background:linear-gradient(135deg,#fff 0%,#f0f7f4 100%);display:flex;flex-direction:column;justify-content:center;overflow:hidden}}.school{{font-size:9pt;font-weight:700;color:#137c5b;text-transform:uppercase;letter-spacing:.04em}}.student-name{{margin:4mm 0 2mm;font-size:15pt;line-height:1.15;font-weight:800;overflow-wrap:anywhere}}.label-caption{{margin-top:1mm;color:#64748b;font-size:6.5pt;font-weight:700;letter-spacing:.1em}}.admission-number,.rfid-uid{{font-family:monospace;font-size:9pt;font-weight:700;overflow-wrap:anywhere}}@media print{{body{{padding:0;background:white}}.toolbar{{display:none}}.document{{max-width:none;margin:0;padding:0;border:0;border-radius:0}}.card-label{{margin:0;border-radius:0}}{print_rules}}}@media(max-width:600px){{body{{padding:12px}}.document{{padding:18px}}.toolbar{{flex-wrap:wrap}}}}
</style></head><body><nav class="toolbar"><button type="button" onclick="window.print()">Print / Save as PDF</button></nav><main class="document">{content}</main>{print_script}</body></html>""", file_suffix


def student_registration_confirmation(student_id):
    student = db.session.get(User, student_id)
    if not student or student.role != "student":
        abort(404)
    safe_text = lambda value: str(escape(value)).replace("{", "&#123;").replace("}", "&#125;")
    details = [
        ("Admission number", student.sdms_code or "-"),
        ("Student", student.full_name),
        ("Department", student.department or student.trade or "-"),
        ("Trade", student.trade or "-"),
        ("Level", student.level or "-"),
        ("Class", student.class_name or "-"),
        ("Class section", student.class_section or "-"),
        ("RFID card UID", student.rfid_uid or "-"),
        ("Guardian", student.guardian_name or "-"),
    ]
    rows = "".join(
        f"<tr><th>{safe_text(label)}</th><td>{safe_text(value)}</td></tr>"
        for label, value in details
    )
    details_print = url_for("student_registration_artifact", student_id=student.id, artifact="registration-details", mode="print")
    details_download = url_for("student_registration_artifact", student_id=student.id, artifact="registration-details", mode="download")
    label_print = url_for("student_registration_artifact", student_id=student.id, artifact="card-label", mode="print")
    label_download = url_for("student_registration_artifact", student_id=student.id, artifact="card-label", mode="download")
    body = f"""<style>.registration-complete{{border-left:5px solid #16815d;background:color-mix(in srgb,#16815d 10%,var(--card));padding:16px 18px;border-radius:10px;margin-bottom:18px}}.registration-complete h1{{margin:0 0 5px}}.registration-actions{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:10px;margin:18px 0}}.registration-actions a{{display:flex;align-items:center;justify-content:center;text-align:center;min-height:48px}}.registration-details-table{{max-width:700px}}.registration-details-table th{{width:35%}}.registration-next-links{{display:flex;gap:10px;flex-wrap:wrap}}@media(max-width:500px){{.registration-details-table th{{width:42%}}}}</style><section class="panel"><div class="registration-complete" role="status"><h1>Registration complete</h1><p class="mb-0">{safe_text(student.full_name)} was registered successfully. Admission number: <strong>{safe_text(student.sdms_code or '-')}</strong>.</p></div><h2>Saved Student Details</h2><div class="table-responsive"><table class="table registration-details-table">{rows}</table></div><h2 class="mt-4">Print or Download</h2><p class="muted">Print opens a print dialog where you can choose a printer or save as PDF. Download saves a print-ready HTML file.</p><div class="registration-actions"><a class="btn btn-accent" href="{details_print}" target="_blank" rel="noopener">Print Registration Details</a><a class="btn btn-outline-success" href="{details_download}">Download Registration Details</a><a class="btn btn-accent" href="{label_print}" target="_blank" rel="noopener">Print RFID Card Label</a><a class="btn btn-outline-success" href="{label_download}">Download RFID Card Label</a></div><div class="registration-next-links"><a class="btn btn-outline-secondary" href="/student/profile/{student.id}">Open Student Profile</a><a class="btn btn-outline-secondary" href="/students">Register Another Student</a></div></section>"""
    response = make_response(page("Registration Complete", body, "teacher"))
    response.headers["Cache-Control"] = "no-store"
    return response


def student_registration_artifact(student_id, artifact):
    student = db.session.get(User, student_id)
    if not student or student.role != "student":
        abort(404)
    mode = request.args.get("mode", "view")
    if mode not in {"view", "print", "download"}:
        abort(400)
    document_html, file_suffix = _registration_document_html(student, artifact, auto_print=mode == "print")
    if mode == "download":
        admission_number = secure_filename(student.sdms_code or f"student_{student.id}")
        response = send_file(
            BytesIO(document_html.encode("utf-8")),
            as_attachment=True,
            download_name=f"{admission_number}_{file_suffix}.html",
            mimetype="text/html; charset=utf-8",
        )
    else:
        response = make_response(document_html)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response

def pending_rfid_cards():
    cards = PendingCard.query.filter_by(state="Pending").order_by(PendingCard.id.desc()).all()
    response = jsonify(cards=[
        {"uid": card.uid, "first_seen": str(card.first_seen or ""), "last_seen": str(card.last_seen or "")}
        for card in cards
    ])
    response.headers["Cache-Control"] = "no-store"
    return response


def register_routes(app):
    app.add_url_rule('/students', endpoint='students', view_func=permission_for('students')(students), methods=['GET', 'POST'])
    app.add_url_rule('/students/<int:student_id>/edit', endpoint='student_edit', view_func=role_required('master_admin', 'secretary')(student_edit), methods=['GET', 'POST'])
    app.add_url_rule('/students/pending-rfid-cards', endpoint='pending_rfid_cards', view_func=permission_for('students')(pending_rfid_cards), methods=['GET'])
    app.add_url_rule('/students/<int:student_id>/registration-complete', endpoint='student_registration_confirmation', view_func=role_required('master_admin', 'secretary')(student_registration_confirmation), methods=['GET'])
    app.add_url_rule('/students/<int:student_id>/registration-artifact/<artifact>', endpoint='student_registration_artifact', view_func=role_required('master_admin', 'secretary')(student_registration_artifact), methods=['GET'])
    app.add_url_rule('/student/profile', endpoint='student_profile', view_func=permission_for('student_profile')(student_profile), methods=['GET', 'POST'])
    app.add_url_rule('/student/profile/<int:student_id>', endpoint='student_profile_by_id', view_func=permission_for('student_profile')(student_profile), methods=['GET'])
    app.add_url_rule('/student/photo/<int:student_id>', endpoint='student_photo', view_func=permission_for('student_photo')(student_photo), methods=['GET'])
