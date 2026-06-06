---
name: verifypdf
description: >-
  Verify whether a PDF document (bank statement, payslip, invoice, tax return,
  ID, contract) is authentic or has been forged, AI-generated or edited. Use
  whenever the user asks to "verify", "check", "validate" or "detect fraud in"
  a PDF, or asks whether a document is real, fake, tampered or trustworthy.
  Submits the file to the VerifyPDF forensic API and returns a structured
  trust_score (0-100), a fraud_risk label and a list of fraud warnings.
license: LicenseRef-VerifyPDF-Proprietary
metadata:
  publisher: VerifyPDF
  homepage: https://verifypdf.com
  terms: Provided by VerifyPDF for use with your own API key.
---

# VerifyPDF document verification

Verify a PDF with VerifyPDF's forensic API. One submit, poll for the verdict,
read back a `trust_score`, a `fraud_risk` label and the fraud `warnings`.

VerifyPDF detects forged and manipulated PDFs: AI-generated bank statements and
payslips, documents edited after creation, template forgeries and metadata
anomalies. It works on any HTTP client, no SDK required.

## Before you start: the API key

Every call needs your VerifyPDF API key in the `API-KEY` request header.

- **Live keys** are prefixed `key_live_` and consume your plan allowance.
- **Test keys** are prefixed `key_test_`, are free, and return a deterministic
  verdict from the filename (see "Testing" below). Use a test key to confirm
  the integration works before spending live quota.

Get a key from the **Developers** section of https://secure.verifypdf.com.
API access is on the Professional and Corporate plans.

This skill resolves the key in this order:

1. The `VERIFYPDF_API_KEY` environment variable (recommended for production and
   CI — keep the secret out of files on disk).
2. A `.verifypdf-key` file in this skill's own directory, containing nothing
   but the key on a single line. Copy `.verifypdf-key.example` to
   `.verifypdf-key` and paste your key in. The file is git-ignored.

If neither is set, the helper scripts stop and tell the user how to add one.
**Never** paste the key into a chat prompt or echo it into logs.

## How to verify a document (the flow)

The two helper scripts in this directory do all of this for you. The flow they
implement, against base URL `https://api.verifypdf.com`:

1. **Submit** — `POST /v2/document` as `multipart/form-data` with the field
   `file` (the PDF) and an optional `custom_id`. Returns `202 Accepted` with a
   `document_id`. The bytes go to the API directly; never base64-encode the PDF
   into a tool argument — models truncate long base64 and produce a meaningless
   verdict.
2. **Poll** — `GET /v2/document?document_id=<id>` every few seconds until
   `status` is no longer `processing` (typically 10-30 seconds).
3. **Read the verdict** from the JSON (see "Response" below).

To avoid polling entirely, set a webhook URL on your API key in the Developers
section; VerifyPDF then POSTs the same JSON to your endpoint when analysis
finishes, signed with an `X-Webhook-Signature` (HMAC-SHA256 of the raw body).

### Use the helper scripts

Bash (needs `curl` and `jq`):

```bash
./verify.sh ./statement.pdf                 # submit + poll + print verdict
./verify.sh ./statement.pdf invoice-4821     # with a custom_id
./verify.sh get   doc_abc123                  # fetch an existing document
./verify.sh delete doc_abc123                 # delete a document
./verify.sh report doc_abc123 ./report.pdf    # download the PDF report
```

Python (standard library only, no `pip install`):

```bash
python3 verify.py ./statement.pdf
python3 verify.py get   doc_abc123
python3 verify.py delete doc_abc123
python3 verify.py report doc_abc123 ./report.pdf
```

Both print the full verdict JSON to stdout and a one-line human summary to
stderr, and exit non-zero on an API error or a `high` fraud_risk so a CI step
or an agent can branch on the exit code.

## Response

A completed `GET /v2/document` returns JSON shaped like:

