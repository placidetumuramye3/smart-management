import datetime
import hashlib
import hmac
import json

from flask import current_app

from app.extensions import db
from app.models import (
    Appointment, Meeting, MeetingActionItem, MeetingAuditEvent,
    MeetingNotificationLog, MeetingParticipant, Setting, StudentGuardian, User,
)
from app.utils.helpers import (
    get_setting, guardian_ids_for_student, now_string, teacher_assigned_classes,
    verified_student_ids,
)
from app.utils.notifications import notify_users


OFFICE_ROLES = {"master_admin", "head_teacher", "secretary"}
APPROVER_ROLES = {"master_admin", "head_teacher"}
CLASS_SUPPORT_ROLES = {"discipline", "patron", "matron"}
MEETING_ROLES = OFFICE_ROLES | {"teacher", "parent"} | CLASS_SUPPORT_ROLES
MEETING_TYPES = {"general", "class", "parent_teacher", "disciplinary", "emergency", "committee", "panel_discussion"}
MEETING_STATUSES = {"Draft", "Pending approval", "Scheduled", "Rescheduled", "Completed", "Cancelled"}
INVITATION_STATUSES = {"invited", "pending_approval", "accepted", "declined", "reschedule_requested", "revoked"}
ATTENDANCE_STATUSES = {"Present", "Absent", "Late", "Excused"}
ACTION_STATUSES = {"Not Started", "In Progress", "Completed", "Overdue", "Reviewed"}
CONFIDENTIALITY_LEVELS = {"participants", "staff_only"}


def staff_classes(user):
    if user.role == "teacher":
        return {name.casefold() for name in teacher_assigned_classes(user) if name}
    if user.role in CLASS_SUPPORT_ROLES:
        return {user.class_name.casefold()} if user.class_name else set()
    return set()


def _guardian_link(student_id, guardian_id):
    link = StudentGuardian.query.filter_by(student_id=student_id, guardian_id=guardian_id).first()
    if link:
        return link.status == "verified"
    student = db.session.get(User, student_id)
    return bool(student and student.role == "student" and student.parent_id == guardian_id)


def meeting_participant(meeting_id, user_id):
    return MeetingParticipant.query.filter_by(meeting_id=meeting_id, user_id=user_id).first()


def can_view_meeting(user, meeting):
    if not user or not meeting or user.role not in MEETING_ROLES:
        return False
    if user.role in OFFICE_ROLES:
        return True
    participant = meeting_participant(meeting.id, user.id)
    if user.role == "parent":
        if meeting.confidentiality == "staff_only":
            return False
        if meeting.status == "Draft" or meeting.approval_status == "pending":
            return False
        if not participant or participant.invitation_status not in {
            "invited", "accepted", "declined", "reschedule_requested",
        }:
            return False
        if meeting.student_id and not _guardian_link(meeting.student_id, user.id):
            return False
        if meeting.class_name:
            child_ids = verified_student_ids(user.id)
            if not any(
                student.id in child_ids and student.class_name
                and student.class_name.casefold() == meeting.class_name.casefold()
                for student in User.query.filter(User.id.in_(child_ids or {-1})).all()
            ):
                return False
        if participant.student_id and not _guardian_link(participant.student_id, user.id):
            return False
        return True
    if participant and participant.invitation_status == "revoked" and meeting.organizer_id != user.id:
        return False
    if meeting.organizer_id == user.id or (participant and participant.invitation_status != "revoked"):
        return True
    if meeting.class_name:
        return meeting.class_name.casefold() in staff_classes(user)
    if meeting.student and meeting.student.class_name:
        return meeting.student.class_name.casefold() in staff_classes(user)
    return False


def can_manage_meeting(user, meeting):
    if not can_view_meeting(user, meeting) or user.role == "parent":
        return False
    if user.role in OFFICE_ROLES:
        return True
    if user.role == "teacher":
        if meeting.organizer_id == user.id or meeting.chairperson_id == user.id or meeting.secretary_id == user.id:
            return True
        class_name = meeting.class_name or (meeting.student.class_name if meeting.student else None)
        return bool(class_name and class_name.casefold() in staff_classes(user))
    return meeting.organizer_id == user.id or meeting.chairperson_id == user.id


