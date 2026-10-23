"""Authenticated JSON API for the meeting panel.

The API uses the application's existing cookie session. Mutating requests must
send the session-bound meeting CSRF token in ``X-CSRF-Token``; obtain it from
``GET /api/meetings/csrf``.
"""
import datetime
from functools import wraps

from flask import jsonify, request
from flask_login import current_user

from app.extensions import db
from app.models import (
    Meeting, MeetingActionComment, MeetingActionItem, MeetingAgendaItem, MeetingAuditEvent,
    MeetingAttendance, MeetingMinutes, MeetingNotificationLog,
    MeetingParticipant, Setting, StudentGuardian, User,
)
from app.routes.meetings import _csrf_token
from app.services.meeting_service import (
    ACTION_STATUSES, APPROVER_ROLES, MEETING_STATUSES, MEETING_TYPES,
    can_manage_meeting, can_view_meeting, record_meeting_audit,
    verified_student_ids, visible_meetings,
)
from app.services.meeting_workflow import (
    add_action_comment, add_agenda_item, add_meeting_participant,
    create_action_item, create_meeting,
    decide_meeting, decide_minutes, record_attendance, remove_agenda_item,
    move_agenda_item,
    respond_to_invitation, revoke_guardian_link, save_minutes,
    submit_meeting_for_approval, submit_minutes, update_action,
    update_meeting, verify_guardian_link, revoke_meeting_participant,
)
from app.utils.constants import ROLE_ACCESS


def _response(data=None, *, ok=True, status=200, error=None, meta=None):
    if error:
        ok = False
    payload = {"ok": ok}
    if error:
        payload["error"] = error
    if data is not None:
        payload["data"] = data
    if meta is not None:
        payload["meta"] = meta
    return jsonify(payload), status


