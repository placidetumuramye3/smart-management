import datetime

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user
from markupsafe import escape

from app.extensions import db
from app.models import Announcement
from app.utils.decorators import permission_for
from app.utils.rendering import page


ANNOUNCEMENT_MANAGERS = {"master_admin", "head_teacher", "secretary"}
ANNOUNCEMENT_AUDIENCES = {"all", "students", "parents", "staff"}


def announcements():
    can_manage = current_user.role in ANNOUNCEMENT_MANAGERS
    if request.method == "POST":
        if not can_manage:
            abort(403)
        action = request.form.get("action")
        if action == "create":
            title = request.form.get("title", "").strip()
            body_text = request.form.get("body", "").strip()
            audience = request.form.get("audience", "")
            if not title or not body_text or audience not in ANNOUNCEMENT_AUDIENCES:
                flash("Enter an announcement title, message, and valid audience.", "danger")
            else:
                publish_now = request.form.get("publish") == "yes"
                db.session.add(Announcement(
                    title=title,
                    body=body_text,
                    audience=audience,
                    is_published=publish_now,
                    published_at=datetime.datetime.utcnow() if publish_now else None,
                    author_id=current_user.id,
                ))
                db.session.commit()
                flash("Announcement published." if publish_now else "Announcement saved as a draft.", "success")
        elif action == "publication":
            announcement = db.session.get(Announcement, request.form.get("announcement_id", type=int))
            if not announcement:
                abort(404)
            announcement.is_published = request.form.get("publish") == "yes"
            announcement.published_at = datetime.datetime.utcnow() if announcement.is_published else None
            db.session.commit()
            flash("Announcement publication updated.", "success")
        else:
            abort(400)
        return redirect(url_for("announcements"))

    query = Announcement.query
    if not can_manage:
        query = query.filter(Announcement.is_published.is_(True))
        if current_user.role == "student":
            query = query.filter(Announcement.audience.in_({"all", "students"}))
        elif current_user.role == "parent":
            query = query.filter(Announcement.audience.in_({"all", "parents"}))
        else:
            query = query.filter(Announcement.audience.in_({"all", "staff"}))
    records = query.order_by(Announcement.created_at.desc(), Announcement.id.desc()).all()
    cards = []
    for announcement in records:
        message = escape(announcement.body)
        state = "Published" if announcement.is_published else "Draft"
        control = ""
        if can_manage:
            control = f"<form method='post' class='mt-3'><input type='hidden' name='action' value='publication'><input type='hidden' name='announcement_id' value='{announcement.id}'><input type='hidden' name='publish' value='{'no' if announcement.is_published else 'yes'}'><button class='btn btn-sm {'btn-outline-secondary' if announcement.is_published else 'btn-accent'}'>{'Unpublish' if announcement.is_published else 'Publish'}</button></form>"
        cards.append(
            f"<article class='panel'><div class='d-flex justify-content-between gap-2 flex-wrap'><h2>{escape(announcement.title)}</h2><span class='pill'>{state} · {escape(announcement.audience.title())}</span></div>"
            f"<p style='white-space:pre-wrap'>{message}</p><small class='muted'>By {escape(announcement.author.full_name if announcement.author else 'School')} · {escape(announcement.created_at.strftime('%Y-%m-%d %H:%M'))}</small>{control}</article>"
        )
    compose = """<section class='panel'><h1>School <span class='accent'>Announcements</span></h1><p class='muted'>Create announcements and publish them to the intended school audience.</p><form method='post' class='row g-3'><input type='hidden' name='action' value='create'><div class='col-md-8'><label class='form-label'>Title</label><input class='form-control' name='title' maxlength='180' required></div><div class='col-md-4'><label class='form-label'>Audience</label><select class='form-select' name='audience'><option value='all'>Everyone</option><option value='students'>Students</option><option value='parents'>Parents / Guardians</option><option value='staff'>Staff</option></select></div><div class='col-12'><label class='form-label'>Announcement</label><textarea class='form-control' name='body' rows='5' required></textarea></div><div class='col-md-6'><label><input type='checkbox' name='publish' value='yes'> Publish immediately</label></div><div class='col-12'><button class='btn btn-accent'>Save Announcement</button></div></form></section>""" if can_manage else "<section class='panel'><h1>School <span class='accent'>Announcements</span></h1><p class='muted'>Published announcements for your account.</p></section>"
    body = compose + "".join(cards or ["<section class='panel muted'>No announcements are available.</section>"])
    return page("Announcements", body, "master_admin" if can_manage else "dashboard")


def register_routes(app):
    app.add_url_rule("/announcements", endpoint="announcements", view_func=permission_for("announcements")(announcements), methods=["GET", "POST"])
