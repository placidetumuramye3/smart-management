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

def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        role = "parent"
        username = request.form.get("username", "").strip()
        full_name = request.form.get("full_name", "").strip()
        password = request.form.get("password", "")
        school_name = " ".join(request.form.get("school_name", "").split())
        sdms_codes = list(dict.fromkeys(code.strip().upper() for code in request.form.get("sdms_codes", "").replace(",", "\n").splitlines() if code.strip()))
        expected_school = " ".join(get_setting("school_name", "Lycée de Muhura").split())
        if not username or not full_name or not school_name or not sdms_codes or len(password) < 8:
            flash("Complete every required field, including the school name and each student's SDMS code.", "danger")
        elif school_name.casefold() != expected_school.casefold():
            flash(f"Enter the registered school name: {expected_school}.", "danger")
        elif User.query.filter_by(username=username).first():
            flash("Username already taken.", "danger")
        else:
            students = User.query.filter(User.role == "student", User.sdms_code.in_(sdms_codes)).all()
            found_codes = {student.sdms_code.upper() for student in students if student.sdms_code}
            if found_codes != set(sdms_codes):
                flash("One or more SDMS codes could not be found. Check the codes with the school office.", "danger")
            elif any(student.parent_id for student in students):
                flash("One or more students are already linked to a parent account. Contact the school office.", "danger")
            else:
                u = User(username=username, full_name=full_name, role=role,
                         email=request.form.get("email", "").strip() or None,
                         phone=request.form.get("phone", "").strip() or None,
                         emergency_contact_name=request.form.get("emergency_contact_name", "").strip() or None,
                         emergency_contact_phone=request.form.get("emergency_contact_phone", "").strip() or None,
                         emergency_contact_relationship=request.form.get("emergency_contact_relationship", "").strip() or None,
                         class_name=None, level=None, trade=None)
                u.set_password(password)
                db.session.add(u)
                db.session.flush()
                for student in students:
                    student.parent_id = u.id
                db.session.commit()
                flash("Parent account created and student records linked. You can now sign in.", "success")
                return redirect(url_for("login"))
    body = f"""<section class="glass-shell"><div class="glass-auth"><div class="glass-side"><div class="eyebrow">Secure access</div><h2>Parent <span style="color:#ffe59a;">Registration</span></h2><p>Link your account to your child records using the school name and each student SDMS code.</p><div class="glass-badges"><span class="glass-badge">School verified</span><span class="glass-badge">Child access linked</span><span class="glass-badge">Secure portal</span></div></div><div class="glass-form"><span class="pill">PARENT ACCOUNT</span><h1>Open your portal</h1><p class="muted">Enter the registered school name and SDMS code for each child. Separate multiple codes with commas or new lines.</p><form method="post" class="row g-3">
<div class="col-md-6"><label class="form-label">Full Name</label><input class="form-control" name="full_name" placeholder="Jane Doe" required></div>
<div class="col-md-6"><label class="form-label">Username</label><input class="form-control" name="username" placeholder="parent.username" required></div>
<div class="col-md-6"><label class="form-label">Email (Optional)</label><input class="form-control" name="email" type="email" placeholder="you@example.com"></div>
<div class="col-md-6"><label class="form-label">Phone (Optional)</label><input class="form-control" name="phone" type="tel" placeholder="Parent / guardian phone"></div>
<div class="col-md-6"><label class="form-label">Emergency Contact Name</label><input class="form-control" name="emergency_contact_name" placeholder="Emergency contact person"></div>
<div class="col-md-6"><label class="form-label">Emergency Contact Phone</label><input class="form-control" name="emergency_contact_phone" type="tel" placeholder="Emergency phone number"></div>
<div class="col-12"><label class="form-label">Emergency Contact Relationship</label><input class="form-control" name="emergency_contact_relationship" placeholder="e.g. aunt, uncle, family friend"></div>
<div class="col-md-6"><label class="form-label">School Name</label><input class="form-control" name="school_name" value="Lycée de Muhura" required></div>
<div class="col-12"><label class="form-label">Student SDMS Code(s)</label><textarea class="form-control" name="sdms_codes" rows="2" placeholder="Example: SDMS-A1B2C3D4" required></textarea></div>
<div class="col-12"><label class="form-label" for="register-password">Password (8+ characters)</label><div class="password-field"><input id="register-password" class="form-control" name="password" type="password" minlength="8" placeholder="Create a strong password" autocomplete="new-password" required><button class="password-toggle" type="button" aria-label="Show password" aria-pressed="false" onclick="togglePasswordVisibility('register-password', this)"><i class="bi bi-eye" aria-hidden="true"></i></button></div></div>
<div class="col-12"><button class="btn btn-accent">Create Parent Account</button></div>
<div class="col-12 secondary-link">Already have an account? <a href="/login">Sign in</a></div></form></div></div></section>"""
    return page("Register", body, "login")

def login():
    if request.method == "POST":
        u = User.query.filter_by(username=request.form.get("username", "").strip()).first()
        if u and u.role == "admin" and u.check_password(request.form.get("password", "")) and u.is_active_account:
            flash("The Admin role has been removed. Contact the Master Admin to reassign this account.", "danger")
            return redirect(url_for("login"))
        if u and u.check_password(request.form.get("password", "")) and u.is_active_account:
            login_user(u)
            return redirect(url_for("dashboard"))
        flash("Invalid credentials or account disabled.", "danger")
    body = """<section class="panel" style="max-width:520px;margin:auto"><span class="pill">SECURE PORTAL</span><h1 class="mt-3">Staff & User <span class="accent">Sign In</span></h1><form method="post" class="mt-4"><label>Username</label><input class="form-control mb-3" name="username" required><label>Password</label><input class="form-control mb-4" type="password" name="password" required><button class="btn btn-accent w-100">Sign In</button></form></section>"""
    return page("Login", body, "login")

def logout():
    logout_user()
    flash("Signed out successfully.", "info")
    return redirect(url_for("home"))

def register_routes(app):
    app.add_url_rule('/register', endpoint='register', view_func=register, methods=['GET', 'POST'])
    app.add_url_rule('/login', endpoint='login', view_func=login, methods=['GET', 'POST'])
    app.add_url_rule('/logout', endpoint='logout', view_func=logout, methods=['GET'])
