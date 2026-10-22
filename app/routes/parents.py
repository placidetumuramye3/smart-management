from flask import abort, flash, redirect, request, url_for
from markupsafe import escape

from app.extensions import db
from app.models import User
from app.utils.decorators import permission_for
from app.utils.rendering import page


def parent_contacts():
    if request.method == "POST":
        parent = db.session.get(User, request.form.get("parent_id", type=int))
        if not parent or parent.role != "parent":
            abort(404)
        parent.full_name = request.form.get("full_name", "").strip()
        parent.email = request.form.get("email", "").strip() or None
        parent.phone = request.form.get("phone", "").strip() or None
        parent.emergency_contact_name = request.form.get("emergency_contact_name", "").strip() or None
        parent.emergency_contact_phone = request.form.get("emergency_contact_phone", "").strip() or None
        parent.emergency_contact_relationship = request.form.get("emergency_contact_relationship", "").strip() or None
        if not parent.full_name:
            flash("Parent or guardian name is required.", "danger")
        else:
            db.session.commit()
            flash("Parent and emergency contact details updated.", "success")
        return redirect(url_for("parent_contacts"))

    parents = User.query.filter_by(role="parent").order_by(User.full_name).all()
    rows = []
    for parent in parents:
        children = User.query.filter_by(parent_id=parent.id, role="student").order_by(User.full_name).all()
        child_names = "".join(f"<span class='parent-student-name'>{escape(child.full_name)}</span>" for child in children)
        if not child_names:
            child_names = "<span class='muted'>No students linked</span>"
        rows.append(
            f"<article class='parent-contact-card'><div class='parent-students'><span class='parent-section-label'>Linked students</span>{child_names}</div><form method='post' class='row g-3 parent-contact-form'><input type='hidden' name='parent_id' value='{parent.id}'>"
            f"<div class='col-md-6'><label class='form-label'>Parent / guardian name</label><input class='form-control' name='full_name' value='{escape(parent.full_name)}' required></div>"
            f"<div class='col-md-6'><label class='form-label'>Email</label><input class='form-control' type='email' name='email' value='{escape(parent.email or '')}'></div>"
            f"<div class='col-md-4'><label class='form-label'>Parent phone</label><input class='form-control' type='tel' name='phone' value='{escape(parent.phone or '')}'></div>"
            f"<div class='col-md-4'><label class='form-label'>Emergency contact name</label><input class='form-control' name='emergency_contact_name' value='{escape(parent.emergency_contact_name or '')}'></div>"
            f"<div class='col-md-4'><label class='form-label'>Emergency contact phone</label><input class='form-control' type='tel' name='emergency_contact_phone' value='{escape(parent.emergency_contact_phone or '')}'></div>"
            f"<div class='col-lg-8'><label class='form-label'>Emergency contact relationship</label><input class='form-control' name='emergency_contact_relationship' value='{escape(parent.emergency_contact_relationship or '')}'></div>"
            f"<div class='col-lg-4 d-flex align-items-end'><button class='btn btn-accent parent-save-button' type='submit'>Save Contact</button></div></form></article>"
        )
    body = f"""<style>.parent-contact-list{{display:grid;gap:16px;margin-top:24px}}.parent-contact-card{{display:grid;grid-template-columns:minmax(190px, .28fr) minmax(0,1fr);gap:24px;padding:22px;border:1px solid var(--border);border-radius:14px;background:var(--card)}}.parent-students{{display:flex;flex-direction:column;align-items:flex-start;gap:9px;min-width:0;padding:8px 12px 8px 0;border-right:1px solid var(--border)}}.parent-section-label{{font-size:.7rem;font-weight:800;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}}.parent-student-name{{font-weight:700;overflow-wrap:anywhere}}.parent-contact-form{{min-width:0}}.parent-contact-form .form-label{{font-size:.82rem;font-weight:650}}.parent-save-button{{min-height:44px}}@media(max-width:767px){{.parent-contact-card{{grid-template-columns:1fr;gap:16px;padding:16px}}.parent-students{{border-right:0;border-bottom:1px solid var(--border);padding:0 0 14px;flex-direction:row;flex-wrap:wrap;align-items:center}}}}</style><section class='panel'><h1>Parents <span class='accent'>&amp; Guardians</span></h1><p class='muted'>Review linked students and update parent, guardian, and emergency contact information.</p><div class='parent-contact-list'>{''.join(rows) or '<div class="muted">No parent or guardian accounts are registered.</div>'}</div></section>"""
    return page("Parents & Guardians", body, "master_admin")


def register_routes(app):
    app.add_url_rule("/parents", endpoint="parent_contacts", view_func=permission_for("parent_contacts")(parent_contacts), methods=["GET", "POST"])
