# Employer and posting verification policy

This deterministic policy assesses the evidence attached to a public job posting. `VERIFIED` means that the posting was present in a recognized public ATS feed and the original ATS-hosted page responded successfully under the supported link check. It does not certify the legal identity, solvency, or conduct of the employer.

## Positive evidence weights

| Signal | Weight | Evidence source |
|---|---:|---|
| Listing returned by a recognized public ATS endpoint | 25 | ATS public board API URL |
| Original posting returned a successful response | 25 | Original posting URL and saved link-check result |
| Original posting uses a recognized ATS host | 20 | Parsed posting hostname |
| Employer label, title, and substantive description present | 10 | Posting content |
| Explicit publication timestamp within the previous 30 days | 10 | ATS publication field |

Weights form the support score, capped at 100. Each signal also records its source, reliability estimate, evidence URL, finding, and observation timestamp. Missing evidence is recorded separately and does not subtract points.

## Risk signals

| Signal | Risk weight |
|---|---:|
| Upfront application/recruitment/training/equipment fee request | 60 |
| Bank login or online banking credentials request | 80 |
| Passport or government ID request during the application stage | 35 |
| Guaranteed employment or no-interview hiring claim | 40 |
| High-pressure urgency language | 20 |

Risk weights are summed. The final score is `max(0, support_score - min(risk_score, 100))`.

## Classifications and transitions

- `SUSPICIOUS`: risk score meets `VERIFICATION_SUSPICIOUS_RISK_THRESHOLD` (default 40). The application moves to `REJECTED_BY_VERIFICATION` if it has not been submitted.
- `VERIFIED`: final score meets `VERIFICATION_VERIFIED_THRESHOLD` (default 70), and the recognized ATS feed, accessible original page, and recognized ATS host checks all pass. A `DISCOVERED` application moves to `VERIFIED`.
- `PROBABLE`: all other cases. The application stays `DISCOVERED`; it is never silently advanced as verified.

Thresholds are environment-configurable and persisted with each assessment. The policy version and full signal list are stored so historical decisions can be understood after later policy changes.

## Limits

The keyword rules are intentionally simple and can miss context or flag unusual phrasing. One narrow negation rule avoids treating an explicit “we never ask you to pay” statement as a fee request; this is not a general natural-language understanding system. Assessments include a permanent uncertainty note that independent company-domain and business-registration evidence were not checked. No result is a guarantee that an employer is trustworthy. The review interface should show evidence and missing information to the user before a decision.

