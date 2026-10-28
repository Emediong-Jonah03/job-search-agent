from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FormFieldSnapshot(StrictModel):
    key: str = Field(min_length=1, max_length=300)
    label: str = Field(default="", max_length=300)
    control_type: str = Field(min_length=1, max_length=40)
    required: bool = False
    visible: bool = True
    options: list[str] = Field(default_factory=list, max_length=100)


class FormSnapshot(StrictModel):
    current_url: str
    page_text: str = ""
    form_count: int = Field(ge=0, le=20)
    submit_button_count: int = Field(ge=0, le=20)
    form_action: str = ""
    form_method: str = ""
    fields: list[FormFieldSnapshot] = Field(default_factory=list, max_length=100)
    frame_urls: list[str] = Field(default_factory=list, max_length=50)


class SubmissionFieldPlan(StrictModel):
    field_key: str
    label: str
    control_type: str
    required: bool
    profile_path: str | None = None
    document_key: Literal["cv_pdf_path", "cover_letter_pdf_path"] | None = None
    source_hash: str


class SubmissionPlan(StrictModel):
    adapter: Literal["greenhouse", "lever"]
    application_id: str
    approval_id: str
    job_url: str
    form_action: str
    form_fingerprint: str
    profile_hash: str
    document_version: int
    verification_id: str
    answers_hash: str
    fields: list[SubmissionFieldPlan] = Field(min_length=1, max_length=100)
    plan_hash: str = ""


class BrowserSubmissionResult(StrictModel):
    status: Literal["CONFIRMED_SUCCESS", "CONFIRMED_FAILURE", "OUTCOME_UNKNOWN"]
    reason_code: str
    confirmation_marker: str = ""
    confirmation_number: str = ""
    screenshot_bytes: bytes | None = None
    submission_clicked: bool = False