```json
{
  "status": "analyzed",
  "document_id": "doc_abc123",
  "trust_score": 42,
  "fraud_risk": "high",
  "warnings": [
    { "indicator_id": "font_anachronism", "description": "..." },
    { "indicator_id": "incremental_update", "description": "..." }
  ],
  "original_filename": "statement.pdf",
  "text_changes": [ { "page": 2, "original_text": "1,200.00", "modified_text": "9,200.00", "bounding_box": { "x": 0, "y": 0, "width": 0, "height": 0 } } ],
  "change_summary": { "pages_changed": 1, "total_changes": 1 },
  "metadata": { },
  "tags": [ ],
  "test_mode": false,
  "recommendation": {
    "action": "reject",
    "text": "Do not accept as-is (trust 42/100). Strong forgery/tampering signals: ... Request the original from the issuer, or reject."
  }
}
```

Fields that matter:

- **`status`** — `processing` (poll again), `analyzed` (verdict ready),
  `password-protected` or `corrupted` (no verdict possible). `trust_score` and
  `fraud_risk` are `null` unless `status` is `analyzed`.
- **`trust_score`** (0-100) — continuous score. 100 = no fraud signals; 0 =
  maximum cumulative penalty. Use this for your own threshold (e.g. flag
  anything under 70 for manual review).
- **`fraud_risk`** — `trusted | low | medium | high`, a coarse label derived
  from `trust_score` for when you want a single word instead of a number. The
  API value stays `medium`; the VerifyPDF dashboard shows that band as **Needs Attention**.
- **`warnings`** — detected fraud signals, heaviest first. Each has an
  `indicator_id` and a human `description`.
- **`text_changes`** / **`change_summary`** — structured per-page diff when the
  document was edited after it was created (what changed, on which page).
- **`recommendation`** — added by this skill (not the API): an actionable
  next step derived from the verdict. `recommendation.action` is a stable token
  your agent or CI step can branch on — `accept`, `review`, `reject` or
  `unverifiable` — and `recommendation.text` is the one-line human explanation
  (it names the heaviest warnings and flags edited documents).

### How an agent should report the result

Lead with `recommendation.text`, then back it with the evidence. The
`recommendation.action` token tells you what to do without re-deriving it:

| `action` | When | What the agent should do |
|----------|------|--------------------------|
| `accept` | `trusted` / `low` | Document looks authentic; state the trust_score and proceed. |
| `review` | `medium` (Needs Attention) | Surface the top 2-3 `warnings` and ask for a human look before accepting. |
| `reject` | `high` | Lead with "likely forged or manipulated", list the warnings and any `text_changes`. Do not approve automatically. |
| `unverifiable` | `password-protected` / `corrupted` | Ask the user for an unlocked, intact PDF and re-submit. |

Quote the verdict and the recommendation; never invent indicators that are not
in `warnings`.

## Testing without spending quota

With a **test key** (`key_test_…`), the verdict comes from the filename, not the
file contents, so you can prove the whole submit/poll/report flow end to end:

| Filename            | Result                                       |
|---------------------|----------------------------------------------|
| `test-trusted.pdf`  | `fraud_risk: trusted`, `trust_score: 100`    |
| `test-low.pdf`      | `fraud_risk: low`, `trust_score: 95`         |
| `test-medium.pdf`   | `fraud_risk: medium` + synthetic warnings    |
| `test-high.pdf`     | `fraud_risk: high` + synthetic warnings      |
| `test-error.pdf`    | `500` — exercises your error-handling path   |

Matching is case-insensitive and anything before `.pdf` after the marker is
ignored (`test-high_001.pdf` works). Any other filename returns `400` with a
test key. Test traffic is capped at 500 requests/org/month and never touches
live quota, billing or your document data.

## Notes and limits

- PDFs up to 100 MB. PDF is always accepted; image formats only when ID
  verification is enabled on your account.
- Uploaded documents are encrypted in transit and at rest and deleted within
  90 days. `submitted_url` / `thumbnail_url` in the response are tokenized links
  valid for 60 minutes.
- `429` means a rate limit (back off and retry) or, on a test key, the monthly
  test cap. `401` = bad/missing key. `403` = account suspended or no permission.
- Full API reference: https://verifypdf.com/agents/ — keep this skill's copy of
  the contract in sync with the published v2 reference if it changes.
