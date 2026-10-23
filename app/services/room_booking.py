import datetime

from sqlalchemy import func

from app.extensions import db
from app.models import Meeting, MeetingAttendance, MeetingParticipant, RoomReservation
from app.services.meeting_service import record_meeting_audit


OFFICE_ROLES = {"master_admin", "head_teacher", "secretary"}
MIN_BOOKING_MINUTES = 15
MAX_BOOKING_MINUTES = 240
ROOM_CHECKIN_GRACE_MINUTES = 10


def _check_minutes(minutes):
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        raise ValueError("Choose a booking duration in minutes.")
    if not MIN_BOOKING_MINUTES <= minutes <= MAX_BOOKING_MINUTES:
        raise ValueError(f"Duration must be between {MIN_BOOKING_MINUTES} and {MAX_BOOKING_MINUTES} minutes.")
    return minutes


def _room_conflict(room_name, starts_at, ends_at, exclude_reservation_id=None):
    normalized_room = room_name.strip().casefold()
    reservations = RoomReservation.query.filter(
        RoomReservation.status == "active",
        RoomReservation.starts_at < ends_at,
        RoomReservation.ends_at > starts_at,
    ).all()
    if any(
        reservation.id != exclude_reservation_id
        and reservation.room_name.strip().casefold() == normalized_room
        for reservation in reservations
    ):
        return True

    first_date = starts_at.date().isoformat()
    last_date = ends_at.date().isoformat()
    meetings = Meeting.query.filter(
        Meeting.status.in_({"Scheduled", "Rescheduled"}),
        Meeting.room_released_at.is_(None),
        Meeting.scheduled_date >= first_date,
        Meeting.scheduled_date <= last_date,
    ).all()
    for meeting in meetings:
        if not meeting.location or meeting.location.strip().casefold() != normalized_room:
            continue
        try:
            meeting_start = datetime.datetime.combine(
                datetime.date.fromisoformat(meeting.scheduled_date),
                datetime.time.fromisoformat(meeting.start_time),
            )
            meeting_end = datetime.datetime.combine(
                datetime.date.fromisoformat(meeting.scheduled_date),
                datetime.time.fromisoformat(meeting.end_time),
            )
        except (TypeError, ValueError):
            continue
        if starts_at < meeting_end and meeting_start < ends_at:
            return True
    return False


def reserve_room(user, room_name, purpose="", minutes=60):
    room_name = (room_name or "").strip()
    purpose = (purpose or "").strip() or "Quick room booking"
    if not room_name or len(room_name) > 160:
        raise ValueError("Choose a room name up to 160 characters long.")
    if len(purpose) > 160:
        raise ValueError("Purpose must be 160 characters or fewer.")
    minutes = _check_minutes(minutes)
    starts_at = datetime.datetime.now().replace(second=0, microsecond=0)
    ends_at = starts_at + datetime.timedelta(minutes=minutes)
    if _room_conflict(room_name, starts_at, ends_at):
        raise ValueError("That room is already reserved during this time. Choose another room or check the schedule.")
    booking = RoomReservation(
        room_name=room_name,
        purpose=purpose,
        reserved_by_id=user.id,
        starts_at=starts_at,
        ends_at=ends_at,
        status="active",
        created_at=starts_at,
    )
    db.session.add(booking)
    db.session.commit()
    return booking


def extend_room_reservation(user, reservation_id, minutes=30):
    minutes = _check_minutes(minutes)
    booking = db.session.get(RoomReservation, reservation_id)
    if not booking or booking.status != "active":
        raise ValueError("This room booking is no longer active.")
    if booking.reserved_by_id != user.id and user.role not in OFFICE_ROLES:
        raise PermissionError("You can only extend your own room booking.")
    now = datetime.datetime.now()
    if booking.ends_at <= now:
        raise ValueError("This booking has ended and can no longer be extended.")
    extended_end = booking.ends_at + datetime.timedelta(minutes=minutes)
    if _room_conflict(booking.room_name, booking.ends_at, extended_end, booking.id):
        raise ValueError("The room is reserved after your current booking, so it cannot be extended that far.")
    booking.ends_at = extended_end
    db.session.commit()
    return booking


def release_room_reservation(user, reservation_id):
    booking = db.session.get(RoomReservation, reservation_id)
    if not booking or booking.status != "active":
        raise ValueError("This room booking is already released.")
    if booking.reserved_by_id != user.id and user.role not in OFFICE_ROLES:
        raise PermissionError("You can only release your own room booking.")
    booking.status = "released"
    booking.released_at = datetime.datetime.now()
    db.session.commit()
    return booking


