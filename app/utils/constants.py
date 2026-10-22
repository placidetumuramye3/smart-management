ROLE_ALIASES = {
    "administrator": "master_admin",
    "super_admin": "master_admin",
    "master_admin": "master_admin",
    "teacher": "teacher",
    "accountant": "accountant",
    "librarian": "librarian",
    "discipline": "discipline",
    "disciplinary": "discipline",
    "patron": "patron",
    "matron": "matron",
    "auditor": "auditor",
    "secretary": "secretary",
    "parent": "parent",
    "student": "student",
}

ROLES = (
    "master_admin",
    "head_teacher",
    "teacher",
    "accountant",
    "librarian",
    "discipline",
    "patron",
    "matron",
    "auditor",
    "secretary",
    "parent",
    "student",
)
ADMIN_ROLES = {"master_admin"}
STAFF_ROLES = set(ROLES) - {"student", "parent"}


def normalize_role(role):
    if role is None:
        return None
    key = str(role).strip().lower()
    return ROLE_ALIASES.get(key, key)


ROLE_ACCESS = {
    "dashboard": {"master_admin", "teacher", "accountant", "librarian", "discipline", "patron", "matron", "secretary", "parent", "student", "auditor"},
    "students": {"master_admin", "teacher", "librarian", "accountant", "discipline", "patron", "matron", "secretary"},
    "admissions": {"master_admin", "head_teacher", "secretary"},
    "documents": {"master_admin", "head_teacher", "secretary"},
    "parent_contacts": {"master_admin", "head_teacher", "secretary"},
    "appointments": {"master_admin", "head_teacher", "secretary"},
    "announcements": {"master_admin", "head_teacher", "teacher", "accountant", "librarian", "discipline", "patron", "matron", "secretary", "parent", "student"},
    "correspondence": {"master_admin", "head_teacher", "secretary"},
    "letters_certificates": {"master_admin", "head_teacher", "secretary"},
    "reports": {"master_admin", "head_teacher", "secretary"},
    "activity_history": {"master_admin", "head_teacher", "secretary"},
    "card_lookup": {"master_admin", "teacher", "secretary"},
    "report_lost_card": {"master_admin", "head_teacher", "secretary"},
    "attendance": {"master_admin", "teacher", "discipline", "patron", "matron", "secretary", "librarian", "parent", "student"},
    "attendance_times": {"master_admin"},
    "export_attendance": {"master_admin", "teacher", "secretary"},
    "grades": {"master_admin", "teacher", "secretary", "parent", "student"},
    "report_card": {"master_admin", "teacher", "secretary", "parent", "student"},
    "export_report_card": {"master_admin", "teacher", "secretary", "parent", "student"},
    "timetable": {"master_admin", "teacher", "librarian", "accountant", "discipline", "patron", "matron", "secretary", "parent", "student"},
    "academic_management": {"master_admin", "head_teacher", "teacher"},
    "day_schedule": {"master_admin", "teacher", "accountant", "librarian", "discipline", "patron", "matron", "secretary", "parent", "student", "auditor"},
    "fees": {"master_admin", "accountant", "parent", "student", "secretary"},
    "library": {"master_admin", "librarian", "teacher", "student", "parent"},
    "discipline": {"master_admin", "teacher", "discipline", "patron", "matron", "secretary", "parent", "student"},
    "messages": {"master_admin", "head_teacher", "teacher", "accountant", "librarian", "discipline", "patron", "matron", "secretary", "parent", "student", "auditor"},
    "notifications": {"master_admin", "head_teacher", "teacher", "accountant", "librarian", "discipline", "patron", "matron", "secretary", "parent", "student", "auditor"},
    "assessments": {"master_admin", "teacher", "student", "parent"},
    "create_rubric": {"master_admin", "teacher"},
    "learning_materials": {"master_admin", "teacher", "student"},
    "learning_material_file": {"master_admin", "teacher", "student"},
    "submit_assessment": {"student"},
    "start_assessment": {"student"},
    "grade_assessment": {"master_admin", "teacher"},
    "assessment_file": {"master_admin", "teacher", "student", "parent"},
    "assessment_submission_file": {"master_admin", "teacher", "student", "parent"},
    "admin": {"master_admin"},
    "parent_child": {"parent"},
    "parent_notification_settings": {"parent"},
    "student_profile": {"master_admin", "teacher", "accountant", "librarian", "discipline", "patron", "matron", "secretary", "parent", "student"},
    "student_photo": {"master_admin", "teacher", "accountant", "librarian", "discipline", "patron", "matron", "secretary", "parent", "student"},
}

# Head Teachers supervise academic and student affairs across the school.
for _endpoint in (
    "dashboard", "students", "card_lookup", "report_lost_card", "admissions", "documents", "parent_contacts", "appointments", "announcements", "correspondence", "letters_certificates", "reports", "activity_history", "attendance", "attendance_times",
    "export_attendance", "grades", "report_card", "export_report_card", "timetable",
    "day_schedule", "discipline", "assessments", "create_rubric", "grade_assessment",
    "learning_materials", "learning_material_file", "assessment_file",
    "assessment_submission_file", "student_profile", "student_photo",
):
    ROLE_ACCESS[_endpoint].add("head_teacher")
VALID_ATTENDANCE = {"Present", "Absent", "Late", "Permission"}
DEFAULT_MORNING_START = "06:30"
DEFAULT_LATE_AFTER = "08:00"
DEFAULT_AFTERNOON_START = "16:00"
DEFAULT_AFTERNOON_END = "16:30"
TRADES = ["ICT and Multimedia", "Fashion Design", "Professional Accountant"]
GENERAL_LEVELS = ["L3", "L4", "L5"]
ACCOUNTING_LEVELS = ["S4", "S5", "S6"]
PROGRAM_LEVELS = {
    "ICT and Multimedia": GENERAL_LEVELS,
    "Fashion Design": GENERAL_LEVELS,
    "Professional Accountant": ACCOUNTING_LEVELS,
}
CLASS_GROUPS = {
    "ICT and Multimedia": {
        "L3": ["L3 Software Development", "L3 Networking and Internet Technology", "L3 Computer System and Architecture"],
        "L4": ["L4 Software Development A", "L4 Software Development B", "L4 Networking and Internet Technology A", "L4 Networking and Internet Technology B", "L4 Computer System and Architecture"],
        "L5": ["L5 Software Development", "L5 Networking and Internet Technology", "L5 Computer System and Architecture"],
    },
    "Fashion Design": {
        "L3": ["L3 Fashion Design"],
        "L4": ["L4 Fashion Design"],
        "L5": ["L5 Fashion Design"],
    },
    "Professional Accountant": {
        "S4": ["S4 Professional Accountant", "S4 Professional Accountant A"],
        "S5": ["S5 Professional Accountant A", "S5 Professional Accountant B"],
        "S6": ["S6 Professional Accountant"],
    },
}
CLASS_TRADE_LEVEL = {
    class_name: (trade, level)
    for trade, levels in CLASS_GROUPS.items()
    for level, class_names in levels.items()
    for class_name in class_names
}
CLASS_NAMES = list(CLASS_TRADE_LEVEL)
ALLOWED_LOGO_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
