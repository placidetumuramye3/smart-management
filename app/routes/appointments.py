import datetime

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user
from markupsafe import escape

from app.extensions import db
from app.models import Appointment, User
from app.utils.decorators import permission_for
from app.utils.helpers import (
    guardian_can_access_student, guardian_ids_for_student, linked_children,
    teacher_assigned_classes,
)
from app.utils.notifications import notify_users
from app.utils.rendering import page


MEETING_STATUSES = {
    "Requested", "Scheduled", "Rescheduled", "Completed", "Cancelled", "Declined",
}
OFFICE_ROLES = {"master_admin", "head_teacher", "secretary"}
CLASS_SUPPORT_ROLES = {"discipline", "patron", "matron"}
MEETING_STAFF_ROLES = OFFICE_ROLES | {"teacher"} | CLASS_SUPPORT_ROLES
FINAL_STATUSES = {"Completed", "Cancelled", "Declined"}


def _staff_classes(user):
    if user.role == "teacher":
        return {name.casefold() for name in teacher_assigned_classes(user) if name}
    if user.role in CLASS_SUPPORT_ROLES:
        return {user.class_name.casefold()} if user.class_name else set()
    return set()


def _students_for_staff(user):
    query = User.query.filter_by(role="student", is_active_account=True)
    if user.role in OFFICE_ROLES:
        return query.order_by(User.full_name).all()
    classes = _staff_classes(user)
    if not classes:
        return []
    return query.filter(db.func.lower(User.class_name).in_(classes)).order_by(User.full_name).all()


def _staff_can_access(user, appointment):
    if user.role in OFFICE_ROLES:
        return True
    if appointment.staff_id == user.id:
        return True
    if not appointment.student:
        return appointment.created_by == user.id
    return bool(appointment.student.class_name and
                appointment.student.class_name.casefold() in _staff_classes(user))


def _visible_appointments(user):
    query = Appointment.query
    if user.role in OFFICE_ROLES:
        pass
    elif user.role == "parent":
        child_ids = [student.id for student in linked_children(user.id)]
        query = query.filter(db.or_(
            Appointment.student_id.in_(child_ids) if child_ids else False,
            db.and_(Appointment.student_id.is_(None), Appointment.parent_id == user.id),
        ))
    else:
        classes = _staff_classes(user)
        student_ids = [student.id for student in User.query.filter(
            User.role == "student",
            db.func.lower(User.class_name).in_(classes) if classes else False,
        ).all()] if classes else []
        query = query.filter(db.or_(
            Appointment.staff_id == user.id,
            db.and_(Appointment.created_by == user.id, Appointment.student_id.is_(None)),
            Appointment.student_id.in_(student_ids) if student_ids else False,
        ))
    return query.order_by(Appointment.scheduled_date.desc(), Appointment.scheduled_time.desc(),
                          Appointment.id.desc()).all()


def _eligible_staff_for_student(student):
    users = User.query.filter(
        User.role.in_(MEETING_STAFF_ROLES), User.is_active_account.is_(True)
    ).order_by(User.full_name).all()
    if not student or not student.class_name:
        return users
    class_key = student.class_name.casefold()
    return [
        user for user in users
        if user.role in OFFICE_ROLES or class_key in _staff_classes(user)
    ]


def _meeting_staff_ids(student):
    return {user.id for user in _eligible_staff_for_student(student)}


def _meeting_parent_id(appointment):
    return appointment.parent_id or (appointment.student.parent_id if appointment.student else None)


def _parent_can_access_appointment(parent, appointment):
    if appointment.student_id:
        return guardian_can_access_student(parent.id, appointment.student_id)
    return appointment.parent_id == parent.id


def _notify_meeting_change(appointment, message="A school meeting record was updated."):
    recipient_ids = set()
    parent_id = _meeting_parent_id(appointment)
    if parent_id:
        recipient_ids.add(parent_id)
    if appointment.student_id:
        recipient_ids.update(guardian_ids_for_student(appointment.student_id))
    if appointment.staff_id:
        recipient_ids.add(appointment.staff_id)
    notify_users(recipient_ids, "Meetings", "Meeting update", message, "/appointments")
    db.session.commit()


