# Approval dashboard (Phase 5)

## Local access

Set a unique `DASHBOARD_PASSWORD` in `.env`, keep `APP_SECRET_KEY` private, then start the API with Docker Compose. The dashboard is at `http://localhost:8000`. Compose binds the API to loopback. In production, use HTTPS at a trusted reverse proxy; secure cookies are enabled when `APP_ENV=production`. Production settings require a dashboard password of at least six characters with at least four distinct characters, and an application session secret of at least 32 characters. Six characters is a low security floor; use a much longer password for any internet-accessible deployment.

## Authentication and request protection

This phase provides a single-user password gate configured through the environment. Sessions are signed with `APP_SECRET_KEY`, stored in HTTP-only cookies, limited to eight hours, and use `SameSite=Strict`. Production cookies are secure-only. Every state-changing form carries a per-session CSRF token; invalid tokens are rejected. Login checks use constant-time password comparison and permit at most eight failed attempts per client address in a ten-minute window in the API process. The API has a same-origin CSP, `X-Content-Type-Options`, `X-Frame-Options`, and referrer policy headers. Document routes require the authenticated session and constrain paths beneath `ARTIFACTS_PATH`.

This is a local single-user gate, not an organization identity provider. The login rate limit is process-local; deploy behind network restrictions and TLS if the API is reachable beyond the local machine. Do not reuse the dashboard password as another account credential.

## Review flow

The queue supports state filtering and shows match score, verification classification, and document version. A detail page shows job description and original listing, verification signals and uncertainty, document previews/downloads, state history, profile data disclosed in the CV, and screening questions. For a `VERIFIED` job, explicitly shortlist it from its detail page; then choose **Generate CV and cover letter**. Generation is user-triggered, not automatic. Jobs enter the queue through the scheduled ARQ discovery worker, which must run separately from the web service. Render Free does not provide background workers, and its local filesystem is ephemeral, so it will not run scheduled discovery and cannot durably retain generated PDFs. The current system does not collect application form questions yet; when present, each question can be answered only by selecting an exact non-empty fact from the profile or marked as needing clarification. The latter blocks approval.

Structured document edits are parsed as `ApplicationDocumentContent`, rechecked against the profile and saved job, rendered into a new private version, and audited. Free-form edits that introduce or rephrase personal claims fail validation. Editing a document after `AWAITING_APPROVAL` or `APPROVED` returns the application to `CV_GENERATED`, invalidating the old approval.

The approval service—not just the UI—requires the application to be in the expected state, the latest stored verification to be `VERIFIED`, the latest document claim report to pass, and every collected screening question to be resolved. The final confirmation posts the document version, verification ID, and a hash of the exact screening answers shown on the review page; the service rechecks them inside a locked database transaction. The durable approval record binds the approver, verification record, document version, and answer hash. A stale review page is rejected. A separate per-application confirmation POST records `AWAITING_APPROVAL` and then `APPROVED` transitions. Approval enables the later Phase 6 hosted-form submission workflow; inspecting a form does not submit it, and the final submit action requires a separate explicit confirmation.

The UI is server-rendered HTML with native forms and a local stylesheet rather than React/HTMX. This avoids a separate JavaScript build and external assets for the current single-user, same-origin workflow. It can be replaced by a richer client without moving authorization or approval checks out of the service layer.

Database-backed route tests and migration execution require the Compose services. The local unit tests exercise the approval policy independently, but do not claim a live database integration test.
