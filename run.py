from app import create_app
from app.extensions import db
from app.models import User, Grade, FeeTransaction, FeeRequirement
from app.utils.helpers import generate_sdms_code
from sqlalchemy import inspect, text

app = create_app()

with app.app_context():
    db.create_all()
    # Upgrade existing SQLite databases without requiring an external migration tool.
    user_columns = {column["name"] for column in inspect(db.engine).get_columns("user")}
    if "sdms_code" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN sdms_code VARCHAR(32)"))
        db.session.commit()
    if "profile_photo" not in user_columns:
        db.session.execute(text("ALTER TABLE user ADD COLUMN profile_photo VARCHAR(255)"))
        db.session.commit()
    db.session.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_user_sdms_code ON user (sdms_code)"))
    db.session.commit()
    for student in User.query.filter_by(role="student", sdms_code=None).all():
        student.sdms_code = generate_sdms_code()
    db.session.commit()
    grade_columns = {column["name"] for column in inspect(db.engine).get_columns("grade")}
    if "remarks" not in grade_columns:
        db.session.execute(text("ALTER TABLE grade ADD COLUMN remarks TEXT"))
    if "is_published" not in grade_columns:
        db.session.execute(text("ALTER TABLE grade ADD COLUMN is_published BOOLEAN NOT NULL DEFAULT 0"))
    db.session.commit()
    fee_columns = {column["name"] for column in inspect(db.engine).get_columns("fee_transaction")}
    if "requirement_id" not in fee_columns:
        db.session.execute(text("ALTER TABLE fee_transaction ADD COLUMN requirement_id INTEGER REFERENCES fee_requirement(id)"))
        db.session.commit()
    discipline_columns = {column["name"] for column in inspect(db.engine).get_columns("discipline_case")}
    if "action_taken" not in discipline_columns:
        db.session.execute(text("ALTER TABLE discipline_case ADD COLUMN action_taken TEXT"))
    if "remarks" not in discipline_columns:
        db.session.execute(text("ALTER TABLE discipline_case ADD COLUMN remarks TEXT"))
    if "record_type" not in discipline_columns:
        db.session.execute(text("ALTER TABLE discipline_case ADD COLUMN record_type VARCHAR(30) NOT NULL DEFAULT 'Concern'"))
    if "is_published" not in discipline_columns:
        db.session.execute(text("ALTER TABLE discipline_case ADD COLUMN is_published BOOLEAN NOT NULL DEFAULT 0"))
    db.session.commit()
    grade_columns = {column["name"] for column in inspect(db.engine).get_columns("grade")}
    grade_additions = {
        "academic_year": "VARCHAR(9)",
        "module_code": "VARCHAR(40)",
        "module_category": "VARCHAR(32)",
        "module_weight": "FLOAT",
        "formative_marks": "FLOAT",
        "integrated_marks": "FLOAT",
        "comprehensive_marks": "FLOAT",
        "reassessment_marks": "FLOAT",
    }
    for column_name, column_type in grade_additions.items():
        if column_name not in grade_columns:
            db.session.execute(text(f"ALTER TABLE grade ADD COLUMN {column_name} {column_type}"))
    db.session.commit()
    material_columns = {column["name"] for column in inspect(db.engine).get_columns("learning_material")}
    material_additions = {
        "resource_type": "VARCHAR(40) NOT NULL DEFAULT 'Reference Notes'",
        "standards": "TEXT",
        "source_reference": "VARCHAR(500)",
        "schematic_data": "TEXT",
        "external_url": "VARCHAR(1000)",
        "activity_data": "TEXT",
    }
    for column_name, column_type in material_additions.items():
        if column_name not in material_columns:
            db.session.execute(text(f"ALTER TABLE learning_material ADD COLUMN {column_name} {column_type}"))
    db.session.commit()
    if not User.query.filter_by(role="master_admin").first():
        adm = User(username="admin", full_name="Master Administrator", role="master_admin")
        adm.set_password("admin1234")
        db.session.add(adm)
        db.session.commit()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
