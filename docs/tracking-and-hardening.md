# Application tracking and follow-ups (Phase 7)

## Reminder lifecycle

Only an application in `SUBMITTED`, backed by a `CONFIRMED_SUCCESS` attempt or explicit manual reconciliation, gets a reminder. One reminder per application is enforced by a unique database constraint. The due timestamp is the confirmed submission timestamp plus `FOLLOW_UP_DAYS` (default 7; allowed range 1–90).

The ARQ worker checks the reminder table daily at 08:00 UTC and on startup. It backfills reminders for confirmed submissions created before this migration, then changes due reminders from `SCHEDULED` to `DRAFTED` and stores a deterministic message draft. Row locks, status transitions, and the unique application constraint make repeated runs safe. If the worker is down, reminders remain stored and are processed after it recovers. Draft generation uses the saved employer, role, confirmed submission timestamp, and profile name; a missing profile name is shown as `[Your name]` for review.

The dashboard lets the user edit and approve a saved draft. It has no send action or email/calendar integration. The user can send it outside the application and then mark the reminder handled; that action records the event and advances the application to `FOLLOW_UP`. Notes are append-only through the UI and are visible only behind dashboard authentication. The reminder, draft text, notes, and completion note contain personal application data and should be included in protected backups and retention decisions.

## Statuses and recovery

| Reminder status | Meaning | Recovery |
| --- | --- | --- |
| `SCHEDULED` | Due date has not arrived, or the worker has not processed it yet. | Worker processes due rows on its next run. |
| `DRAFTED` | A saved message is awaiting review. | Edit and save or explicitly approve it. |
| `APPROVED` | User approved the draft for manual use. | Send or handle it outside the dashboard, then mark complete. |
| `COMPLETED` | User recorded that the follow-up was handled. | Retained in application history. |
| `CANCELLED` | Reserved for a future explicit cancellation flow. | No automatic action is taken. |

No reminder is created for `OUTCOME_UNKNOWN`, failed, or merely prepared submission attempts. If the database commit after an external confirmation fails, the existing reconciliation path is still the source of truth; the worker can backfill reminders only for a persisted confirmed submission. The worker does not infer that an application was submitted from a screenshot alone.

## Security and operational review

- Dashboard mutations continue to require an authenticated session and CSRF token. User-authored text is escaped on render and is not copied into audit payloads.
- Reminder and application note routes are scoped by both application and record IDs. Draft approval and completion use row locks and reject stale or already-processed records.
- A database uniqueness constraint protects against duplicate reminders if the worker is retried or runs concurrently. The daily worker processes bounded batches and locks due rows with `SKIP LOCKED`.
- Drafts are stored as private database records and require user approval. No external message is sent by the worker or dashboard.
- The supplied Compose stack is for local development: API, PostgreSQL, and Redis bind to loopback. It requires distinct PostgreSQL and Redis passwords and enables Redis authentication. Keep the data-service ports off public interfaces in production, terminate TLS at a trusted proxy, and set that proxy's exact IPs in `FORWARDED_ALLOW_IPS`.
- Back up PostgreSQL and the private artifact volume together. Restoring only one can separate confirmation history from the associated screenshots/documents.

## Local commands

```powershell
docker compose stop api worker
docker compose up -d db redis
docker compose run --build --rm api alembic upgrade head
docker compose up --build -d api worker
python -m pip install -e '.[dev]'
python -m pytest -q
```

The reminder worker runs as part of the existing ARQ worker service. Verify its startup and scheduled-job logs before relying on a due reminder. The app records UTC timestamps and labels them as UTC in the dashboard.

## Remaining limitations

- Reminder visibility is dashboard-only; there are no email, push, or calendar notifications.
- The follow-up draft is a deterministic template, not a generated message tailored by an LLM. The user can edit it and must approve it.
- Marking a reminder handled records the user's attestation; the application cannot verify messages sent from another client.
- End-to-end operation still depends on Docker services, PostgreSQL migrations, Redis, and an active worker. Tests never submit real job applications.
