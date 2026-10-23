from app.extensions import db
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import validates
from app.utils.constants import normalize_role

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    full_name = db.Column(db.String(160), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(30), nullable=False, default="student")
    email = db.Column(db.String(160), unique=True)
    phone = db.Column(db.String(40))
    date_of_birth = db.Column(db.Date)
    gender = db.Column(db.String(30))
    academic_year = db.Column(db.String(9))
    guardian_name = db.Column(db.String(160))
    guardian_relationship = db.Column(db.String(80))
    guardian_phone = db.Column(db.String(40))
    address = db.Column(db.String(500))
    emergency_contact_name = db.Column(db.String(160))
    emergency_contact_phone = db.Column(db.String(40))
    emergency_contact_relationship = db.Column(db.String(80))
    level = db.Column(db.String(50))
    trade = db.Column(db.String(100))
    class_name = db.Column(db.String(100))
    sdms_code = db.Column(db.String(32), unique=True)
    profile_photo = db.Column(db.String(255))
    rfid_uid = db.Column(db.String(100), unique=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    is_active_account = db.Column(db.Boolean, default=True)
    children = db.relationship("User", backref=db.backref("parent", remote_side=[id]), lazy=True)

    @validates("role")
    def normalize_role_alias(self, _key, value):
        return normalize_role(value)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

