"""Add Microsoft calendar sync state to meetings."""

from sqlalchemy import inspect, text


def upgrade(connection):
    inspector = inspect(connection)
    if not inspector.has_table("meeting"):
        return
    columns = {column["name"] for column in inspector.get_columns("meeting")}
    additions = {
        "microsoft_event_id": "VARCHAR(255)",
        "microsoft_join_url": "VARCHAR(1000)",
        "microsoft_sync_status": "VARCHAR(24) NOT NULL DEFAULT 'not_configured'",
        "microsoft_sync_error": "VARCHAR(500)",
    }
    for name, column_type in additions.items():
        if name not in columns:
            connection.execute(text(f"ALTER TABLE meeting ADD COLUMN {name} {column_type}"))
