# Document generation and claim checks (Phase 4)

Document generation is a service operation for applications in `SHORTLISTED` or `CV_GENERATED`. It reads the saved job description and the configured local profile. The selected provider sees only the public job description; profile details are never included in an LLM prompt.

The model returns a bounded `JobDescriptionAnalysis` structure. The builder uses that analysis only to rank facts already present in the profile. CV sections and cover-letter evidence are assembled from exact profile strings. Fixed connective text is local and does not claim experience, qualifications, or results. A deterministic validator resolves every personal claim path against the profile and checks the original wording before rendering. Generation stops if any claim fails validation.

Four files are produced for each version: CV PDF, CV DOCX, cover-letter PDF, and cover-letter DOCX. PDF generation uses WeasyPrint in the application image, with a ReportLab fallback for environments where WeasyPrint is unavailable. DOCX files use a simple, single-column structure. Rendered files are reopened and checked for extractable text and reading order before persistence. The cover letter is capped below 250 words.

Files are stored in the private `ARTIFACTS_PATH` volume under `<application UUID>/<version>/`; the service does not expose a public file URL. PostgreSQL stores relative paths, SHA-256 hashes, the profile snapshot hash, claim-validation result, structured job analysis, provider/model, and version. Audit events contain provider/model and counts, but not profile contents or generated text. `SHORTLISTED` transitions to `CV_GENERATED` after files are validated and persisted. Later generations create a new version.

Provider configuration is in `.env.example`. `LLM_PROVIDER=mock` is the default and performs deterministic keyword extraction without network access. Hosted options are `claude`, `gemini`, and `groq`; set the corresponding API key, a supported `LLM_MODEL`, and optionally `LLM_TIMEOUT_SECONDS`. Hosted API requests use provider JSON-schema output modes. Provider errors do not include response bodies or secret values in their messages. Do not put API keys in source control.

The service currently has no authenticated download endpoint or user interface. Keep artifact storage private and access it only through trusted application code. If a database transaction fails after the version directory has been written, files may remain as an unreferenced private directory; reconcile those directories during operations before deleting anything.

Tests cover exact fact validation, rejection of altered claims, word-count limits, and readable PDF/DOCX output. Live hosted-provider calls and PostgreSQL transaction behavior require configured provider credentials and a running database and are not exercised by the local unit suite.
