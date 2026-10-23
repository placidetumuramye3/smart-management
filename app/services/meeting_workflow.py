import datetime

from app.extensions import db
from app.models import (
    Meeting, MeetingActionComment, MeetingActionItem, MeetingAgendaItem,
    MeetingAttendance, MeetingMinutes, MeetingMinutesRevision,
    MeetingParticipant, StudentGuardian, User,
)
from app.services.meeting_service import (
    ACTION_STATUSES, APPROVER_ROLES, ATTENDANCE_STATUSES, CLASS_SUPPORT_ROLES,
    CONFIDENTIALITY_LEVELS, INVITATION_STATUSES, MEETING_ROLES, MEETING_STATUSES,
    MEETING_TYPES, OFFICE_ROLES, can_manage_meeting, can_view_meeting,
    detect_conflicts, guardian_ids_for_student, notify_meeting_users,
    record_meeting_audit, staff_classes,
    verified_student_ids,
)
from app.utils.helpers import now_string
from app.utils.helpers import get_setting


def _items(payload, key):
    if hasattr(payload, "getlist"):
        return payload.getlist(key)
    value = payload.get(key, [])
    return value if isinstance(value, list) else [value] if value not in (None, "") else []


def _as_int(value, field_name):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        raise ValueError(f"{field_name} must be a valid account or record ID.")


def _as_bool(value):
    return value is True or str(value).lower() in {"1", "true", "yes", "on"}


def _student_for_meeting(actor, student_id, class_name):
    student = db.session.get(User, student_id) if student_id else None
    if student_id and (not student or student.role != "student" or not student.is_active_account):
        raise ValueError("Choose an active student record.")
    if actor.role in {"teacher"} | CLASS_SUPPORT_ROLES:
        authorized = staff_classes(actor)
        if student and (not student.class_name or student.class_name.casefold() not in authorized):
            raise PermissionError("You can only create student meetings within your assigned class scope.")
        if class_name and class_name.casefold() not in authorized:
            raise PermissionError("You can only create meetings for your assigned classes.")
    if student and class_name and student.class_name and student.class_name.casefold() != class_name.casefold():
        raise ValueError("The selected student does not belong to the selected class.")
    if student and not class_name:
        class_name = student.class_name
    return student, class_name


def _eligible_parent_ids(student_id=None, class_name=None):
    if student_id:
        return {student_id: guardian_ids_for_student(student_id)}
    if class_name:
        students = User.query.filter(
            User.role == "student", db.func.lower(User.class_name) == class_name.casefold(),
            User.is_active_account.is_(True),
        ).all()
        return {student.id: guardian_ids_for_student(student.id) for student in students}
    return {}


def _resolve_invitees(actor, payload, student_id, class_name, panelist_ids=None):
    mode = str(payload.get("audience_mode", "selected")).strip() or "selected"
    raw_ids = _items(payload, "participant_ids")
    try:
        parsed_ids = [int(value) for value in raw_ids]
    except (ValueError, TypeError):
        raise ValueError("Choose valid participants.")
    selected_ids = set(parsed_ids)
    if len(selected_ids) != len(parsed_ids):
        raise ValueError("Do not select the same participant more than once.")

    guardian_students = _eligible_parent_ids(student_id, class_name)
    eligible_guardian_ids = set().union(*guardian_students.values()) if guardian_students else set()
    invitees = {}

    def include(user, role=None, student_anchor=None):
        if not user or not user.is_active_account or user.role not in MEETING_ROLES:
            raise ValueError("All meeting participants must be active parent or staff accounts.")
        if user.role == "parent":
            if not (student_id or class_name) and mode != "all_parents":
                if actor.role in OFFICE_ROLES:
                    linked_ids = verified_student_ids(user.id)
                    if linked_ids:
                        student_anchor = min(linked_ids)
                    else:
                        raise PermissionError("A selected parent must have a verified student relationship.")
                else:
                    raise ValueError("Select a student or class before inviting a specific parent or guardian.")
            if (student_id or class_name) and user.id not in eligible_guardian_ids:
                raise PermissionError("A parent can only be invited to a student or class meeting through a verified guardian link.")
            anchor = student_anchor
            if not anchor and student_id:
                anchor = student_id
            invitees[user.id] = (user, role or "parent", anchor)
            return
        if actor.role not in OFFICE_ROLES:
            if user.role in OFFICE_ROLES:
                pass
            elif class_name and class_name.casefold() not in staff_classes(user):
                raise PermissionError("You can only invite staff authorized for this class.")
            elif student_id and student_id:
                student = db.session.get(User, student_id)
                if student and student.class_name and student.class_name.casefold() not in staff_classes(user) and user.role not in OFFICE_ROLES:
                    raise PermissionError("You can only invite staff authorized for this student.")
        invitees[user.id] = (user, role or "staff", student_id)

    if mode == "all_parents":
        if actor.role not in OFFICE_ROLES:
            raise PermissionError("Only school leadership or the office can invite all parent accounts.")
        if student_id or class_name:
            raise ValueError("All-parent invitations are only available for school-wide meetings.")
        for parent in User.query.filter_by(role="parent", is_active_account=True).all():
            include(parent)
    elif mode == "class_parents":
        if student_id:
            raise ValueError("Clear the specific student to invite guardians of the whole class.")
        if not class_name:
            raise ValueError("Choose a class before inviting its guardians.")
        guardian_students = _eligible_parent_ids(None, class_name)
        eligible_guardian_ids = set().union(*guardian_students.values()) if guardian_students else set()
        for student_id_for_class, parent_ids in guardian_students.items():
            for parent_id in parent_ids:
                parent = db.session.get(User, parent_id)
                if parent:
                    include(parent, student_anchor=None)
    elif mode == "guardians":
        if not student_id:
            raise ValueError("Choose a student before inviting guardians.")
        for parent_id in guardian_ids_for_student(student_id):
            parent = db.session.get(User, parent_id)
            if parent:
                include(parent, student_anchor=student_id)
    elif mode in {"selected", "committee"}:
        for user_id in selected_ids:
            user = db.session.get(User, user_id)
            if not user:
                raise ValueError("One or more selected participants do not exist.")
            include(user, "committee" if mode == "committee" else None)
    else:
        raise ValueError("Choose an available invitation audience.")

    for user_id in panelist_ids or []:
        user = db.session.get(User, user_id)
        if not user or user.role not in OFFICE_ROLES | {"teacher"} | CLASS_SUPPORT_ROLES:
            raise ValueError("Panelists must be active school staff accounts.")
        include(user, "panelist")

    for name, role in (("chairperson_id", "chairperson"), ("secretary_id", "secretary"),
                       ("minutes_approver_id", "approver")):
        value = payload.get("panel_moderator_id") if name == "chairperson_id" and payload.get("meeting_type") == "panel_discussion" else payload.get(name)
        user_id = _as_int(value, name)
        if user_id:
            user = db.session.get(User, user_id)
            if not user or not user.is_active_account:
                raise ValueError(f"Choose an active account for {role}.")
            if name == "minutes_approver_id" and user.role not in APPROVER_ROLES:
                raise ValueError("Only the headteacher or super administrator can approve minutes.")
            if name in {"chairperson_id", "secretary_id"} and user.role not in OFFICE_ROLES | {"teacher"}:
                raise ValueError(f"Choose an authorized staff member for {role}.")
            include(user, role, student_id)

    if actor.id not in invitees:
        include(actor, "organizer", student_id)
    return list(invitees.values())


