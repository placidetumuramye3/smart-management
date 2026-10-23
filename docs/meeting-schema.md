# Meeting Panel Schema

The meeting schema is introduced by `migrations/versions/v20261023_02_add_meeting_workflows.py`. The preceding `v20261023_01_expand_parent_school_meetings.py` migration adds assignment, attendance, notes, decision summary, and follow-up fields to the existing `appointment` table. Both upgrades are idempotently applied at application startup.

| Table | Purpose and key constraints |
| --- | --- |
| `student_guardian` | Verified/revoked parent-to-student relationships; unique `(student_id, guardian_id)` pair and indexed student, guardian, status. Migration backfills existing `user.parent_id` links. |
| `meeting` | Schedule, type, class/student scope, organizer, chair, secretary, designated minutes approver, approval state, confidentiality, and timestamps. Indexed by date, status, type, class, and responsible accounts. |
| `meeting_participant` | Unique `(meeting_id, user_id)` invitation record, optional student anchor, role, response, comment, and response timestamp. |
| `meeting_attendance` | One row per participant, with status, representative identity/relationship, recording staff, and update timestamp. |
| `meeting_agenda_item` | Ordered agenda entries scoped to a meeting. |
| `meeting_minutes` | One versioned minutes workflow per meeting, with submitter, designated approver, decision, and status. |
| `meeting_minutes_revision` | Immutable content snapshot for every save, submission, approval, and return-for-revision event; unique `(minutes_id, version)`. |
| `meeting_action_item` | Assigned decision follow-up with deadline, owner, workflow status, evidence, review outcome, and parent visibility. |
| `meeting_action_comment` | Chronological action comments with an explicit parent-visible flag. |
| `meeting_audit_event` | Append-only event metadata and HMAC chain (`previous_hash`, unique `event_hash`); covers meeting lifecycle, approvals, attendance, actions, guardian links, and configuration. |
| `meeting_notification_log` | Per-recipient event de-duplication plus in-app and delegated-provider status; unique `(dedupe_key, user_id)`. |
| `message_conversation.meeting_id` | Nullable, unique link from an audited private conversation to one meeting. Existing conversations remain unlinked. |

Foreign keys refer to existing `user` records and cascade or set null where appropriate. Student/guardian authorization is checked in application services at every relevant read and write; a numeric user ID alone is never treated as proof of a relationship.

## Migration and compatibility

The startup migration checks table and column existence before alteration. Existing appointment rows remain in place. Existing `User.parent_id` links are copied to verified guardian rows, while the legacy field remains available to older modules. Explicit revoked guardian rows override the legacy link for meeting access. New meeting tables are also added by SQLAlchemy `create_all` for new or test databases.
