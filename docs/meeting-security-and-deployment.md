# Meeting Panel Security and Deployment

## Authorization and privacy

- Browser and API routes use the existing authenticated session and role matrix. Meeting visibility is additionally checked against invitation status, assigned class, organizer/participant role, meeting confidentiality, and verified guardian links.
- A parent must have an active invitation and a verified `StudentGuardian` link or a legacy `User.parent_id` link to see a student meeting. An explicit revoked link overrides a legacy link. Staff cannot select students outside their assigned class scope.
- Meeting and minutes approvals are accepted only from the designated headteacher or super administrator. Action completion is limited to the assignee or school leadership; review is limited to leadership. Overdue state is assigned by the scheduled maintenance command.
- Browser forms and mutating JSON endpoints require a session-bound CSRF token. API clients obtain it from `GET /api/meetings/csrf`. Flask session cookies are HttpOnly and SameSite Lax; set `SESSION_COOKIE_SECURE=true` behind HTTPS.
- Meeting API writes are capped at 120 successful audited writes per account per rolling hour. Secure meeting message replies use the messaging module's existing per-thread rate limit.
- The headteacher and super-administrator meeting settings screen controls whether staff-created meetings need designated approval and the number of grace days before action escalation. New installations default to approval required and zero grace days.
- Meeting audit events are HMAC chained using Flask `SECRET_KEY`; the current head is stored separately in `Setting.meeting_audit_head`. The super-administrator-only `/meetings/audit` screen verifies the chain and reports integrity failures.
- Reports and exports contain schedule, title, class, status, and aggregate invitation/attendance counts. They omit student names, descriptions, messages, minutes, and action comments.

## Notifications

In-app notification rows are the durable source. `MeetingNotificationLog` deduplicates each event per user and records `in_app_status` plus `external_status`. The current SMS/email integration is delegated to the existing parent notification service, which does not return provider delivery receipts; external status therefore remains `delegated` rather than claiming confirmed delivery. Configure SMTP and/or Twilio credentials only when those channels are approved for the school.

## Configuration and production setup

1. Set a unique, high-entropy `SECRET_KEY` and production `DATABASE_URL` in the deployment environment. Do not use the development fallback key in production.
2. Terminate HTTPS at the application gateway and set `SESSION_COOKIE_SECURE=true`. Configure the proxy's forwarded headers only for trusted proxies.
3. Apply database changes by deploying the updated application; startup uses idempotent upgrades `v20261023_01` and `v20261023_02` before registering model tables. Take a backup before the first production rollout.
4. Configure a scheduler to run `flask --app run.py send-meeting-reminders` hourly and `flask --app run.py process-meeting-actions` daily. Use the same database and environment as the web application. These commands are idempotent through per-recipient notification keys and audit checks.
5. Confirm the production email/SMS provider configuration using test accounts before enabling parent alerts. In-app alerts work without those providers.

## Backup, restore, and retention

- Back up the database at least daily and before schema upgrades. Store encrypted copies outside the application host and retain a tested restore point according to the school's approved retention schedule.
- Verify backups by restoring to a separate environment and checking user, student, guardian, meeting, notification, and audit tables. Do not restore production data into an unprotected development instance.
- Meeting data is not automatically purged. Define and approve retention periods for meeting details, attendance, minutes, notifications, and audit events with school policy before implementing deletion. Preserve audit and approved minutes for the required period.
- Keep the application `SECRET_KEY` stable across instances and backups; rotating it invalidates session cookies and prevents verification of older HMAC audit events. If key rotation is required, first implement a versioned audit-key rollover procedure.

## Known operational limits

- The repository has no scheduler, queue worker, or provider delivery-receipt interface. The CLI commands need deployment-specific scheduling; notification provider failures are logged by the existing service but are not reflected as final provider status in `MeetingNotificationLog`.
- The initial audit chain is protected against undetected row edits while the application secret and stored head remain secure. Database superusers who can alter both the event table and anchor can rewrite history; independent immutable storage is required for stronger external tamper evidence.
- Meeting timestamps use the server's local clock, consistent with the existing application. Configure the host timezone to the school's timezone.
