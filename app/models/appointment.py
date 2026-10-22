from app.extensions import db


class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    visitor_name = db.Column(db.String(160), nullable=False)
    visitor_phone = db.Column(db.String(40))
    visitor_email = db.Column(db.String(160))
    student_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    purpose = db.Column(db.String(200), nullable=False)
    scheduled_date = db.Column(db.String(10), nullable=False, index=True)
    scheduled_time = db.Column(db.String(5), nullable=False)
    notes = db.Column(db.Text)
    status = db.Column(db.String(30), nullable=False, default="Scheduled", index=True)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    student = db.relationship("User", foreign_keys=[student_id])
    creator = db.relationship("User", foreign_keys=[created_by])
