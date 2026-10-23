"""Create on-the-spot room reservation records."""

from sqlalchemy import inspect


def upgrade(connection):
    if not inspect(connection).has_table("user"):
        return
    from app.models import RoomReservation
    RoomReservation.__table__.create(bind=connection, checkfirst=True)