def _parse_attendance(value):
    if value == "yes":
        return True
    if value == "no":
        return False
    return None


def _attendance_choice(value):
    options = [("", "Not recorded"), ("yes", "Present"), ("no", "Absent")]
    return "".join(
        f"<option value='{choice}'{' selected' if (value is None and not choice) or value is (choice == 'yes') else ''}>{label}</option>"
        for choice, label in options
    )


def _staff_choices(staff, selected_id):
    options = ["<option value=''>Not assigned</option>"]
    for user in staff:
        selected = " selected" if user.id == selected_id else ""
        options.append(
            f"<option value='{user.id}'{selected}>{escape(user.full_name)} · "
            f"{escape(user.role.replace('_', ' ').title())}</option>"
        )
    return "".join(options)


def _valid_meeting_datetime(date_value, time_value):
    try:
        datetime.date.fromisoformat(date_value)
        datetime.datetime.strptime(time_value, "%H:%M")
    except (TypeError, ValueError):
        return False
    return True


def appointments():
    is_parent = current_user.role == "parent"
    is_staff = current_user.role in MEETING_STAFF_ROLES

    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            date_value = request.form.get("scheduled_date", "").strip()
            time_value = request.form.get("scheduled_time", "").strip()
            purpose = request.form.get("purpose", "").strip()
            student_id = request.form.get("student_id", type=int)
            student = db.session.get(User, student_id) if student_id else None

            if not _valid_meeting_datetime(date_value, time_value):
                flash("Enter a valid meeting date and time.", "danger")
            elif not purpose or len(purpose) > 200:
                flash("Enter a meeting purpose of 200 characters or fewer.", "danger")
            elif is_parent and (not student or not guardian_can_access_student(current_user.id, student.id) or student.role != "student"):
                flash("Choose one of your linked children for the meeting request.", "danger")
            elif is_staff and student_id and (not student or student.role != "student" or
                    (current_user.role not in OFFICE_ROLES and student not in _students_for_staff(current_user))):
                flash("Choose a student in your authorized class or student-care scope.", "danger")
            elif not is_parent and not is_staff:
                abort(403)
            else:
                visitor_name = current_user.full_name if is_parent else request.form.get("visitor_name", "").strip()
                visitor_phone = (current_user.phone if is_parent else request.form.get("visitor_phone", "").strip()) or None
                visitor_email = (current_user.email if is_parent else request.form.get("visitor_email", "").strip()) or None
                if (not visitor_name or len(visitor_name) > 160
                        or len(visitor_phone or "") > 40 or len(visitor_email or "") > 160):
                    flash("Enter a name, phone, and email within their allowed lengths.", "danger")
                else:
                    parent_id = current_user.id if is_parent else (
                        student.parent_id if student else None
                    )
                    staff_id = None
                    if is_staff:
                        staff_id = request.form.get("staff_id", type=int)
                        if current_user.role not in OFFICE_ROLES:
                            staff_id = current_user.id
                        elif staff_id:
                            candidate = db.session.get(User, staff_id)
                            if (not candidate or candidate not in _eligible_staff_for_student(student)):
                                flash("Choose an authorized staff member for this student.", "danger")
                                return redirect(url_for("appointments"))

                    appointment = Appointment(
                        visitor_name=visitor_name,
                        visitor_phone=visitor_phone,
                        visitor_email=visitor_email,
                        student_id=student.id if student else None,
                        parent_id=parent_id,
                        staff_id=staff_id,
                        purpose=purpose,
                        scheduled_date=date_value,
                        scheduled_time=time_value,
                        notes=request.form.get("notes", "").strip() or None,
                        status="Requested" if is_parent else "Scheduled",
                        created_by=current_user.id,
                    )
                    db.session.add(appointment)
                    db.session.commit()
                    if is_parent:
                        notify_users(
                            _meeting_staff_ids(student), "Meetings", "New parent meeting request",
                            "A parent meeting request is ready for review. Sign in to view it.",
                            "/appointments",
                        )
                        db.session.commit()
                        flash("Meeting request sent to the school.", "success")
                    else:
                        _notify_meeting_change(appointment, "A school meeting was scheduled. Sign in to view the details.")
                        flash("Meeting scheduled.", "success")

        elif action == "cancel_request":
            appointment = db.session.get(Appointment, request.form.get("appointment_id", type=int))
            if not appointment:
                abort(404)
            if not is_parent or not _parent_can_access_appointment(current_user, appointment):
                abort(403)
            if appointment.status in FINAL_STATUSES:
                flash("This meeting can no longer be cancelled from the parent portal.", "danger")
            else:
                appointment.status = "Cancelled"
                db.session.commit()
                notify_users(
                    _meeting_staff_ids(appointment.student) | ({appointment.staff_id} if appointment.staff_id else set()),
                    "Meetings", "Parent cancelled a meeting",
                    "A parent cancelled a meeting. Sign in to review the updated record.",
                    "/appointments",
                )
                db.session.commit()
                flash("Meeting cancelled.", "success")

        elif action == "reschedule_request":
            appointment = db.session.get(Appointment, request.form.get("appointment_id", type=int))
            if not appointment:
                abort(404)
            if not is_parent or not _parent_can_access_appointment(current_user, appointment):
                abort(403)
            date_value = request.form.get("scheduled_date", "").strip()
            time_value = request.form.get("scheduled_time", "").strip()
            if appointment.status in FINAL_STATUSES:
                flash("This meeting can no longer be rescheduled from the parent portal.", "danger")
            elif not _valid_meeting_datetime(date_value, time_value):
                flash("Enter a valid preferred meeting date and time.", "danger")
            else:
                appointment.scheduled_date = date_value
                appointment.scheduled_time = time_value
                appointment.status = "Requested"
                db.session.commit()
                notify_users(
                    _meeting_staff_ids(appointment.student) | ({appointment.staff_id} if appointment.staff_id else set()),
                    "Meetings", "Parent requested a new meeting time",
                    "A parent requested a different meeting time. Sign in to review it.",
                    "/appointments",
                )
                db.session.commit()
                flash("Your preferred time was sent to the school.", "success")

        elif action == "update":
            appointment = db.session.get(Appointment, request.form.get("appointment_id", type=int))
            if not appointment:
                abort(404)
            if not is_staff or not _staff_can_access(current_user, appointment):
                abort(403)
            status = request.form.get("status", "")
            date_value = request.form.get("scheduled_date", "").strip()
            time_value = request.form.get("scheduled_time", "").strip()
            staff_id = request.form.get("staff_id", type=int)
            new_staff = db.session.get(User, staff_id) if staff_id else None
            if status not in MEETING_STATUSES:
                abort(400)
            elif not _valid_meeting_datetime(date_value, time_value):
                flash("Enter a valid meeting date and time.", "danger")
            elif (current_user.role not in OFFICE_ROLES and staff_id not in {None, current_user.id, appointment.staff_id}):
                abort(403)
            elif (current_user.role not in OFFICE_ROLES and appointment.staff_id not in {None, current_user.id}
                  and staff_id != appointment.staff_id):
                abort(403)
            elif staff_id and (not new_staff or (staff_id != appointment.staff_id and
                    new_staff not in _eligible_staff_for_student(appointment.student))):
                flash("Choose an authorized staff member for this student.", "danger")
            elif request.form.get("follow_up_due_date", "").strip() and not _valid_meeting_datetime(
                    request.form.get("follow_up_due_date", "").strip(), "00:00"):
                flash("Enter a valid follow-up due date.", "danger")
            else:
                previous_staff_id = appointment.staff_id
                appointment.status = status
                appointment.scheduled_date = date_value
                appointment.scheduled_time = time_value
                appointment.staff_id = staff_id
                appointment.parent_attended = _parse_attendance(request.form.get("parent_attended", ""))
                appointment.staff_attended = _parse_attendance(request.form.get("staff_attended", ""))
                appointment.staff_notes = request.form.get("staff_notes", "").strip() or None
                appointment.decision_summary = request.form.get("decision_summary", "").strip() or None
                appointment.follow_up_actions = request.form.get("follow_up_actions", "").strip() or None
                due_date = request.form.get("follow_up_due_date", "").strip()
                appointment.follow_up_due_date = due_date or None
                db.session.commit()
                _notify_meeting_change(appointment)
                if appointment.staff_id and appointment.staff_id != previous_staff_id:
                    notify_users(
                        {appointment.staff_id}, "Meetings", "Meeting assigned to you",
                        "A meeting was assigned to you. Sign in to view the details.",
                        "/appointments",
                    )
                    db.session.commit()
                flash("Meeting record updated.", "success")
        else:
            abort(400)
        return redirect(url_for("appointments"))

    if is_parent:
        children = [student for student in linked_children(current_user.id) if student.is_active_account]
        student_options = "".join(
            f"<option value='{student.id}'>{escape(student.full_name)} · {escape(student.class_name or 'No class')}</option>"
            for student in children
        )
        create_form = (
            "<section class='panel'><h2>Request a meeting</h2>"
            "<p class='muted'>Send a private request to the school about one of your linked children. The school will confirm the meeting time.</p>"
            if children else
            "<section class='panel'><h2>Meeting requests</h2><p class='muted'>No children are linked to this account yet. Contact the school office to link your parent account.</p>"
        )
        if children:
            create_form += f"""<form method='post' class='row g-3'>
<input type='hidden' name='action' value='create'>
<div class='col-md-6'><label class='form-label'>Child</label><select class='form-select' name='student_id' required><option value=''>Choose child</option>{student_options}</select></div>
<div class='col-md-6'><label class='form-label'>Meeting purpose</label><input class='form-control' name='purpose' maxlength='200' required></div>
<div class='col-md-3'><label class='form-label'>Preferred date</label><input class='form-control' type='date' name='scheduled_date' value='{datetime.date.today().isoformat()}' required></div>
<div class='col-md-3'><label class='form-label'>Preferred time</label><input class='form-control' type='time' name='scheduled_time' required></div>
<div class='col-12'><label class='form-label'>Request details</label><textarea class='form-control' name='notes' rows='3' maxlength='4000'></textarea><small class='muted'>Share only information relevant to arranging this meeting.</small></div>
<div class='col-12'><button class='btn btn-accent'>Send meeting request</button></div></form>"""
        create_form += "</section>"
    else:
        students = _students_for_staff(current_user)
        student_options = "".join(
            f"<option value='{student.id}'>{escape(student.full_name)} · {escape(student.class_name or 'No class')}</option>"
            for student in students
        )
        staff_candidates = _eligible_staff_for_student(None) if current_user.role in OFFICE_ROLES else [current_user]
        staff_options = _staff_choices(staff_candidates, current_user.id if current_user.role not in OFFICE_ROLES else None)
        create_form = f"""<section class='panel'><h2>Schedule a meeting</h2>
<form method='post' class='row g-3'><input type='hidden' name='action' value='create'>
<div class='col-md-4'><label class='form-label'>Parent / visitor name</label><input class='form-control' name='visitor_name' maxlength='160' required></div>
<div class='col-md-4'><label class='form-label'>Phone</label><input class='form-control' type='tel' name='visitor_phone' maxlength='40'></div>
<div class='col-md-4'><label class='form-label'>Email</label><input class='form-control' type='email' name='visitor_email' maxlength='160'></div>
<div class='col-md-4'><label class='form-label'>Related student (optional)</label><select class='form-select' name='student_id'><option value=''>General visit</option>{student_options}</select></div>
<div class='col-md-4'><label class='form-label'>Meeting purpose</label><input class='form-control' name='purpose' maxlength='200' required></div>
<div class='col-md-2'><label class='form-label'>Date</label><input class='form-control' type='date' name='scheduled_date' value='{datetime.date.today().isoformat()}' required></div>
<div class='col-md-2'><label class='form-label'>Time</label><input class='form-control' type='time' name='scheduled_time' required></div>
<div class='col-md-6'><label class='form-label'>Assigned staff</label><select class='form-select' name='staff_id'>{staff_options}</select></div>
<div class='col-12'><label class='form-label'>Request notes</label><textarea class='form-control' name='notes' rows='2' maxlength='4000'></textarea></div>
<div class='col-12'><button class='btn btn-accent'>Schedule meeting</button></div></form></section>"""

    records = _visible_appointments(current_user)
    cards = []
    for appointment in records:
        student_name = appointment.student.full_name if appointment.student else "General visit"
        assigned_name = appointment.assigned_staff.full_name if appointment.assigned_staff else "Not assigned"
        parent_summary = ""
        if appointment.decision_summary:
            parent_summary += f"<p><strong>Decision summary:</strong> {escape(appointment.decision_summary)}</p>"
        if appointment.follow_up_actions:
            due = f" · Due {escape(appointment.follow_up_due_date)}" if appointment.follow_up_due_date else ""
            parent_summary += f"<p><strong>Follow-up:</strong> {escape(appointment.follow_up_actions)}{due}</p>"
        attendance_labels = []
        if appointment.parent_attended is not None:
            attendance_labels.append(f"Parent: {'Present' if appointment.parent_attended else 'Absent'}")
        if appointment.staff_attended is not None:
            attendance_labels.append(f"Staff: {'Present' if appointment.staff_attended else 'Absent'}")
        attendance_text = " · ".join(attendance_labels) or "Not recorded"

        if is_parent:
            parent_actions = ""
            if appointment.status not in FINAL_STATUSES:
                parent_actions = f"""<details class='mt-3'><summary class='btn btn-sm btn-outline-success'>Suggest a new time</summary>
<form method='post' class='row g-2 mt-2'><input type='hidden' name='action' value='reschedule_request'><input type='hidden' name='appointment_id' value='{appointment.id}'>
<div class='col-auto'><label class='form-label'>Preferred date</label><input class='form-control' type='date' name='scheduled_date' required></div>
<div class='col-auto'><label class='form-label'>Preferred time</label><input class='form-control' type='time' name='scheduled_time' required></div>
<div class='col-auto d-flex align-items-end'><button class='btn btn-sm btn-outline-success'>Send new preference</button></div></form></details>
<form method='post' class='mt-2' onsubmit="return confirm('Cancel this meeting request?')">
<input type='hidden' name='action' value='cancel_request'><input type='hidden' name='appointment_id' value='{appointment.id}'>
<button class='btn btn-sm btn-outline-danger'>Cancel request</button></form>"""
            detail = (
                f"<p><strong>Preferred / scheduled time:</strong> {escape(appointment.scheduled_date)} at {escape(appointment.scheduled_time)}</p>"
                f"<p><strong>School contact:</strong> {escape(assigned_name)}</p>"
                f"<p><strong>Request details:</strong> {escape(appointment.notes or 'None provided')}</p>"
                f"<p><strong>Attendance:</strong> {escape(attendance_text)}</p>{parent_summary}"
                f"<a class='btn btn-sm btn-outline-success me-2' href='/messages'>Open secure messages</a>{parent_actions}"
            )
        else:
            staff_candidates = (
                _eligible_staff_for_student(appointment.student)
                if current_user.role in OFFICE_ROLES else [current_user]
            )
            if appointment.assigned_staff and appointment.assigned_staff not in staff_candidates:
                staff_candidates.append(appointment.assigned_staff)
            status_options = "".join(
                f"<option value='{status}'{' selected' if appointment.status == status else ''}>{status}</option>"
                for status in sorted(MEETING_STATUSES)
            )
            parent_attended_options = _attendance_choice(appointment.parent_attended)
            staff_attended_options = _attendance_choice(appointment.staff_attended)
            staff_note = f"<p><strong>Staff notes:</strong> {escape(appointment.staff_notes)}</p>" if appointment.staff_notes else ""
            contact = " · ".join(value for value in (appointment.visitor_phone, appointment.visitor_email) if value)
            detail = f"""<p><strong>Scheduled:</strong> {escape(appointment.scheduled_date)} at {escape(appointment.scheduled_time)}</p>
<p><strong>Parent / visitor:</strong> {escape(appointment.visitor_name)}{f' · {escape(contact)}' if contact else ''}</p>
<p><strong>Student:</strong> {escape(student_name)}</p><p><strong>Assigned staff:</strong> {escape(assigned_name)}</p>
<p><strong>Request details:</strong> {escape(appointment.notes or 'None provided')}</p>{staff_note}{parent_summary}
<a class='btn btn-sm btn-outline-success me-2' href='/messages'>Open secure messages</a>
<details class='mt-3'><summary class='btn btn-sm btn-outline-success'>Update meeting, attendance, and follow-up</summary>
<form method='post' class='row g-3 border rounded p-3 mt-2'><input type='hidden' name='action' value='update'><input type='hidden' name='appointment_id' value='{appointment.id}'>
<div class='col-md-3'><label class='form-label'>Status</label><select class='form-select' name='status'>{status_options}</select></div>
<div class='col-md-3'><label class='form-label'>Date</label><input class='form-control' type='date' name='scheduled_date' value='{escape(appointment.scheduled_date)}' required></div>
<div class='col-md-3'><label class='form-label'>Time</label><input class='form-control' type='time' name='scheduled_time' value='{escape(appointment.scheduled_time)}' required></div>
<div class='col-md-3'><label class='form-label'>Assigned staff</label><select class='form-select' name='staff_id'>{_staff_choices(staff_candidates, appointment.staff_id)}</select></div>
<div class='col-md-3'><label class='form-label'>Parent attendance</label><select class='form-select' name='parent_attended'>{parent_attended_options}</select></div>
<div class='col-md-3'><label class='form-label'>Staff attendance</label><select class='form-select' name='staff_attended'>{staff_attended_options}</select></div>
<div class='col-md-6'><label class='form-label'>Follow-up due date</label><input class='form-control' type='date' name='follow_up_due_date' value='{escape(appointment.follow_up_due_date or '')}'></div>
<div class='col-12'><label class='form-label'>Decision summary (visible to parent)</label><textarea class='form-control' name='decision_summary' rows='2' maxlength='4000'>{escape(appointment.decision_summary or '')}</textarea></div>
<div class='col-12'><label class='form-label'>Follow-up actions (visible to parent)</label><textarea class='form-control' name='follow_up_actions' rows='2' maxlength='4000'>{escape(appointment.follow_up_actions or '')}</textarea></div>
<div class='col-12'><label class='form-label'>Staff-only notes</label><textarea class='form-control' name='staff_notes' rows='2' maxlength='4000'>{escape(appointment.staff_notes or '')}</textarea></div>
<div class='col-12'><button class='btn btn-accent'>Save meeting record</button></div></form></details>"""

        cards.append(
            f"<article class='panel meeting-card'><div class='d-flex flex-wrap align-items-start justify-content-between gap-2'>"
            f"<div><span class='pill'>{escape(appointment.status)}</span><h3 class='mt-2 mb-1'>{escape(appointment.purpose)}</h3>"
            f"<p class='muted mb-2'>{escape(student_name)} · Attendance: {escape(attendance_text)}</p></div></div>{detail}</article>"
        )

    heading = "Parent–School Meeting Requests" if is_parent else "Parent & School Administration Meetings"
    empty_state = '<div class="panel muted">No meetings are available.</div>'
    body = (
        f"<section class='panel'><h1>{heading}</h1><p class='muted'>"
        f"{'Request meetings for your linked children and review school responses.' if is_parent else 'Review authorized meetings, record attendance, and share decisions and follow-up with parents.'}"
        f"</p></section>{create_form}<section><h2 class='px-1'>Meeting records</h2>"
        f"{''.join(cards) if cards else empty_state}</section>"
    )
    return page("Meeting Panel", body, "parent" if is_parent else "master_admin")


def register_routes(app):
    app.add_url_rule(
        "/appointments", endpoint="appointments",
        view_func=permission_for("appointments")(appointments), methods=["GET", "POST"],
    )
