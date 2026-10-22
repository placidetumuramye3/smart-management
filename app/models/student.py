"""Student domain helpers.

The current database stores students in User rows with role="student".
A dedicated StudentProfile model can be introduced later via a migration without breaking current data.
"""
from .user import User

def student_query():
    return User.query.filter_by(role="student")
