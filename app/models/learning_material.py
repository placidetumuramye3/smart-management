from app.extensions import db


class LearningMaterial(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    title = db.Column(db.String(180), nullable=False)
    resource_type = db.Column(db.String(40), nullable=False, default="Reference Notes")
    class_name = db.Column(db.String(100), nullable=False, index=True)
    subject = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text)
    reference_content = db.Column(db.Text)
    standards = db.Column(db.Text)
    source_reference = db.Column(db.String(500))
    schematic_data = db.Column(db.Text)
    external_url = db.Column(db.String(1000))
    activity_data = db.Column(db.Text)
    attachment_path = db.Column(db.String(255))
    attachment_name = db.Column(db.String(255))
    is_published = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.String(19), nullable=False)
    teacher = db.relationship("User", foreign_keys=[teacher_id])
