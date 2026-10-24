"""Optional Microsoft Graph calendar sync for published school meetings."""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from flask import current_app

from app.extensions import db


GRAPH_ROOT = "https://graph.microsoft.com/v1.0"


def sync_meeting_to_microsoft(meeting, force_online=False):
    config = current_app.config
    tenant_id = config.get("MS_GRAPH_TENANT_ID")
    client_id = config.get("MS_GRAPH_CLIENT_ID")
    client_secret = config.get("MS_GRAPH_CLIENT_SECRET")
    mailbox = config.get("MS_GRAPH_CALENDAR_MAILBOX")
    if not all((tenant_id, client_id, client_secret, mailbox)):
        meeting.microsoft_sync_status = "not_configured"
        meeting.microsoft_sync_error = None
        db.session.commit()
        return False

    if meeting.status not in {"Scheduled", "Rescheduled", "Cancelled"}:
        return False

    try:
        import msal
        client = msal.ConfidentialClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=client_secret,
        )
        token_result = client.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"]
        )
        access_token = token_result.get("access_token")
        if not access_token:
            message = token_result.get("error_description") or token_result.get("error") or "Microsoft authentication failed."
            raise RuntimeError(message[:450])

        mailbox_path = quote(mailbox, safe="")
        event_path = f"/users/{mailbox_path}/calendar/events"
        if meeting.microsoft_event_id:
            event_path += "/" + quote(meeting.microsoft_event_id, safe="")
        url = GRAPH_ROOT + event_path

        if meeting.status == "Cancelled":
            if not meeting.microsoft_event_id:
                meeting.microsoft_sync_status = "cancelled"
                meeting.microsoft_sync_error = None
                db.session.commit()
                return True
            request = Request(url, method="DELETE", headers={"Authorization": f"Bearer {access_token}"})
            with urlopen(request, timeout=15):
                pass
            meeting.microsoft_event_id = None
            meeting.microsoft_join_url = None
            meeting.microsoft_sync_status = "cancelled"
            meeting.microsoft_sync_error = None
            db.session.commit()
            return True

        attendees = []
        for participant in meeting.participants:
            person = participant.user
            email = (person.email or "").strip() if person else ""
            if not email or participant.invitation_status in {"declined", "revoked"}:
                continue
            attendees.append({
                "emailAddress": {"address": email, "name": person.full_name},
                "type": "required",
            })
        event = {
            "subject": meeting.title,
            "body": {"contentType": "text", "content": meeting.description or ""},
            "start": {
                "dateTime": f"{meeting.scheduled_date}T{meeting.start_time}:00",
                "timeZone": config.get("MS_GRAPH_TIME_ZONE", "South Africa Standard Time"),
            },
            "end": {
                "dateTime": f"{meeting.scheduled_date}T{meeting.end_time}:00",
                "timeZone": config.get("MS_GRAPH_TIME_ZONE", "South Africa Standard Time"),
            },
            "location": {"displayName": meeting.location or ""},
            "attendees": attendees,
        }
        if not meeting.microsoft_event_id:
            event["transactionId"] = f"school-meeting-{meeting.id}"
        if force_online or config.get("MS_GRAPH_CREATE_TEAMS_MEETINGS"):
            event["isOnlineMeeting"] = True
            event["onlineMeetingProvider"] = "teamsForBusiness"
        payload = json.dumps(event).encode("utf-8")
        method = "PATCH" if meeting.microsoft_event_id else "POST"
        request = Request(url, data=payload, method=method, headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
        meeting.microsoft_event_id = result.get("id") or meeting.microsoft_event_id
        online_meeting = result.get("onlineMeeting") or {}
        meeting.microsoft_join_url = online_meeting.get("joinUrl") or meeting.microsoft_join_url
        meeting.microsoft_sync_status = "synced"
        meeting.microsoft_sync_error = None
        db.session.commit()
        return True
    except (HTTPError, URLError, RuntimeError, ValueError, OSError, ImportError) as error:
        detail = str(error)
        if isinstance(error, HTTPError):
            try:
                response = json.loads(error.read().decode("utf-8"))
                detail = response.get("error", {}).get("message") or detail
            except (ValueError, AttributeError):
                pass
        meeting.microsoft_sync_status = "error"
        meeting.microsoft_sync_error = detail[:500]
        db.session.commit()
        current_app.logger.warning("Microsoft calendar sync failed for meeting %s: %s", meeting.id, detail[:300])
        return False
