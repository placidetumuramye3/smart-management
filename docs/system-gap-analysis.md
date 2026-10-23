# Integrated School System: Repository Gap Analysis

## Repository baseline

The application is a Flask 3 and Flask-SQLAlchemy system. It uses server rendered Jinja pages, Flask-Login sessions, and a single relational database. Student accounts are canonical `User` rows with `role="student"`; `app/models/student.py` only supplies a query helper. Existing features include admissions, student registration, class/subject assignments, lesson plans, learning materials, homework/quizzes/tests, rubric grading, official grades/report cards, RFID based daily attendance, messaging, parent notifications, and the parent-school meeting panel.

The system should continue using those records as its source of truth. In particular, LMS submissions should remain separate from approved official `Grade` records unless a future school policy explicitly links them.

## Gap matrix

| Area | Current implementation | Main gap |
| --- | --- | --- |
| Student identity and enrollment | Student is a `User`; new registrations and staff transfers create dated `StudentEnrollment` history while synchronizing current class fields. Existing placements are imported as legacy active rows. | Historical start dates remain unknown where the old database did not record them. Promotion, withdrawal, and graduation use the history service; remaining class routes still read synchronized `User` fields. |
| Academic structure | `AcademicSubject` stores class, subject, teacher, and year; timetable and syllabus/lesson models exist. | Class/subject/year membership is not consistently enforced through a shared enrollment-aware service; a subject row allows only one teacher assignment. |
| LMS classrooms | `/classrooms` now groups official active subject assignments with lesson plans, published resources, and assignments. Students and parents are scoped through active enrollment and verified guardian links; students see reviewed lessons and published/targeted work. | Existing academic routes still use a mix of current `User` class fields and assignment rows. Resource version/archive history and a persistent classroom entity are absent. |
| Assignments and assessment | Homework, quizzes, tests, timed attempts, auto-marking, rubric grading, and one submission per student exist. | No resubmission history, configurable late policy, explicit LMS-grade release state, or broad workflow/API tests. Official grades are already separate. |
| Guardians and parent access | Central helpers resolve verified/revocable links with a backward-compatible fallback for unconverted `parent_id` records. Registration, office linking, reports, student records, discipline, assessment files, appointments, messages, meeting workflows, and absence notifications use guardian-aware scope. | Self-registration treats possession of the school's SDMS code as a verified link; the school should confirm that this is acceptable identity proof. Legacy routes outside these audited paths may still rely on current class or single-role fields. |
| Attendance and RFID | RFID UID assignment, pending cards, duplicate daily lookup, arrival/late classification, absence reconciliation, and class notifications exist. | No database uniqueness constraint for student/day, authorized manual correction with audit, device/event history, or offline reconciliation model. |
| Meetings and communication | Role-scoped meeting panel, secure linked conversations, approvals, attendance, minutes, actions, guardian verification, audit, and reports exist. | Provider delivery receipts/retries and school-specific retention remain unavailable; messages are not linked to assignments throughout the app. |
| Results and governance | Separate `Grade` model, publication flag, report-card approval, and parent/student views exist. | No correction request/approval workflow or comprehensive history for grade edits. |
| Roles and account governance | Role matrix and class-scope checks exist for current roles. | One stored role per account; no multiple-role assignment or controlled workspace switcher. Deputy and academic-coordinator roles are not represented. |
| APIs and operations | Meeting JSON API and RFID endpoint exist; Flask CLI jobs run meeting reminders/escalation. | Most academic and enrollment workflows have no documented JSON API; no queue, scheduler, health checks, performance suite, or backup verification automation. |
| Tests | 29 pytest tests pass, including enrollment migration/transitions, classroom scope, guardian privacy, multi-guardian access, meetings, attendance, RFID, role access, and student routes. | Assignment submission/grading end-to-end journeys, manual attendance correction, grade release, and complete secretary-to-parent acceptance still need tests. |

## Incremental implementation plan

1. **Completed in this slice:** add enrollment history around the existing `User` student and current class fields. Import existing placements as legacy active rows, expose authorized transfer/withdrawal/graduation, and keep the user row synchronized.
2. **Partly complete:** add a classroom landing page over existing `AcademicSubject`, `LessonPlan`, `LearningMaterial`, and `Assessment` records with server-side teacher, student, and guardian scopes. Next, bring every resource and assignment action into the same enrollment-aware service and add end-to-end submission and grading tests.
3. Add assignment resubmission history, a configurable late policy, and a clear release state for LMS grades. Keep official grades and report-card approvals independent.
4. Add database-level daily attendance uniqueness and an audited, role-scoped correction workflow. Extend RFID event history only after confirming deployment device requirements.
5. **Completed in this slice:** centralize verified guardian lookup, apply it to the listed student/result/notification/meeting paths, and add multi-guardian privacy tests. Continue auditing remaining legacy access routes.
6. Add the missing API contracts, accessibility checks, operational health and recovery guidance, and test the end-to-end secretary-to-parent journey. Avoid claiming production readiness until school policy and deployment requirements are supplied and verified.

## Initial verification

- Repository test baseline: `python -m pytest` — 17 passed (Python 3.13.15, pytest 9.1.1).
- Before enrollment changes: 17 tests passed (Python 3.13.15, pytest 9.1.1).
- After enrollment, guardian-access, and classroom hub changes: 29 tests passed (Python 3.13.15, pytest 9.1.1).
- No new student identity or class identity was introduced. Full LMS, attendance-correction, multi-role, and operational requirements remain open.
