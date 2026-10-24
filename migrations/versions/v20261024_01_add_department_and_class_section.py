"""Add department and class-section fields to student placement records."""

from sqlalchemy import inspect, text


def upgrade(connection):
    inspector = inspect(connection)

    if inspector.has_table("user"):
        user_columns = {column["name"] for column in inspector.get_columns("user")}
        if "department" not in user_columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN department VARCHAR(100)"))
        if "class_section" not in user_columns:
            connection.execute(text("ALTER TABLE user ADD COLUMN class_section VARCHAR(50)"))
        if "trade" in user_columns:
            connection.execute(text(
                "UPDATE user SET department = trade "
                "WHERE role = 'student' AND (department IS NULL OR TRIM(department) = '') "
                "AND trade IS NOT NULL AND TRIM(trade) != ''"
            ))

    if inspector.has_table("admission_applicant"):
        applicant_columns = {
            column["name"] for column in inspector.get_columns("admission_applicant")
        }
        if "desired_department" not in applicant_columns:
            connection.execute(text(
                "ALTER TABLE admission_applicant ADD COLUMN desired_department VARCHAR(100)"
            ))
        if "desired_section" not in applicant_columns:
            connection.execute(text(
                "ALTER TABLE admission_applicant ADD COLUMN desired_section VARCHAR(50)"
            ))
        if "desired_trade" in applicant_columns:
            connection.execute(text(
                "UPDATE admission_applicant SET desired_department = desired_trade "
                "WHERE (desired_department IS NULL OR TRIM(desired_department) = '') "
                "AND desired_trade IS NOT NULL AND TRIM(desired_trade) != ''"
            ))