def _panel_discussion_settings(payload, meeting_type, start, end):
    if meeting_type != "panel_discussion":
        return None, [], None
    start_time = datetime.time.fromisoformat(start)
    end_time = datetime.time.fromisoformat(end)
    duration = int((datetime.datetime.combine(datetime.date.today(), end_time)
                    - datetime.datetime.combine(datetime.date.today(), start_time)).total_seconds() // 60)
    if duration < 30 or duration > 90:
        raise ValueError("Panel discussions must run between 30 and 90 minutes.")
    try:
        qa_duration = int(payload.get("qa_duration_minutes") or 15)
    except (TypeError, ValueError):
        raise ValueError("Choose an audience Q&A duration of 15 to 20 minutes.")
    if qa_duration < 15 or qa_duration > 20 or qa_duration > duration - 10:
        raise ValueError("Audience Q&A must run 15 to 20 minutes and leave at least 10 minutes for the welcome and wrap-up.")
    raw_panelists = _items(payload, "panelist_ids")
    try:
        panelist_ids = [int(value) for value in raw_panelists]
    except (TypeError, ValueError):
        raise ValueError("Choose three to five valid panelists.")
    if len(set(panelist_ids)) != len(panelist_ids) or not 3 <= len(panelist_ids) <= 5:
        raise ValueError("Choose three to five different panelists.")
    moderator_id = _as_int(payload.get("panel_moderator_id") or payload.get("chairperson_id"), "chairperson_id")
    if not moderator_id:
        raise ValueError("Choose an active moderator for the panel discussion.")
    if moderator_id in panelist_ids:
        raise ValueError("The moderator must be separate from the three to five panelists.")
    perspectives = str(payload.get("panel_perspectives") or "").strip()
    if len(perspectives) > 1000:
        raise ValueError("Panel perspectives must be 1,000 characters or fewer.")
    return qa_duration, panelist_ids, perspectives or None


def _validate_schedule(payload):
    date_value = str(payload.get("scheduled_date", "")).strip()
    start = str(payload.get("start_time", "")).strip()
    end = str(payload.get("end_time", "")).strip()
    try:
        datetime.date.fromisoformat(date_value)
        datetime.time.fromisoformat(start)
        datetime.time.fromisoformat(end)
        if datetime.time.fromisoformat(end) <= datetime.time.fromisoformat(start):
            raise ValueError("The meeting end time must be later than its start time.")
    except ValueError as error:
        if str(error) == "The meeting end time must be later than its start time.":
            raise
        raise ValueError("Enter a valid meeting date, start time, and end time.")
    return date_value, start[:5], end[:5]


def _raise_conflicts(date_value, start, end, location, participant_ids, exclude_id=None):
    conflicts = detect_conflicts(date_value, start, end, location, participant_ids, exclude_id)
    if conflicts:
        names = "; ".join(reason for _record, reason in conflicts[:3])
        raise ValueError(f"Scheduling conflict: {names}")


def create_meeting(actor, payload):
    if actor.role not in OFFICE_ROLES | {"teacher"} | CLASS_SUPPORT_ROLES:
        raise PermissionError("Your role cannot create meetings.")
    title = str(payload.get("title") or "").strip()
    description = str(payload.get("description") or "").strip()
    meeting_type = str(payload.get("meeting_type") or "general").strip()
    class_name = str(payload.get("class_name") or "").strip() or None
    student_id = _as_int(payload.get("student_id"), "student_id")
    location = str(payload.get("location") or "").strip() or None
    confidentiality = str(payload.get("confidentiality") or "participants").strip()
    if not title or len(title) > 160:
        raise ValueError("Enter a meeting title of 1 to 160 characters.")
    if len(description) > 10000:
        raise ValueError("Meeting description must be 10,000 characters or fewer.")
    if meeting_type not in MEETING_TYPES:
        raise ValueError("Choose a valid meeting type.")
    if confidentiality not in CONFIDENTIALITY_LEVELS:
        raise ValueError("Choose a valid confidentiality level.")
    if actor.role == "parent":
        raise PermissionError("Use the parent appointment request workflow to request a meeting.")
    student, class_name = _student_for_meeting(actor, student_id, class_name)
    if class_name and len(class_name) > 100:
        raise ValueError("Choose a valid class.")
    date_value, start, end = _validate_schedule(payload)
    qa_duration, panelist_ids, panel_perspectives = _panel_discussion_settings(payload, meeting_type, start, end)
    invitees = _resolve_invitees(
        actor, payload, student.id if student else None, class_name,
        panelist_ids=panelist_ids,
    )
    if confidentiality == "staff_only" and any(user.role == "parent" for user, _role, _anchor in invitees):
        raise ValueError("Staff-only meetings cannot include parent or guardian participants.")
    if location and len(location) > 160:
        raise ValueError("Room or location must be 160 characters or fewer.")
    participant_ids = {user.id for user, _role, _anchor in invitees}
    publish = _as_bool(payload.get("publish", True))
    approval_required = (
        actor.role not in APPROVER_ROLES
        and str(get_setting("meeting_approval_required", "true")).lower() != "false"
    )
    if publish:
        _raise_conflicts(date_value, start, end, location, participant_ids)
    now = now_string()
    approval_status = "pending" if publish and approval_required else "approved" if publish else "not_required"
    status = "Pending approval" if publish and approval_required else "Scheduled" if publish else "Draft"
    meeting = Meeting(
        title=title, description=description or None, meeting_type=meeting_type,
        class_name=class_name, student_id=student.id if student else None,
        organizer_id=actor.id,
        chairperson_id=_as_int(
            payload.get("panel_moderator_id") if meeting_type == "panel_discussion" else payload.get("chairperson_id"),
            "chairperson_id",
        ),
        secretary_id=_as_int(payload.get("secretary_id"), "secretary_id"),
        minutes_approver_id=_as_int(payload.get("minutes_approver_id"), "minutes_approver_id"),
        scheduled_date=date_value, start_time=start, end_time=end, location=location,
        status=status, approval_status=approval_status,
        confidentiality=confidentiality, qa_duration_minutes=qa_duration,
        panel_perspectives=panel_perspectives,
        created_at=now, updated_at=now,
    )
    if publish and not meeting.minutes_approver_id:
        approver = actor if actor.role in APPROVER_ROLES else User.query.filter(
            User.role.in_(APPROVER_ROLES), User.is_active_account.is_(True)
        ).order_by(User.role, User.id).first()
        if not approver:
            raise ValueError("An active headteacher or super administrator is required to approve minutes.")
        meeting.minutes_approver_id = approver.id
    db.session.add(meeting)
    db.session.flush()
    for user, role, anchor in invitees:
        invitation_status = "pending_approval" if approval_status == "pending" else (
            "accepted" if user.id == actor.id else "invited"
        )
        db.session.add(MeetingParticipant(
            meeting_id=meeting.id, user_id=user.id, student_id=anchor,
            participant_role=role, invited_at=now, invitation_status=invitation_status,
        ))
    agenda_items = payload.get("agenda_items") or []
    if isinstance(agenda_items, str):
        agenda_items = [item.strip() for item in agenda_items.splitlines() if item.strip()]
    if meeting_type == "panel_discussion":
        duration = int((datetime.datetime.combine(datetime.date.today(), datetime.time.fromisoformat(end))
                        - datetime.datetime.combine(datetime.date.today(), datetime.time.fromisoformat(start))).total_seconds() // 60)
        curated_minutes = duration - qa_duration - 10
        agenda_items = [
            {"title": "Welcome and moderator framing (3 min)", "details": "Moderator welcomes the audience, introduces the topic and panelists, and sets discussion ground rules."},
            {"title": "Opening remarks (4 min)", "details": "Brief opening perspectives from each panelist; keep remarks concise and conversational."},
        ] + ([{"title": f"Curated panel conversation ({curated_minutes} min)", "details": "Use open prompts. Panelists should respond to and challenge one another naturally, without separate slide presentations. Invite contrasting professional viewpoints."}] if curated_minutes else []) + agenda_items + [
            {"title": f"Audience Q&A ({qa_duration} min)", "details": "Reserve this time for audience questions, asked directly or collected through an engagement app."},
            {"title": "Wrap-up (3 min)", "details": "Moderator summarizes key takeaways and closes the session."},
        ]
    if not isinstance(agenda_items, list) or len(agenda_items) > 40:
        raise ValueError("Provide no more than 40 agenda items.")
    for position, item in enumerate(agenda_items):
        title_text = str(item.get("title") or "").strip() if isinstance(item, dict) else str(item).strip()
        details = str(item.get("details") or "").strip() if isinstance(item, dict) else None
        if len(title_text) > 200 or len(details or "") > 5000:
            raise ValueError("Agenda titles must be at most 200 characters and details at most 5,000 characters.")
        if title_text:
            db.session.add(MeetingAgendaItem(
                meeting_id=meeting.id, title=title_text[:200], details=details or None,
                position=position, created_by_id=actor.id, created_at=now,
            ))
    event = record_meeting_audit(actor, meeting.id, "meeting_created", {
        "title": meeting.title, "meeting_type": meeting.meeting_type,
        "status": meeting.status, "participant_count": len(invitees),
        "panelist_count": len(panelist_ids), "qa_duration_minutes": qa_duration,
    })
    if meeting.status == "Scheduled":
        _activate_invitation_thread(meeting, actor)
        notify_meeting_users(
            meeting, participant_ids - {actor.id}, f"meeting-created:{event.id}",
            "Meeting invitation", "You have been invited to a school meeting. Sign in to review the details.",
        )
    elif meeting.status == "Pending approval" and meeting.minutes_approver_id:
        notify_meeting_users(
            meeting, {meeting.minutes_approver_id}, f"meeting-approval:{event.id}",
            "Meeting awaiting approval", "A meeting is waiting for your review.",
        )
    db.session.commit()
    return meeting


def _activate_invitation_thread(meeting, actor):
    from app.routes.messages import create_meeting_conversation
    participants = MeetingParticipant.query.filter_by(meeting_id=meeting.id).all()
    for participant in participants:
        if participant.invitation_status == "pending_approval":
            participant.invitation_status = "invited"
    recipient_users = [participant.user for participant in participants
                       if participant.user and participant.invitation_status != "revoked"]
    return create_meeting_conversation(meeting, actor, recipient_users, send_notifications=False)


def update_meeting(actor, meeting, payload):
    if not can_manage_meeting(actor, meeting):
        raise PermissionError("You are not allowed to update this meeting.")
    title = str(payload.get("title", meeting.title) or "").strip()
    description = str(payload.get("description", meeting.description or "") or "").strip()
    location = str(payload.get("location", meeting.location or "") or "").strip() or None
    status = str(payload.get("status", meeting.status)).strip()
    if not title or len(title) > 160 or len(description) > 10000:
        raise ValueError("Enter a valid title and description.")
    if status not in MEETING_STATUSES:
        raise ValueError("Choose a valid meeting status.")
    if meeting.status in {"Completed", "Cancelled"} and status != meeting.status:
        raise ValueError("Completed or cancelled meetings cannot be reopened.")
    if meeting.status == "Draft" and status in {"Scheduled", "Rescheduled"}:
        raise ValueError("Submit a draft through the approval workflow before it can be scheduled.")
    if status in {"Draft", "Pending approval"} and status != meeting.status:
        raise ValueError("Use the meeting approval workflow to change draft or approval status.")
    if meeting.status == "Pending approval" and status != "Cancelled":
        raise ValueError("A meeting awaiting approval must be approved or returned before it can be edited.")
    if status == "Completed" and actor.role not in OFFICE_ROLES | APPROVER_ROLES:
        raise PermissionError("Only school leadership can mark a meeting completed.")
    old_values = (
        meeting.title, meeting.description, meeting.location, meeting.scheduled_date,
        meeting.start_time, meeting.end_time, meeting.status,
    )
    old_schedule = (meeting.scheduled_date, meeting.start_time, meeting.end_time)
    date_value, start, end = _validate_schedule({
        "scheduled_date": payload.get("scheduled_date", meeting.scheduled_date),
        "start_time": payload.get("start_time", meeting.start_time),
        "end_time": payload.get("end_time", meeting.end_time),
    })
    participant_ids = {row.user_id for row in meeting.participants if row.invitation_status != "revoked"}
    if status in {"Scheduled", "Rescheduled", "Pending approval"}:
        _raise_conflicts(date_value, start, end, location, participant_ids, meeting.id)
    if (status in {"Scheduled", "Rescheduled"}
            and meeting.approval_status == "pending" and actor.role not in APPROVER_ROLES):
        raise PermissionError("A designated approver must approve this meeting before it is scheduled.")
    if status in {"Scheduled", "Rescheduled"} and meeting.approval_status != "approved" and actor.role in APPROVER_ROLES:
        raise ValueError("Use the meeting approval action to publish this pending meeting.")
    meeting.title = title
    meeting.description = description or None
    meeting.location = location
    meeting.scheduled_date = date_value
    meeting.start_time = start
    meeting.end_time = end
    if (date_value, start, end) != old_schedule and meeting.approval_status == "approved" and status not in {"Cancelled", "Completed"}:
        status = "Rescheduled"
    if (title, description or None, location, date_value, start, end, status) == old_values:
        return meeting
    meeting.status = status
    meeting.updated_at = now_string()
    event = record_meeting_audit(actor, meeting.id, "meeting_updated", {
        "status": status, "scheduled_date": date_value,
        "start_time": start, "end_time": end,
    })
    if status == "Cancelled":
        event_type = "meeting-cancelled"
        title_text = "Meeting cancelled"
        body = "A school meeting was cancelled. Sign in to review the update."
    else:
        event_type = "meeting-updated"
        title_text = "Meeting details updated"
        body = "Meeting details changed. Sign in to review the update."
    notify_meeting_users(
        meeting, participant_ids - {actor.id}, f"{event_type}:{event.id}", title_text, body,
    )
    db.session.commit()
    return meeting


def submit_meeting_for_approval(actor, meeting):
    if not can_manage_meeting(actor, meeting):
        raise PermissionError("You are not allowed to submit this meeting for approval.")
    if meeting.status != "Draft":
        raise ValueError("Only a draft meeting can be submitted for approval.")
    if not meeting.minutes_approver_id:
        approver = User.query.filter(
            User.role.in_(APPROVER_ROLES), User.is_active_account.is_(True)
        ).order_by(User.role, User.id).first()
        if not approver:
            raise ValueError("Assign a headteacher or super administrator as approver first.")
        meeting.minutes_approver_id = approver.id
    participants = {row.user_id for row in meeting.participants if row.invitation_status != "revoked"}
    _raise_conflicts(
        meeting.scheduled_date, meeting.start_time, meeting.end_time,
        meeting.location, participants, meeting.id,
    )
    meeting.status = "Pending approval"
    meeting.approval_status = "pending"
    for participant in meeting.participants:
        if participant.user_id != actor.id and participant.invitation_status == "invited":
            participant.invitation_status = "pending_approval"
    meeting.updated_at = now_string()
    event = record_meeting_audit(actor, meeting.id, "meeting_submitted_for_approval", {})
    notify_meeting_users(
        meeting, {meeting.minutes_approver_id}, f"meeting-approval:{event.id}",
        "Meeting awaiting approval", "A meeting is waiting for your review.",
    )
    db.session.commit()
    return meeting


def decide_meeting(actor, meeting, decision, note=""):
    if decision not in {"approved", "rejected"}:
        raise ValueError("Choose approve or reject.")
    designated = meeting.minutes_approver_id == actor.id
    if actor.role not in APPROVER_ROLES or not designated:
        raise PermissionError("Only the designated headteacher or super administrator can decide this meeting.")
    if meeting.status != "Pending approval" or meeting.approval_status != "pending":
        raise ValueError("This meeting is not awaiting approval.")
    note = str(note or "").strip()
    if decision == "rejected" and not note:
        raise ValueError("Add a reason when returning this meeting for changes.")
    if len(note) > 1000:
        raise ValueError("Approval note must be 1,000 characters or fewer.")
    if decision == "rejected":
        meeting.status = "Draft"
        meeting.approval_status = "rejected"
    else:
        participant_ids = {row.user_id for row in meeting.participants if row.invitation_status != "revoked"}
        _raise_conflicts(
            meeting.scheduled_date, meeting.start_time, meeting.end_time,
            meeting.location, participant_ids, meeting.id,
        )
        meeting.status = "Scheduled"
        meeting.approval_status = "approved"
        for participant in meeting.participants:
            if participant.invitation_status == "pending_approval":
                participant.invitation_status = "invited"
        _activate_invitation_thread(meeting, actor)
    meeting.updated_at = now_string()
    event = record_meeting_audit(actor, meeting.id, f"meeting_{decision}", {"note": note})
    if decision == "approved":
        notify_meeting_users(
            meeting, {row.user_id for row in meeting.participants if row.user_id != actor.id},
            f"meeting-approved:{event.id}", "Meeting invitation", "A school meeting has been approved. Sign in to review its details.",
        )
    else:
        notify_meeting_users(
            meeting, {meeting.organizer_id}, f"meeting-rejected:{event.id}",
            "Meeting returned for revision", "A meeting was returned for revision. Sign in to review the decision.",
        )
    db.session.commit()
    return meeting


def respond_to_invitation(actor, meeting, response, comment=""):
    if not can_view_meeting(actor, meeting):
        raise PermissionError("You do not have access to this meeting invitation.")
    participant = MeetingParticipant.query.filter_by(meeting_id=meeting.id, user_id=actor.id).first()
    if not participant or participant.invitation_status not in {"invited", "accepted", "declined", "reschedule_requested"}:
        raise PermissionError("You do not have an active invitation to this meeting.")
    if response not in {"accepted", "declined", "reschedule_requested"}:
        raise ValueError("Choose accept, decline, or request rescheduling.")
    comment = str(comment or "").strip()
    if len(comment) > 1000:
        raise ValueError("Response comment must be 1,000 characters or fewer.")
    if participant.invitation_status == response and (participant.response_comment or "") == comment:
        return participant
    participant.invitation_status = response
    participant.response_comment = comment or None
    participant.responded_at = now_string()
    event = record_meeting_audit(actor, meeting.id, "invitation_response", {
        "response": response,
    })
    notify_meeting_users(
        meeting, {meeting.organizer_id, meeting.chairperson_id, meeting.secretary_id} - {None, actor.id},
        f"invitation-response:{participant.id}:{event.id}", "Meeting invitation response",
        "A meeting participant updated their invitation response. Sign in to view the response.",
    )
    db.session.commit()
    return participant


def add_meeting_participant(actor, meeting, user_id):
    if not can_manage_meeting(actor, meeting):
        raise PermissionError("You are not allowed to change this meeting's participants.")
    if meeting.status in {"Cancelled", "Completed"}:
        raise ValueError("Participants cannot be added to a completed or cancelled meeting.")
    invitees = _resolve_invitees(actor, {
        "audience_mode": "selected", "participant_ids": [str(user_id)],
    }, meeting.student_id, meeting.class_name)
    selected = next((item for item in invitees if item[0].id == user_id), None)
    if not selected:
        raise ValueError("Choose an active authorized parent or staff participant.")
    user, role, student_anchor = selected
    if meeting.confidentiality == "staff_only" and user.role == "parent":
        raise ValueError("Staff-only meetings cannot include parent or guardian participants.")
    participant = MeetingParticipant.query.filter_by(
        meeting_id=meeting.id, user_id=user.id,
    ).first()
    if participant and participant.invitation_status != "revoked":
        raise ValueError("This account already has an active invitation.")
    now = now_string()
    invitation_status = "pending_approval" if meeting.status == "Pending approval" else "invited"
    if participant:
        participant.student_id = student_anchor
        participant.participant_role = role
        participant.invited_at = now
        participant.invitation_status = invitation_status
        participant.response_comment = None
        participant.responded_at = None
    else:
        participant = MeetingParticipant(
            meeting_id=meeting.id, user_id=user.id, student_id=student_anchor,
            participant_role=role, invited_at=now, invitation_status=invitation_status,
        )
        db.session.add(participant)
    db.session.flush()
    event = record_meeting_audit(actor, meeting.id, "meeting_participant_invited", {
        "participant_id": participant.id, "user_id": user.id,
        "invitation_status": invitation_status,
    })
    if meeting.status in {"Scheduled", "Rescheduled"}:
        from app.routes.messages import add_meeting_conversation_participant
        add_meeting_conversation_participant(meeting, actor, user)
        notify_meeting_users(
            meeting, {user.id}, f"participant-invited:{event.id}",
            "Meeting invitation", "You have been invited to a school meeting. Sign in to review the details.",
        )
    db.session.commit()
    return participant


def revoke_meeting_participant(actor, meeting, participant_id):
    if not can_manage_meeting(actor, meeting):
        raise PermissionError("You are not allowed to change this meeting's participants.")
    participant = db.session.get(MeetingParticipant, participant_id)
    if not participant or participant.meeting_id != meeting.id:
        raise ValueError("Choose a participant in this meeting.")
    if participant.user_id == meeting.organizer_id:
        raise ValueError("The meeting organizer cannot be removed from the participant list.")
    if participant.invitation_status == "revoked":
        raise ValueError("This invitation has already been withdrawn.")
    previous_status = participant.invitation_status
    participant.invitation_status = "revoked"
    event = record_meeting_audit(actor, meeting.id, "meeting_participant_revoked", {
        "participant_id": participant.id, "user_id": participant.user_id,
        "previous_status": previous_status,
    })
    if meeting.status not in {"Draft", "Pending approval"}:
        notify_meeting_users(
            meeting, {participant.user_id}, f"participant-revoked:{event.id}",
            "Meeting invitation withdrawn", "A school meeting invitation was withdrawn.",
            link="/meetings",
        )
    db.session.commit()
    return participant


def record_attendance(actor, meeting, participant_id, status, representative_name="", representative_relationship=""):
    if not can_manage_meeting(actor, meeting):
        raise PermissionError("You are not allowed to update this attendance register.")
    participant = db.session.get(MeetingParticipant, participant_id)
    if not participant or participant.meeting_id != meeting.id:
        raise ValueError("Choose a participant in this meeting.")
    if status not in ATTENDANCE_STATUSES:
        raise ValueError("Choose Present, Absent, Late, or Excused.")
    name = str(representative_name or "").strip()
    relationship = str(representative_relationship or "").strip()
    if len(name) > 160 or len(relationship) > 80 or (name and not relationship):
        raise ValueError("Enter a representative name and relationship within their allowed lengths.")
    attendance = participant.attendance
    if not attendance:
        attendance = MeetingAttendance(participant_id=participant.id)
        db.session.add(attendance)
    elif (attendance.status == status and attendance.representative_name == (name or None)
          and attendance.representative_relationship == (relationship or None)):
        return attendance
    attendance.status = status
    attendance.representative_name = name or None
    attendance.representative_relationship = relationship or None
    attendance.verified_by_id = actor.id
    attendance.updated_at = now_string()
    event = record_meeting_audit(actor, meeting.id, "attendance_recorded", {
        "participant_id": participant.id, "status": status,
        "representative": bool(name),
    })
    notify_meeting_users(
        meeting, {participant.user_id} - {actor.id}, f"attendance:{participant.id}:{event.id}",
        "Meeting attendance updated", "Your attendance record was updated. Sign in to view it.",
    )
    db.session.commit()
    return attendance


def add_agenda_item(actor, meeting, title, details=""):
    if not can_manage_meeting(actor, meeting):
        raise PermissionError("You are not allowed to edit this meeting agenda.")
    title = str(title or "").strip()
    details = str(details or "").strip()
    if not title or len(title) > 200 or len(details) > 5000:
        raise ValueError("Enter an agenda title (up to 200 characters) and details (up to 5,000 characters).")
    position = max((item.position for item in meeting.agenda_items), default=0) + 1
    item = MeetingAgendaItem(
        meeting_id=meeting.id, title=title, details=details or None,
        position=position, created_by_id=actor.id, created_at=now_string(),
    )
    db.session.add(item)
    event = record_meeting_audit(actor, meeting.id, "agenda_item_added", {"title": title})
    db.session.commit()
    return item


def move_agenda_item(actor, meeting, item_id, direction=None, position=None):
    if not can_manage_meeting(actor, meeting):
        raise PermissionError("You are not allowed to organize this meeting agenda.")
    ordered = list(meeting.agenda_items)
    item = next((row for row in ordered if row.id == item_id), None)
    if not item:
        raise ValueError("Choose an agenda item in this meeting.")
    current = ordered.index(item)
    if direction in {"up", "down"}:
        target = current - 1 if direction == "up" else current + 1
    else:
        try:
            target = int(position)
        except (TypeError, ValueError):
            raise ValueError("Choose an agenda position or move direction.")
    if target < 0 or target >= len(ordered):
        raise ValueError("That agenda item is already at the requested boundary.")
    ordered.insert(target, ordered.pop(current))
    for index, row in enumerate(ordered):
        row.position = index
    record_meeting_audit(actor, meeting.id, "agenda_item_reordered", {
        "agenda_item_id": item.id, "position": target,
    })
    db.session.commit()
    return item


def remove_agenda_item(actor, meeting, item_id):
    if not can_manage_meeting(actor, meeting):
        raise PermissionError("You are not allowed to edit this meeting agenda.")
    item = db.session.get(MeetingAgendaItem, item_id)
    if not item or item.meeting_id != meeting.id:
        raise ValueError("Choose an agenda item in this meeting.")
    title = item.title
    db.session.delete(item)
    record_meeting_audit(actor, meeting.id, "agenda_item_removed", {"title": title})
    db.session.commit()


def save_minutes(actor, meeting, content):
    if not can_manage_meeting(actor, meeting):
        raise PermissionError("You are not allowed to edit these minutes.")
    content = str(content or "").strip()
    if not content or len(content) > 30000:
        raise ValueError("Minutes must contain 1 to 30,000 characters.")
    minutes = MeetingMinutes.query.filter_by(meeting_id=meeting.id).first()
    if not minutes:
        minutes = MeetingMinutes(meeting_id=meeting.id, status="draft", current_version=0)
        db.session.add(minutes)
        db.session.flush()
    if minutes.status == "pending_approval":
        raise ValueError("Withdraw the pending approval request before editing these minutes.")
    previous_status = minutes.status
    minutes.current_version += 1
    minutes.status = "draft"
    minutes.submitted_by_id = None
    minutes.submitted_at = None
    minutes.approved_by_id = None
    minutes.approved_at = None
    minutes.approval_note = None
    revision = MeetingMinutesRevision(
        minutes_id=minutes.id, version=minutes.current_version, content=content,
        author_id=actor.id, created_at=now_string(),
        event="revised_after_approval" if previous_status == "approved" else "saved",
    )
    db.session.add(revision)
    record_meeting_audit(actor, meeting.id, "minutes_saved", {
        "version": minutes.current_version, "previous_status": previous_status,
    })
    db.session.commit()
    return minutes


def submit_minutes(actor, meeting):
    if not can_manage_meeting(actor, meeting):
        raise PermissionError("You are not allowed to submit these minutes.")
    minutes = MeetingMinutes.query.filter_by(meeting_id=meeting.id).first()
    if not minutes or minutes.status != "draft":
        raise ValueError("Save a draft before submitting minutes for approval.")
    if not meeting.minutes_approver_id:
        raise ValueError("Assign a headteacher or super administrator as minutes approver first.")
    minutes.status = "pending_approval"
    minutes.submitted_by_id = actor.id
    minutes.submitted_at = now_string()
    minutes.current_version += 1
    previous = minutes.revisions[-1]
    db.session.add(MeetingMinutesRevision(
        minutes_id=minutes.id, version=minutes.current_version, content=previous.content,
        author_id=actor.id, created_at=now_string(), event="submitted",
    ))
    event = record_meeting_audit(actor, meeting.id, "minutes_submitted", {"version": minutes.current_version})
    notify_meeting_users(
        meeting, {meeting.minutes_approver_id}, f"minutes-submitted:{event.id}",
        "Minutes awaiting approval", "Meeting minutes are ready for your review.",
    )
    db.session.commit()
    return minutes


def decide_minutes(actor, meeting, decision, note=""):
    if decision not in {"approved", "rejected"}:
        raise ValueError("Choose approve or reject.")
    is_designated = meeting.minutes_approver_id == actor.id
    if actor.role not in APPROVER_ROLES or not is_designated:
        raise PermissionError("Only the designated headteacher or super administrator can decide these minutes.")
    minutes = MeetingMinutes.query.filter_by(meeting_id=meeting.id).first()
    if not minutes or minutes.status != "pending_approval":
        raise ValueError("These minutes are not awaiting approval.")
    note = str(note or "").strip()
    if decision == "rejected" and not note:
        raise ValueError("Add a reason when returning minutes for revision.")
    if len(note) > 1000:
        raise ValueError("Approval note must be 1,000 characters or fewer.")
    minutes.status = decision
    minutes.approved_by_id = actor.id
    minutes.approved_at = now_string()
    minutes.approval_note = note or None
    minutes.current_version += 1
    content = minutes.revisions[-1].content
    db.session.add(MeetingMinutesRevision(
        minutes_id=minutes.id, version=minutes.current_version, content=content,
        author_id=actor.id, created_at=now_string(), event=decision,
        decision_note=note or None,
    ))
    event = record_meeting_audit(actor, meeting.id, f"minutes_{decision}", {
        "version": minutes.current_version, "note": note,
    })
    recipient_ids = {row.user_id for row in meeting.participants if row.user_id != actor.id}
    notify_meeting_users(
        meeting, recipient_ids, f"minutes-{decision}:{event.id}",
        f"Meeting minutes {decision}",
        "Approved minutes are available." if decision == "approved" else "Meeting minutes were returned for revision.",
    )
    db.session.commit()
    return minutes


def _eligible_action_assignee(actor, meeting, assignee_id):
    assignee = db.session.get(User, assignee_id)
    if not assignee or not assignee.is_active_account or assignee.role not in MEETING_ROLES - {"parent"}:
        return None
    participant_ids = {row.user_id for row in meeting.participants if row.invitation_status != "revoked"}
    if assignee.id not in participant_ids and assignee.id != actor.id and actor.role not in OFFICE_ROLES:
        return None
    class_name = meeting.class_name or (meeting.student.class_name if meeting.student else None)
    if actor.role not in OFFICE_ROLES and class_name and class_name.casefold() not in staff_classes(assignee):
        return None
    return assignee


def create_action_item(actor, meeting, payload):
    if not can_manage_meeting(actor, meeting):
        raise PermissionError("You are not allowed to assign meeting actions.")
    minutes = MeetingMinutes.query.filter_by(meeting_id=meeting.id).first()
    if not minutes or minutes.status != "approved":
        raise ValueError("Action items can only be assigned from approved meeting decisions.")
    title = str(payload.get("title", "")).strip()
    details = str(payload.get("details", "")).strip()
    deadline = str(payload.get("deadline", "")).strip()
    agenda_item_id = _as_int(payload.get("agenda_item_id"), "agenda_item_id")
    assignee_id = _as_int(payload.get("assignee_id"), "assignee_id")
    try:
        datetime.date.fromisoformat(deadline)
    except ValueError:
        raise ValueError("Enter a valid action deadline.")
    if not title or len(title) > 200 or len(details) > 5000:
        raise ValueError("Enter an action title (up to 200 characters) and details (up to 5,000 characters).")
    if not assignee_id or not _eligible_action_assignee(actor, meeting, assignee_id):
        raise PermissionError("Choose an active staff participant authorized for this meeting.")
    agenda_item = db.session.get(MeetingAgendaItem, agenda_item_id) if agenda_item_id else None
    if agenda_item_id and (not agenda_item or agenda_item.meeting_id != meeting.id):
        raise ValueError("Choose an agenda item in this meeting.")
    action = MeetingActionItem(
        meeting_id=meeting.id, agenda_item_id=agenda_item.id if agenda_item else None,
        title=title, details=details or None, assignee_id=assignee_id,
        assigned_by_id=actor.id, deadline=deadline, status="Not Started",
        is_parent_visible=_as_bool(payload.get("is_parent_visible")),
        created_at=now_string(), updated_at=now_string(),
    )
    db.session.add(action)
    db.session.flush()
    event = record_meeting_audit(actor, meeting.id, "action_assigned", {
        "action_id": action.id, "assignee_id": assignee_id, "deadline": deadline,
    })
    notify_meeting_users(
        meeting, {assignee_id}, f"action-assigned:{event.id}", "Meeting action assigned",
        "You have been assigned a meeting follow-up action. Sign in to review it.",
    )
    db.session.commit()
    return action


def update_action(actor, action, payload):
    meeting = action.meeting
    if not can_view_meeting(actor, meeting):
        raise PermissionError("You cannot access this meeting action.")
    status = str(payload.get("status", action.status)).strip()
    is_assignee = actor.id == action.assignee_id
    is_manager = can_manage_meeting(actor, meeting)
    if not (is_assignee or is_manager or actor.role in APPROVER_ROLES):
        raise PermissionError("Only the assigned person or authorized meeting staff can update this action.")
    if status not in ACTION_STATUSES:
        raise ValueError("Choose a valid action status.")
    if status == "Overdue" and action.status != "Overdue":
        raise ValueError("Overdue status is set automatically after the deadline passes.")
    if status in {"Not Started", "In Progress"} and not (is_assignee or is_manager):
        raise PermissionError("Only the assigned person or meeting staff can update action progress.")
    if status == "Completed" and action.status != "Completed" and not (is_assignee or actor.role in APPROVER_ROLES):
        raise PermissionError("Only the assigned person or school leadership can complete this action.")
    if status == "Reviewed" and actor.role not in APPROVER_ROLES:
        raise PermissionError("Only school leadership can review a completed action.")
    if status == "Reviewed" and action.status not in {"Completed", "Reviewed"}:
        raise ValueError("Only completed actions can be reviewed.")
    if action.status == "Reviewed" and status != "Reviewed" and actor.role not in APPROVER_ROLES:
        raise PermissionError("Only school leadership can reopen a reviewed action.")
    if action.status == "Reviewed" and actor.role not in APPROVER_ROLES:
        raise PermissionError("A reviewed action cannot be changed by this account.")
    evidence = str(payload.get("completion_evidence") or action.completion_evidence or "").strip()
    review = str(payload.get("review_outcome") or action.review_outcome or "").strip()
    if len(evidence) > 5000 or len(review) > 1000:
        raise ValueError("Evidence or review outcome exceeds the allowed length.")
    if review != (action.review_outcome or "") and actor.role not in APPROVER_ROLES:
        raise PermissionError("Only school leadership can add a review outcome.")
    if evidence != (action.completion_evidence or "") and not (is_assignee or actor.role in APPROVER_ROLES):
        raise PermissionError("Only the assigned person or school leadership can update completion evidence.")
    assignee_value = payload.get("assignee_id")
    if assignee_value not in (None, ""):
        new_assignee_id = _as_int(assignee_value, "assignee_id")
        if new_assignee_id != action.assignee_id:
            can_reassign = actor.role in OFFICE_ROLES or actor.id in {
                action.assigned_by_id, meeting.organizer_id, meeting.chairperson_id, meeting.secretary_id,
            }
            if not can_reassign or not new_assignee_id or not _eligible_action_assignee(actor, meeting, new_assignee_id):
                raise PermissionError("Only authorized meeting staff can reassign this action.")
    else:
        new_assignee_id = action.assignee_id
    deadline = str(payload.get("deadline", action.deadline)).strip()
    try:
        datetime.date.fromisoformat(deadline)
    except ValueError:
        raise ValueError("Enter a valid action deadline.")
    if deadline != action.deadline and not (actor.role in OFFICE_ROLES or actor.id in {
        action.assigned_by_id, meeting.organizer_id, meeting.chairperson_id, meeting.secretary_id,
    }):
        raise PermissionError("Only authorized meeting staff can change an action deadline.")
    old_assignee_id = action.assignee_id
    old_status = action.status
    if (status == old_status and evidence == (action.completion_evidence or "")
            and deadline == action.deadline and new_assignee_id == old_assignee_id
            and review == (action.review_outcome or "")):
        return action
    action.status = status
    action.completion_evidence = evidence or None
    action.deadline = deadline
    action.assignee_id = new_assignee_id
    if new_assignee_id != old_assignee_id:
        action.assigned_by_id = actor.id
    if actor.role in APPROVER_ROLES:
        action.review_outcome = review or None
    action.updated_at = now_string()
    event = record_meeting_audit(actor, meeting.id, "action_updated", {
        "action_id": action.id, "old_status": old_status, "status": status,
    })
    notify_meeting_users(
        meeting, {action.assigned_by_id, action.assignee_id, old_assignee_id} - {actor.id},
        f"action-updated:{action.id}:{event.id}", "Meeting action updated",
        "A meeting follow-up action was updated. Sign in to review it.",
    )
    db.session.commit()
    return action


def add_action_comment(actor, action, body, is_parent_visible=False):
    if not can_view_meeting(actor, action.meeting):
        raise PermissionError("You cannot access this meeting action.")
    if actor.role == "parent" and not action.is_parent_visible:
        raise PermissionError("This action is not shared with parent participants.")
    if actor.role == "parent" and not is_parent_visible:
        raise PermissionError("Parent comments must be shared with the meeting participants.")
    if not (can_manage_meeting(actor, action.meeting) or actor.id == action.assignee_id or actor.role == "parent"):
        raise PermissionError("You are not allowed to comment on this action.")
    body = str(body or "").strip()
    if not body or len(body) > 2000:
        raise ValueError("Enter a comment of 1 to 2,000 characters.")
    comment = MeetingActionComment(
        action_id=action.id, author_id=actor.id, body=body,
        is_parent_visible=is_parent_visible, created_at=now_string(),
    )
    db.session.add(comment)
    record_meeting_audit(actor, action.meeting_id, "action_comment_added", {"action_id": action.id})
    db.session.commit()
    return comment


def verify_guardian_link(actor, student_id, guardian_id, relationship):
    if actor.role not in OFFICE_ROLES:
        raise PermissionError("Only school administration can verify guardian relationships.")
    student = db.session.get(User, student_id)
    guardian = db.session.get(User, guardian_id)
    relationship = str(relationship or "").strip() or "Parent / Guardian"
    if (not student or student.role != "student" or not student.is_active_account
            or not guardian or guardian.role != "parent" or not guardian.is_active_account):
        raise ValueError("Choose an active student and parent or guardian account.")
    if len(relationship) > 80:
        raise ValueError("Relationship must be 80 characters or fewer.")
    link = StudentGuardian.query.filter_by(student_id=student_id, guardian_id=guardian_id).first()
    now = now_string()
    if not link:
        link = StudentGuardian(
            student_id=student_id, guardian_id=guardian_id, relationship=relationship,
            status="verified", verified_by_id=actor.id, verified_at=now,
        )
        db.session.add(link)
    else:
        link.relationship = relationship
        link.status = "verified"
        link.verified_by_id = actor.id
        link.verified_at = now
        link.revoked_by_id = None
        link.revoked_at = None
    if not student.parent_id:
        student.parent_id = guardian.id
    event = record_meeting_audit(actor, None, "guardian_link_verified", {
        "student_id": student.id, "guardian_id": guardian.id,
        "relationship": relationship,
    })
    db.session.commit()
    return link


def revoke_guardian_link(actor, link):
    if actor.role not in OFFICE_ROLES:
        raise PermissionError("Only school administration can revoke guardian relationships.")
    if not link or link.status != "verified":
        raise ValueError("Choose an active verified guardian link.")
    link.status = "revoked"
    link.revoked_by_id = actor.id
    link.revoked_at = now_string()
    if link.student and link.student.parent_id == link.guardian_id:
        link.student.parent_id = None
    record_meeting_audit(actor, None, "guardian_link_revoked", {
        "student_id": link.student_id, "guardian_id": link.guardian_id,
    })
    db.session.commit()
    return link
