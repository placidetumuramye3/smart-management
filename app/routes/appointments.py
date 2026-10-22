import datetime

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user
from markupsafe import escape

from app.extensions import db
from app.models import Appointment, User
from app.utils.decorators import permission_for
from app.utils.rendering import page


APPOINTMENT_STATUSES = {"Scheduled", "Rescheduled", "Completed", "Cancelled"}


def appointments():
    if request.method == "POST":
        action = request.form.get("action")
        appointment = None
        if action == "create":
            visitor_name = request.form.get("visitor_name", "").strip()
            purpose = request.form.get("purpose", "").strip()
            date_value = request.form.get("scheduled_date", "").strip()
            time_value = request.form.get("scheduled_time", "").strip()
            student_id = request.form.get("student_id", type=int)
            student = db.session.get(User, student_id) if student_id else None
            try:
                datetime.date.fromisoformat(date_value)
                datetime.datetime.strptime(time_value, "%H:%M")
            except ValueError:
                flash("Enter a valid meeting date and time.", "danger")
            else:
                if not visitor_name or not purpose or (student_id and (not student or student.role != "student")):
                    flash("Enter the visitor name and meeting purpose, and choose a valid student if applicable.", "danger")
                else:
                    appointment = Appointment(
                        visitor_name=visitor_name,
                        visitor_phone=request.form.get("visitor_phone", "").strip() or None,
                        visitor_email=request.form.get("visitor_email", "").strip() or None,
                        student_id=student.id if student else None,
                        purpose=purpose,
                        scheduled_date=date_value,
                        scheduled_time=time_value,
                        notes=request.form.get("notes", "").strip() or None,
                        created_by=current_user.id,
                    )
                    db.session.add(appointment)
                    db.session.commit()
                    flash("Appointment scheduled.", "success")
        elif action == "update":
            appointment = db.session.get(Appointment, request.form.get("appointment_id", type=int))
            status = request.form.get("status", "")
            if not appointment:
                abort(404)
            if status not in APPOINTMENT_STATUSES:
                abort(400)
            appointment.status = status
            db.session.commit()
            flash("Appointment status updated.", "success")
        else:
            abort(400)
        return redirect(url_for("appointments"))

    students = User.query.filter_by(role="student").order_by(User.full_name).all()
    student_options = "".join(
        f"<option value='{student.id}'>{escape(student.full_name)} ({escape(student.class_name or 'No class')})</option>"
        for student in students
    )
    records = Appointment.query.order_by(Appointment.scheduled_date.desc(), Appointment.scheduled_time.desc()).all()
    rows = []
    for appointment in records:
        status_options = "".join(
            f"<option value='{status}'{' selected' if appointment.status == status else ''}>{status}</option>"
            for status in sorted(APPOINTMENT_STATUSES)
        )
        rows.append(
            f"<tr><td>{escape(appointment.scheduled_date)}<br>{escape(appointment.scheduled_time)}</td>"
            f"<td>{escape(appointment.visitor_name)}<br><small>{escape(appointment.visitor_phone or appointment.visitor_email or '')}</small></td>"
            f"<td>{escape(appointment.student.full_name if appointment.student else 'General visit')}</td>"
            f"<td>{escape(appointment.purpose)}{f'<br><small>{escape(appointment.notes)}</small>' if appointment.notes else ''}</td>"
            f"<td><form method='post' class='d-flex gap-2'><input type='hidden' name='action' value='update'><input type='hidden' name='appointment_id' value='{appointment.id}'><select class='form-select form-select-sm' name='status'>{status_options}</select><button class='btn btn-sm btn-outline-success'>Save</button></form></td></tr>"
        )
    today = datetime.date.today().isoformat()
    body = f"""<section class='panel'><h1>Parent & Visitor <span class='accent'>Appointments</span></h1><p class='muted'>Schedule meetings and track their status.</p><form method='post' class='row g-3'><input type='hidden' name='action' value='create'><div class='col-md-6'><label class='form-label'>Parent / visitor name</label><input class='form-control' name='visitor_name' required></div><div class='col-md-3'><label class='form-label'>Phone</label><input class='form-control' type='tel' name='visitor_phone'></div><div class='col-md-3'><label class='form-label'>Email</label><input class='form-control' type='email' name='visitor_email'></div><div class='col-md-4'><label class='form-label'>Related student (optional)</label><select class='form-select' name='student_id'><option value=''>General visit</option>{student_options}</select></div><div class='col-md-4'><label class='form-label'>Meeting purpose</label><input class='form-control' name='purpose' required></div><div class='col-md-2'><label class='form-label'>Date</label><input class='form-control' type='date' name='scheduled_date' value='{today}' required></div><div class='col-md-2'><label class='form-label'>Time</label><input class='form-control' type='time' name='scheduled_time' required></div><div class='col-12'><label class='form-label'>Notes</label><textarea class='form-control' name='notes' rows='2'></textarea></div><div class='col-12'><button class='btn btn-accent'>Schedule Appointment</button></div></form></section><section class='panel'><h2>Appointments</h2><div class='table-responsive'><table class='table align-middle'><thead><tr><th>Scheduled</th><th>Visitor</th><th>Student</th><th>Purpose / Notes</th><th>Status</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="5" class="muted">No appointments scheduled.</td></tr>'}</tbody></table></div></section>"""
    return page("Appointments", body, "master_admin")


def register_routes(app):
    app.add_url_rule("/appointments", endpoint="appointments", view_func=permission_for("appointments")(appointments), methods=["GET", "POST"])
