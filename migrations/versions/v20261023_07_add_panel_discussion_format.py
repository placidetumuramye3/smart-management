"""Store panel discussion Q&A duration."""

from sqlalchemy import inspect, text


def upgrade(connection):
    inspector = inspect(connection)
    if not inspector.has_table("meeting"):
        return
    columns = {column["name"] for column in inspector.get_columns("meeting")}
    if "qa_duration_minutes" not in columns:
        connection.execute(text("ALTER TABLE meeting ADD COLUMN qa_duration_minutes INTEGER"))
    if "panel_perspectives" not in columns:
        connection.execute(text("ALTER TABLE meeting ADD COLUMN panel_perspectives VARCHAR(1000)"))
