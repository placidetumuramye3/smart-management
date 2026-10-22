from app.extensions import db


class Correspondence(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    direction = db.Column(db.String(20), nullable=False, index=True)
    reference_number = db.Column(db.String(80), nullable=False, unique=True)
    contact_name = db.Column(db.String(180), nullable=False)
    subject = db.Column(db.String(240), nullable=False)
    details = db.Column(db.Text)
    letter_date = db.Column(db.String(10), nullable=False, index=True)
    status = db.Column(db.String(30), nullable=False, default="Received", index=True)
    recorded_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    recorded_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    recorder = db.relationship("User", foreign_keys=[recorded_by])
