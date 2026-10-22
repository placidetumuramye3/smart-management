from app.services.rfid_service import normalize_uid

def test_normalize_uid():
    assert normalize_uid(" a1 b2 c3 ") == "A1B2C3"
