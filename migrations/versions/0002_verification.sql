CREATE TABLE job_verifications (
    id uuid PRIMARY KEY,
    job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    application_id uuid REFERENCES applications(id) ON DELETE SET NULL,
    classification varchar(20) NOT NULL CHECK (classification IN ('VERIFIED','PROBABLE','SUSPICIOUS')),
    score integer NOT NULL CHECK (score BETWEEN 0 AND 100),
    support_score integer NOT NULL CHECK (support_score BETWEEN 0 AND 100),
    risk_score integer NOT NULL CHECK (risk_score >= 0),
    policy_version varchar(20) NOT NULL,
    reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
    missing_evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
    uncertainty jsonb NOT NULL DEFAULT '[]'::jsonb,
    policy_parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
    assessed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_job_verifications_job_time ON job_verifications(job_id, assessed_at);
CREATE INDEX ix_job_verifications_classification ON job_verifications(classification);

CREATE TABLE verification_signals (
    id uuid PRIMARY KEY,
    verification_id uuid NOT NULL REFERENCES job_verifications(id) ON DELETE CASCADE,
    name varchar(80) NOT NULL,
    kind varchar(20) NOT NULL CHECK (kind IN ('POSITIVE','RISK')),
    weight integer NOT NULL CHECK (weight BETWEEN 0 AND 100),
    finding text NOT NULL,
    evidence_url text,
    data_source varchar(100) NOT NULL,
    reliability double precision NOT NULL CHECK (reliability BETWEEN 0 AND 1),
    observed_at timestamptz NOT NULL
);
CREATE INDEX ix_verification_signals_assessment ON verification_signals(verification_id);

