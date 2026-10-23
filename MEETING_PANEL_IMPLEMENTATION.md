# Parent and School Administration Meeting Panel

## Repository analysis

- The application is a Flask 3 and Flask-SQLAlchemy system. Routes are registered from `app/routes/`, permissions are enforced by `permission_for()` and `ROLE_ACCESS`, and most interfaces render server-generated HTML through the shared page helper.
- `Appointment` already stores front-office visits and parent meeting requests. It will remain compatible and continue to back `/appointments` and existing reports.
- The `User.parent_id` relationship represents one parent per student. A separate verified guardian relation is required for multiple guardians; legacy `parent_id` links will remain valid.
- `MessageConversation` and `MessageParticipant` provide the existing private messaging foundation. Meeting communication will link to that system and retain its access checks.
- App startup applies idempotent schema upgrades before `create_all`; tests use an in-memory SQLite database. `openpyxl` is already a dependency for Excel output.

## Delivery status

Implemented the meeting workspace and the supporting workflows in this repository. The legacy `/appointments` workflow remains available. New schema and compatibility changes are in `v20261023_01` and `v20261023_02`; user and API documentation is under `docs/`.

- Added scoped dashboards, calendar and filter views, parent requests, meeting creation and publication approval, invitation responses, participant management, attendance, ordered agendas, versioned minutes and approval, action assignment/progress/comments, guardian verification, notifications, audit view, policy settings, and filtered XLSX/PDF reports.
- Added linked private meeting conversations with participant access checks and audit coverage when participants are added or withdrawn.
- Added authenticated, session-based JSON endpoints with CSRF checks, role and record scope, pagination, and write limits. The endpoint matrix is in `docs/meetings-api.md`.
- Added Flask CLI jobs `send-meeting-reminders` and `process-meeting-actions`, plus deployment and schema notes in `docs/`.
- Added focused tests in `tests/test_meetings.py` for roles, privacy, workflows, migration behavior, API protections, exports, and notifications.

## Verification status

- `git diff --check` completed without whitespace errors.
- `python -m pytest`: **17 passed** (Python 3.13.15, pytest 9.1.1).
- Production scheduling of the reminder and escalation CLI jobs, delivery-provider receipts, and retention policy remain deployment choices; the repository documents the required operational setup.

## Change plan (completed)

1. **Foundation:** Add meeting, participant/invitation, verified guardian, agenda, minutes/revision, action, notification-delivery, and audit entities. Apply an idempotent migration that preserves existing appointments and imports legacy parent links.
2. **Authorization and service rules:** Add centralized meeting visibility, guardian verification, class scope, conflict detection, invitation deduplication, allowed status transitions, notification deduplication, and audit recording.
3. **Core workflows:** Add a responsive meeting workspace for parent requests and responses, staff scheduling and participant selection, secretary agendas and attendance, headteacher approvals, approved minutes, and action tracking. Keep `/appointments` available.
4. **Communication and reports:** Associate secure conversations with meetings, add role-scoped dashboards and filters, and export scoped reports to XLSX and PDF.
5. **API and operations:** Add authenticated JSON endpoints for core meeting operations, publish an endpoint/role matrix, and provide CLI jobs for reminders and overdue-action escalation.
6. **Verification:** Add permission, migration, lifecycle, conflict, privacy, export, and API tests. Run the full test suite and record the result here.

## Role boundaries

| Role | Meeting scope and permitted changes |
| --- | --- |
| Parent / guardian | Own invitations and requests; respond, request a new time, view approved minutes, own attendance, relevant decisions, and action progress. A student-specific record requires an active verified guardian relation or a legacy parent link. |
| Class teacher | Meetings for currently assigned classes or meetings assigned to them; create class appointments, prepare agenda/minutes, record attendance, and update assigned actions. |
| Secretary | School-wide scheduling and participant management; agendas, attendance, and draft minutes. Cannot approve minutes unless also the designated approver. |
| Headteacher | School-wide review, chairing, designated approvals, and action oversight. |
| Master administrator | School-wide system access and configuration. |
| Other support staff | Only assigned meetings or students in their current class scope. |

## API outline

- `GET/POST /api/meetings`: list authorized meetings and create a meeting.
- `GET/PATCH/DELETE /api/meetings/<id>`: read, update, or cancel within the caller's scope.
- `POST /api/meetings/<id>/responses`: accept, decline, or request rescheduling for the caller's invitation.
- `POST /api/meetings/<id>/attendance`: record or update authorized participant attendance.
- `GET/POST /api/meetings/<id>/agenda`: list or manage agenda items.
- `GET/POST /api/meetings/<id>/minutes` and `POST .../minutes/submit`, `.../approve`, `.../reject`: draft, version, and approval workflow.
- `GET/POST /api/meetings/<id>/actions` and `PATCH /api/meeting-actions/<id>`: assign and track follow-up work.
- `GET /api/meetings/reports`: filtered, permission-scoped report data.

## Operational notes

Reminder and overdue escalation jobs will be exposed as Flask CLI commands. Production scheduling remains an environment-specific deployment step and will be documented; no scheduler is configured in this repository today.
