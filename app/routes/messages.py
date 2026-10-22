import hashlib
import hmac
import json
import secrets

from flask import abort, current_app, flash, redirect, request, session, url_for
from flask_login import current_user, login_required
from markupsafe import escape

from app.extensions import db
from app.models import (
    MessageAuditEvent,
    MessageConversation,
    MessageParticipant,
    MessagePermission,
    MessageReport,
    OfficialNotice,
    OfficialNoticeAuditEvent,
    OfficialNoticePermission,
    SchoolMessage,
    Setting,
    User,
)
from app.utils.constants import ROLE_ACCESS
from app.utils.decorators import permission_for
from app.utils.helpers import now_string, teacher_assigned_classes
from app.utils.notifications import message_recipients, notify_users
from app.utils.rendering import page


LEADERSHIP_ROLES = {"master_admin", "head_teacher"}
OFFICE_ROLES = LEADERSHIP_ROLES | {"secretary"}
APPROVER_ROLES = LEADERSHIP_ROLES
AUDIT_ROLES = APPROVER_ROLES | {"auditor"}
CLASS_SUPPORT_ROLES = {"discipline", "patron", "matron"}
MESSAGE_ACCESS_ROLES = ROLE_ACCESS["messages"]
MESSAGE_SEND_ROLES = set(MESSAGE_ACCESS_ROLES) - {"auditor"}
MESSAGE_RECIPIENT_ROLES = set(MESSAGE_SEND_ROLES)
GLOBAL_BROADCAST_ROLES = OFFICE_ROLES
STAFF_MESSAGE_ROLES = MESSAGE_SEND_ROLES - {"student", "parent"}
MAX_SUBJECT_LENGTH = 160
MAX_BODY_LENGTH = 10000
MAX_REPORT_LENGTH = 1000

ROLE_LABELS = {
    "master_admin": "Super Admin",
    "head_teacher": "School Head / Principal",
    "secretary": "School Secretary",
    "teacher": "Teacher",
    "student": "Student",
    "parent": "Parent / Guardian",
    "accountant": "Accountant",
    "librarian": "Librarian",
    "discipline": "Discipline Officer",
    "patron": "Patron",
    "matron": "Matron",
    "auditor": "Messaging Auditor",
}
ROLE_AUTHORITY = {
    "master_admin": "System access and security authority",
    "head_teacher": "School decisions within delegated authority",
    "secretary": "Authorized administrative notices",
    "teacher": "Assigned classes and authorized academic matters",
    "student": "Student communication; no authority to issue school decisions",
    "parent": "Communication about linked children",
    "accountant": "Authorized financial communications",
    "librarian": "Authorized library communications",
    "discipline": "Authorized discipline communications",
    "patron": "Authorized student-care communications",
    "matron": "Authorized student-care communications",
    "auditor": "Read-only messaging audit access; no sending or school decision authority",
}
MESSAGE_TYPE_LABELS = {
    "general": "Ordinary message",
    "official": "Legacy official message",
    "instruction": "Legacy official instruction",
    "system": "Legacy system notice",
}
NOTICE_PERMISSION_FIELDS = (
    ("can_create", "Create notices"),
    ("can_approve", "Approve notices"),
    ("can_approve_sensitive", "Approve sensitive notices"),
    ("can_publish", "Publish notices"),
    ("can_revoke", "Revoke published notices"),
)


def _role_label(role):
    return ROLE_LABELS.get(role, (role or "school account").replace("_", " ").title())


def _role_authority(role):
    return ROLE_AUTHORITY.get(role, "Authorized school account")


def _can_send_messages(user):
    if user.role not in MESSAGE_SEND_ROLES:
        return False
    override = MessagePermission.query.filter_by(user_id=user.id).first()
    return override.can_send if override else True


def _notice_permissions(user):
    if not user or not user.is_active_account:
        return None
    return OfficialNoticePermission.query.filter_by(user_id=user.id).first()


def _has_notice_permission(user, permission):
    if permission not in {field for field, _label in NOTICE_PERMISSION_FIELDS}:
        return False
    grants = _notice_permissions(user)
    return bool(grants and getattr(grants, permission, False))


def _active_users(roles=None):
    query = User.query.filter(
        User.is_active_account.is_(True),
        User.role.in_(MESSAGE_RECIPIENT_ROLES),
    )
    if roles is not None:
        roles = set(roles) & MESSAGE_RECIPIENT_ROLES
        if not roles:
            return []
        query = query.filter(User.role.in_(roles))
    return query.order_by(User.full_name).all()


def _users_by_ids(user_ids):
    if not user_ids:
        return []
    return User.query.filter(
        User.id.in_(set(user_ids)),
        User.is_active_account.is_(True),
        User.role.in_(MESSAGE_RECIPIENT_ROLES),
    ).order_by(User.full_name).all()


def _class_keys(class_names):
    return {name.strip().casefold() for name in class_names if name and name.strip()}


def _students_in_classes(class_names):
    keys = _class_keys(class_names)
    if not keys:
        return []
    return User.query.filter(
        User.role == "student",
        User.is_active_account.is_(True),
        db.func.lower(User.class_name).in_(keys),
    ).order_by(User.full_name).all()


def _teachers_for_classes(class_names):
    keys = _class_keys(class_names)
    if not keys:
        return []
    teachers = User.query.filter_by(role="teacher", is_active_account=True).all()
    return [
        teacher for teacher in teachers
        if keys.intersection(_class_keys(teacher_assigned_classes(teacher)))
    ]


def _support_staff_for_classes(class_names):
    keys = _class_keys(class_names)
    if not keys:
        return []
    return User.query.filter(
        User.role.in_(CLASS_SUPPORT_ROLES),
        User.is_active_account.is_(True),
        db.func.lower(User.class_name).in_(keys),
    ).order_by(User.full_name).all()


def _children_for_parent(parent):
    return User.query.filter_by(
        parent_id=parent.id,
        role="student",
        is_active_account=True,
    ).order_by(User.full_name).all()


def _allowed_user_options(sender):
    """Return active accounts that this sender is currently authorized to contact."""
    target_ids = set()

    if sender.role in OFFICE_ROLES:
        target_ids.update(user.id for user in _active_users())
    elif sender.role == "teacher":
        classes = teacher_assigned_classes(sender)
        students = _students_in_classes(classes)
        target_ids.update(student.id for student in students)
        target_ids.update(student.parent_id for student in students if student.parent_id)
        target_ids.update(user.id for user in _teachers_for_classes(classes))
        target_ids.update(user.id for user in _support_staff_for_classes(classes))
        target_ids.update(user.id for user in _active_users(OFFICE_ROLES))
    elif sender.role == "student":
        classes = {sender.class_name} if sender.class_name else set()
        target_ids.update(peer.id for peer in _students_in_classes(classes))
        target_ids.update(user.id for user in _teachers_for_classes(classes))
        target_ids.update(user.id for user in _active_users(OFFICE_ROLES))
    elif sender.role == "parent":
        children = _children_for_parent(sender)
        if children:
            classes = {child.class_name for child in children if child.class_name}
            target_ids.update(user.id for user in _teachers_for_classes(classes))
            target_ids.update(user.id for user in _support_staff_for_classes(classes))
            target_ids.update(user.id for user in _active_users(OFFICE_ROLES | {"accountant"}))
    elif sender.role == "accountant":
        target_ids.update(user.id for user in _active_users(OFFICE_ROLES | {"accountant", "parent"}))
    elif sender.role == "librarian":
        target_ids.update(user.id for user in _active_users(OFFICE_ROLES | {"student", "parent"}))
    elif sender.role in CLASS_SUPPORT_ROLES:
        classes = {sender.class_name} if sender.class_name else set()
        students = _students_in_classes(classes)
        target_ids.update(student.id for student in students)
        target_ids.update(student.parent_id for student in students if student.parent_id)
        target_ids.update(user.id for user in _active_users(OFFICE_ROLES))

    target_ids.discard(sender.id)
    return _users_by_ids(target_ids)


def _allowed_message_types(sender):
    # Official Notices use the separate /official-notices workflow and table.
    return {"general"}


def _recipient_modes(sender, user_options):
    """Build server-validated recipient modes and their eligible audience values."""
    values_by_role = {}
    for user in user_options:
        values_by_role.setdefault(user.role, []).append(user)

    modes = {}
    labels = {}
    if (sender.role == "teacher" or sender.role in CLASS_SUPPORT_ROLES) and values_by_role.get("student"):
        modes["class_group"] = {"role:student"} | {
            f"user:{user.id}" for user in values_by_role["student"]
        }
        labels["class_group"] = "My assigned class group"
    if values_by_role.get("parent") and sender.role not in {"student", "parent"}:
        modes["linked_parent"] = {
            f"user:{user.id}" for user in values_by_role["parent"]
        }
        labels["linked_parent"] = "Linked parent or guardian"

    staff_audiences = {
        f"user:{user.id}" for user in user_options if user.role in STAFF_MESSAGE_ROLES
    }
    if staff_audiences:
        modes["staff"] = staff_audiences
        labels["staff"] = "Authorized staff member"

    if sender.role in GLOBAL_BROADCAST_ROLES:
        school_audiences = {"all"} | {f"role:{role}" for role in values_by_role}
        modes["school"] = school_audiences
        labels["school"] = "Authorized school recipients"

    specific = {f"user:{user.id}" for user in user_options}
    if specific and sender.role in {"student", "parent"}:
        modes["specific"] = specific
        labels["specific"] = "Specific authorized recipient"
    return modes, labels, values_by_role


def _recipient_choices(user_options, values_by_role, allow_all=False):
    choices = ['<option value="">Choose recipients</option>']
    if values_by_role:
        choices.append('<optgroup label="Authorized groups">')
        for role in sorted(values_by_role):
            role_label = _role_label(role)
            choices.append(
                f'<option value="role:{escape(role)}" data-role="{escape(role)}">{escape(role_label)} group</option>'
            )
        choices.append("</optgroup>")
    choices.append('<optgroup label="Specific account">')
    for user in user_options:
        label = f"{user.full_name} ({_role_label(user.role)})"
        choices.append(
            f'<option value="user:{user.id}" data-role="{escape(user.role)}">{escape(label)}</option>'
        )
    choices.append("</optgroup>")
    if allow_all:
        choices.insert(1, '<option value="all">All active users with messaging access</option>')
    return "".join(choices)


