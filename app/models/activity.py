from app.extensions import db


class ActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer)
    actor_name = db.Column(db.String(160), nullable=False)
    student_id = db.Column(db.Integer, index=True)
    student_name = db.Column(db.String(160), nullable=False)
    action = db.Column(db.String(80), nullable=False, index=True)
    details = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now(), index=True)
