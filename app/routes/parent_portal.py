import re

from flask import flash, redirect, request, url_for
from flask_login import current_user
from markupsafe import escape

from app.extensions import db
from app.models import ParentNotificationPreference, User
from app.utils.decorators import permission_for
from app.utils.rendering import page


def notification_settings():
    preferences = ParentNotificationPreference.query.filter_by(parent_id=current_user.id).first()
    if not preferences:
        preferences = ParentNotificationPreference(parent_id=current_user.id,
                                                  email_enabled=bool(current_user.email),
                                                  sms_enabled=bool(current_user.phone))
        db.session.add(preferences)
        db.session.commit()

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower() or None
        phone = request.form.get("phone", "").strip() or None
        email_enabled = request.form.get("email_enabled") == "yes"
        sms_enabled = request.form.get("sms_enabled") == "yes"
        if email and (len(email) > 160 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)):
            flash("Enter a valid email address.", "danger")
        elif email and User.query.filter(User.email == email, User.id != current_user.id).first():
            flash("That email address is already used by another account.", "danger")
        elif phone and (len(phone) > 40 or not re.fullmatch(r"\+?[0-9 ()-]{7,40}", phone)):
            flash("Enter a valid phone number, including the country code for SMS.", "danger")
        elif email_enabled and not email:
            flash("Add an email address or turn off email alerts.", "danger")
        elif sms_enabled and not phone:
            flash("Add a phone number or turn off SMS alerts.", "danger")
        elif sms_enabled and not phone.startswith("+"):
            flash("Use international phone format beginning with + for SMS delivery.", "danger")
        else:
            current_user.email = email
            current_user.phone = phone
            preferences.email_enabled = email_enabled
            preferences.sms_enabled = sms_enabled
            db.session.commit()
            flash("Parent contact and alert preferences saved.", "success")
            return redirect(url_for("parent_notification_settings"))

    email_checked = "checked" if preferences.email_enabled else ""
    sms_checked = "checked" if preferences.sms_enabled else ""
    body = f"""<section class='panel'><h1>Parent <span class='accent'>Alert Settings</span></h1>
<p class='muted'>Choose how you receive attendance, fee, behavior, academic, and school alerts for your linked children. Alerts also appear in your portal notifications.</p>
<form method='post' class='row g-3'><div class='col-md-6'><label>Email address</label><input class='form-control' type='email' name='email' maxlength='160' value='{escape(current_user.email or '')}'></div>
<div class='col-md-6'><label>Mobile phone</label><input class='form-control' type='tel' name='phone' maxlength='40' placeholder='+250...' value='{escape(current_user.phone or '')}'></div>
<div class='col-12'><label class='d-flex align-items-center gap-2'><input type='checkbox' name='email_enabled' value='yes' {email_checked}> Send alerts by email</label></div>
<div class='col-12'><label class='d-flex align-items-center gap-2'><input type='checkbox' name='sms_enabled' value='yes' {sms_checked}> Send alerts by SMS</label></div>
<div class='col-12'><p class='muted small'>Email and SMS delivery require the school's mail and SMS provider settings. Your portal notifications remain available regardless.</p><button class='btn btn-accent'>Save Settings</button> <a class='btn btn-outline-success' href='/dashboard'>Back to Parent Portal</a></div></form></section>"""
    return page("Parent Alert Settings", body, "parent")


def register_routes(app):
    app.add_url_rule('/parent/notification-settings', endpoint='parent_notification_settings',
                     view_func=permission_for('parent_notification_settings')(notification_settings), methods=['GET', 'POST'])
