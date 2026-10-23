from app.models import User, UserNotification, ParentNotificationPreference
from app.extensions import db
from app.utils.helpers import guardian_ids_for_student, now_string
from app.utils.constants import ROLE_ACCESS
from app.services.notification_service import send_parent_alert

MESSAGE_RECIPIENT_ROLES = ROLE_ACCESS["messages"] - {"auditor"}


def notify_users(user_ids, category, title, body, link="/notifications"):
    """Queue in-app notifications and deliver configured parent alerts."""
    timestamp = now_string()
    recipients = User.query.filter(User.id.in_(set(user_ids))).all() if user_ids else []
    for recipient in recipients:
        user_id = recipient.id
        if user_id:
            db.session.add(UserNotification(user_id=user_id, category=category, title=title,
                                            body=body, link=link, created_at=timestamp))
        if recipient.role == "parent":
            preferences = ParentNotificationPreference.query.filter_by(parent_id=recipient.id).first()
            send_parent_alert(recipient, category, title, body,
                              email_enabled=preferences.email_enabled if preferences else True,
                              sms_enabled=preferences.sms_enabled if preferences else True)


def notify_student_and_parent(student, category, title, body, link="/notifications"):
    user_ids = {student.id, *guardian_ids_for_student(student.id)}
    notify_users(user_ids, category, title, body, link)


def notify_school_admins(category, title, body, link="/notifications"):
    staff_ids = [user.id for user in User.query.filter(
        User.role.in_({"master_admin", "head_teacher", "secretary"})
    ).all()]
    notify_users(staff_ids, category, title, body, link)


def message_recipients(sender, recipient_id=None, recipient_role=None):
    recipients = set()
    if recipient_id:
        target = db.session.get(User, recipient_id)
        if target and target.is_active_account:
            recipients.add(target.id)
        else:
            target = None
        if target and target.role == "student" and sender.role != "student":
            recipients.update(
                guardian.id for guardian in User.query.filter(
                    User.id.in_(guardian_ids_for_student(target.id) or {-1}),
                    User.is_active_account.is_(True), User.role == "parent",
                ).all()
            )
    if recipient_role == "all":
        recipients.update(user.id for user in User.query.filter(
            User.id != sender.id,
            User.is_active_account.is_(True),
            User.role.in_(MESSAGE_RECIPIENT_ROLES),
        ).all())
    elif recipient_role and recipient_role in MESSAGE_RECIPIENT_ROLES:
        recipients.update(user.id for user in User.query.filter_by(
            role=recipient_role, is_active_account=True
        ).all())
    if not recipient_id and not recipient_role:
        recipients.update(user.id for user in User.query.filter(
            User.id != sender.id,
            User.is_active_account.is_(True),
            User.role.in_(MESSAGE_RECIPIENT_ROLES),
        ).all())
    recipients.discard(sender.id)
    return recipients
