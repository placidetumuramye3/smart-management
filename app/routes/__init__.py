def register_all_routes(app):
    from . import main, auth, dashboard, rfid, admissions, documents, parents, appointments, meetings, meeting_api, announcements, correspondence, letters, reports, activity_history, students, attendance, fees, library, discipline, messages, academics, admin, notifications, assessments, parent_portal, learning_materials, classrooms
    for module in (main, auth, dashboard, rfid, admissions, documents, parents, appointments, meetings, meeting_api, announcements, correspondence, letters, reports, activity_history, students, attendance, fees, library, discipline, messages, academics, admin, notifications, assessments, parent_portal, learning_materials, classrooms):
        module.register_routes(app)
