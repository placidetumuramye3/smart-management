from flask import abort, redirect, request, url_for
from flask_login import current_user
from markupsafe import escape

from app.extensions import db
from app.models import UserNotification
from app.utils.decorators import permission_for
from app.utils.rendering import page


def notifications():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "mark_all_read":
            UserNotification.query.filter_by(user_id=current_user.id, is_read=False).update(
                {"is_read": True}, synchronize_session=False)
        elif action == "mark_read":
            notification = UserNotification.query.filter_by(
                id=request.form.get("notification_id", type=int), user_id=current_user.id
            ).first()
            if not notification:
                abort(404)
            notification.is_read = True
        else:
            abort(400)
        db.session.commit()
        return redirect(url_for("notifications"))

    items = UserNotification.query.filter_by(user_id=current_user.id).order_by(
        UserNotification.created_at.desc(), UserNotification.id.desc()).all()
    unread_count = sum(not item.is_read for item in items)
    rows = []
    for item in items:
        safe_link = item.link if item.link and item.link.startswith("/") and not item.link.startswith("//") else "/notifications"
        state = "Unread" if not item.is_read else "Read"
        mark_read = (f"<form method='post' class='mt-2'><input type='hidden' name='action' value='mark_read'>"
                     f"<input type='hidden' name='notification_id' value='{item.id}'>"
                     "<button class='btn btn-sm btn-outline-success'>Mark as read</button></form>") if not item.is_read else ""
        rows.append(
            f"<article class='panel {'border-success' if not item.is_read else ''}'>"
            f"<div class='d-flex justify-content-between gap-3 flex-wrap'><div><span class='pill'>{escape(item.category)}</span> "
            f"<strong>{escape(item.title)}</strong></div><small class='muted'>{escape(item.created_at)} · {state}</small></div>"
            f"<p class='mt-3 mb-2'>{escape(item.body)}</p><a href='{escape(safe_link)}'>Open related page</a>{mark_read}</article>"
        )
    mark_all = ("<form method='post' class='mb-3'><input type='hidden' name='action' value='mark_all_read'>"
                "<button class='btn btn-outline-success'>Mark all as read</button></form>") if unread_count else ""
    empty_state = "<section class='panel muted'>You have no notifications yet.</section>"
    body = ("<section class='panel'><h1><span class='accent'>Notifications</span></h1>"
            "<p class='muted'>Attendance alerts, fee requests, behavior reports, academic notices, and school announcements.</p>"
            f"<p>{unread_count} unread</p>{mark_all}</section>"
            f"{''.join(rows) or empty_state}")
    return page("Notifications", body, "dashboard")


def register_routes(app):
    app.add_url_rule('/notifications', endpoint='notifications',
                     view_func=permission_for('notifications')(notifications), methods=['GET', 'POST'])
