"""Store message classification and the sender identity at send time."""

from sqlalchemy import inspect, text


def upgrade(connection):
    """Add message metadata to existing school_message tables without losing history."""
    inspector = inspect(connection)
    if not inspector.has_table("school_message"):
        return

    columns = {column["name"] for column in inspector.get_columns("school_message")}
    if "message_type" not in columns:
        connection.execute(text(
            "ALTER TABLE school_message "
            "ADD COLUMN message_type VARCHAR(24) NOT NULL DEFAULT 'general'"
        ))
    if "sender_name" not in columns:
        connection.execute(text("ALTER TABLE school_message ADD COLUMN sender_name VARCHAR(160)"))
    if "sender_role" not in columns:
        connection.execute(text("ALTER TABLE school_message ADD COLUMN sender_role VARCHAR(30)"))
