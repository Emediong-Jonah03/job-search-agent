CREATE TABLE jobs (
    id uuid PRIMARY KEY,
    employer varchar(200) NOT NULL,
    employer_key varchar(200) NOT NULL,
    title varchar(200) NOT NULL,
    title_key varchar(200) NOT NULL,
    canonical_url text NOT NULL,
    source_url text NOT NULL,
    posted_at timestamptz,
    discovered_at timestamptz NOT NULL DEFAULT now(),
    description text NOT NULL DEFAULT '',
    CONSTRAINT uq_job_identity UNIQUE (canonical_url, employer_key, title_key)
);
CREATE INDEX ix_jobs_posted_at ON jobs(posted_at);

CREATE TABLE job_sources (
    id uuid PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    adapter varchar(80) NOT NULL,
    source_url text NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT now(),
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE applications (
    id uuid PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT,
    state varchar(40) NOT NULL DEFAULT 'DISCOVERED',
    version integer NOT NULL DEFAULT 1,
    match_score integer CHECK (match_score BETWEEN 0 AND 100),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_application_state CHECK (state IN ('DISCOVERED','VERIFIED','SHORTLISTED','CV_GENERATED','AWAITING_APPROVAL','APPROVED','SUBMITTED','FOLLOW_UP','REJECTED_BY_VERIFICATION','SKIPPED'))
);
CREATE INDEX ix_applications_state ON applications(state);

CREATE TABLE application_transitions (
    id uuid PRIMARY KEY,
    application_id uuid NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    previous_state varchar(40),
    new_state varchar(40) NOT NULL,
    actor varchar(200) NOT NULL,
    reason text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_transitions_application_time ON application_transitions(application_id, occurred_at);

CREATE TABLE audit_events (
    id uuid PRIMARY KEY,
    event_type varchar(100) NOT NULL,
    actor varchar(200) NOT NULL,
    application_id uuid,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_occurred_at ON audit_events(occurred_at);
CREATE INDEX ix_audit_type ON audit_events(event_type);

CREATE TABLE submission_attempts (
    id uuid PRIMARY KEY,
    application_id uuid NOT NULL REFERENCES applications(id) ON DELETE RESTRICT,
    status varchar(30) NOT NULL DEFAULT 'NOT_ATTEMPTED',
    idempotency_key varchar(100) NOT NULL UNIQUE,
    started_at timestamptz,
    finished_at timestamptz,
    result_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT ck_submission_status CHECK (status IN ('NOT_ATTEMPTED','IN_PROGRESS','CONFIRMED_SUCCESS','CONFIRMED_FAILURE','OUTCOME_UNKNOWN'))
);
CREATE INDEX ix_submission_application_status ON submission_attempts(application_id, status);

