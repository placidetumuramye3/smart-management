# Student Enrollment History

`User` remains the canonical account and student identity. The `student_enrollment` table records dated placements for that same `User`; it does not create a second student or class identity.

## Fields

| Field | Meaning |
| --- | --- |
| `student_id` | Canonical student `User.id` |
| `class_name`, `trade`, `level` | Class placement selected from the existing configured class list |
| `academic_year` | Year associated with the placement |
| `start_date`, `end_date` | Effective dates; imported legacy placements have an unknown start date (`NULL`) |
| `status` | `active`, `transferred`, `withdrawn`, or `graduated` |
| `reason` | Short enrollment or transfer note |
| `recorded_by_id`, `created_at` | Staff account and timestamp for the change |

The partial unique index permits at most one `active` placement per student on SQLite and PostgreSQL. The service also checks the active record before transitions. Existing classroom, timetable, attendance, assessment, and report routes continue reading the student's synchronized current `User.class_name`, `trade`, `level`, and `academic_year` fields for compatibility.

## Migration and behavior

Migration `v20261023_03_add_student_enrollment_history` imports one active row for each existing student. Historical start dates are unknown and are not fabricated. New registrations create an enrollment row in the same database transaction as the student account. Transfers close the old placement and append a new active row. Withdrawal or graduation closes the active row and clears the current class assignment.

The `/students/<student_id>/enrollment` page is available to the master administrator, headteacher, and secretary. POST requests use a session-bound CSRF token. Transfers are validated against the configured programme/class mapping and require a valid academic year and effective date. Every transition records a student activity log entry.

The enrollment migration is `migrations/versions/v20261023_03_add_student_enrollment_history.py`. Guardian access remains in the existing `StudentGuardian` table; enrollment does not create duplicate guardian or student records. See [the integrated-system gap analysis](system-gap-analysis.md) for current verification status and next implementation stages.

## Current limitation

The new classroom hub and learning-material route use active enrollment for students and verified guardian links for parents. Other academic/class routes still use current class fields and `AcademicSubject` records; the next LMS step is to make every assignment and resource action consume the enrollment service consistently, while preserving old student records and report history.
