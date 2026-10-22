from app.services.attendance_service import classify_morning_status

def test_attendance_classification():
    assert classify_morning_status("07:30", "08:00") == "Present"
    assert classify_morning_status("08:01", "08:00") == "Late"
