import os
import datetime
import json
from flask import request, redirect, url_for, flash, jsonify, abort, send_file, current_app
from flask_login import login_user, login_required, logout_user, current_user
from werkzeug.utils import secure_filename
from markupsafe import escape
from openpyxl import Workbook
from app.extensions import db
from app.models import (User, Attendance, PendingCard, FeeTransaction, FeeRequirement, Book, BookLoan,
                        DisciplineCase, SchoolMessage, UserNotification, Grade, TimetableEntry,
                        AssessmentSubmission, AssessmentAttempt, Setting,
                        MessageParticipant, MessagePermission, MessageReport,
                        OfficialNoticePermission)
from app.models import StudentGuardian
from app.models import AcademicSubject
from app.utils.constants import *
from app.utils.decorators import role_required
from app.utils.helpers import get_setting, set_setting, attendance_for_student
from app.utils.rendering import page
from app.utils.decorators import permission_for
from app.services.activity import log_student_activity
from app.services.meeting_workflow import verify_guardian_link

def admin():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "save_motto":
            motto = request.form.get("school_motto", "").strip()
            if len(motto) > 200:
                flash("The school motto must be 200 characters or fewer.", "danger")
            else:
                set_setting("school_motto", motto)
                flash("School motto saved and displayed on the home page.", "success")
        elif action == "delete_user":
            try:
                target_id = int(request.form.get("user_id", "0"))
            except ValueError:
                target_id = 0
            target = db.session.get(User, target_id)
            if not target:
                flash("Account not found.", "danger")
            elif target.id == current_user.id:
                flash("You cannot delete the account you are currently using.", "danger")
            elif target.role == "master_admin":
                flash("Master Admin accounts are protected from deletion here.", "danger")
            else:
                if target.role == "student":
                    log_student_activity(current_user, target, "Student removed", "Student account removed from the school system.")
                # Remove related records that directly belong to this user before deleting the account.
                for model_name in (
                    "Attendance", "Grade", "FeeTransaction", "FeeRequirement", "DisciplineCase",
                    "BookLoan", "AssessmentSubmission", "AssessmentAttempt", "UserNotification",
                    "MessageParticipant", "MessagePermission", "OfficialNoticePermission",
                ):
                    model = globals().get(model_name)
                    if model is not None and hasattr(model, "student_id") and target.role == "student":
                        model.query.filter_by(student_id=target.id).delete(synchronize_session=False)
                    elif model is not None and hasattr(model, "user_id"):
                        model.query.filter_by(user_id=target.id).delete(synchronize_session=False)
                MessageReport.query.filter_by(reporter_id=target.id).update(
                    {MessageReport.reporter_id: None}, synchronize_session=False
                )
                # Bulk deletes bypass ORM relationship synchronization. Expire loaded
                # collections so deleting the user cannot try to null their foreign keys.
                db.session.expire(target)
                db.session.delete(target)
                db.session.commit()
                flash("Account deleted successfully.", "success")
        elif action == "edit_user":
            target = db.session.get(User, request.form.get("user_id", type=int))
            full_name = request.form.get("full_name", "").strip()
            username = request.form.get("username", "").strip()
            role = request.form.get("role", "").strip()
            class_name = request.form.get("class_name", "").strip() or None
            email = request.form.get("email", "").strip() or None
            phone = request.form.get("phone", "").strip() or None
            new_password = request.form.get("password", "")
            active = request.form.get("is_active_account") == "on"
            valid_teacher_class = (
                role != "teacher"
                or (class_name and User.query.filter_by(role="student", class_name=class_name).first())
            )
            if not target:
                flash("Account not found.", "danger")
            elif not full_name or not username or role not in ROLES or not valid_teacher_class:
                flash("Enter a name and username, choose a valid role, and assign teachers to a class with students.", "danger")
            elif User.query.filter(User.username == username, User.id != target.id).first():
                flash("That username is already in use.", "danger")
            elif email and User.query.filter(User.email == email, User.id != target.id).first():
                flash("That email address is already in use.", "danger")
            elif new_password and len(new_password) < 8:
                flash("A new password must contain at least 8 characters.", "danger")
            elif target.id == current_user.id and (role not in ADMIN_ROLES or not active):
                flash("You cannot remove or disable your own Master Admin access.", "danger")
            else:
                was_student = target.role == "student"
                old_values = {
                    "name": target.full_name,
                    "role": target.role,
                    "class": target.class_name,
                    "email": target.email,
                    "phone": target.phone,
                }
                target.full_name = full_name
                target.username = username
                target.role = role
                target.class_name = class_name if role == "teacher" else None
                target.email = email
                target.phone = phone
                target.is_active_account = active
                if new_password:
                    target.set_password(new_password)
                if was_student or target.role == "student":
                    new_values = {
                        "name": target.full_name,
                        "role": target.role,
                        "class": target.class_name,
                        "email": target.email,
                        "phone": target.phone,
                    }
                    changed = [field for field in old_values if old_values[field] != new_values[field]]
                    if changed:
                        log_student_activity(current_user, target, "Student record updated", f"Updated fields: {', '.join(changed)}.")
                db.session.commit()
                flash("Account updated successfully.", "success")
        elif action == "upload_logo":
            uploaded = request.files.get("logo_file")
            if not uploaded or not uploaded.filename:
                flash("Choose a logo image to upload.", "danger")
            else:
                safe_name = secure_filename(uploaded.filename)
                ext = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
                if ext not in ALLOWED_LOGO_EXTENSIONS:
                    flash("Logo must be PNG, JPG, JPEG, or WEBP.", "danger")
                else:
                    filename = "school_logo." + ext
                    os.makedirs(current_app.config["UPLOAD_FOLDER"], exist_ok=True)
                    uploaded.save(os.path.join(current_app.config["UPLOAD_FOLDER"], filename))
                    set_setting("school_logo", filename)
                    flash("School logo updated on the home page and navigation bar.", "success")
        elif action == "create_staff":
            username = request.form.get("username", "").strip()
            full_name = request.form.get("full_name", "").strip()
            password = request.form.get("password", "")
            role = request.form.get("role", "teacher")
            assignment_trades = request.form.getlist("assignment_trade[]")
            assignment_levels = request.form.getlist("assignment_level[]")
            assignment_classes = request.form.getlist("assignment_class[]")
            assignment_subjects = request.form.getlist("assignment_subject[]")
            assignments = []
            assignment_error = False
            if role == "teacher":
                row_count = max(map(len, (assignment_trades, assignment_levels, assignment_classes, assignment_subjects)), default=0)
                for index in range(row_count):
                    trade = assignment_trades[index].strip() if index < len(assignment_trades) else ""
                    level = assignment_levels[index].strip() if index < len(assignment_levels) else ""
                    class_name = assignment_classes[index].strip() if index < len(assignment_classes) else ""
                    subject = assignment_subjects[index].strip() if index < len(assignment_subjects) else ""
                    if not any((trade, level, class_name, subject)):
                        continue
                    if CLASS_TRADE_LEVEL.get(class_name) != (trade, level) or not subject:
                        assignment_error = True
                        break
                    assignments.append((trade, level, class_name, subject))
                if not assignments:
                    assignment_error = True
                assignment_keys = [(class_name.casefold(), subject.casefold()) for _, _, class_name, subject in assignments]
                if len(assignment_keys) != len(set(assignment_keys)):
                    assignment_error = True
            conflicting_assignment = any(
                AcademicSubject.query.filter_by(class_name=class_name, subject=subject).filter(
                    AcademicSubject.teacher_id.isnot(None)
                ).first()
                for _, _, class_name, subject in assignments
            )
            if not username or not full_name or len(password) < 8 or role not in STAFF_ROLES or assignment_error:
                flash("Enter valid staff details and add at least one complete teacher assignment (trade, level, class, and lesson).", "danger")
            elif conflicting_assignment:
                flash("One of these class lessons is already assigned to another teacher. Review Academic Management first.", "danger")
            elif User.query.filter_by(username=username).first():
                flash("Username already exists.", "danger")
            else:
                u = User(username=username, full_name=full_name, role=role, email=request.form.get("email") or None,
                         class_name=assignments[0][2] if role == "teacher" else None)
                u.set_password(password)
                db.session.add(u)
                db.session.flush()
                for trade, level, class_name, subject in assignments:
                    existing_subjects = AcademicSubject.query.filter_by(class_name=class_name, subject=subject).all()
                    if existing_subjects:
                        for existing in existing_subjects:
                            existing.teacher_id = u.id
                            existing.is_active = True
                    else:
                        db.session.add(AcademicSubject(trade=trade, level=level, class_name=class_name,
                                                       subject=subject, teacher_id=u.id))
                db.session.commit()
                flash("Staff account created successfully.", "success")
        elif action == "assign_teacher_class":
            teacher = db.session.get(User, request.form.get("teacher_id", type=int))
            class_name = request.form.get("class_name", "").strip()
            valid_class = User.query.filter_by(role="student", class_name=class_name).first() if class_name else None
            if not teacher or teacher.role != "teacher" or not valid_class:
                flash("Choose a teacher and a class that has students.", "danger")
            else:
                teacher.class_name = class_name
                db.session.commit()
                flash("Teacher class assignment saved.", "success")
        elif action == "link_parent":
            parent_id = request.form.get("parent_id")
            student_id = request.form.get("student_id")
            if parent_id and student_id:
                s = db.session.get(User, int(student_id))
                p = db.session.get(User, int(parent_id))
                if s and s.role == "student" and p and p.role == "parent":
                    try:
                        verify_guardian_link(current_user, s.id, p.id, "Parent / Guardian")
                    except (PermissionError, ValueError) as error:
                        db.session.rollback()
                        flash(str(error), "danger")
                    else:
                        flash("Parent successfully linked to student.", "success")
                else:
                    flash("Choose a valid parent and student account.", "danger")
        return redirect(url_for("admin"))

    users = User.query.order_by(User.full_name).all()
    parents = [u for u in users if u.role == "parent"]
    students = [u for u in users if u.role == "student"]
    teachers = [u for u in users if u.role == "teacher"]
    class_names = sorted({student.class_name for student in students if student.class_name})
    class_options = "".join(f"<option value='{name}'>{name}</option>" for name in class_names)
    staff_trade_options = "".join(
        f'<option value="{escape(trade)}">{escape(trade)}</option>' for trade in TRADES
    )
    staff_level_options = "".join(
        f'<option value="{escape(level)}" data-trade="{escape(trade)}">{escape(level)}</option>'
        for trade, levels in PROGRAM_LEVELS.items() for level in levels
    )
    staff_class_options = "".join(
        f'<option value="{escape(class_name)}" data-trade="{escape(trade)}" data-level="{escape(level)}">{escape(class_name)}</option>'
        for class_name, (trade, level) in CLASS_TRADE_LEVEL.items()
    )
    staff_subject_options = "".join(
        f'<option value="{escape(subject)}">' for subject in (
            "Accounting", "Accounting Professional", "FAD", "Mathematics",
            "Computer Literacy and Skills", "Religious Education", "Business",
            "Citizenship", "Occupation", "Social and Health Education (SHE)",
            "General Studies",
        )
    )
    user_row_html = []
    for user in users:
        can_manage_user = current_user.role == "master_admin"
        role_options = "".join(
            f'<option value="{escape(role)}"{" selected" if user.role == role else ""}>{escape(role.replace("_", " ").title())}</option>'
            for role in sorted(ROLES)
        )
        if user.role not in ROLES:
            role_options = f'<option value="" selected disabled>Removed role: {escape(user.role)} â€” choose a replacement</option>' + role_options
        teacher_assignments = AcademicSubject.query.filter_by(teacher_id=user.id, is_active=True).order_by(
            AcademicSubject.class_name, AcademicSubject.subject
        ).all() if user.role == "teacher" else []
        assignment_summary = " · ".join(f"{item.class_name}: {item.subject}" for item in teacher_assignments)
        class_display = assignment_summary or user.class_name or "-"
        assignment_detail_html = (
            f"<dt class='col-sm-4'>Assigned lessons</dt><dd class='col-sm-8'>{escape(assignment_summary)}</dd>"
            if user.role == "teacher" and assignment_summary else ""
        )
        account_details = (
            f"<dl class='row mb-0'><dt class='col-sm-4'>Email</dt><dd class='col-sm-8'>{escape(user.email or 'Not set')}</dd>"
            f"<dt class='col-sm-4'>Phone</dt><dd class='col-sm-8'>{escape(user.phone or 'Not set')}</dd>"
            f"{assignment_detail_html}"
            f"<dt class='col-sm-4'>Account status</dt><dd class='col-sm-8'>{'Active' if user.is_active_account else 'Disabled'}</dd></dl>"
        )
        view_dialog = f"<button class='btn btn-sm btn-outline-secondary' type='button' onclick=\"document.getElementById('view-user-{user.id}').showModal()\">View</button><dialog class='account-edit-dialog' id='view-user-{user.id}' aria-labelledby='view-user-title-{user.id}'><div class='account-dialog-header'><h2 id='view-user-title-{user.id}'>{escape(user.full_name)}</h2><button class='account-dialog-close' type='button' aria-label='Close' onclick=\"this.closest('dialog').close()\">&times;</button></div>{account_details}</dialog>"
        edit_form = f"""<button class="btn btn-sm btn-outline-primary" type="button" onclick="document.getElementById('edit-user-{user.id}').showModal()">Edit</button><dialog class="account-edit-dialog" id="edit-user-{user.id}" aria-labelledby="edit-user-title-{user.id}"><div class="account-dialog-header"><h2 id="edit-user-title-{user.id}">Edit {escape(user.full_name)}</h2><button class="account-dialog-close" type="button" aria-label="Close" onclick="this.closest('dialog').close()">&times;</button></div><form method="post" class="row g-2"><input type="hidden" name="action" value="edit_user"><input type="hidden" name="user_id" value="{user.id}"><div class="col-md-6"><label class="form-label">Full name</label><input class="form-control" name="full_name" value="{escape(user.full_name)}" required></div><div class="col-md-6"><label class="form-label">Username</label><input class="form-control" name="username" value="{escape(user.username)}" required></div><div class="col-md-6"><label class="form-label">Email</label><input class="form-control" type="email" name="email" value="{escape(user.email or '')}"></div><div class="col-md-6"><label class="form-label">Phone</label><input class="form-control" name="phone" value="{escape(user.phone or '')}"></div><div class="col-md-6"><label class="form-label">Role</label><select class="form-select" name="role">{role_options}</select></div><div class="col-md-6"><label class="form-label">Assigned class <span class="text-danger">(required for teachers)</span></label><select class="form-select" name="class_name"><option value="">Choose a class with enrolled students...</option>{''.join(f'<option value="{escape(name)}"{" selected" if user.class_name == name else ""}>{escape(name)}</option>' for name in class_names)}</select></div><div class="col-md-6"><label class="form-label">New password (optional)</label><input class="form-control" type="password" name="password" minlength="8" autocomplete="new-password"></div><div class="col-md-6 d-flex align-items-end"><label><input type="checkbox" name="is_active_account"{' checked' if user.is_active_account else ''}> Account enabled</label></div><div class="col-12 text-end mt-3"><button class="btn btn-accent" type="submit">Save changes</button></div></form></dialog>""" if can_manage_user else "<span class='muted'>Restricted</span>"
        delete_action = (
            "<span class='muted'>Protected</span>" if user.role == "master_admin" or not can_manage_user else
            f"<form method='post' class='account-delete' onsubmit=\"return confirm('Delete this account? This action cannot be undone.')\"><input type='hidden' name='action' value='delete_user'><input type='hidden' name='user_id' value='{user.id}'><button class='btn btn-sm btn-danger' type='submit'>Delete</button></form>"
        )
        user_row_html.append(
            f"<tr><td>{escape(user.full_name)}</td><td>{escape(user.username)}</td>"
            f"<td><span class='pill account-role-pill'>{escape(user.role.replace('_', ' ').title())}</span></td>"
            f"<td>{escape(class_display)}</td><td class='account-actions'>{view_dialog}{edit_form}{delete_action}</td></tr>"
        )
    user_rows = "".join(user_row_html)
    parent_options = "".join(f"<option value='{p.id}'>{p.full_name}</option>" for p in parents)
    student_options = "".join(f"<option value='{s.id}'>{s.full_name} ({s.class_name or 'No class'})</option>" for s in students)
    teacher_options = "".join(f"<option value='{teacher.id}'>{teacher.full_name} ({teacher.class_name or 'No class assigned'})</option>" for teacher in teachers)
    role_options = "".join(
        f"<option value='{role}'>{'Master Admin - Full system administrator' if role == 'master_admin' else 'Head Teacher - School-wide academic and student affairs supervision' if role == 'head_teacher' else 'Accountant - School finances and fee reporting' if role == 'accountant' else 'Discipline Officer - Student behavior and discipline cases' if role == 'discipline' else 'Librarian - Books, loans, returns, and overdue tracking' if role == 'librarian' else 'Matron / Patron - Boarding student supervision' if role in {'matron', 'patron'} else 'Secretary - Official correspondence and authorized reports' if role == 'secretary' else 'Teacher - Classes, lessons, assessments, and academic reports' if role == 'teacher' else role.replace('_', ' ').title()}</option>"
        for role in sorted(STAFF_ROLES)
    )

    body = f"""<section class="panel"><h1>Master Admin <span class="accent">Control Center</span></h1><p class="muted"><strong>Master Admin</strong> is the main system administrator. This role manages users, roles, permissions, and system settings, with access to all school information.</p><p class="muted"><strong>Head Teacher</strong> leads school activities and supervises teachers, students, examination results, discipline, and general school reports.</p><p class="muted"><strong>Accountant</strong> manages school finances, student payments, receipts, outstanding fees, and financial reports.</p><p class="muted"><strong>Discipline Officer</strong> manages student behavior, disciplinary offenses, punishments, discipline reports, and measures to resolve disciplinary issues.</p><p class="muted"><strong>Librarian</strong> manages library books, registers new books, issues and receives books, and tracks overdue or unreturned books.</p><p class="muted"><strong>Matron / Patron</strong> manages boarding students, monitors attendance in the dormitories, supervises behavior, and handles issues affecting assigned students.</p><p class="muted"><strong>Secretary</strong> manages official letters and documents, receives and sends correspondence, and prepares administrative reports they are authorized to access.</p><p class="muted"><strong>Teacher</strong> manages attendance for assigned classes, lessons, assessments, and academic reports.</p><p class="muted"><strong>Student</strong> can view personal examination results, attendance records, class timetables, school announcements, and outstanding school fees according to assigned permissions.</p><p class="muted"><strong>Parent / Guardian</strong> can view information about their child, including examination results, attendance, paid or outstanding fees, and school announcements.</p><div class="row g-4"><div class="col-md-6"><h3>Create Staff Account</h3><form method="post" id="create-staff-form"><input type="hidden" name="action" value="create_staff"><label class="mt-2">Full Name</label><input class="form-control" name="full_name" required><label class="mt-2">Username</label><input class="form-control" name="username" required><label class="mt-2">Role</label><select class="form-select" name="role">{role_options}</select><div class="teacher-class-fields" hidden><p class="muted mt-2 mb-1">Assign one or more lessons. You can include classes from different trades or departments.</p><div id="teacher-assignments"><div class="teacher-assignment row g-2 border rounded p-2 mt-2"><div class="col-md-3"><label>Trade / Department</label><select class="form-select assignment-trade" name="assignment_trade[]"><option value="">Choose trade...</option>{staff_trade_options}</select></div><div class="col-md-2"><label>Level</label><select class="form-select assignment-level" name="assignment_level[]" disabled><option value="">Choose trade first...</option>{staff_level_options}</select></div><div class="col-md-3"><label>Class</label><select class="form-select assignment-class" name="assignment_class[]" disabled><option value="">Choose level first...</option>{staff_class_options}</select></div><div class="col-md-3"><label>Lesson / Subject</label><input class="form-control" name="assignment_subject[]" maxlength="160" list="general-course-subjects" placeholder="e.g. Software Development"></div><div class="col-md-1 d-flex align-items-end"><button class="btn btn-outline-danger remove-assignment" type="button" aria-label="Remove assignment">&times;</button></div></div></div><datalist id="general-course-subjects">{staff_subject_options}</datalist><button class="btn btn-sm btn-outline-primary mt-2" type="button" id="add-teacher-assignment">Add another class / lesson assignment</button></div><label class="mt-2">Password (8+ chars)</label><input type="password" minlength="8" class="form-control" name="password" required><button class="btn btn-accent mt-3">Create Staff Account</button></form><template id="teacher-assignment-template"><div class="teacher-assignment row g-2 border rounded p-2 mt-2"><div class="col-md-3"><label>Trade / Department</label><select class="form-select assignment-trade" name="assignment_trade[]"><option value="">Choose trade...</option>{staff_trade_options}</select></div><div class="col-md-2"><label>Level</label><select class="form-select assignment-level" name="assignment_level[]" disabled><option value="">Choose trade first...</option>{staff_level_options}</select></div><div class="col-md-3"><label>Class</label><select class="form-select assignment-class" name="assignment_class[]" disabled><option value="">Choose level first...</option>{staff_class_options}</select></div><div class="col-md-3"><label>Lesson / Subject</label><input class="form-control" name="assignment_subject[]" maxlength="160" list="general-course-subjects" placeholder="e.g. Software Development"></div><div class="col-md-1 d-flex align-items-end"><button class="btn btn-outline-danger remove-assignment" type="button" aria-label="Remove assignment">&times;</button></div></div></template><script>(function(){{const container=document.getElementById('teacher-assignments'),template=document.getElementById('teacher-assignment-template');function setup(row){{const trade=row.querySelector('.assignment-trade'),level=row.querySelector('.assignment-level'),klass=row.querySelector('.assignment-class'),levels=[...level.querySelectorAll('option[data-trade]')],classes=[...klass.querySelectorAll('option[data-trade]')];function updateLevels(){{level.innerHTML='<option value="">Choose level...</option>';levels.filter(option=>option.dataset.trade===trade.value).forEach(option=>level.appendChild(option.cloneNode(true)));level.disabled=!trade.value;updateClasses()}}function updateClasses(){{klass.innerHTML='<option value="">Choose class...</option>';classes.filter(option=>option.dataset.trade===trade.value&&option.dataset.level===level.value).forEach(option=>klass.appendChild(option.cloneNode(true)));klass.disabled=!trade.value||!level.value}}trade.addEventListener('change',updateLevels);level.addEventListener('change',updateClasses);row.querySelector('.remove-assignment').addEventListener('click',()=>{{if(container.children.length>1)row.remove()}})}}container.querySelectorAll('.teacher-assignment').forEach(setup);document.getElementById('add-teacher-assignment').addEventListener('click',()=>{{const fragment=template.content.cloneNode(true),row=fragment.querySelector('.teacher-assignment');container.appendChild(fragment);setup(row)}})}})();</script></div>
<div class="col-md-6"><h3>Link Parent to Student</h3><form method="post"><input type="hidden" name="action" value="link_parent"><label class="mt-2">Parent</label><select class="form-select" name="parent_id" required><option value="">Choose parent...</option>{parent_options}</select><label class="mt-2">Student</label><select class="form-select" name="student_id" required><option value="">Choose student...</option>{student_options}</select><button class="btn btn-accent mt-3">Link Parent & Student</button></form></div></div></section>
<section class="panel"><h2>Assign Teacher to Class</h2><form method="post" class="row g-3 assign-teacher-form"><input type="hidden" name="action" value="assign_teacher_class"><div class="col-md-6"><label>Teacher</label><select class="form-select" name="teacher_id" required><option value="">Choose teacher...</option>{teacher_options}</select></div><div class="col-md-4"><label>Class</label><select class="form-select" name="class_name" required><option value="">Choose class...</option>{class_options}</select></div><div class="col-md-2 d-flex align-items-end"><button class="btn btn-accent">Save Assignment</button></div></form></section>
<section class="panel"><h2>Home Page Motto</h2><p class="muted">Write or change the school's motto here. It appears beneath the school name on the public home page.</p><form method="post"><input type="hidden" name="action" value="save_motto"><label class="mt-2">School Motto</label><input class="form-control" name="school_motto" maxlength="200" value="{get_setting('school_motto', '').replace('&', '&amp;').replace('\"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')}" placeholder="Enter the official school motto"><button class="btn btn-accent mt-3">Save Motto</button></form></section>
<section class="panel"><h2>Home Page School Logo</h2><p class="muted">Upload a PNG, JPG, JPEG, or WEBP logo. It appears on the left side of the home page and in the navigation bar.</p><form method="post" enctype="multipart/form-data" class="row g-3"><input type="hidden" name="action" value="upload_logo"><div class="col-md-8"><input type="file" name="logo_file" class="form-control" accept=".png,.jpg,.jpeg,.webp" required></div><div class="col-md-4"><button class="btn btn-accent">Upload / Replace Logo</button></div></form></section>
<section class="panel"><h2>All System Accounts</h2><p class="muted">Use Delete to remove an account. Master Admin accounts are protected.</p><div class="table-responsive"><table class="table"><tr><th>Full Name</th><th>Username</th><th>Role</th><th>Class</th><th>Action</th></tr>{user_rows}</table></div></section>"""
    return page("Admin Center", body, "master_admin")

def register_routes(app):
    app.add_url_rule('/admin', endpoint='admin', view_func=permission_for('admin')(admin), methods=['GET', 'POST'])
