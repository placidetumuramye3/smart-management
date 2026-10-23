"""Track when a meeting room is released for missing attendee check-ins."""

from sqlalchemy import inspect, text


def upgrade(connection):
    inspector = inspect(connection)
    if not inspector.has_table("meeting"):
        return
    columns = {column["name"] for column in inspector.get_columns("meeting")}
    if "room_released_at" not in columns:
        connection.execute(text("ALTER TABLE meeting ADD COLUMN room_released_at VARCHAR(19)"))
    if "room_release_reason" not in columns:
        connection.execute(text("ALTER TABLE meeting ADD COLUMN room_release_reason VARCHAR(160)"))
