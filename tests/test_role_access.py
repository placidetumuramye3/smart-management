from app.utils.constants import ROLE_ACCESS, ROLES, normalize_role


def test_role_aliases_and_membership():
    assert normalize_role("admin") == "master_admin"
    assert normalize_role("master_admin") == "master_admin"
    assert "master_admin" in ROLES
    assert "admin" in ROLES


def test_module_role_matrix_matches_core_permissions():
    assert "student" in ROLE_ACCESS["dashboard"]
    assert "parent" in ROLE_ACCESS["dashboard"]
    assert "teacher" in ROLE_ACCESS["attendance"]
    assert "accountant" in ROLE_ACCESS["fees"]
    assert "librarian" in ROLE_ACCESS["library"]
    assert "secretary" in ROLE_ACCESS["grades"]
    assert "teacher" not in ROLE_ACCESS["fees"]
    assert "student" in ROLE_ACCESS["library"]
