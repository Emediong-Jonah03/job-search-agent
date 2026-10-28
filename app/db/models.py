from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("canonical_url", "employer_key", "title_key", name="uq_job_identity"),
        Index("ix_jobs_posted_at", "posted_at"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    employer: Mapped[str] = mapped_column(String(200), nullable=False)
    employer_key: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    title_key: Mapped[str] = mapped_column(String(200), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sources: Mapped[list["JobSource"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class JobSource(Base):
    __tablename__ = "job_sources"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    adapter: Mapped[str] = mapped_column(String(80), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    job: Mapped[Job] = relationship(back_populates="sources")


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (Index("ix_applications_state", "state"),)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="RESTRICT"), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False, default="DISCOVERED")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    match_score: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ApplicationDocument(Base):
    __tablename__ = "application_documents"
    __table_args__ = (
        UniqueConstraint("application_id", "version", name="uq_application_document_version"),
        Index("ix_application_documents_application_time", "application_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    cv_pdf_path: Mapped[str] = mapped_column(Text, nullable=False)
    cv_docx_path: Mapped[str] = mapped_column(Text, nullable=False)
    cover_letter_pdf_path: Mapped[str] = mapped_column(Text, nullable=False)
    cover_letter_docx_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_hashes: Mapped[dict] = mapped_column(JSON, nullable=False)
    profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_validation: Mapped[dict] = mapped_column(JSON, nullable=False)
    job_analysis: Mapped[dict] = mapped_column(JSON, nullable=False)
    llm_provider: Mapped[str] = mapped_column(String(40), nullable=False)
    llm_model: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApplicationTransition(Base):
    __tablename__ = "application_transitions"
    __table_args__ = (Index("ix_transitions_application_time", "application_id", "occurred_at"),)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), nullable=False)
    previous_state: Mapped[str | None] = mapped_column(String(40))
    new_state: Mapped[str] = mapped_column(String(40), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_occurred_at", "occurred_at"), Index("ix_audit_type", "event_type"))
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    application_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SubmissionAttempt(Base):
    __tablename__ = "submission_attempts"
    __table_args__ = (Index("ix_submission_application_status", "application_id", "status"),)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("applications.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="NOT_ATTEMPTED")
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class JobVerification(Base):
    __tablename__ = "job_verifications"
    __table_args__ = (
        Index("ix_job_verifications_job_time", "job_id", "assessed_at"),
        Index("ix_job_verifications_classification", "classification"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    application_id: Mapped[UUID | None] = mapped_column(ForeignKey("applications.id", ondelete="SET NULL"))
    classification: Mapped[str] = mapped_column(String(20), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    support_score: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(20), nullable=False)
    reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    missing_evidence: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    uncertainty: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    policy_parameters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VerificationSignalRecord(Base):
    __tablename__ = "verification_signals"
    __table_args__ = (Index("ix_verification_signals_assessment", "verification_id"),)
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    verification_id: Mapped[UUID] = mapped_column(ForeignKey("job_verifications.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    finding: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_url: Mapped[str | None] = mapped_column(Text)
    data_source: Mapped[str] = mapped_column(String(100), nullable=False)
    reliability: Mapped[float] = mapped_column()
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

