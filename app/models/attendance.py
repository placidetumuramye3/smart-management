from app.extensions import db
class Attendance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    day = db.Column(db.String(10), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="Present")
    arrival_time = db.Column(db.String(19))
    afternoon_time = db.Column(db.String(19))
    source = db.Column(db.String(20), default="RFID")
    note = db.Column(db.String(250))
    edited_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    student = db.relationship("User", foreign_keys=[student_id], backref="attendance_records")
    editor = db.relationship("User", foreign_keys=[edited_by])

