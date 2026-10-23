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
from app.utils.helpers import get_setting, set_setting, attendance_for_student, teacher_assigned_classes, teacher_can_access_class, linked_children
from app.utils.rendering import page
from app.utils.decorators import permission_for
from markupsafe import escape
from app.utils.notifications import notify_student_and_parent

def discipline():
    managers = {"master_admin", "head_teacher", "discipline", "patron", "matron"}
    can_manage = current_user.role in managers
    can_report = can_manage or current_user.role == "teacher"
    if request.method == "POST":
        action = request.form.get("action", "create_case")
        if not can_manage and not (current_user.role == "teacher" and action == "create_case"):
            abort(403)
        if action == "set_publication":
            case = db.session.get(DisciplineCase, request.form.get("case_id", type=int)) or abort(404)
            if current_user.role == "teacher" and not teacher_can_access_class(current_user, case.student.class_name):
                abort(403)
            if current_user.role in {"discipline", "patron", "matron"} and (not current_user.class_name or case.student.class_name != current_user.class_name):
                abort(403)
            was_published = case.is_published
            case.is_published = request.form.get("is_published") == "yes"
            if case.is_published and not was_published:
                kind = case.record_type or "Concern"
                notify_student_and_parent(case.student, "Behavior", f"Behavior {kind.lower()} reported",
                                          f"{case.details} {case.remarks or ''}".strip(), "/discipline")
            db.session.commit()
            flash("Discipline case publication updated.", "success")
            return redirect(url_for("discipline"))

        student_id = request.form.get("student_id", type=int)
        record_type = request.form.get("record_type", "Concern").strip()
        details = request.form.get("details", "").strip()
        action_taken = request.form.get("action_taken", "").strip()
        remarks = request.form.get("remarks", "").strip()
        try:
            mark_deduction = float(request.form.get("mark_deduction", 0))
        except ValueError:
            mark_deduction = 0
        if not student_id or not details:
            flash("Student and behavior report details are required.", "danger")
        elif record_type not in {"Achievement", "Concern"}:
            flash("Choose Achievement or Concern for the behavior report.", "danger")
        elif record_type == "Concern" and mark_deduction < 0:
            flash("Conduct mark deduction cannot be negative.", "danger")
        else:
            student = db.session.get(User, student_id)
            if not student or student.role != "student":
                abort(404)
            if current_user.role == "teacher" and not teacher_can_access_class(current_user, student.class_name):
                abort(403)
            if current_user.role in {"discipline", "patron", "matron"} and (not current_user.class_name or student.class_name != current_user.class_name):
                abort(403)
            case = DisciplineCase(student_id=student_id, record_type=record_type,
                                  details=details, action_taken=action_taken or None,
                                  remarks=remarks or None,
                                  mark_deduction=mark_deduction if record_type == "Concern" else 0,
                                  is_published=can_manage and request.form.get("is_published") == "yes", created_at=now_string())
            db.session.add(case)
            if case.is_published:
                notify_student_and_parent(student, "Behavior", f"Behavior {record_type.lower()} reported",
                                          f"{details} {remarks}".strip(), "/discipline")
            db.session.commit()
            flash("Discipline case recorded.", "success")
            return redirect(url_for("discipline"))
    student_query = User.query.filter_by(role="student")
    if current_user.role == "teacher":
        student_query = student_query.filter(db.func.lower(User.class_name).in_({name.casefold() for name in teacher_assigned_classes(current_user)}))
    elif current_user.role in {"discipline", "patron", "matron"}:
        student_query = student_query.filter_by(class_name=current_user.class_name)
    elif current_user.role == "student":
        student_query = student_query.filter_by(id=current_user.id)
    elif current_user.role == "parent":
        student_query = student_query.filter(User.id.in_([child.id for child in linked_children(current_user.id)]))
    students = student_query.order_by(User.full_name).all()
    case_query = DisciplineCase.query.join(User, DisciplineCase.student_id == User.id)
    if current_user.role == "student":
        case_query = case_query.filter(DisciplineCase.student_id == current_user.id)
    elif current_user.role == "parent":
        case_query = case_query.filter(DisciplineCase.student_id.in_(
            [child.id for child in linked_children(current_user.id)]
        ))
    elif current_user.role == "teacher":
        case_query = case_query.filter(db.func.lower(User.class_name).in_({name.casefold() for name in teacher_assigned_classes(current_user)}))
    elif current_user.role in {"discipline", "patron", "matron"}:
        case_query = case_query.filter(User.class_name == current_user.class_name)
    if current_user.role not in managers and current_user.role != "teacher":
        case_query = case_query.filter(DisciplineCase.is_published.is_(True))
    cases = case_query.order_by(DisciplineCase.id.desc()).all()
    student_options = "".join(f"<option value='{s.id}'>{escape(s.full_name)} ({escape(s.class_name or 'No class assigned')})</option>" for s in students)
    case_rows = []
    for case in cases:
        publish_form = "Published" if case.is_published else "Draft"
        if can_manage:
            checked = "checked" if case.is_published else ""
            publish_form = f"<form method='post' class='d-flex align-items-center gap-2'><input type='hidden' name='action' value='set_publication'><input type='hidden' name='case_id' value='{case.id}'><label class='small'><input type='checkbox' name='is_published' value='yes' {checked}> Published</label><button class='btn btn-sm btn-outline-success'>Save</button></form>"
        case_rows.append(f"<tr><td>{escape(case.created_at)}</td><td>{escape(case.student.full_name if case.student else '-')}</td><td>{escape(case.record_type or 'Concern')}</td><td>{escape(case.details)}</td><td>{escape(case.action_taken or '-')}</td><td>{escape(case.remarks or '-')}</td><td>{f'{case.mark_deduction or 0:g}' if (case.record_type or 'Concern') == 'Concern' else '—'}</td><td>{publish_form}</td></tr>")
    publish_control = "<div class='col-12'><label><input type='checkbox' name='is_published' value='yes'> Publish this report for the student and parent</label></div>" if can_manage else "<div class='col-12'><small class='muted'>Reports are saved for staff review before publication.</small></div>"
    form = f"""<form method="post" class="row g-3"><input type="hidden" name="action" value="create_case"><div class="col-md-6"><label>Student</label><select class="form-select" name="student_id" required><option value="">Choose student...</option>{student_options}</select></div><div class="col-md-6"><label>Report Type</label><select class="form-select" name="record_type"><option>Achievement</option><option selected>Concern</option></select></div><div class="col-md-6"><label>Conduct Mark Deduction (concerns only)</label><input type="number" min="0" step="any" class="form-control" name="mark_deduction" value="0"></div><div class="col-12"><label>Behavior Report Details</label><textarea class="form-control" name="details" rows="3" required placeholder="Describe the achievement or behavior concern"></textarea></div><div class="col-12"><label>Action Taken</label><textarea class="form-control" name="action_taken" rows="2"></textarea></div><div class="col-12"><label>Relevant Remarks</label><textarea class="form-control" name="remarks" rows="2"></textarea></div>{publish_control}<div class="col-12"><button class="btn btn-accent">Save Behavior Report</button></div></form>""" if can_report else ""
    if can_report:
        form = (form.replace('name="record_type"', 'name="record_type" id="behavior-record-type"')
                    .replace('name="mark_deduction" value="0"', 'name="mark_deduction" id="conduct-mark-deduction" value="0" aria-describedby="conduct-deduction-help"')
                    .replace('value="0" aria-describedby="conduct-deduction-help"></div>', 'value="0" aria-describedby="conduct-deduction-help"><small id="conduct-deduction-help" class="muted">Conduct deductions apply to concerns only.</small></div>'))
        form += """<script>(function(){const type=document.getElementById('behavior-record-type'),deduction=document.getElementById('conduct-mark-deduction'),help=document.getElementById('conduct-deduction-help');if(!type||!deduction)return;function sync(){const applies=type.value==='Concern';deduction.disabled=!applies;if(!applies)deduction.value='0';help.textContent=applies?'Conduct deductions apply to concerns only.':'No deduction is applied to achievements.';}type.addEventListener('change',sync);sync();})();</script>"""
    body = f"""<section class="panel"><h1>Student <span class="accent">Behavior</span></h1><p class="muted">Record positive achievements and behavior concerns with actions taken and relevant remarks.</p>{form}</section>
<section class="panel"><h2>{'Behavior Reports' if can_manage or current_user.role == 'teacher' else 'Published Behavior Reports'}</h2><div class="table-responsive"><table class="table"><tr><th>Date</th><th>Student</th><th>Type</th><th>Details</th><th>Action Taken</th><th>Remarks</th><th>Mark Deduction</th><th>{'Manage' if can_manage else 'Status'}</th></tr>{''.join(case_rows) or '<tr><td colspan="8" class="muted">No behavior reports are available.</td></tr>'}</table></div></section>"""
    return page("Discipline", body, "discipline")

def register_routes(app):
    app.add_url_rule('/discipline', endpoint='discipline', view_func=permission_for('discipline')(discipline), methods=['GET', 'POST'])
