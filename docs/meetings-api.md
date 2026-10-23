# Parents and School Meetings API

The API is JSON over the application's existing Flask-Login session. It uses the same role and student-scope checks as the web panel.

## Authentication and request format

Sign in through the normal application login flow. Read requests use the session cookie. Every `POST`, `PUT`, `PATCH`, and `DELETE` request must include a session-bound CSRF token:

```http
GET /api/meetings/csrf
Cookie: session=...
```

```json
{"ok": true, "data": {"csrf_token": "..."}}
```

Send the returned token in `X-CSRF-Token` and send JSON with `Content-Type: application/json`. Successful responses use `{"ok":true,"data":...}`; list endpoints add `meta`. Errors use `{"ok":false,"error":"..."}` and an appropriate HTTP status. Meeting API writes are limited to 120 successful audited writes per account in a rolling hour. The existing secure-message thread also applies its per-thread reply limit.

## Endpoints and roles

`Meeting roles` means headteacher, super administrator, secretary, teacher, parent, discipline staff, patron, and matron. A role may see only meetings allowed by its participant, class, organizer, and verified guardian scope. Parents receive approved minutes and parent-visible actions only.

| Method and path | Purpose | Access |
| --- | --- | --- |
| `GET /api/meetings/csrf` | Get a CSRF token | Meeting roles |
| `GET /api/meetings` | List scoped meetings; accepts `page`, `per_page` (max 100), `status`, `type`, `class_name`, `organizer_id`, `date_from`, `date_to`, `q` | Meeting roles |
| `POST /api/meetings` | Create meeting and participants | Teacher, office, or class support staff; creation validates guardian/class scope |
| `GET /api/meetings/{id}` | Read authorized meeting details | Meeting roles with record access |
| `PATCH /api/meetings/{id}` | Update schedule/details | Organizer or authorized meeting staff |
| `DELETE /api/meetings/{id}` | Cancel a meeting | Organizer or authorized meeting staff |
| `POST /api/meetings/{id}/responses` | Accept, decline, or request rescheduling | Invited participant |
| `GET/POST /api/meetings/{id}/participants` | List participants or invite an authorized parent/guardian or staff account | Meeting roles / authorized meeting staff |
| `DELETE /api/meetings/{id}/participants/{participant_id}` | Withdraw an invitation and remove linked conversation access | Authorized meeting staff |
| `GET /api/meetings/{id}/attendance` | Read scoped register | Authorized meeting roles |
| `POST /api/meetings/{id}/attendance` | Record status and representative | Authorized meeting staff |
| `GET/POST /api/meetings/{id}/agenda` | Read or add agenda item | Meeting roles / authorized meeting staff |
| `PATCH /api/meetings/{id}/agenda/{item_id}` | Move an agenda item with `{"direction":"up"}` / `down` or a zero-based `position` | Authorized meeting staff |
| `DELETE /api/meetings/{id}/agenda/{item_id}` | Remove agenda item | Authorized meeting staff |
| `GET/PUT /api/meetings/{id}/minutes` | Read visible minutes or save a versioned draft | Meeting roles / authorized meeting staff |
| `POST /api/meetings/{id}/minutes/submit` | Submit draft to approver | Authorized meeting staff |
| `POST /api/meetings/{id}/minutes/approve` | Approve minutes | Designated headteacher or super administrator |
| `POST /api/meetings/{id}/minutes/reject` | Return minutes with a reason | Designated headteacher or super administrator |
| `GET/POST /api/meetings/{id}/actions` | List scoped or assign an action | Meeting roles / authorized meeting staff after minutes approval |
| `PATCH /api/meetings/{id}/actions/{action_id}` | Update action progress or authorized assignment | Assignee or authorized meeting staff |
| `POST /api/meetings/{id}/actions/{action_id}/comments` | Add a scoped comment | Assignee, authorized meeting staff, or invited parent on a shared action |
| `GET /api/meetings/{id}/notifications` | Read this user's meeting delivery log | Authorized meeting participant |
| `GET /api/meetings/reports` | Filtered attendance and participation summary | Headteacher, secretary, super administrator |
| `GET/PATCH /api/meetings/settings` | Read or update approval and action-escalation policy | Headteacher, super administrator |
| `GET/POST /api/guardians` | List or verify guardian links | Headteacher, secretary, super administrator |
| `GET /api/guardians/{student_id}` | List links for one student | Headteacher, secretary, super administrator |
| `DELETE /api/guardian-links/{link_id}` | Revoke verified link | Headteacher, secretary, super administrator |

## Example: create a student meeting

```http
POST /api/meetings
X-CSRF-Token: <token>
Content-Type: application/json
```

```json
{
  "title": "Progress discussion",
  "meeting_type": "parent_teacher",
  "student_id": 412,
  "scheduled_date": "2026-11-05",
  "start_time": "14:00",
  "end_time": "14:30",
  "location": "Office 2",
  "audience_mode": "guardians",
  "minutes_approver_id": 8,
  "agenda_items": ["Learning progress", "Agreed support"]
}
```

Parents are selected from active verified student guardian relationships. Teachers and class support staff are limited to assigned classes. A meeting created by a non-approver is pending approval and is hidden from invitees until the designated approver publishes it.

## Meeting detail fields

Meeting list results are paginated objects with `id`, `title`, type, class, schedule, status, organizer, and the current user's invitation response. Detail results add authorized participant responses, attendance, agenda, minutes, and actions. Student identity is omitted unless the viewer is an authorized guardian or scoped staff member. The API never returns a parent's private messages or staff-only action comments through the meeting detail representation.

## Compatibility and versioning

This is the first API contract; URLs are currently unversioned because the existing app has no API versioning scheme. Add `/api/v2` before making incompatible response or status changes. `DELETE` cancels a meeting and preserves its audit history; it does not delete the record.
