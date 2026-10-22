import base64
import logging
import os
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def _send_email(recipient, subject, body):
    host = os.environ.get("SMTP_HOST")
    sender = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USERNAME")
    if not host or not sender:
        return False
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    port = int(os.environ.get("SMTP_PORT", "587"))
    timeout = float(os.environ.get("NOTIFICATION_TIMEOUT", "10"))
    if os.environ.get("SMTP_USE_SSL", "false").lower() == "true":
        with smtplib.SMTP_SSL(host, port, timeout=timeout) as client:
            _smtp_login(client)
            client.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=timeout) as client:
            if os.environ.get("SMTP_STARTTLS", "true").lower() == "true":
                client.starttls()
            _smtp_login(client)
            client.send_message(message)
    return True


def _smtp_login(client):
    username = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")
    if username and password:
        client.login(username, password)


def _send_sms(recipient, body):
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    sender = os.environ.get("TWILIO_FROM_NUMBER")
    if not account_sid or not auth_token or not sender:
        return False
    recipient = "+" + "".join(character for character in recipient if character.isdigit()) if recipient.strip().startswith("+") else "".join(character for character in recipient if character.isdigit())
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    payload = urllib.parse.urlencode({"To": recipient, "From": sender, "Body": body}).encode()
    credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
    request = urllib.request.Request(url, data=payload, headers={"Authorization": f"Basic {credentials}"})
    timeout = float(os.environ.get("NOTIFICATION_TIMEOUT", "10"))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return 200 <= response.status < 300


def notify_attendance_event(parent, title, body):
    """Send parent attendance alerts through configured external providers."""
    send_parent_alert(parent, "Attendance", title, body)


def send_parent_alert(parent, category, title, body, email_enabled=True, sms_enabled=True):
    """Best-effort delivery; in-app notifications remain the durable record."""
    if email_enabled and parent.email:
        try:
            _send_email(parent.email, f"School alert: {title}", f"{category}\n\n{body}")
        except Exception:
            logger.exception("Could not send parent email notification (parent id %s)", parent.id)
    if sms_enabled and parent.phone:
        try:
            _send_sms(parent.phone, f"{title}: {body}")
        except Exception:
            logger.exception("Could not send parent SMS notification (parent id %s)", parent.id)
