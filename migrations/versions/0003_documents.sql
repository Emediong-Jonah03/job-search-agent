CREATE TABLE application_documents (
    id uuid PRIMARY KEY,
    application_id uuid NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    version integer NOT NULL CHECK (version > 0),
    cv_pdf_path text NOT NULL,
    cv_docx_path text NOT NULL,
    cover_letter_pdf_path text NOT NULL,
    cover_letter_docx_path text NOT NULL,
    file_hashes jsonb NOT NULL,
    profile_hash varchar(64) NOT NULL,
    claim_validation jsonb NOT NULL,
    job_analysis jsonb NOT NULL,
    llm_provider varchar(40) NOT NULL,
    llm_model varchar(200) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_application_document_version UNIQUE (application_id, version)
);
CREATE INDEX ix_application_documents_application_time ON application_documents(application_id, created_at);