def _audit_key():
    secret = current_app.secret_key or ""
    return secret if isinstance(secret, bytes) else str(secret).encode("utf-8")


def _audit_digest(previous_hash, actor_id, actor_name, actor_role, event_type,
                  conversation_id, message_id, details, created_at):
    canonical = json.dumps({
        "actor_id": actor_id,
        "actor_name": actor_name,
        "actor_role": actor_role,
        "event_type": event_type,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "details": details,
        "created_at": created_at,
        "previous_hash": previous_hash,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(_audit_key(), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _record_audit(actor, event_type, details, conversation_id=None, message_id=None):
    previous = MessageAuditEvent.query.order_by(MessageAuditEvent.id.desc()).first()
    previous_hash = previous.event_hash if previous else "0" * 64
    timestamp = now_string()
    details_json = json.dumps(details, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = _audit_digest(
        previous_hash, actor.id if actor else None,
        actor.full_name if actor else "System", actor.role if actor else "system",
        event_type, conversation_id, message_id, details_json, timestamp,
    )
    db.session.add(MessageAuditEvent(
        actor_id=actor.id if actor else None,
        actor_name=actor.full_name if actor else "System",
        actor_role=actor.role if actor else "system",
        event_type=event_type,
        conversation_id=conversation_id,
        message_id=message_id,
        details=details_json,
        created_at=timestamp,
        previous_hash=previous_hash,
        event_hash=digest,
    ))
    head = Setting.query.filter_by(key="message_audit_head").first()
    if head:
        head.value = digest
    else:
        db.session.add(Setting(key="message_audit_head", value=digest))


def _verify_audit_chain():
    previous_hash = "0" * 64
    records = MessageAuditEvent.query.order_by(MessageAuditEvent.id.asc()).all()
    for event in records:
        expected = _audit_digest(
            previous_hash, event.actor_id, event.actor_name, event.actor_role,
            event.event_type, event.conversation_id, event.message_id,
            event.details, event.created_at,
        )
        if event.previous_hash != previous_hash or not hmac.compare_digest(event.event_hash, expected):
            return False, records
        previous_hash = event.event_hash
        try:
            details = json.loads(event.details)
        except (TypeError, ValueError):
            return False, records
        if event.event_type in {"message_sent", "message_submitted", "message_reply_sent"}:
            message = db.session.get(SchoolMessage, event.message_id)
            if not message or not hmac.compare_digest(
                hashlib.sha256(message.body.encode("utf-8")).hexdigest(),
                details.get("body_sha256", ""),
            ):
                return False, records
            if (message.sender_id != event.actor_id
                    or message.sender_name != event.actor_name
                    or message.sender_role != event.actor_role
                    or message.conversation_id != event.conversation_id):
                return False, records
            if event.event_type != "message_reply_sent" and (
                message.subject != details.get("subject")
                or message.message_type != details.get("message_type")
            ):
                return False, records
            if event.event_type in {"message_sent", "message_submitted"}:
                expected_ids = set(details.get("recipient_ids", []))
                expected_ids.add(event.actor_id)
                existing_expected_ids = {
                    user.id for user in User.query.filter(User.id.in_(expected_ids)).all()
                }
                participant_rows = MessageParticipant.query.filter_by(
                    conversation_id=event.conversation_id
                ).all()
                actual_ids = {row.user_id for row in participant_rows}
                if actual_ids != existing_expected_ids:
                    return False, records
                conversation = db.session.get(MessageConversation, event.conversation_id)
                if (not conversation or conversation.title != message.subject
                        or conversation.created_by_id != event.actor_id
                        or conversation.created_by_name != event.actor_name
                        or conversation.created_by_role != event.actor_role):
                    return False, records
        elif event.event_type in {"instruction_approved", "instruction_rejected"}:
            message = db.session.get(SchoolMessage, event.message_id)
            expected_status = "approved" if event.event_type == "instruction_approved" else "rejected"
            if (not message or message.approval_status != expected_status
                    or message.approved_by_id != event.actor_id):
                return False, records
        elif event.event_type == "message_reported":
            report = db.session.get(MessageReport, details.get("report_id"))
            if not report or not hmac.compare_digest(
                hashlib.sha256(report.reason.encode("utf-8")).hexdigest(),
                details.get("reason_sha256", ""),
            ):
                return False, records
        elif event.event_type in {"message_report_resolved", "message_report_dismissed"}:
            report = db.session.get(MessageReport, details.get("report_id"))
            expected_status = "resolved" if event.event_type == "message_report_resolved" else "dismissed"
            if (not report or report.status != expected_status
                    or not hmac.compare_digest(
                        hashlib.sha256((report.resolution or "").encode("utf-8")).hexdigest(),
                        details.get("resolution_sha256", ""),
                    )):
                return False, records
    latest_access_events = {}
    for event in records:
        if event.event_type == "message_send_access_updated":
            details = json.loads(event.details)
            latest_access_events[details.get("target_user_id")] = details.get("can_send")
    for target_id, expected_can_send in latest_access_events.items():
        target = db.session.get(User, target_id) if target_id else None
        if target:
            permission = MessagePermission.query.filter_by(user_id=target.id).first()
            actual_can_send = permission.can_send if permission else True
            if actual_can_send != expected_can_send:
                return False, records

    anchor = Setting.query.filter_by(key="message_audit_head").first()
    if anchor and not hmac.compare_digest(anchor.value, previous_hash):
        return False, records
    if records and not anchor:
        return False, records
    return True, records


def _participants(conversation_id):
    return MessageParticipant.query.filter_by(conversation_id=conversation_id).all()


def _is_participant(conversation_id, user_id):
    return MessageParticipant.query.filter_by(
        conversation_id=conversation_id, user_id=user_id
    ).first() is not None


def _visible_conversations(user):
    participant_ids = [row.conversation_id for row in MessageParticipant.query.filter_by(user_id=user.id).all()]
    if not participant_ids:
        return []
    conversations = MessageConversation.query.filter(
        MessageConversation.id.in_(participant_ids)
    ).order_by(MessageConversation.id.desc()).all()
    visible = []
    for conversation in conversations:
        first_message = SchoolMessage.query.filter_by(conversation_id=conversation.id).order_by(SchoolMessage.id.asc()).first()
        if not first_message:
            continue
        if first_message.approval_status == "pending":
            if user.id != conversation.created_by_id and not _has_notice_permission(user, "can_approve"):
                continue
        elif first_message.approval_status == "rejected" and user.id != conversation.created_by_id:
            continue
        visible.append(conversation)
    return visible


def _conversation_latest(conversation):
    return SchoolMessage.query.filter_by(conversation_id=conversation.id).order_by(SchoolMessage.id.desc()).first()


def _can_view_conversation(conversation, user):
    if _is_participant(conversation.id, user.id):
        first_message = SchoolMessage.query.filter_by(conversation_id=conversation.id).order_by(SchoolMessage.id.asc()).first()
        if first_message and first_message.approval_status != "approved":
            if first_message.approval_status == "pending":
                return user.id == conversation.created_by_id or _has_notice_permission(user, "can_approve")
            return user.id == conversation.created_by_id
        return True
    if _has_notice_permission(user, "can_approve"):
        first_message = SchoolMessage.query.filter_by(conversation_id=conversation.id).order_by(SchoolMessage.id.asc()).first()
        if first_message and first_message.approval_status == "pending":
            return True
        return MessageReport.query.join(
            SchoolMessage, MessageReport.message_id == SchoolMessage.id
        ).filter(SchoolMessage.conversation_id == conversation.id).first() is not None
    return False


def _legacy_inbox_messages(user):
    conditions = [
        SchoolMessage.recipient_id == user.id,
        SchoolMessage.recipient_role == user.role,
        db.and_(
            SchoolMessage.recipient_role == "all",
            _sender_role_filter(OFFICE_ROLES),
        ),
        db.and_(
            SchoolMessage.recipient_id.is_(None),
            SchoolMessage.recipient_role.is_(None),
            _sender_role_filter(STAFF_MESSAGE_ROLES),
        ),
    ]
    if user.role == "parent":
        child_ids = [child.id for child in _children_for_parent(user)]
        if child_ids:
            conditions.append(db.and_(
                SchoolMessage.recipient_id.in_(child_ids),
                _sender_role_filter(STAFF_MESSAGE_ROLES),
            ))
        authorized_senders = STAFF_MESSAGE_ROLES
    else:
        authorized_senders = MESSAGE_SEND_ROLES
    return SchoolMessage.query.filter(
        SchoolMessage.conversation_id.is_(None),
        db.or_(*conditions),
        _sender_role_filter(authorized_senders),
    ).order_by(SchoolMessage.id.desc()).all()


def _sender_role_filter(roles):
    return db.or_(
        SchoolMessage.sender_role.in_(roles),
        db.and_(
            SchoolMessage.sender_role.is_(None),
            SchoolMessage.sender.has(User.role.in_(roles)),
        ),
    )


def _add_participant(conversation, user, timestamp):
    existing = MessageParticipant.query.filter_by(
        conversation_id=conversation.id, user_id=user.id
    ).first()
    if not existing:
        db.session.add(MessageParticipant(
            conversation_id=conversation.id,
            user_id=user.id,
            user_name=user.full_name,
            user_role=user.role,
            added_at=timestamp,
        ))


def _target_users(sender, audience, user_options):
    if audience == "all":
        if sender.role not in GLOBAL_BROADCAST_ROLES:
            abort(403)
        return [user for user in _active_users() if user.id != sender.id]
    if audience.startswith("role:"):
        role = audience.partition(":")[2]
        if role not in MESSAGE_RECIPIENT_ROLES or sender.role in {"student", "parent"}:
            abort(403)
        return [user for user in user_options if user.role == role]
    if audience.startswith("user:"):
        raw_id = audience.partition(":")[2]
        if not raw_id.isdigit():
            abort(400)
        target = next((user for user in user_options if user.id == int(raw_id)), None)
        if not target:
            abort(403)
        return [target]
    abort(400)


def _send_new_conversation(sender, subject, body, audience, recipient_mode,
                           message_type, user_options, mode_audiences):
    if message_type != "general" or message_type not in _allowed_message_types(sender):
        abort(403)
    if recipient_mode not in mode_audiences or audience not in mode_audiences[recipient_mode]:
        abort(403)
    targets = _target_users(sender, audience, user_options)
    if not targets:
        flash("There are no active recipients for that selection.", "warning")
        return None

    timestamp = now_string()
    conversation_type = "direct"
    if len(targets) > 1 or audience.startswith("role:") or audience == "all":
        conversation_type = "class_group" if recipient_mode == "class_group" else "group"

    requires_approval = False
    conversation = MessageConversation(
        title=subject,
        conversation_type=conversation_type,
        created_by_id=sender.id,
        created_by_name=sender.full_name,
        created_by_role=sender.role,
        created_at=timestamp,
    )
    db.session.add(conversation)
    db.session.flush()
    _add_participant(conversation, sender, timestamp)
    conversation_targets = list(targets)
    if sender.role != "student" and len(targets) == 1:
        parent_ids = {
            target.parent_id for target in targets
            if target.role == "student" and target.parent_id
        }
        for parent in _users_by_ids(parent_ids):
            if parent.role == "parent" and all(existing.id != parent.id for existing in conversation_targets):
                conversation_targets.append(parent)
    for target in conversation_targets:
        _add_participant(conversation, target, timestamp)

    target_role = audience.partition(":")[2] if audience.startswith("role:") else None
    target_id = targets[0].id if len(targets) == 1 and audience.startswith("user:") else None
    message = SchoolMessage(
        sender_id=sender.id,
        sender_name=sender.full_name,
        sender_role=sender.role,
        recipient_id=target_id,
        recipient_role=target_role or ("all" if audience == "all" else None),
        subject=subject,
        body=body,
        message_type=message_type,
        conversation_id=conversation.id,
        approval_status="pending" if requires_approval else "approved",
        created_at=timestamp,
    )
    db.session.add(message)
    db.session.flush()

    body_digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    _record_audit(sender, "message_submitted" if requires_approval else "message_sent", {
        "subject": subject,
        "message_type": message_type,
        "approval_status": message.approval_status,
        "recipient_count": len(conversation_targets),
        "recipient_ids": sorted(target.id for target in conversation_targets),
        "recipient_roles": sorted({target.role for target in conversation_targets}),
        "body_sha256": body_digest,
    }, conversation_id=conversation.id, message_id=message.id)

    if requires_approval:
        approver_ids = {user.id for user in _active_users(APPROVER_ROLES)}
        notify_users(
            approver_ids,
            "Official Instruction Approval",
            f"Approval requested: {subject}",
            f"{sender.full_name} ({_role_label(sender.role)}) submitted an official instruction for approval. Open the Messaging Hub to review it.",
            "/messages?view=review",
        )
    else:
        notify_recipients = {target.id for target in conversation_targets}
        notify_users(
            notify_recipients,
            MESSAGE_TYPE_LABELS.get(message_type, "School Message"),
            subject,
            f"From: {sender.full_name} ({_role_label(sender.role)}). Authority: {_role_authority(sender.role)}.\n\n{body}",
            f"/messages?conversation={conversation.id}",
        )

    db.session.commit()
    return conversation


def _send_reply(sender, conversation, body):
    if not _is_participant(conversation.id, sender.id):
        abort(403)
    if not _can_view_conversation(conversation, sender):
        abort(403)
    first_message = SchoolMessage.query.filter_by(conversation_id=conversation.id).order_by(SchoolMessage.id.asc()).first()
    if not first_message or first_message.approval_status != "approved":
        abort(403)
    timestamp = now_string()
    latest = _conversation_latest(conversation)
    message = SchoolMessage(
        sender_id=sender.id,
        sender_name=sender.full_name,
        sender_role=sender.role,
        subject=conversation.title,
        body=body,
        message_type="general",
        conversation_id=conversation.id,
        in_reply_to_id=latest.id if latest else first_message.id,
        approval_status="approved",
        created_at=timestamp,
    )
    db.session.add(message)
    db.session.flush()
    participant_ids = {
        row.user_id for row in _participants(conversation.id)
        if row.user_id != sender.id
    }
    _record_audit(sender, "message_reply_sent", {
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "participant_count": len(participant_ids),
    }, conversation_id=conversation.id, message_id=message.id)
    notify_users(
        participant_ids,
        "School Message Reply",
        conversation.title,
        f"New reply from {sender.full_name} ({_role_label(sender.role)}). Open the conversation to view it.",
        f"/messages?conversation={conversation.id}",
    )
    db.session.commit()


def _approve_instruction(actor, message, decision, note=""):
    if not _has_notice_permission(actor, "can_approve"):
        abort(403)
    if message.message_type != "instruction" or message.approval_status != "pending":
        abort(400)
    if actor.id == message.sender_id:
        abort(403)
    timestamp = now_string()
    accepted = decision == "approve"
    message.approval_status = "approved" if accepted else "rejected"
    message.approved_by_id = actor.id
    message.approved_by_name = actor.full_name
    message.approved_at = timestamp
    if message.conversation:
        message.conversation.conversation_type = "official_instruction" if accepted else "instruction_rejected"
    _record_audit(actor, "instruction_approved" if accepted else "instruction_rejected", {
        "subject": message.subject,
        "sender_id": message.sender_id,
        "decision_note": note[:MAX_REPORT_LENGTH],
    }, conversation_id=message.conversation_id, message_id=message.id)
    if accepted:
        recipients = {
            participant.user_id for participant in _participants(message.conversation_id)
            if participant.user_id != message.sender_id
        }
        notify_users(
            recipients,
            "Approved Official Instruction",
            message.subject,
            f"An official instruction from {message.sender_name or 'school staff'} has been approved by {actor.full_name} ({_role_label(actor.role)}). Open the Messaging Hub to read it.",
            f"/messages?conversation={message.conversation_id}",
        )
    else:
        notify_users(
            {message.sender_id},
            "Official Instruction Not Approved",
            message.subject,
            f"Your instruction was not approved by {actor.full_name}. {note[:MAX_REPORT_LENGTH]}",
            f"/messages?conversation={message.conversation_id}",
        )
    db.session.commit()


def _report_message(reporter, message, reason):
    if not message.conversation_id or not _is_participant(message.conversation_id, reporter.id):
        abort(403)
    if message.approval_status != "approved":
        abort(403)
    if MessageReport.query.filter_by(message_id=message.id, reporter_id=reporter.id).first():
        flash("You have already reported this message. Its existing report is available to school leadership for review.", "info")
        return False
    timestamp = now_string()
    report = MessageReport(
        message_id=message.id,
        reporter_id=reporter.id,
        reporter_name=reporter.full_name,
        reporter_role=reporter.role,
        reason=reason,
        created_at=timestamp,
    )
    db.session.add(report)
    db.session.flush()
    _record_audit(reporter, "message_reported", {
        "report_id": report.id,
        "reason_sha256": hashlib.sha256(reason.encode("utf-8")).hexdigest(),
    }, conversation_id=message.conversation_id, message_id=message.id)
    leaders = {user.id for user in _active_users(APPROVER_ROLES)}
    notify_users(
        leaders,
        "Message Report Submitted",
        f"A message was reported: {message.subject}",
        f"{reporter.full_name} ({_role_label(reporter.role)}) reported a message for review.",
        "/messages?view=review",
    )
    db.session.commit()
    return True


def _review_report(actor, report, decision, resolution):
    if actor.role not in APPROVER_ROLES:
        abort(403)
    if report.status != "open":
        abort(400)
    if decision not in {"resolve", "dismiss"}:
        abort(400)
    report.status = "resolved" if decision == "resolve" else "dismissed"
    report.resolution = resolution[:MAX_REPORT_LENGTH]
    report.reviewed_by_id = actor.id
    report.reviewed_by_name = actor.full_name
    report.reviewed_at = now_string()
    _record_audit(actor, "message_report_resolved" if decision == "resolve" else "message_report_dismissed", {
        "report_id": report.id,
        "message_id": report.message_id,
        "resolution_sha256": hashlib.sha256(report.resolution.encode("utf-8")).hexdigest(),
    }, conversation_id=report.message.conversation_id, message_id=report.message_id)
    db.session.commit()


def _csrf_token():
    return session.setdefault("messages_csrf_token", secrets.token_urlsafe(32))


def _csrf_valid():
    expected = session.get("messages_csrf_token", "")
    submitted = request.form.get("csrf_token", "")
    return bool(expected and submitted and hmac.compare_digest(expected, submitted))


def _notice_audit_digest(previous_hash, actor_id, actor_name, actor_role,
                         event_type, notice_id, details, created_at):
    canonical = json.dumps({
        "actor_id": actor_id,
        "actor_name": actor_name,
        "actor_role": actor_role,
        "event_type": event_type,
        "notice_id": notice_id,
        "details": details,
        "created_at": created_at,
        "previous_hash": previous_hash,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(_audit_key(), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _record_notice_audit(actor, event_type, details, notice=None):
    previous = OfficialNoticeAuditEvent.query.order_by(
        OfficialNoticeAuditEvent.id.desc()
    ).first()
    previous_hash = previous.event_hash if previous else "0" * 64
    timestamp = now_string()
    details_json = json.dumps(details, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    actor_id = actor.id if actor else None
    actor_name = actor.full_name if actor else "System"
    actor_role = actor.role if actor else "system"
    digest = _notice_audit_digest(
        previous_hash, actor_id, actor_name, actor_role, event_type,
        notice.id if notice else None, details_json, timestamp,
    )
    db.session.add(OfficialNoticeAuditEvent(
        notice_id=notice.id if notice else None,
        actor_id=actor_id,
        actor_name=actor_name,
        actor_role=actor_role,
        event_type=event_type,
        details=details_json,
        created_at=timestamp,
        previous_hash=previous_hash,
        event_hash=digest,
    ))


def _verify_notice_audit_chain():
    previous_hash = "0" * 64
    records = OfficialNoticeAuditEvent.query.order_by(
        OfficialNoticeAuditEvent.id.asc()
    ).all()
    for event in records:
        expected = _notice_audit_digest(
            previous_hash, event.actor_id, event.actor_name, event.actor_role,
            event.event_type, event.notice_id, event.details, event.created_at,
        )
        if event.previous_hash != previous_hash or not hmac.compare_digest(event.event_hash, expected):
            return False, records
        try:
            json.loads(event.details)
        except (TypeError, ValueError):
            return False, records
        previous_hash = event.event_hash
    return True, records


def _notice_status_label(notice):
    if notice.status == "pending_approval":
        return "Pending approval"
    if notice.status == "awaiting_sensitive_approval":
        return "Awaiting additional sensitive notice approval"
    if notice.status == "approved":
        first = notice.approved_by_name or "Authorized approver"
        if notice.is_sensitive:
            second = notice.sensitive_approved_by_name or "Second authorized approver"
            return f"Approved by {first} and {second}; awaiting publication"
        return f"Approved by {first}; awaiting publication"
    if notice.status == "published":
        publisher = notice.published_by_name or "Authorized publisher"
        return f"Published by {publisher}"
    if notice.status == "revoked":
        revoker = notice.revoked_by_name or "Authorized revoker"
        return f"Revoked by {revoker}"
    if notice.status == "rejected":
        approver = notice.approved_by_name or "Authorized approver"
        return f"Rejected by {approver}"
    return notice.status.replace("_", " ").title()


def _notice_is_visible_to(notice, user):
    grants = _notice_permissions(user)
    if notice.created_by_id == user.id:
        return True
    if notice.status == "pending_approval" and grants and grants.can_approve:
        return True
    if notice.status == "awaiting_sensitive_approval" and grants and (
        grants.can_approve or grants.can_approve_sensitive
    ):
        return True
    if notice.status == "approved" and grants and (
        grants.can_approve or grants.can_approve_sensitive or grants.can_publish
    ):
        return True
    if notice.status in {"published", "revoked"} and grants and (
        grants.can_publish or grants.can_revoke
    ):
        return True
    if notice.status not in {"published", "revoked"}:
        return False
    try:
        recipient_ids = {int(item) for item in json.loads(notice.recipient_ids_json)}
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    return user.id in recipient_ids


def _notice_action_form(csrf_token, action, notice, label, style="outline-secondary", note_label=None):
    note = (
        f"<input class='form-control form-control-sm' name='decision_note' maxlength='{MAX_REPORT_LENGTH}' "
        f"required placeholder='{escape(note_label)}'>"
        if note_label else ""
    )
    return (
        f"<form method='post' class='notice-action-form'><input type='hidden' name='csrf_token' value='{escape(csrf_token)}'>"
        f"<input type='hidden' name='notice_id' value='{notice.id}'><input type='hidden' name='action' value='{escape(action)}'>"
        f"{note}<button class='btn btn-sm btn-{escape(style)}' type='submit'>{escape(label)}</button></form>"
    )


def _render_official_notice(notice, csrf_token, user):
    creator = db.session.get(User, notice.created_by_id) if notice.created_by_id else None
    if creator:
        sender_name = creator.full_name
        sender_role = creator.role
        sender_label = f"Verified sender: {sender_name} · {_role_label(sender_role)}"
    else:
        sender_label = (
            f"Recorded sender: {notice.created_by_name} · {_role_label(notice.created_by_role)} "
            "(account unavailable)"
        )

    grants = _notice_permissions(user)
    actions = []
    if grants and grants.can_approve and notice.status == "pending_approval" and user.id != notice.created_by_id:
        actions.append(_notice_action_form(
            csrf_token, "notice_review", notice, "Approve", "success",
        ).replace("<button", "<button name='decision' value='approve'", 1))
        actions.append(_notice_action_form(
            csrf_token, "notice_review", notice, "Reject", "outline-danger", "Reason required when rejecting",
        ).replace("<button", "<button name='decision' value='reject'", 1))
    if (grants and grants.can_approve_sensitive
            and notice.status == "awaiting_sensitive_approval"
            and user.id not in {notice.created_by_id, notice.approved_by_id}):
        actions.append(_notice_action_form(
            csrf_token, "notice_sensitive_approval", notice, "Approve sensitive notice", "success",
        ))
    if grants and grants.can_publish and notice.status == "approved":
        actions.append(_notice_action_form(
            csrf_token, "notice_publish", notice, "Publish notice", "success",
        ))
    if grants and grants.can_revoke and notice.status == "published":
        actions.append(_notice_action_form(
            csrf_token, "notice_revoke", notice, "Revoke notice", "outline-danger", "Reason for revocation",
        ))

    content_visible = (
        notice.created_by_id == user.id
        or _notice_is_visible_to(notice, user)
    )
    body = str(escape(notice.body)).replace("\r\n", "\n").replace("\n", "<br>")
    sensitive_badge = (
        "<span class='badge text-bg-warning'>Sensitive · second approval required</span>"
        if notice.is_sensitive else ""
    )
    body_block = f"<div class='notice-body'>{body}</div>" if content_visible else (
        "<p class='muted'>Notice content is available after publication.</p>"
    )
    decision_note_block = (
        f"<p class='notice-decision'>Decision note: {escape(notice.decision_note)}</p>"
        if notice.decision_note else ""
    )
    return (
        "<article class='official-notice-card'>"
        f"<div class='notice-card-heading'><div><span class='pill'>{escape(_notice_status_label(notice))}</span> "
        f"{sensitive_badge}"
        f"<h3>{escape(notice.subject)}</h3></div><time>{escape(notice.created_at)}</time></div>"
        f"<p class='notice-sender'>{escape(sender_label)}</p>"
        f"<p class='notice-approval'>Approval status: {escape(_notice_status_label(notice))}</p>"
        f"{body_block}{decision_note_block}"
        f"<div class='notice-actions'>{''.join(actions)}</div></article>"
    )


def _notice_permissions_panel(csrf_token):
    if current_user.role != "master_admin":
        return ""
    users = User.query.filter_by(is_active_account=True).order_by(User.role, User.full_name).all()
    rows = []
    for user in users:
        grants = OfficialNoticePermission.query.filter_by(user_id=user.id).first()
        boxes = []
        for field, label in NOTICE_PERMISSION_FIELDS:
            checked = " checked" if grants and getattr(grants, field) else ""
            boxes.append(
                f"<label class='notice-grant'><input type='checkbox' name='{field}' value='1'{checked}>"
                f"<span>{escape(label)}</span></label>"
            )
        rows.append(
            "<tr><td>"
            f"<strong>{escape(user.full_name)}</strong><br><small>{escape(_role_label(user.role))}</small></td><td>"
            f"<form method='post' class='notice-grant-form'><input type='hidden' name='csrf_token' value='{escape(csrf_token)}'>"
            f"<input type='hidden' name='action' value='notice_grants_update'><input type='hidden' name='user_id' value='{user.id}'>"
            f"{''.join(boxes)}<button class='btn btn-sm btn-outline-success' type='submit'>Save grants</button></form></td></tr>"
        )
    return (
        "<section class='panel official-notice-permissions'><h2>Official Notice permissions</h2>"
        "<p class='muted'>Grant actions to individual accounts. Role labels are displayed for identification only and do not grant notice permissions. "
        "Super Admin accounts can assign these grants; they still need a grant to create, approve, publish, or revoke notices.</p>"
        f"<div class='table-responsive'><table class='table'><thead><tr><th>Account</th><th>Explicit grants</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>"
    )


def _notice_audit_panel():
    if current_user.role != "master_admin" and not any(
        _has_notice_permission(current_user, field) for field, _label in NOTICE_PERMISSION_FIELDS
    ):
        return ""
    valid, records = _verify_notice_audit_chain()
    chain_status = (
        "<span class='badge text-bg-success'>Integrity check passed</span>"
        if valid else "<span class='badge text-bg-danger'>Integrity check failed</span>"
    )
    rows = []
    for event in reversed(records[-200:]):
        details = escape(event.details)
        rows.append(
            f"<tr><td>{escape(event.created_at)}</td><td>{escape(event.actor_name)} ({escape(_role_label(event.actor_role))})</td>"
            f"<td>{escape(event.event_type.replace('_', ' ').title())}</td><td>{escape(event.notice_id or '—')}</td>"
            f"<td><code>{escape(event.event_hash[:16])}…</code><br><small>{details}</small></td></tr>"
        )
    return (
        "<section class='panel'><h2>Official Notice audit log</h2>"
        f"<p class='muted'>Notice creation, approvals, publication, revocation, and permission changes are recorded. {chain_status}</p>"
        f"<div class='table-responsive'><table class='table'><thead><tr><th>Time</th><th>Verified actor</th><th>Action</th><th>Notice</th><th>Record</th></tr></thead>"
        f"<tbody>{''.join(rows) or '<tr><td colspan=\'5\' class=\'muted\'>No official notice actions recorded.</td></tr>'}</tbody></table></div></section>"
    )


def _active_notice_approvers(sensitive=False):
    field = "can_approve_sensitive" if sensitive else "can_approve"
    grants = OfficialNoticePermission.query.filter_by(**{field: True}).all()
    user_ids = {grant.user_id for grant in grants}
    if not user_ids:
        return []
    return User.query.filter(
        User.id.in_(user_ids), User.is_active_account.is_(True)
    ).order_by(User.full_name).all()


def _notify_notice_reviewers(notice, sensitive=False):
    reviewers = _active_notice_approvers(sensitive=sensitive)
    notify_users(
        {reviewer.id for reviewer in reviewers if reviewer.id != notice.created_by_id},
        "Sensitive Official Notice Approval" if sensitive else "Official Notice Approval",
        f"Approval requested: {notice.subject}",
        f"{notice.created_by_name} submitted a notice for review. Open Official Notices to review it.",
        "/official-notices",
    )


def _create_official_notice(actor, subject, body, recipient_ids, sensitive):
    if not _has_notice_permission(actor, "can_create"):
        abort(403)
    if not subject or len(subject) > MAX_SUBJECT_LENGTH:
        flash(f"Subject must contain 1 to {MAX_SUBJECT_LENGTH} characters.", "danger")
        return False
    if not body or len(body) > MAX_BODY_LENGTH:
        flash(f"Notice body must contain 1 to {MAX_BODY_LENGTH} characters.", "danger")
        return False
    if not recipient_ids or len(recipient_ids) > 500:
        flash("Choose between 1 and 500 active recipients.", "danger")
        return False
    if any(not item.isdigit() for item in recipient_ids):
        abort(400)
    selected_ids = sorted({int(item) for item in recipient_ids})
    recipients = User.query.filter(
        User.id.in_(selected_ids), User.is_active_account.is_(True)
    ).all()
    if len(recipients) != len(selected_ids):
        abort(400)
    notice = OfficialNotice(
        created_by_id=actor.id,
        created_by_name=actor.full_name,
        created_by_role=actor.role,
        subject=subject,
        body=body,
        recipient_ids_json=json.dumps(selected_ids, separators=(",", ":")),
        is_sensitive=bool(sensitive),
        status="pending_approval",
        created_at=now_string(),
    )
    db.session.add(notice)
    db.session.flush()
    _record_notice_audit(actor, "notice_created", {
        "subject": subject,
        "recipient_ids": selected_ids,
        "sensitive": bool(sensitive),
        "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "status": notice.status,
    }, notice)
    _notify_notice_reviewers(notice)
    db.session.commit()
    return True


def _review_official_notice(actor, notice, decision, note):
    if not _has_notice_permission(actor, "can_approve"):
        abort(403)
    if notice.status != "pending_approval" or actor.id == notice.created_by_id:
        abort(403)
    if decision not in {"approve", "reject"}:
        abort(400)
    if decision == "reject" and (not note or len(note) > MAX_REPORT_LENGTH):
        flash("A rejection reason of 1 to 1,000 characters is required.", "danger")
        return False

    notice.approved_by_id = actor.id
    notice.approved_by_name = actor.full_name
    notice.approved_by_role = actor.role
    notice.approved_at = now_string()
    if decision == "reject":
        notice.status = "rejected"
        notice.decision_note = note
        event_type = "notice_rejected"
        notify_users(
            {notice.created_by_id}, "Official Notice Not Approved", notice.subject,
            f"Your notice was not approved by {actor.full_name}. {note}", "/official-notices",
        )
    elif notice.is_sensitive:
        notice.status = "awaiting_sensitive_approval"
        event_type = "notice_approved_pending_sensitive_review"
        _notify_notice_reviewers(notice, sensitive=True)
    else:
        notice.status = "approved"
        event_type = "notice_approved"
    _record_notice_audit(actor, event_type, {
        "decision": decision,
        "status": notice.status,
        "note": note[:MAX_REPORT_LENGTH],
        "sensitive": notice.is_sensitive,
    }, notice)
    db.session.commit()
    return True


def _approve_sensitive_official_notice(actor, notice):
    if not _has_notice_permission(actor, "can_approve_sensitive"):
        abort(403)
    if (not notice.is_sensitive or notice.status != "awaiting_sensitive_approval"
            or actor.id in {notice.created_by_id, notice.approved_by_id}):
        abort(403)
    notice.sensitive_approved_by_id = actor.id
    notice.sensitive_approved_by_name = actor.full_name
    notice.sensitive_approved_by_role = actor.role
    notice.sensitive_approved_at = now_string()
    notice.status = "approved"
    _record_notice_audit(actor, "notice_sensitive_approval_granted", {
        "status": notice.status,
        "first_approver_id": notice.approved_by_id,
        "sensitive": True,
    }, notice)
    db.session.commit()


def _publish_official_notice(actor, notice):
    if not _has_notice_permission(actor, "can_publish"):
        abort(403)
    if notice.status != "approved":
        abort(403)
    if notice.is_sensitive and (
        not notice.sensitive_approved_by_id
        or notice.sensitive_approved_by_id == notice.approved_by_id
        or notice.sensitive_approved_by_id == notice.created_by_id
    ):
        abort(403)
    notice.status = "published"
    notice.published_by_id = actor.id
    notice.published_by_name = actor.full_name
    notice.published_by_role = actor.role
    notice.published_at = now_string()
    recipient_ids = {int(item) for item in json.loads(notice.recipient_ids_json)}
    _record_notice_audit(actor, "notice_published", {
        "status": notice.status,
        "sensitive": notice.is_sensitive,
        "recipient_ids": sorted(recipient_ids),
    }, notice)
    notify_users(
        recipient_ids, "Official School Notice", notice.subject,
        f"Official notice from {notice.created_by_name} ({_role_label(notice.created_by_role)}). "
        f"Published by {actor.full_name}. Open Official Notices to read it.",
        "/official-notices",
    )
    db.session.commit()


def _revoke_official_notice(actor, notice, note):
    if not _has_notice_permission(actor, "can_revoke"):
        abort(403)
    if notice.status != "published":
        abort(403)
    if not note or len(note) > MAX_REPORT_LENGTH:
        flash("A revocation reason of 1 to 1,000 characters is required.", "danger")
        return False
    notice.status = "revoked"
    notice.revoked_by_id = actor.id
    notice.revoked_by_name = actor.full_name
    notice.revoked_by_role = actor.role
    notice.revoked_at = now_string()
    notice.decision_note = note
    recipient_ids = {int(item) for item in json.loads(notice.recipient_ids_json)}
    _record_notice_audit(actor, "notice_revoked", {
        "status": notice.status,
        "reason": note,
        "recipient_ids": sorted(recipient_ids),
    }, notice)
    notify_users(
        recipient_ids, "Official Notice Revoked", notice.subject,
        f"An official notice was revoked by {actor.full_name}. Reason: {note}",
        "/official-notices",
    )
    db.session.commit()
    return True


def official_notices():
    if request.method == "POST":
        if not _csrf_valid():
            abort(400)
        action = request.form.get("action", "").strip()
        if action == "notice_grants_update":
            # Super Admin manages the grant registry but gains no notice action by role.
            if current_user.role != "master_admin":
                abort(403)
            target = User.query.filter_by(
                id=request.form.get("user_id", type=int), is_active_account=True
            ).first()
            if not target:
                abort(404)
            for field, _label in NOTICE_PERMISSION_FIELDS:
                if any(value != "1" for value in request.form.getlist(field)):
                    abort(400)
            grants = OfficialNoticePermission.query.filter_by(user_id=target.id).first()
            if not grants:
                grants = OfficialNoticePermission(user_id=target.id)
                db.session.add(grants)
            changed = {}
            for field, _label in NOTICE_PERMISSION_FIELDS:
                value = request.form.get(field) == "1"
                setattr(grants, field, value)
                changed[field] = value
            grants.updated_by_id = current_user.id
            grants.updated_by_name = current_user.full_name
            grants.updated_at = now_string()
            _record_notice_audit(current_user, "notice_permissions_updated", {
                "target_user_id": target.id,
                "target_user_name": target.full_name,
                "grants": changed,
            })
            db.session.commit()
            flash(f"Official Notice permissions saved for {target.full_name}.", "success")
            return redirect(url_for("official_notices"))

        if action == "notice_create":
            if not _has_notice_permission(current_user, "can_create"):
                abort(403)
            created = _create_official_notice(
                current_user,
                request.form.get("subject", "").strip(),
                request.form.get("body", "").strip(),
                request.form.getlist("recipient_ids"),
                request.form.get("is_sensitive") == "1",
            )
            if created:
                flash("Official Notice submitted for approval. Recipients will see it after approval and publication.", "success")
                return redirect(url_for("official_notices"))
        elif action in {
            "notice_review", "notice_sensitive_approval", "notice_publish", "notice_revoke",
        }:
            notice = db.session.get(OfficialNotice, request.form.get("notice_id", type=int))
            if not notice:
                abort(404)
            note = request.form.get("decision_note", "").strip()
            if action == "notice_review":
                saved = _review_official_notice(
                    current_user, notice, request.form.get("decision", ""), note,
                )
                if saved:
                    flash("Notice review recorded in the audit log.", "success")
            elif action == "notice_sensitive_approval":
                _approve_sensitive_official_notice(current_user, notice)
                flash("Additional sensitive notice approval recorded.", "success")
            elif action == "notice_publish":
                _publish_official_notice(current_user, notice)
                flash("Official Notice published to its selected recipients.", "success")
            else:
                saved = _revoke_official_notice(current_user, notice, note)
                if saved:
                    flash("Official Notice revoked and the recipients were notified.", "success")
            return redirect(url_for("official_notices"))
        else:
            abort(400)

    csrf_token = _csrf_token()
    grants = _notice_permissions(current_user)
    active_users = User.query.filter_by(is_active_account=True).order_by(User.role, User.full_name).all()
    visible_notices = [
        notice for notice in OfficialNotice.query.order_by(OfficialNotice.id.desc()).all()
        if _notice_is_visible_to(notice, current_user)
    ]
    notice_cards = "".join(
        _render_official_notice(notice, csrf_token, current_user)
        for notice in visible_notices
    ) or "<p class='muted'>No official notices are available to this account.</p>"

    compose_form = ""
    if grants and grants.can_create:
        recipient_options = "".join(
            f"<option value='{user.id}'>{escape(user.full_name)} ({escape(_role_label(user.role))})</option>"
            for user in active_users
        )
        compose_form = (
            "<section class='panel'><h2>Create Official Notice</h2>"
            f"<form method='post' class='official-notice-compose'><input type='hidden' name='csrf_token' value='{escape(csrf_token)}'>"
            "<input type='hidden' name='action' value='notice_create'>"
            f"<label class='form-label' for='notice-recipients'>Recipients</label><select class='form-select' id='notice-recipients' name='recipient_ids' multiple required size='6'>{recipient_options}</select>"
            "<small class='muted'>Select one or more active accounts. Recipients can read a notice only after it is published.</small>"
            f"<label class='form-label mt-3' for='notice-subject'>Subject</label><input class='form-control' id='notice-subject' name='subject' maxlength='{MAX_SUBJECT_LENGTH}' required>"
            f"<label class='form-label mt-3' for='notice-body'>Notice</label><textarea class='form-control' id='notice-body' name='body' rows='5' maxlength='{MAX_BODY_LENGTH}' required></textarea>"
            "<label class='notice-sensitive mt-3'><input type='checkbox' name='is_sensitive' value='1'>"
            "<span>Sensitive notice — requires a second approval from a different user with sensitive notice approval permission.</span></label>"
            "<button class='btn btn-accent mt-3' type='submit'>Submit for approval</button></form></section>"
        )
    elif not visible_notices and not grants:
        compose_form = (
            "<section class='panel'><h2>Official Notices</h2>"
            "<p class='muted'>This account can read notices addressed to it. Creating, approving, publishing, and revoking notices require explicit per-account grants.</p></section>"
        )

    permission_panel = _notice_permissions_panel(csrf_token)
    audit_panel = _notice_audit_panel()
    verified_role = _role_label(current_user.role)
    body = f"""<style>
.official-notices{{max-width:1180px;margin:0 auto;display:grid;gap:16px}}
.official-notice-compose,.notice-grant-form{{display:grid;gap:9px}}
.official-notice-compose select[multiple]{{min-height:150px}}
.notice-sensitive{{display:flex;align-items:flex-start;gap:9px;border:1px solid var(--border);border-radius:10px;padding:12px}}
.notice-sensitive input{{margin-top:5px;accent-color:var(--green-700)}}
.official-notice-list{{display:grid;gap:12px}}
.official-notice-card{{border:1px solid var(--border);border-radius:14px;padding:16px;background:var(--card);overflow-wrap:anywhere}}
.notice-card-heading{{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}}
.notice-card-heading h3{{font-size:1.05rem;margin:8px 0 0}}
.notice-card-heading time,.notice-sender,.notice-approval{{font-size:.8rem;color:var(--muted)}}
.notice-body{{line-height:1.55;margin-top:10px}}
.notice-actions{{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}}
.notice-action-form{{display:flex;gap:7px;flex-wrap:wrap;align-items:center}}
.notice-action-form input{{min-width:220px}}
.notice-grant{{display:inline-flex;gap:5px;align-items:center;margin:0 12px 8px 0;font-size:.78rem}}
.notice-grant input{{accent-color:var(--green-700)}}
.notice-grant-form button{{width:max-content}}
@media(max-width:650px){{.official-notice-card{{padding:13px}}.notice-action-form{{width:100%}}.notice-action-form input{{min-width:0;flex:1}}}}
</style><div class='official-notices'>
<section class='panel'><div class='panel-heading'><div><h1>Official <span class='accent'>Notices</span></h1><p class='muted'>Separate from ordinary messages. Action authority comes from explicit per-account grants.</p></div><span class='pill'>Signed in · {escape(current_user.full_name)} · {escape(verified_role)}</span></div></section>
{compose_form}
<section class='panel'><h2>Notice records</h2><div class='official-notice-list'>{notice_cards}</div></section>
{permission_panel}{audit_panel}</div>"""
    return page("Official Notices", body, "teacher")


def _permission_management_panel():
    if current_user.role != "master_admin":
        return ""
    users = User.query.filter(
        User.role.in_(MESSAGE_SEND_ROLES)
    ).order_by(User.role, User.full_name).all()
    rows = []
    for user in users:
        override = MessagePermission.query.filter_by(user_id=user.id).first()
        allowed = override.can_send if override else True
        state = "Enabled" if allowed else "Revoked"
        source = "Admin override" if override else "Role policy"
        if user.id == current_user.id:
            control = "<span class='muted'>Current account protected</span>"
        else:
            next_value = "0" if allowed else "1"
            action_label = "Revoke sending" if allowed else "Restore role access"
            style = "outline-danger" if allowed else "outline-success"
            control = (
                f"<form method='post'><input type='hidden' name='csrf_token' value='{escape(_csrf_token())}'>"
                "<input type='hidden' name='action' value='permission_update'>"
                f"<input type='hidden' name='user_id' value='{user.id}'><input type='hidden' name='can_send' value='{next_value}'>"
                f"<button class='btn btn-sm btn-{style}' type='submit'>{action_label}</button></form>"
            )
        rows.append(
            f"<tr><td>{escape(user.full_name)}</td><td>{escape(_role_label(user.role))}</td>"
            f"<td>{escape(state)} <small class='muted'>({escape(source)})</small></td><td>{control}</td></tr>"
        )
    return f"""<section class='panel'><h2>Messaging access</h2>
<p class='muted'>The existing role policy defines who may send and which recipients they can reach. Super Admin can revoke an individual account’s sending access or restore its role access. This does not change who may receive messages.</p>
<div class='table-responsive'><table class='table'><thead><tr><th>Account</th><th>Role</th><th>Send access</th><th>Action</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></section>"""


def _approval_queue():
    if current_user.role not in APPROVER_ROLES:
        return ""
    reports = MessageReport.query.filter_by(status="open").order_by(MessageReport.id.desc()).limit(200).all()
    report_rows = []
    for report in reports:
        message = report.message
        link = url_for("messages", conversation=message.conversation_id) if message and message.conversation_id else url_for("messages")
        report_rows.append(
            f"<tr><td>{escape(report.created_at)}</td><td>{escape(report.reporter_name)} ({escape(_role_label(report.reporter_role))})</td>"
            f"<td><a href='{escape(link)}'>{escape(message.subject if message else 'Unavailable message')}</a><br>{escape(report.reason)}</td><td>"
            f"<form method='post' class='d-flex flex-column gap-2'><input type='hidden' name='csrf_token' value='{escape(_csrf_token())}'>"
            f"<input type='hidden' name='action' value='report_review'><input type='hidden' name='report_id' value='{report.id}'>"
            "<input class='form-control form-control-sm' name='resolution' maxlength='1000' required placeholder='Explain the review outcome'>"
            "<div class='d-flex gap-2'><button class='btn btn-sm btn-success' name='decision' value='resolve'>Resolve</button>"
            "<button class='btn btn-sm btn-outline-secondary' name='decision' value='dismiss'>Dismiss</button></div></form></td></tr>"
        )
    return f"""
<section class='panel' id='message-review'><h2>Approval and report review</h2>
<p class='muted'>Ordinary-message reports are reviewed here. Official Notices use the separate workflow and explicit per-user grants.</p>
<p class='muted'>Legacy official instructions are read-only. New official directions must use the Official Notices workflow.</p>
<h3 class='h6 mt-4'>Open reports</h3>
<div class='table-responsive'><table class='table'><thead><tr><th>Reported</th><th>Reporter</th><th>Message and reason</th><th>Review</th></tr></thead><tbody>{''.join(report_rows) or '<tr><td colspan="4" class="muted">No open message reports.</td></tr>'}</tbody></table></div></section>"""


def _audit_panel():
    if current_user.role not in AUDIT_ROLES:
        return ""
    valid, records = _verify_audit_chain()
    status = (
        "<span class='badge text-bg-success'>Integrity check passed</span>"
        if valid else
        "<span class='badge text-bg-danger'>Integrity check failed — preserve records for investigation</span>"
    )
    visible_records = records[-200:]
    if current_user.role == "auditor":
        official_messages = SchoolMessage.query.filter(
            SchoolMessage.message_type.in_({"official", "instruction", "system"})
        ).all()
        official_message_ids = {message.id for message in official_messages}
        official_conversation_ids = {
            message.conversation_id for message in official_messages if message.conversation_id
        }
        visible_records = [
            event for event in visible_records
            if event.message_id in official_message_ids
            or event.conversation_id in official_conversation_ids
        ]
    rows = []
    for event in reversed(visible_records):
        detail_content = escape(event.details)
        audited_message = db.session.get(SchoolMessage, event.message_id) if event.message_id else None
        if audited_message and audited_message.message_type in {"official", "instruction", "system"}:
            body = str(escape(audited_message.body)).replace("\r\n", "\n").replace("\n", "<br>")
            member_rows = [
                f"<li>{escape(member.user_name)} ({escape(_role_label(member.user_role))})</li>"
                for member in _participants(audited_message.conversation_id)
            ] if audited_message.conversation_id else []
            detail_content += (
                f"<details class='mt-2'><summary>Open official record</summary>"
                f"<p class='mt-2'><strong>{escape(audited_message.subject)}</strong></p>"
                f"<div>{body}</div><strong>Conversation participants</strong>"
                f"<ul>{''.join(member_rows) or '<li>Recipients recorded in audit details</li>'}</ul></details>"
            )
        rows.append(
            f"<tr><td>{escape(event.created_at)}</td><td>{escape(event.actor_name)} ({escape(_role_label(event.actor_role))})</td>"
            f"<td>{escape(event.event_type.replace('_', ' ').title())}</td><td>{detail_content}</td>"
            f"<td><code>{escape(event.event_hash[:16])}…</code></td></tr>"
        )
    return f"""<section class='panel'><h2>Messaging audit trail</h2>
<p class='muted'>Each communication and moderation action is linked to the previous signed event. {status}</p>
<div class='table-responsive'><table class='table'><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Recorded details</th><th>Hash</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="5" class="muted">No messaging actions recorded yet.</td></tr>'}</tbody></table></div></section>"""


def _render_conversation_list(conversations, selected_id):
    rows = []
    for conversation in conversations:
        latest = _conversation_latest(conversation)
        if not latest:
            continue
        selected_class = " active" if conversation.id == selected_id else ""
        kind = conversation.conversation_type.replace("_", " ").title()
        latest_sender = latest.sender_name or "School account"
        preview = latest.body.replace("\n", " ")[:105]
        rows.append(
            f"<a class='conversation-row{selected_class}' href='{escape(url_for('messages', conversation=conversation.id))}'>"
            f"<span class='conversation-icon'><i class='bi bi-chat-left-text' aria-hidden='true'></i></span>"
            f"<span class='conversation-copy'><strong>{escape(conversation.title)}</strong>"
            f"<small>{escape(kind)} · {escape(latest_sender)}: {escape(preview)}</small></span>"
            f"<span class='conversation-date'>{escape(latest.created_at)}</span></a>"
        )
    return "".join(rows) or '<p class="muted mb-0">No conversations yet. Start one using the form.</p>'


def _render_thread(conversation, user):
    if not conversation:
        return "<div class='conversation-empty'><i class='bi bi-chat-square-text'></i><p>Select a conversation to read or reply.</p></div>"
    if not _can_view_conversation(conversation, user):
        abort(403)
    messages = SchoolMessage.query.filter_by(conversation_id=conversation.id).order_by(SchoolMessage.id.asc()).all()
    visible_messages = []
    for message in messages:
        if message.approval_status != "approved" and user.id != conversation.created_by_id and user.role not in APPROVER_ROLES:
            continue
        sender_role = message.sender_role or (message.sender.role if message.sender else "")
        sender_name = message.sender_name or (message.sender.full_name if message.sender else "Unavailable account")
        own = message.sender_id == user.id
        badge = MESSAGE_TYPE_LABELS.get(message.message_type or "general", "Ordinary message")
        status_badge = (
            f" <span class='badge text-bg-warning'>{escape(message.approval_status.title())}</span>"
            if message.approval_status != "approved" else ""
        )
        body = str(escape(message.body)).replace("\r\n", "\n").replace("\n", "<br>")
        report_form = ""
        if not own and message.approval_status == "approved" and message.conversation_id and _is_participant(message.conversation_id, user.id):
            already_reported = MessageReport.query.filter_by(message_id=message.id, reporter_id=user.id).first()
            if not already_reported:
                report_form = (
                    f"<form method='post' class='report-message-form'><input type='hidden' name='csrf_token' value='{escape(_csrf_token())}'>"
                    f"<input type='hidden' name='action' value='report_message'><input type='hidden' name='message_id' value='{message.id}'>"
                    "<label class='visually-hidden' for='report-reason'>Report this message</label>"
                    "<input id='report-reason' class='form-control form-control-sm' name='reason' maxlength='1000' required placeholder='Report suspicious or conflicting message'>"
                    "<button class='btn btn-sm btn-outline-danger' type='submit'>Report</button></form>"
                )
            else:
                report_form = "<small class='muted'>Reported for review</small>"
        review_controls = ""
        if message.approval_status == "pending" and user.role in APPROVER_ROLES and user.id != message.sender_id:
            review_controls = (
                f"<form method='post' class='mt-3'><input type='hidden' name='csrf_token' value='{escape(_csrf_token())}'>"
                "<input type='hidden' name='action' value='instruction_review'>"
                f"<input type='hidden' name='message_id' value='{message.id}'>"
                "<label class='form-label small'>Approval note</label><input class='form-control form-control-sm mb-2' name='review_note' maxlength='1000'>"
                "<button class='btn btn-sm btn-success me-2' name='decision' value='approve'>Approve and publish</button>"
                "<button class='btn btn-sm btn-outline-danger' name='decision' value='reject'>Reject</button></form>"
            )
        visible_messages.append(
            f"<article class='message-bubble{' own' if own else ''}'><div class='message-meta'><strong>{escape(sender_name)}</strong>"
            f"<span>{escape(_role_label(sender_role))} · {escape(message.created_at)}<br>Authority: {escape(_role_authority(sender_role))}</span></div>"
            f"<div class='message-kind'>{escape(badge)}{status_badge}</div><h3>{escape(message.subject)}</h3>"
            f"<div class='message-body'>{body}</div>{review_controls}{report_form}</article>"
        )
    if not visible_messages:
        visible_messages.append("<p class='muted'>This conversation has no messages visible to your account.</p>")
    first = messages[0] if messages else None
    reply_form = ""
    if first and first.approval_status == "approved" and _is_participant(conversation.id, user.id):
        reply_form = (
            f"<form method='post' class='reply-form'><input type='hidden' name='csrf_token' value='{escape(_csrf_token())}'>"
            "<input type='hidden' name='action' value='reply'>"
            f"<input type='hidden' name='conversation_id' value='{conversation.id}'>"
            "<label for='reply-body'>Reply</label><textarea id='reply-body' class='form-control' name='body' rows='3' maxlength='10000' required placeholder='Write a reply to this conversation'></textarea>"
            "<button class='btn btn-accent mt-2' type='submit'>Send reply</button></form>"
        )
    return f"<div class='thread-heading'><div><span class='pill'>{escape(conversation.conversation_type.replace('_', ' ').title())}</span><h2>{escape(conversation.title)}</h2></div></div><div class='thread-messages'>{''.join(visible_messages)}</div>{reply_form}"


def _legacy_rows(messages):
    rows = []
    for message in messages:
        sender = message.sender
        sender_name = message.sender_name or (sender.full_name if sender else "Unavailable account")
        role = message.sender_role or (sender.role if sender else "")
        body = str(escape(message.body)).replace("\r\n", "\n").replace("\n", "<br>")
        label = MESSAGE_TYPE_LABELS.get(message.message_type or "general", "Ordinary message")
        rows.append(
            f"<article class='message-bubble'><div class='message-meta'><strong>{escape(sender_name)}</strong>"
            f"<span>{escape(_role_label(role))} · {escape(message.created_at)}</span></div>"
            f"<div class='message-kind'>{escape(label)}</div><h3>{escape(message.subject)}</h3><div class='message-body'>{body}</div></article>"
        )
    return "".join(rows) or '<p class="muted">No earlier messages are available.</p>'


def messages():
    can_send = _can_send_messages(current_user)
    user_options = _allowed_user_options(current_user) if can_send else []
    modes, mode_labels, values_by_role = _recipient_modes(current_user, user_options)
    if request.method == "POST":
        if current_user.role not in MESSAGE_SEND_ROLES:
            abort(403)
        if not _csrf_valid():
            abort(400)
        action = request.form.get("action", "send").strip()
        if action == "permission_update":
            if current_user.role != "master_admin":
                abort(403)
            target = db.session.get(User, request.form.get("user_id", type=int))
            submitted_permission = request.form.get("can_send", "")
            if not target or target.role not in MESSAGE_SEND_ROLES or submitted_permission not in {"0", "1"}:
                abort(400)
            if target.id == current_user.id:
                abort(403)
            permission = MessagePermission.query.filter_by(user_id=target.id).first()
            if not permission:
                permission = MessagePermission(user_id=target.id)
                db.session.add(permission)
            permission.can_send = submitted_permission == "1"
            permission.updated_by_id = current_user.id
            permission.updated_by_name = current_user.full_name
            permission.updated_at = now_string()
            _record_audit(current_user, "message_send_access_updated", {
                "target_user_id": target.id,
                "target_user_name": target.full_name,
                "can_send": permission.can_send,
            })
            db.session.commit()
            flash(f"Messaging send access {'enabled' if permission.can_send else 'revoked'} for {target.full_name}.", "success")
            return redirect(url_for("messages", view="review"))
        elif action == "send":
            if not can_send:
                abort(403)
            subject = request.form.get("subject", "").strip()
            body = request.form.get("body", "").strip()
            audience = request.form.get("audience", "").strip()
            recipient_mode = request.form.get("recipient_mode", "").strip()
            message_type = request.form.get("message_type", "general").strip()
            if not subject or not body:
                flash("Subject and message body are required.", "danger")
            elif len(subject) > MAX_SUBJECT_LENGTH:
                flash(f"Subject must be {MAX_SUBJECT_LENGTH} characters or fewer.", "danger")
            elif len(body) > MAX_BODY_LENGTH:
                flash(f"Message body must be {MAX_BODY_LENGTH} characters or fewer.", "danger")
            elif message_type not in _allowed_message_types(current_user):
                abort(403)
            else:
                conversation = _send_new_conversation(
                    current_user, subject, body, audience, recipient_mode,
                    message_type, user_options, modes,
                )
                if conversation:
                    if message_type == "instruction" and current_user.role not in APPROVER_ROLES:
                        flash("Instruction submitted for School Head / Super Admin approval. Recipients will see it only after approval.", "success")
                    else:
                        flash("Conversation started and message sent.", "success")
                    return redirect(url_for("messages", conversation=conversation.id))
        elif action == "reply":
            if not can_send:
                abort(403)
            try:
                conversation_id = int(request.form.get("conversation_id", "0"))
            except (TypeError, ValueError):
                abort(400)
            conversation = db.session.get(MessageConversation, conversation_id)
            body = request.form.get("body", "").strip()
            if not conversation:
                abort(404)
            if not body or len(body) > MAX_BODY_LENGTH:
                flash("Enter a reply of 1 to 10,000 characters.", "danger")
            else:
                _send_reply(current_user, conversation, body)
                flash("Reply sent.", "success")
                return redirect(url_for("messages", conversation=conversation.id))
        elif action == "report_message":
            try:
                message_id = int(request.form.get("message_id", "0"))
            except (TypeError, ValueError):
                abort(400)
            message = db.session.get(SchoolMessage, message_id)
            reason = request.form.get("reason", "").strip()
            if not message:
                abort(404)
            if not reason or len(reason) > MAX_REPORT_LENGTH:
                flash("A report reason of 1 to 1,000 characters is required.", "danger")
            else:
                created = _report_message(current_user, message, reason)
                if created:
                    flash("Report sent to school leadership for review.", "success")
                return redirect(url_for("messages", conversation=message.conversation_id))
        elif action == "instruction_review":
            try:
                message_id = int(request.form.get("message_id", "0"))
            except (TypeError, ValueError):
                abort(400)
            message = db.session.get(SchoolMessage, message_id)
            if not message:
                abort(404)
            decision = request.form.get("decision", "")
            if decision not in {"approve", "reject"}:
                abort(400)
            review_note = request.form.get("review_note", "").strip()
            if len(review_note) > MAX_REPORT_LENGTH or (decision == "reject" and not review_note):
                flash("A rejection requires a decision note of up to 1,000 characters.", "danger")
                return redirect(url_for("messages", view="review"))
            _approve_instruction(current_user, message, decision, review_note)
            flash("Decision recorded and included in the messaging audit trail.", "success")
            return redirect(url_for("messages", view="review"))
        elif action == "report_review":
            try:
                report_id = int(request.form.get("report_id", "0"))
            except (TypeError, ValueError):
                abort(400)
            report = db.session.get(MessageReport, report_id)
            if not report:
                abort(404)
            resolution = request.form.get("resolution", "").strip()
            if not resolution or len(resolution) > MAX_REPORT_LENGTH:
                flash("A review outcome of 1 to 1,000 characters is required.", "danger")
                return redirect(url_for("messages", view="review"))
            _review_report(
                current_user, report,
                request.form.get("decision", ""),
                resolution,
            )
            flash("Report review saved in the messaging audit trail.", "success")
            return redirect(url_for("messages", view="review"))
        else:
            abort(400)

    csrf_token = _csrf_token()
    conversation_list = _visible_conversations(current_user)
    selected_id_value = request.args.get("conversation", "")
    try:
        selected_id = int(selected_id_value) if selected_id_value else None
    except ValueError:
        abort(400)
    selected_conversation = None
    if selected_id:
        selected_conversation = db.session.get(MessageConversation, selected_id)
        if not selected_conversation:
            abort(404)
        if not _can_view_conversation(selected_conversation, current_user):
            abort(403)

    mode_inputs = []
    for index, (mode, label) in enumerate(mode_labels.items()):
        checked = " checked" if index == 0 else ""
        mode_inputs.append(
            f"<label class='recipient-mode'><input type='radio' name='recipient_mode' value='{escape(mode)}'{checked} required>"
            f"<span>{escape(label)}</span></label>"
        )
    recipient_options = _recipient_choices(
        user_options, values_by_role, allow_all=current_user.role in GLOBAL_BROADCAST_ROLES
    )
    message_type_options = ['<option value="general">Ordinary message</option>']
    modes_json = json.dumps({key: sorted(value) for key, value in modes.items()})

    compose_form = ""
    if can_send and user_options:
        compose_form = f"""
<section class='panel compose-panel'><div class='hub-title'><div><span class='pill'>Authenticated</span><h1>School <span class='accent'>Messaging Hub</span></h1><p class='muted'>Secure school communication · Sending as {escape(current_user.full_name)} ({escape(_role_label(current_user.role))})</p></div></div>
<form method='post' id='message-compose'><input type='hidden' name='csrf_token' value='{escape(csrf_token)}'><input type='hidden' name='action' value='send'>
<fieldset><legend>Recipient</legend><div class='recipient-modes'>{''.join(mode_inputs)}</div>
<label class='form-label mt-2' for='message-audience'>Authorized recipients</label><select class='form-select' id='message-audience' name='audience' required>{recipient_options}</select></fieldset>
<div class='row g-3 mt-1'><div class='col-md-5'><label class='form-label' for='message-type'>Message type</label><select class='form-select' id='message-type' name='message_type' required>{''.join(message_type_options)}</select></div>
<div class='col-md-7'><label class='form-label' for='message-subject'>Subject</label><input class='form-control' id='message-subject' name='subject' maxlength='{MAX_SUBJECT_LENGTH}' required></div>
<div class='col-12'><label class='form-label' for='message-body'>Message</label><textarea class='form-control' id='message-body' name='body' rows='4' maxlength='{MAX_BODY_LENGTH}' required></textarea></div>
<div class='col-12 d-flex align-items-center gap-3 flex-wrap'><button class='btn btn-accent' type='submit'>Start conversation</button><small class='muted'>Official Notices are created and reviewed in the separate Official Notices hub.</small></div></div></form></section>
<script>(function(){{const modes={modes_json};const radios=[...document.querySelectorAll('input[name="recipient_mode"]')];const audience=document.getElementById('message-audience');function update(){{const selected=radios.find(item=>item.checked)?.value;const allowed=new Set(modes[selected]||[]);let first='';[...audience.options].forEach(option=>{{if(!option.value){{option.hidden=false;option.disabled=false;return}}const visible=allowed.has(option.value);option.hidden=!visible;option.disabled=!visible;if(visible&&!first)first=option.value}});if(!allowed.has(audience.value))audience.value=first}}radios.forEach(radio=>radio.addEventListener('change',update));update()}})();</script>"""
    else:
        if can_send:
            compose_form = "<section class='panel'><h1>School <span class='accent'>Messaging Hub</span></h1><p class='muted'>No currently authorized recipients are available for your account.</p></section>"
        elif current_user.role == "auditor":
            compose_form = "<section class='panel'><h1>School <span class='accent'>Messaging Hub</span></h1><p class='muted'>This is a read-only audit account. It can review messaging records but cannot read private conversations or send messages.</p></section>"
        else:
            compose_form = "<section class='panel'><h1>School <span class='accent'>Messaging Hub</span></h1><p class='muted'>Your account can read conversations where it is a participant, but message sending has been revoked by school administration.</p></section>"

    conversations_html = _render_conversation_list(conversation_list, selected_id)
    thread_html = _render_thread(selected_conversation, current_user)
    legacy_html = _legacy_rows(_legacy_inbox_messages(current_user))
    conversation_panel = f"""<section class='panel conversation-panel'><div class='panel-heading'><div><h2>Conversations</h2><p class='muted'>Only participants can open a conversation; school leadership can inspect pending instructions and reported threads.</p></div><span class='pill'>{len(conversation_list)} conversations</span></div>
<div class='conversation-layout'><nav class='conversation-list' aria-label='Conversations'>{conversations_html}</nav><div class='conversation-thread'>{thread_html}</div></div></section>"""
    legacy_panel = f"<details class='panel legacy-panel'><summary>Earlier Inbox / Broadcasts</summary><div class='legacy-messages'>{legacy_html}</div></details>"
    review_panel = _approval_queue() if current_user.role in APPROVER_ROLES else ""
    permissions_panel = _permission_management_panel() if current_user.role == "master_admin" else ""
    audit_panel = _audit_panel() if request.args.get("view") == "audit" and current_user.role in AUDIT_ROLES else ""
    review_link = ""
    if current_user.role in APPROVER_ROLES:
        review_link = (
            f"<a class='btn btn-outline-success btn-sm' href='{escape(url_for('messages', view='review'))}#message-review'>Review approvals and reports</a> "
            f"<a class='btn btn-outline-secondary btn-sm' href='{escape(url_for('messages', view='audit'))}'>Messaging audit trail</a>"
        )
    elif current_user.role == "auditor":
        review_link = f"<a class='btn btn-outline-secondary btn-sm' href='{escape(url_for('messages', view='audit'))}'>Open read-only messaging audit trail</a>"
    review_links_panel = f"<section class='panel'>{review_link}</section>" if review_link else ""
    body = f"""<style>
.messaging-layout{{max-width:1180px;margin:0 auto}}.compose-panel,.conversation-panel{{border-radius:18px}}
.hub-title,.panel-heading{{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;flex-wrap:wrap}}
.hub-title h1{{margin:12px 0 4px}}.hub-title p,.panel-heading p{{margin-bottom:0}}
.recipient-modes{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:8px}}
.recipient-mode{{display:flex;align-items:center;gap:9px;border:1px solid var(--border);border-radius:10px;padding:11px 12px;cursor:pointer}}
.recipient-mode:has(input:checked){{border-color:var(--green-700);background:rgba(8,155,112,.07)}}
.recipient-mode input{{accent-color:var(--green-700);width:17px;height:17px;flex:0 0 auto}}
.conversation-layout{{display:grid;grid-template-columns:minmax(250px,.75fr) minmax(0,1.5fr);border:1px solid var(--border);border-radius:13px;overflow:hidden;margin-top:14px;min-height:370px}}
.conversation-list{{background:color-mix(in srgb,var(--page) 58%,var(--card));border-right:1px solid var(--border);padding:8px;overflow:auto;max-height:620px}}
.conversation-row{{display:flex;align-items:center;gap:10px;padding:12px 10px;color:var(--text);border-bottom:1px solid var(--border);border-radius:8px}}
.conversation-row:hover,.conversation-row.active{{background:var(--card);color:var(--text)}}.conversation-icon{{width:34px;height:34px;border-radius:50%;display:grid;place-items:center;background:rgba(8,155,112,.12);color:var(--green-700);flex:0 0 auto}}
.conversation-copy{{min-width:0;flex:1}}.conversation-copy strong,.conversation-copy small{{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.conversation-copy small,.conversation-date{{color:var(--muted);font-size:.7rem;margin-top:3px}}.conversation-date{{white-space:nowrap}}
.conversation-thread{{padding:18px;min-width:0;max-height:700px;overflow:auto}}.thread-heading{{border-bottom:1px solid var(--border);margin-bottom:14px;padding-bottom:12px}}.thread-heading h2{{margin:8px 0 0}}
.thread-messages{{display:flex;flex-direction:column;gap:12px}}.message-bubble{{max-width:92%;border:1px solid var(--border);background:var(--page);border-radius:13px;padding:13px 14px;overflow-wrap:anywhere}}
.message-bubble.own{{align-self:flex-end;background:rgba(8,155,112,.07)}}.message-meta{{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;font-size:.8rem}}.message-meta span{{font-size:.72rem;color:var(--muted)}}
.message-kind{{font-size:.72rem;color:var(--green-800);margin-top:7px}}.message-bubble h3{{font-size:.92rem;font-weight:750;margin:5px 0}}.message-body{{font-size:.86rem;line-height:1.55}}.report-message-form{{display:flex;gap:7px;margin-top:11px;align-items:center}}.report-message-form input{{flex:1;min-width:90px}}.reply-form{{border-top:1px solid var(--border);margin-top:16px;padding-top:14px}}
.conversation-empty{{height:100%;min-height:300px;display:grid;place-content:center;text-align:center;color:var(--muted)}}.conversation-empty i{{font-size:2rem}}.legacy-panel summary{{cursor:pointer;font-weight:750}}.legacy-messages{{display:grid;gap:10px;margin-top:14px}}
@media(max-width:760px){{.conversation-layout{{grid-template-columns:1fr}}.conversation-list{{border-right:0;border-bottom:1px solid var(--border);max-height:240px}}.conversation-thread{{max-height:none;padding:13px}}.message-bubble{{max-width:100%}}.conversation-date{{display:none}}}}
</style><div class='messaging-layout'>{compose_form}{conversation_panel}{legacy_panel}{review_links_panel}{permissions_panel}{review_panel}{audit_panel}</div>"""
    return page("Messages", body, "teacher")


def register_routes(app):
    app.add_url_rule("/messages", endpoint="messages", view_func=permission_for("messages")(messages), methods=["GET", "POST"])
    app.add_url_rule("/official-notices", endpoint="official_notices", view_func=login_required(official_notices), methods=["GET", "POST"])