def visible_meetings(user):
    if user.role in OFFICE_ROLES:
        return Meeting.query.order_by(Meeting.scheduled_date.desc(), Meeting.start_time.desc()).all()
    if user.role == "parent":
        participant_ids = [row.meeting_id for row in MeetingParticipant.query.filter_by(user_id=user.id).all()]
        candidates = Meeting.query.filter(Meeting.id.in_(participant_ids or {-1})).order_by(
            Meeting.scheduled_date.desc(), Meeting.start_time.desc()
        ).all()
    else:
        classes = staff_classes(user)
        participant_ids = [row.meeting_id for row in MeetingParticipant.query.filter_by(user_id=user.id).all()]
        clauses = [Meeting.organizer_id == user.id, Meeting.id.in_(participant_ids or {-1})]
        if classes:
            clauses.append(db.func.lower(Meeting.class_name).in_(classes))
        candidates = Meeting.query.filter(db.or_(*clauses)).order_by(
            Meeting.scheduled_date.desc(), Meeting.start_time.desc()
        ).all()
    return [meeting for meeting in candidates if can_view_meeting(user, meeting)]


def _date_time_range(date_value, start_value, end_value):
    date = datetime.date.fromisoformat(date_value)
    start = datetime.datetime.combine(date, datetime.time.fromisoformat(start_value))
    end = datetime.datetime.combine(date, datetime.time.fromisoformat(end_value))
    if end <= start:
        raise ValueError("The meeting end time must be later than its start time.")
    return start, end


def detect_conflicts(date_value, start_value, end_value, location, participant_ids, exclude_id=None):
    start, end = _date_time_range(date_value, start_value, end_value)
    query = Meeting.query.filter(
        Meeting.scheduled_date == date_value,
        Meeting.status.notin_({"Cancelled", "Completed", "Draft"}),
    )
    if exclude_id:
        query = query.filter(Meeting.id != exclude_id)
    conflicts = []
    room = (location or "").strip().casefold()
    for existing in query.all():
        try:
            old_start, old_end = _date_time_range(
                existing.scheduled_date, existing.start_time, existing.end_time
            )
        except ValueError:
            continue
        overlap = start < old_end and old_start < end
        if not overlap:
            continue
        reason = None
        if room and existing.location and room == existing.location.strip().casefold():
            reason = f"Room '{existing.location}' is already booked."
        if not reason and participant_ids:
            existing_ids = {row.user_id for row in existing.participants if row.invitation_status != "revoked"}
            if participant_ids.intersection(existing_ids):
                reason = "One or more selected participants already have a meeting at that time."
        if reason:
            conflicts.append((existing, reason))

    if participant_ids:
        appointment_query = Appointment.query.filter(
            Appointment.scheduled_date == date_value,
            Appointment.status.in_({"Scheduled", "Rescheduled"}),
        )
        for appointment in appointment_query.all():
            try:
                appt_start = datetime.datetime.combine(
                    datetime.date.fromisoformat(appointment.scheduled_date),
                    datetime.time.fromisoformat(appointment.scheduled_time),
                )
            except (ValueError, TypeError):
                continue
            appt_end = appt_start + datetime.timedelta(hours=1)
            if start < appt_end and appt_start < end and ({appointment.parent_id, appointment.staff_id} & participant_ids):
                conflicts.append((appointment, "A selected participant already has a parent appointment at that time."))
    return conflicts