def _api_auth(endpoint="meeting_api", csrf=False):
    allowed = ROLE_ACCESS.get(endpoint, set())

    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return _response(error="Authentication is required.", status=401)
            if current_user.role not in allowed:
                return _response(error="Your role cannot access this endpoint.", status=403)
            if csrf and request.method not in {"GET", "HEAD", "OPTIONS"}:
                from app.routes.meetings import _csrf_valid
                if not _csrf_valid():
                    return _response(error="A valid meeting CSRF token is required.", status=400)
                cutoff = (datetime.datetime.now() - datetime.timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
                recent_writes = MeetingAuditEvent.query.filter(
                    MeetingAuditEvent.actor_id == current_user.id,
                    MeetingAuditEvent.created_at >= cutoff,
                ).count()
                if recent_writes >= 120:
                    return _response(error="Meeting API write limit reached. Try again later.", status=429)
            return fn(*args, **kwargs)
        return wrapped
    return decorate


def _meeting(meeting_id):
    record = db.session.get(Meeting, meeting_id)
    if not record:
        return None, _response(error="Meeting not found.", status=404)
    if not can_view_meeting(current_user, record):
        return None, _response(error="You cannot access this meeting.", status=403)
    return record, None


def _serialize_action(action, viewer):
    parent = viewer.role == "parent"
    comments = [
        {"id": comment.id, "author": comment.author.full_name if comment.author else "School account",
         "body": comment.body, "created_at": comment.created_at}
        for comment in action.comments if not parent or comment.is_parent_visible
    ]
    return {
        "id": action.id, "title": action.title, "details": action.details,
        "assignee": action.assignee.full_name if action.assignee else None,
        "deadline": action.deadline, "status": action.status,
        "completion_evidence": action.completion_evidence if not parent or action.is_parent_visible else None,
        "review_outcome": action.review_outcome if not parent or action.is_parent_visible else None,
        "is_parent_visible": action.is_parent_visible, "comments": comments,
    }


def _serialize_meeting(meeting, viewer, detail=False):
    parent = viewer.role == "parent"
    participant = next((row for row in meeting.participants if row.user_id == viewer.id), None)
    data = {
        "id": meeting.id, "title": meeting.title, "description": meeting.description,
        "meeting_type": meeting.meeting_type, "class_name": meeting.class_name,
        "scheduled_date": meeting.scheduled_date, "start_time": meeting.start_time,
        "end_time": meeting.end_time, "location": meeting.location,
        "status": meeting.status, "approval_status": meeting.approval_status,
        "organizer": meeting.organizer.full_name if meeting.organizer else None,
        "invitation_status": participant.invitation_status if participant else None,
        "created_at": meeting.created_at, "updated_at": meeting.updated_at,
    }
    if detail:
        data["student"] = (
            {"id": meeting.student_id, "name": meeting.student.full_name,
             "class_name": meeting.student.class_name}
            if meeting.student and (not parent or meeting.student_id in verified_student_ids(viewer.id)) else None
        )
        data["participants"] = [
            {"id": row.id, "name": row.user.full_name if row.user else None,
             "role": row.participant_role,
             "invitation_status": row.invitation_status if not parent or row.user_id == viewer.id else "invited",
             "attendance": row.attendance.status if row.attendance else None}
            for row in meeting.participants
            if not parent or row.user_id == viewer.id or (row.user and row.user.role != "parent")
        ]
        data["agenda"] = [
            {"id": item.id, "title": item.title, "details": item.details}
            for item in meeting.agenda_items
        ]
        minutes = meeting.minutes
        if minutes and minutes.status == "approved" and minutes.revisions:
            data["minutes"] = {
                "status": "approved", "version": minutes.current_version,
                "approved_at": minutes.approved_at,
                "content": minutes.revisions[-1].content,
            }
        elif not parent and minutes:
            data["minutes"] = {
                "status": minutes.status, "version": minutes.current_version,
                "content": minutes.revisions[-1].content if minutes.revisions else None,
            }
        else:
            data["minutes"] = None
        data["actions"] = [
            _serialize_action(action, viewer) for action in meeting.actions
            if not parent or action.is_parent_visible
        ]
    return data


def _meeting_query(filters=None):
    records = visible_meetings(current_user)
    filters = filters or request.args
    status = str(filters.get("status", "")).strip()
    meeting_type = str(filters.get("type", "")).strip()
    class_name = str(filters.get("class_name", "")).strip().casefold()
    date_from = str(filters.get("date_from", "")).strip()
    date_to = str(filters.get("date_to", "")).strip()
    query = str(filters.get("q", "")).strip().casefold()
    organizer_value = str(filters.get("organizer_id", "")).strip()
    if status and status not in MEETING_STATUSES:
        raise ValueError("Choose a valid meeting status filter.")
    if meeting_type and meeting_type not in MEETING_TYPES:
        raise ValueError("Choose a valid meeting type filter.")
    if status:
        records = [item for item in records if item.status == status]
    if meeting_type:
        records = [item for item in records if item.meeting_type == meeting_type]
    if class_name:
        records = [item for item in records if (item.class_name or "").casefold() == class_name]
    for value in (date_from, date_to):
        if value:
            try:
                datetime.date.fromisoformat(value)
            except ValueError:
                raise ValueError("Report dates must use YYYY-MM-DD.")
    if date_from:
        records = [item for item in records if item.scheduled_date >= date_from]
    if date_to:
        records = [item for item in records if item.scheduled_date <= date_to]
    if query:
        records = [item for item in records if query in item.title.casefold() or
                   query in (item.class_name or "").casefold() or
                   query in (item.location or "").casefold()]
    if organizer_value:
        try:
            organizer_id = int(organizer_value)
        except ValueError:
            raise ValueError("Organizer must be a valid account ID.")
        records = [item for item in records if item.organizer_id == organizer_id]
    return records


@_api_auth(csrf=True)
def api_csrf_token():
    return _response({"csrf_token": _csrf_token()})


@_api_auth(csrf=True)
def api_meetings():
    if request.method == "GET":
        try:
            records = _meeting_query()
        except ValueError as error:
            return _response(error=str(error), status=400)
        try:
            page = max(1, int(request.args.get("page", "1")))
            per_page = min(100, max(1, int(request.args.get("per_page", "25"))))
        except ValueError:
            return _response(error="page and per_page must be integers.", status=400)
        start = (page - 1) * per_page
        items = records[start:start + per_page]
        return _response([_serialize_meeting(item, current_user) for item in items], meta={
            "page": page, "per_page": per_page, "total": len(records),
            "pages": (len(records) + per_page - 1) // per_page,
        })
    try:
        meeting = create_meeting(current_user, request.get_json(silent=True) or {})
    except PermissionError as error:
        return _response(error=str(error), status=403)
    except ValueError as error:
        db.session.rollback()
        return _response(error=str(error), status=400)
    return _response(_serialize_meeting(meeting, current_user, detail=True), status=201)


@_api_auth(csrf=True)
def api_meeting_detail(meeting_id):
    meeting, error = _meeting(meeting_id)
    if error:
        return error
    if request.method == "GET":
        return _response(_serialize_meeting(meeting, current_user, detail=True))
    payload = request.get_json(silent=True) or {}
    try:
        if request.method == "DELETE":
            payload = {"status": "Cancelled"}
        update_meeting(current_user, meeting, payload)
    except PermissionError as exception:
        return _response(error=str(exception), status=403)
    except ValueError as exception:
        db.session.rollback()
        return _response(error=str(exception), status=400)
    return _response(_serialize_meeting(meeting, current_user, detail=True))


@_api_auth(csrf=True)
def api_meeting_response(meeting_id):
    meeting, error = _meeting(meeting_id)
    if error:
        return error
    payload = request.get_json(silent=True) or {}
    try:
        participant = respond_to_invitation(current_user, meeting, payload.get("response", ""),
                                             payload.get("comment", ""))
    except PermissionError as exception:
        return _response(error=str(exception), status=403)
    except ValueError as exception:
        return _response(error=str(exception), status=400)
    return _response({"invitation_status": participant.invitation_status,
                      "responded_at": participant.responded_at})


@_api_auth(csrf=True)
def api_meeting_participants(meeting_id, participant_id=None):
    meeting, error = _meeting(meeting_id)
    if error:
        return error
    if request.method == "GET":
        parent = current_user.role == "parent"
        return _response([
            {"id": row.id, "name": row.user.full_name if row.user else None,
             "role": row.participant_role,
             "invitation_status": row.invitation_status if not parent or row.user_id == current_user.id else "invited"}
            for row in meeting.participants
            if not parent or row.user_id == current_user.id or (row.user and row.user.role != "parent")
        ])
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        try:
            user_id = int(payload.get("user_id"))
            participant = add_meeting_participant(current_user, meeting, user_id)
        except PermissionError as exception:
            return _response(error=str(exception), status=403)
        except (TypeError, ValueError) as exception:
            db.session.rollback()
            return _response(error=str(exception) or "Choose a valid account ID.", status=400)
        return _response({"id": participant.id, "user_id": participant.user_id,
                          "invitation_status": participant.invitation_status}, status=201)
    try:
        participant = revoke_meeting_participant(current_user, meeting, participant_id)
    except PermissionError as exception:
        return _response(error=str(exception), status=403)
    except ValueError as exception:
        db.session.rollback()
        return _response(error=str(exception), status=400)
    return _response({"id": participant.id, "user_id": participant.user_id,
                      "invitation_status": participant.invitation_status})


@_api_auth(csrf=True)
def api_meeting_attendance(meeting_id):
    meeting, error = _meeting(meeting_id)
    if error:
        return error
    if request.method == "GET":
        rows = []
        for participant in meeting.participants:
            if current_user.role == "parent" and participant.user_id != current_user.id and participant.user.role == "parent":
                continue
            rows.append({"participant_id": participant.id,
                         "name": participant.user.full_name,
                         "status": participant.attendance.status if participant.attendance else None})
        return _response(rows)
    payload = request.get_json(silent=True) or {}
    try:
        attendance = record_attendance(
            current_user, meeting, payload.get("participant_id"), payload.get("status", ""),
            payload.get("representative_name", ""), payload.get("representative_relationship", ""),
        )
    except PermissionError as exception:
        return _response(error=str(exception), status=403)
    except ValueError as exception:
        return _response(error=str(exception), status=400)
    return _response({"participant_id": attendance.participant_id,
                      "status": attendance.status, "updated_at": attendance.updated_at})


@_api_auth(csrf=True)
def api_meeting_agenda(meeting_id, item_id=None):
    meeting, error = _meeting(meeting_id)
    if error:
        return error
    if request.method == "GET":
        return _response([{"id": item.id, "title": item.title, "details": item.details,
                           "position": item.position} for item in meeting.agenda_items])
    try:
        if request.method == "DELETE":
            remove_agenda_item(current_user, meeting, item_id)
            return _response({"deleted": True})
        payload = request.get_json(silent=True) or {}
        if request.method == "PATCH":
            item = move_agenda_item(current_user, meeting, item_id,
                                    payload.get("direction"), payload.get("position"))
        else:
            item = add_agenda_item(current_user, meeting, payload.get("title", ""), payload.get("details", ""))
    except PermissionError as exception:
        return _response(error=str(exception), status=403)
    except ValueError as exception:
        return _response(error=str(exception), status=400)
    return _response({"id": item.id, "title": item.title, "details": item.details,
                      "position": item.position}, status=201)


@_api_auth(csrf=True)
def api_meeting_minutes(meeting_id, operation=None):
    meeting, error = _meeting(meeting_id)
    if error:
        return error
    minutes = MeetingMinutes.query.filter_by(meeting_id=meeting.id).first()
    if request.method == "GET":
        if not minutes or not minutes.revisions:
            return _response(None)
        if current_user.role == "parent" and minutes.status != "approved":
            return _response(None)
        revision = minutes.revisions[-1]
        return _response({"status": minutes.status, "version": minutes.current_version,
                          "content": revision.content if minutes.status == "approved" or current_user.role != "parent" else None,
                          "approved_at": minutes.approved_at})
    payload = request.get_json(silent=True) or {}
    try:
        if operation == "submit":
            minutes = submit_minutes(current_user, meeting)
        elif operation in {"approve", "reject"}:
            minutes = decide_minutes(current_user, meeting, "approved" if operation == "approve" else "rejected",
                                     payload.get("note", ""))
        else:
            minutes = save_minutes(current_user, meeting, payload.get("content", ""))
    except PermissionError as exception:
        return _response(error=str(exception), status=403)
    except ValueError as exception:
        db.session.rollback()
        return _response(error=str(exception), status=400)
    return _response({"status": minutes.status, "version": minutes.current_version,
                      "approved_at": minutes.approved_at})


@_api_auth(csrf=True)
def api_meeting_actions(meeting_id, action_id=None, operation=None):
    if action_id:
        action = db.session.get(MeetingActionItem, action_id)
        if not action or action.meeting_id != meeting_id:
            return _response(error="Action item not found.", status=404)
        if not can_view_meeting(current_user, action.meeting):
            return _response(error="You cannot access this action item.", status=403)
        payload = request.get_json(silent=True) or {}
        try:
            if operation == "comments":
                comment = add_action_comment(current_user, action, payload.get("body", ""),
                                             payload.get("is_parent_visible", False))
                return _response({"id": comment.id, "body": comment.body,
                                  "created_at": comment.created_at}, status=201)
            update_action(current_user, action, payload)
        except PermissionError as exception:
            return _response(error=str(exception), status=403)
        except ValueError as exception:
            db.session.rollback()
            return _response(error=str(exception), status=400)
        return _response(_serialize_action(action, current_user))
    meeting, error = _meeting(meeting_id)
    if error:
        return error
    if request.method == "GET":
        return _response([_serialize_action(action, current_user) for action in meeting.actions
                          if current_user.role != "parent" or action.is_parent_visible])
    payload = request.get_json(silent=True) or {}
    try:
        action = create_action_item(current_user, meeting, payload)
    except PermissionError as exception:
        return _response(error=str(exception), status=403)
    except ValueError as exception:
        db.session.rollback()
        return _response(error=str(exception), status=400)
    return _response(_serialize_action(action, current_user), status=201)


@_api_auth(csrf=True)
def api_meeting_notifications(meeting_id):
    meeting, error = _meeting(meeting_id)
    if error:
        return error
    logs = MeetingNotificationLog.query.filter_by(meeting_id=meeting.id, user_id=current_user.id).order_by(
        MeetingNotificationLog.id.desc()
    ).limit(100).all()
    return _response([{"event": log.dedupe_key, "in_app_status": log.in_app_status,
                       "external_status": log.external_status, "created_at": log.created_at}
                      for log in logs])


@_api_auth(endpoint="meeting_admin", csrf=True)
def api_guardians(student_id=None):
    if request.method == "GET":
        query = StudentGuardian.query
        if student_id is not None:
            query = query.filter_by(student_id=student_id)
        rows = query.order_by(StudentGuardian.student_id, StudentGuardian.guardian_id).limit(500).all()
        return _response([{"id": row.id, "student_id": row.student_id,
                           "student": row.student.full_name if row.student else None,
                           "guardian_id": row.guardian_id,
                           "guardian": row.guardian.full_name if row.guardian else None,
                           "relationship": row.relationship, "status": row.status,
                           "verified_at": row.verified_at, "revoked_at": row.revoked_at} for row in rows])
    if request.method == "DELETE":
        link = db.session.get(StudentGuardian, student_id)
        if not link:
            return _response(error="Guardian relationship not found.", status=404)
        try:
            revoke_guardian_link(current_user, link)
        except ValueError as exception:
            return _response(error=str(exception), status=400)
        return _response({"id": link.id, "status": link.status})
    payload = request.get_json(silent=True) or {}
    try:
        link = verify_guardian_link(
            current_user, int(payload.get("student_id")), int(payload.get("guardian_id")),
            str(payload.get("relationship", "Parent / Guardian")),
        )
    except (TypeError, ValueError) as exception:
        db.session.rollback()
        return _response(error=str(exception) or "Student and guardian IDs must be integers.", status=400)
    except PermissionError as exception:
        return _response(error=str(exception), status=403)
    return _response({"id": link.id, "student_id": link.student_id,
                      "guardian_id": link.guardian_id, "status": link.status}, status=201)


@_api_auth(endpoint="meeting_admin")
def api_meeting_reports():
    try:
        records = _meeting_query()
    except ValueError as error:
        return _response(error=str(error), status=400)
    rows = []
    for meeting in records:
        parents = [item for item in meeting.participants if item.user and item.user.role == "parent"]
        attendance = [item.attendance.status for item in parents if item.attendance]
        rows.append({
            "id": meeting.id, "title": meeting.title, "date": meeting.scheduled_date,
            "type": meeting.meeting_type, "class_name": meeting.class_name,
            "status": meeting.status, "participant_count": len(meeting.participants),
            "parents_invited_count": len(parents),
            "parents_accepted_count": sum(item.invitation_status == "accepted" for item in parents),
            "parents_present_count": sum(value == "Present" for value in attendance),
            "parents_absent_count": sum(value == "Absent" for value in attendance),
            "parents_late_count": sum(value == "Late" for value in attendance),
            "parents_excused_count": sum(value == "Excused" for value in attendance),
        })
    return _response(rows, meta={"total": len(rows), "filters": {
        key: request.args.get(key) for key in ("date_from", "date_to", "class_name", "organizer_id", "type", "status")
        if request.args.get(key)
    }})


@_api_auth(endpoint="meeting_settings", csrf=True)
def api_meeting_settings():
    from app.utils.helpers import get_setting
    if request.method == "GET":
        try:
            grace_days = min(30, max(0, int(get_setting("meeting_overdue_escalation_days", "0"))))
        except (TypeError, ValueError):
            grace_days = 0
        return _response({
            "meeting_approval_required": get_setting("meeting_approval_required", "true") != "false",
            "meeting_overdue_escalation_days": grace_days,
        })
    payload = request.get_json(silent=True) or {}
    approval_required = payload.get("meeting_approval_required")
    if not isinstance(approval_required, bool):
        return _response(error="meeting_approval_required must be true or false.", status=400)
    try:
        grace_days = int(payload.get("meeting_overdue_escalation_days", 0))
    except (TypeError, ValueError):
        return _response(error="meeting_overdue_escalation_days must be an integer from 0 to 30.", status=400)
    if not 0 <= grace_days <= 30:
        return _response(error="meeting_overdue_escalation_days must be an integer from 0 to 30.", status=400)
    values = {
        "meeting_approval_required": "true" if approval_required else "false",
        "meeting_overdue_escalation_days": str(grace_days),
    }
    changed = {}
    for key, value in values.items():
        setting = Setting.query.filter_by(key=key).first()
        old_value = setting.value if setting else None
        if old_value != value:
            changed[key] = {"old": old_value, "new": value}
            if setting:
                setting.value = value
            else:
                db.session.add(Setting(key=key, value=value))
    if changed:
        record_meeting_audit(current_user, None, "meeting_configuration_updated", changed)
    db.session.commit()
    return _response({
        "meeting_approval_required": approval_required,
        "meeting_overdue_escalation_days": grace_days,
    })


def register_routes(app):
    app.add_url_rule("/api/meetings/csrf", endpoint="meeting_api_csrf", view_func=api_csrf_token, methods=["GET"])
    app.add_url_rule("/api/meetings", endpoint="meeting_api_list", view_func=api_meetings, methods=["GET", "POST"])
    app.add_url_rule("/api/meetings/<int:meeting_id>", endpoint="meeting_api_detail", view_func=api_meeting_detail, methods=["GET", "PATCH", "DELETE"])
    app.add_url_rule("/api/meetings/<int:meeting_id>/responses", endpoint="meeting_api_response", view_func=api_meeting_response, methods=["POST"])
    app.add_url_rule("/api/meetings/<int:meeting_id>/participants", endpoint="meeting_api_participants", view_func=api_meeting_participants, methods=["GET", "POST"])
    app.add_url_rule("/api/meetings/<int:meeting_id>/participants/<int:participant_id>", endpoint="meeting_api_participant", view_func=api_meeting_participants, methods=["DELETE"])
    app.add_url_rule("/api/meetings/<int:meeting_id>/attendance", endpoint="meeting_api_attendance", view_func=api_meeting_attendance, methods=["GET", "POST"])
    app.add_url_rule("/api/meetings/<int:meeting_id>/agenda", endpoint="meeting_api_agenda", view_func=api_meeting_agenda, methods=["GET", "POST"])
    app.add_url_rule("/api/meetings/<int:meeting_id>/agenda/<int:item_id>", endpoint="meeting_api_agenda_item", view_func=api_meeting_agenda, methods=["PATCH", "DELETE"])
    app.add_url_rule("/api/meetings/<int:meeting_id>/minutes", endpoint="meeting_api_minutes", view_func=api_meeting_minutes, methods=["GET", "PUT"])
    for operation in ("submit", "approve", "reject"):
        app.add_url_rule(f"/api/meetings/<int:meeting_id>/minutes/{operation}",
                         endpoint=f"meeting_api_minutes_{operation}", view_func=api_meeting_minutes,
                         defaults={"operation": operation}, methods=["POST"])
    app.add_url_rule("/api/meetings/<int:meeting_id>/actions", endpoint="meeting_api_actions", view_func=api_meeting_actions, methods=["GET", "POST"])
    app.add_url_rule("/api/meetings/<int:meeting_id>/actions/<int:action_id>", endpoint="meeting_api_action", view_func=api_meeting_actions, methods=["PATCH"])
    app.add_url_rule("/api/meetings/<int:meeting_id>/actions/<int:action_id>/comments", endpoint="meeting_api_action_comments", view_func=api_meeting_actions, defaults={"operation": "comments"}, methods=["POST"])
    app.add_url_rule("/api/meetings/<int:meeting_id>/notifications", endpoint="meeting_api_notifications", view_func=api_meeting_notifications, methods=["GET"])
    app.add_url_rule("/api/guardians", endpoint="meeting_api_guardians", view_func=api_guardians, methods=["GET", "POST"])
    app.add_url_rule("/api/guardians/<int:student_id>", endpoint="meeting_api_guardian_student", view_func=api_guardians, methods=["GET"])
    app.add_url_rule("/api/guardian-links/<int:student_id>", endpoint="meeting_api_guardian_link_delete", view_func=api_guardians, methods=["DELETE"])
    app.add_url_rule("/api/meetings/reports", endpoint="meeting_api_reports", view_func=api_meeting_reports, methods=["GET"])
    app.add_url_rule("/api/meetings/settings", endpoint="meeting_api_settings", view_func=api_meeting_settings, methods=["GET", "PATCH"])
