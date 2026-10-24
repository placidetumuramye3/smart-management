import datetime
from flask import render_template, render_template_string
from app.utils.helpers import get_school_logo_static_url

def page(title, body, theme="dashboard", **context):
    context.setdefault("school_logo_url", get_school_logo_static_url())
    content = render_template_string(body, **context)
    from flask_login import current_user
    unread_notification_count = 0
    if current_user.is_authenticated:
        from app.models import UserNotification
        unread_notification_count = UserNotification.query.filter_by(user_id=current_user.id, is_read=False).count()
    return render_template("base.html", title=title, theme=theme, content=content,
                           year=datetime.datetime.now().year,
                           unread_notification_count=unread_notification_count,
                           school_logo_url=context["school_logo_url"])
