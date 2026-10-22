from app.extensions import db
class PendingCard(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    uid = db.Column(db.String(100), unique=True, nullable=False)
    first_seen = db.Column(db.String(19), nullable=False)
    last_seen = db.Column(db.String(26))
    state = db.Column(db.String(30), default="Pending")