def record_meeting_audit(actor, meeting_id, event_type, details):
    previous = MeetingAuditEvent.query.order_by(MeetingAuditEvent.id.desc()).first()
    previous_hash = previous.event_hash if previous else "0" * 64
    created_at = now_string()
    details_json = json.dumps(details, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    canonical = json.dumps({
        "actor_id": actor.id if actor else None,
        "actor_name": actor.full_name if actor else "System",
        "actor_role": actor.role if actor else "system",
        "meeting_id": meeting_id,
        "event_type": event_type,
        "details": details_json,
        "created_at": created_at,
        "previous_hash": previous_hash,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    secret = current_app.secret_key or ""
    key = secret if isinstance(secret, bytes) else str(secret).encode("utf-8")
    event_hash = hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    event = MeetingAuditEvent(
        meeting_id=meeting_id,
        actor_id=actor.id if actor else None,
        actor_name=actor.full_name if actor else "System",
        actor_role=actor.role if actor else "system",
        event_type=event_type,
        details=details_json,
        created_at=created_at,
        previous_hash=previous_hash,
        event_hash=event_hash,
    )
    db.session.add(event)
    db.session.flush()
    anchor = Setting.query.filter_by(key="meeting_audit_head").first()
    if anchor:
        anchor.value = event_hash
    else:
        db.session.add(Setting(key="meeting_audit_head", value=event_hash))
    return event


def verify_meeting_audit_chain():
    """Verify the ordered HMAC chain and its independently stored head."""
    previous_hash = "0" * 64
    key = current_app.secret_key or ""
    key = key if isinstance(key, bytes) else str(key).encode("utf-8")
    events = MeetingAuditEvent.query.order_by(MeetingAuditEvent.id.asc()).all()
    for event in events:
        canonical = json.dumps({
            "actor_id": event.actor_id,
            "actor_name": event.actor_name,
            "actor_role": event.actor_role,
            "meeting_id": event.meeting_id,
            "event_type": event.event_type,
            "details": event.details,
            "created_at": event.created_at,
            "previous_hash": previous_hash,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        if event.previous_hash != previous_hash or not hmac.compare_digest(event.event_hash, digest):
            return False, events
        previous_hash = event.event_hash
    anchor = Setting.query.filter_by(key="meeting_audit_head").first()
    if events and (not anchor or not hmac.compare_digest(anchor.value, previous_hash)):
        return False, events
    if not events and anchor and anchor.value != "0" * 64:
        return False, events
    return True, events


def notify_meeting_users(meeting, user_ids, event_key, title, body, link=None):
    deliver_ids = set()
    for user_id in set(user_ids):
        existing = MeetingNotificationLog.query.filter_by(
            dedupe_key=event_key, user_id=user_id
        ).first()
        if existing:
            continue
        db.session.add(MeetingNotificationLog(
            meeting_id=meeting.id,
            user_id=user_id,
            dedupe_key=event_key,
            in_app_status="queued",
            external_status="delegated",
            created_at=now_string(),
        ))
        deliver_ids.add(user_id)
    if deliver_ids:
        notify_users(deliver_ids, "Meetings", title, body, link or f"/meetings/{meeting.id}")


def meeting_reminders():
    now = datetime.datetime.now()
    end = now + datetime.timedelta(hours=24)
    meetings = Meeting.query.filter(
        Meeting.status.in_({"Scheduled", "Rescheduled"}),
        Meeting.scheduled_date >= now.date().isoformat(),
        Meeting.scheduled_date <= end.date().isoformat(),
    ).all()
    sent = 0
    for meeting in meetings:
        try:
            meeting_start = datetime.datetime.combine(
                datetime.date.fromisoformat(meeting.scheduled_date),
                datetime.time.fromisoformat(meeting.start_time),
            )
        except (ValueError, TypeError):
            continue
        if not now < meeting_start <= end:
            continue
        recipients = {row.user_id for row in meeting.participants if row.invitation_status in {"invited", "accepted"}}
        key = f"reminder:{meeting.scheduled_date}:{meeting.start_time}"
        before = MeetingNotificationLog.query.filter_by(meeting_id=meeting.id, dedupe_key=key).count()
        notify_meeting_users(meeting, recipients, key, "Meeting reminder", "You have a meeting within the next 24 hours. Sign in to view its details.")
        sent += max(0, len(recipients) - before)
    today = datetime.date.today().isoformat()
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    due_actions = MeetingActionItem.query.filter(
        MeetingActionItem.deadline >= today,
        MeetingActionItem.deadline <= tomorrow,
        MeetingActionItem.status.in_({"Not Started", "In Progress"}),
    ).all()
    for action in due_actions:
        key = f"action-deadline:{action.id}:{action.deadline}"
        already_notified = MeetingNotificationLog.query.filter_by(
            meeting_id=action.meeting_id, user_id=action.assignee_id, dedupe_key=key,
        ).first()
        if already_notified:
            continue
        notify_meeting_users(
            action.meeting, {action.assignee_id}, key,
            "Meeting action deadline reminder",
            f"A meeting follow-up action is due on {action.deadline}. Sign in to review it.",
        )
        sent += 1
    db.session.commit()
    return sent


def escalate_overdue_actions():
    try:
        grace_days = min(30, max(0, int(get_setting("meeting_overdue_escalation_days", "0"))))
    except (TypeError, ValueError):
        grace_days = 0
    overdue_before = (datetime.date.today() - datetime.timedelta(days=grace_days)).isoformat()
    actions = MeetingActionItem.query.filter(
        MeetingActionItem.deadline < overdue_before,
        MeetingActionItem.status.in_({"Not Started", "In Progress", "Overdue"}),
    ).all()
    escalated = 0
    admin_ids = {user.id for user in User.query.filter(
        User.role.in_({"master_admin", "head_teacher"}), User.is_active_account.is_(True)
    ).all()}
    for action in actions:
        changed = action.status != "Overdue"
        if not changed:
            continue
        action.status = "Overdue"
        action.updated_at = now_string()
        record_meeting_audit(None, action.meeting_id, "action_overdue", {"action_id": action.id})
        recipients = admin_ids | {action.assignee_id}
        notify_meeting_users(
            action.meeting,
            recipients,
            f"overdue:{action.id}:{action.deadline}",
            "Follow-up action overdue",
            "A meeting follow-up action is overdue. Sign in to review it.",
        )
        escalated += 1
    db.session.commit()
    return escalated