def current_room_reservations():
    now = datetime.datetime.now()
    return RoomReservation.query.filter(
        RoomReservation.status == "active",
        RoomReservation.starts_at <= now,
        RoomReservation.ends_at > now,
    ).order_by(RoomReservation.room_name, RoomReservation.starts_at).all()


def suggested_room_names():
    today = datetime.date.today().isoformat()
    horizon = (datetime.date.today() + datetime.timedelta(days=7)).isoformat()
    names = {
        reservation.room_name.strip()
        for reservation in current_room_reservations()
        if reservation.room_name and reservation.room_name.strip()
    }
    names.update(
        location.strip() for location, in db.session.query(Meeting.location).filter(
            Meeting.status.in_({"Scheduled", "Rescheduled"}),
            Meeting.scheduled_date >= today,
            Meeting.scheduled_date <= horizon,
            Meeting.location.isnot(None),
            func.trim(Meeting.location) != "",
        ).distinct().all()
    )
    return sorted(names, key=str.casefold)


def check_in_student_for_current_meetings(student, now=None):
    now = now or datetime.datetime.now()
    day = now.date().isoformat()
    meetings = Meeting.query.filter(
        Meeting.scheduled_date == day,
        Meeting.status.in_({"Scheduled", "Rescheduled"}),
        Meeting.room_released_at.is_(None),
    ).all()
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    checked_in = 0
    for meeting in meetings:
        try:
            start = datetime.datetime.combine(
                datetime.date.fromisoformat(meeting.scheduled_date),
                datetime.time.fromisoformat(meeting.start_time),
            )
            end = datetime.datetime.combine(
                datetime.date.fromisoformat(meeting.scheduled_date),
                datetime.time.fromisoformat(meeting.end_time),
            )
        except (TypeError, ValueError):
            continue
        if not start <= now < min(end, start + datetime.timedelta(minutes=ROOM_CHECKIN_GRACE_MINUTES)):
            continue
        participant = MeetingParticipant.query.filter_by(
            meeting_id=meeting.id, user_id=student.id,
        ).first()
        if not participant or participant.invitation_status in {"declined", "revoked"}:
            continue
        attendance = MeetingAttendance.query.filter_by(participant_id=participant.id).first()
        if attendance and attendance.status == "Present":
            continue
        if not attendance:
            attendance = MeetingAttendance(participant_id=participant.id)
            db.session.add(attendance)
        attendance.status = "Present"
        attendance.verified_by_id = student.id
        attendance.updated_at = stamp
        record_meeting_audit(student, meeting.id, "rfid_room_checkin", {
            "participant_id": participant.id,
            "checked_in_at": stamp,
        })
        checked_in += 1
    if checked_in:
        db.session.commit()
    return checked_in


def release_unchecked_meeting_rooms(now=None):
    now = now or datetime.datetime.now()
    meetings = Meeting.query.filter(
        Meeting.scheduled_date == now.date().isoformat(),
        Meeting.status.in_({"Scheduled", "Rescheduled"}),
        Meeting.room_released_at.is_(None),
        Meeting.location.isnot(None),
    ).all()
    released = 0
    for meeting in meetings:
        try:
            start = datetime.datetime.combine(
                datetime.date.fromisoformat(meeting.scheduled_date),
                datetime.time.fromisoformat(meeting.start_time),
            )
            end = datetime.datetime.combine(
                datetime.date.fromisoformat(meeting.scheduled_date),
                datetime.time.fromisoformat(meeting.end_time),
            )
        except (TypeError, ValueError):
            continue
        deadline = start + datetime.timedelta(minutes=ROOM_CHECKIN_GRACE_MINUTES)
        if now < deadline or now >= end:
            continue
        checked_in = MeetingAttendance.query.join(
            MeetingParticipant, MeetingAttendance.participant_id == MeetingParticipant.id,
        ).filter(
            MeetingParticipant.meeting_id == meeting.id,
            MeetingAttendance.status == "Present",
        ).first()
        if checked_in:
            continue
        meeting.room_released_at = now.strftime("%Y-%m-%d %H:%M:%S")
        meeting.room_release_reason = "No attendee checked in within 10 minutes of the start time."
        record_meeting_audit(None, meeting.id, "room_auto_released_no_checkin", {
            "room": meeting.location,
            "reason": meeting.room_release_reason,
            "grace_minutes": ROOM_CHECKIN_GRACE_MINUTES,
        })
        released += 1
    if released:
        db.session.commit()
    return released
