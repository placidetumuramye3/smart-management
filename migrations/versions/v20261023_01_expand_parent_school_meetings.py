"""Add parent meeting requests, staff assignment, attendance, and follow-up."""

from sqlalchemy import inspect, text


def upgrade(connection):
    if not inspect(connection).has_table("appointment"):
        return

    columns = {column["name"] for column in inspect(connection).get_columns("appointment")}
    additions = {
        "parent_id": "INTEGER",
        "staff_id": "INTEGER",
        "parent_attended": "BOOLEAN",
        "staff_attended": "BOOLEAN",
        "staff_notes": "TEXT",
        "decision_summary": "TEXT",
        "follow_up_actions": "TEXT",
        "follow_up_due_date": "VARCHAR(10)",
    }
    for name, column_type in additions.items():
        if name not in columns:
            connection.execute(text(
                f"ALTER TABLE appointment ADD COLUMN {name} {column_type}"
            ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_appointment_parent_id ON appointment (parent_id)"
    ))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_appointment_staff_id ON appointment (staff_id)"
    ))
