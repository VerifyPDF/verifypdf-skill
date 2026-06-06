#!/usr/bin/env python3
"""VerifyPDF document verification helper (standard library only).

    python3 verify.py <file.pdf> [custom_id]   submit a PDF, poll, print verdict
    python3 verify.py get    <document_id>     fetch an existing document
    python3 verify.py delete <document_id>     delete a document
    python3 verify.py report <document_id> [out.pdf] [lang]   download report

The API key is read from $VERIFYPDF_API_KEY, or from a `.verifypdf-key` file
next to this script (one line, the key only). Never pass it on the command line
or print it. See SKILL.md.

Exit codes: 0 ok (trusted/low/unresolved), 2 API/usage error, 3 fraud_risk
high, 4 fraud_risk medium, 5 password-protected/corrupted.
"""
import json
import os
import sys
import time
import uuid
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

API_BASE = os.environ.get("VERIFYPDF_API_BASE", "https://api.verifypdf.com")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
POLL_INTERVAL = float(os.environ.get("VERIFYPDF_POLL_INTERVAL", "3"))
POLL_MAX = int(os.environ.get("VERIFYPDF_POLL_MAX", "40"))  # 40 * 3s = 120s


def die(msg, code=1):
    print(f"verify.py: {msg}", file=sys.stderr)
    sys.exit(code)


def resolve_key():
    env = os.environ.get("VERIFYPDF_API_KEY")
    if env:
        return env.strip()
    path = os.path.join(SCRIPT_DIR, ".verifypdf-key")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    die(
        "No API key. Set VERIFYPDF_API_KEY, or copy .verifypdf-key.example to "
        ".verifypdf-key and paste your key. Get a key at "
        "https://secure.verifypdf.com (Developers).",
        2,
    )


API_KEY = resolve_key()


def request(method, path, *, body=None, content_type=None, raw_out=None):
    req = urllib.request.Request(f"{API_BASE}{path}", data=body, method=method)
    req.add_header("API-KEY", API_KEY)
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
            if raw_out is not None:
                with open(raw_out, "wb") as fh:
                    fh.write(data)
                return resp.status, None
            return resp.status, json.loads(data.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        die(f"{method} {path} failed (HTTP {exc.code}): {detail}", 2)
    except urllib.error.URLError as exc:
        die(f"{method} {path} failed: {exc.reason}", 2)


def build_multipart(file_path, custom_id):
    boundary = f"----verifypdf{uuid.uuid4().hex}"
    with open(file_path, "rb") as fh:
        file_bytes = fh.read()
    filename = os.path.basename(file_path)
    crlf = b"\r\n"
    parts = [
        b"--" + boundary.encode() + crlf,
        f'Content-Disposition: form-data; name="file"; filename="{filename}"'.encode() + crlf,
        b"Content-Type: application/pdf" + crlf + crlf,
        file_bytes + crlf,
    ]
    if custom_id:
        parts += [
            b"--" + boundary.encode() + crlf,
            b'Content-Disposition: form-data; name="custom_id"' + crlf + crlf,
            custom_id.encode() + crlf,
        ]
    parts.append(b"--" + boundary.encode() + b"--" + crlf)
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _top_signals(warnings, limit=3):
    """A short, human phrase listing the heaviest warnings (description or id)."""
    labels = []
    for w in warnings[:limit]:
        labels.append(w.get("description") or w.get("indicator_id") or "unknown")
    extra = len(warnings) - len(labels)
    phrase = "; ".join(labels)
    if extra > 0:
        phrase += f"; +{extra} more"
    return phrase


def recommend(doc):
    """Map a verdict to an actionable recommendation.

    Returns (action, text) where action is a stable machine token an agent or
    CI step can branch on: accept | review | reject | unverifiable.
    """
    status = doc.get("status")
    if status == "password-protected":
        return ("unverifiable",
                "Password-protected — no verdict possible. Ask the sender for "
                "an unlocked copy and re-submit.")
    if status == "corrupted":
        return ("unverifiable",
                "File is corrupted/unreadable — no verdict possible. Request a "
                "freshly exported PDF and re-submit.")
    if status != "analyzed":
        return ("unverifiable", f"status {status!r} — no verdict yet.")

    risk = doc.get("fraud_risk")
    score = doc.get("trust_score")
    warnings = doc.get("warnings") or []
    changes = doc.get("change_summary") or {}
    edited = bool((doc.get("text_changes") or [])) or bool(changes.get("total_changes"))

    if risk == "trusted":
        return ("accept",
                f"Authentic — no fraud signals (trust {score}/100). "
                "Safe to accept through your normal process.")
    if risk == "low":
        return ("accept",
                f"Likely authentic (trust {score}/100). No special action "
                "needed beyond your normal checks.")
    if risk == "medium":
        return ("review",
                f"Review manually before accepting (trust {score}/100). "
                f"{len(warnings)} indicator(s) fired: {_top_signals(warnings)}. "
                "Confirm the document against the issuer/source.")
    if risk == "high":
        txt = (f"Do not accept as-is (trust {score}/100). Strong "
               f"forgery/tampering signals: {_top_signals(warnings)}.")
        if edited:
            txt += (" The document was edited after creation — inspect "
                    "text_changes for what was altered.")
        txt += " Request the original from the issuer, or reject."
        return ("reject", txt)
    return ("review", f"Unrecognized risk {risk!r} (trust {score}/100). "
                      "Treat with caution and review manually.")


def summarize(doc):
    action, text = recommend(doc)
    if doc.get("status") == "analyzed":
        warnings = doc.get("warnings") or []
        line = (
            f"verdict: {doc.get('fraud_risk')} "
            f"(trust_score {doc.get('trust_score')}/100), {len(warnings)} warning(s)"
        )
        for w in warnings[:3]:
            line += f"\n  - {w.get('indicator_id')}"
        print(line, file=sys.stderr)
    else:
        print(
            f"status: {doc.get('status')} — no verdict "
            "(trust_score/fraud_risk are null)",
            file=sys.stderr,
        )
    print(f"recommendation [{action}]: {text}", file=sys.stderr)


def verdict_code(doc):
    status = doc.get("status")
    if status == "analyzed":
        return {"high": 3, "medium": 4}.get(doc.get("fraud_risk"), 0)
    if status in ("password-protected", "corrupted"):
        return 5
    return 0


def emit(doc):
    # Enrich the verdict with a structured, actionable recommendation so an
    # agent can branch on doc["recommendation"]["action"] without re-deriving
    # intent from the risk label. This field is added by the skill, not the API.
    action, text = recommend(doc)
    doc["recommendation"] = {"action": action, "text": text}
    print(json.dumps(doc, indent=2))
    summarize(doc)
    sys.exit(verdict_code(doc))


def cmd_submit(file_path, custom_id):
    if not os.path.isfile(file_path):
        die(f"file not found: {file_path}", 2)
    body, content_type = build_multipart(file_path, custom_id)
    status, resp = request("POST", "/v2/document", body=body, content_type=content_type)
    if status != 202:
        die(f"submit returned unexpected status {status}: {resp}", 2)
    doc_id = resp.get("document_id")
    if not doc_id:
        die(f"no document_id in response: {resp}", 2)
    print(f"submitted, document_id={doc_id} — polling for verdict...", file=sys.stderr)
    for i in range(POLL_MAX):
        time.sleep(POLL_INTERVAL)
        _, doc = request("GET", f"/v2/document?document_id={quote(doc_id)}")
        if doc.get("status") != "processing":
            emit(doc)  # exits
        else:
            print(f"  still processing ({int((i + 1) * POLL_INTERVAL)}s)...", file=sys.stderr)
    die(
        f"timed out after {int(POLL_MAX * POLL_INTERVAL)}s still processing. "
        f"Fetch later: python3 verify.py get {doc_id}",
        2,
    )


def cmd_get(doc_id):
    _, doc = request("GET", f"/v2/document?document_id={quote(doc_id)}")
    emit(doc)


def cmd_delete(doc_id):
    _, resp = request("DELETE", f"/v2/document?document_id={quote(doc_id)}")
    print(json.dumps(resp, indent=2))


def cmd_report(doc_id, out, lang):
    out = out or f"report-{doc_id}.pdf"
    lang = lang or "en"
    query = urlencode({"document_id": doc_id, "lang": lang})
    request("GET", f"/v2/document/report?{query}", raw_out=out)
    print(f"report saved to {out}", file=sys.stderr)


def main(argv):
    if not argv:
        die("usage: verify.py <file.pdf> [custom_id] | get <id> | delete <id> | report <id> [out.pdf] [lang]", 2)
    cmd = argv[0]
    if cmd == "get":
        if len(argv) < 2:
            die("usage: verify.py get <document_id>", 2)
        cmd_get(argv[1])
    elif cmd == "delete":
        if len(argv) < 2:
            die("usage: verify.py delete <document_id>", 2)
        cmd_delete(argv[1])
    elif cmd == "report":
        if len(argv) < 2:
            die("usage: verify.py report <document_id> [out.pdf] [lang]", 2)
        cmd_report(argv[1], argv[2] if len(argv) > 2 else None, argv[3] if len(argv) > 3 else None)
    else:
        cmd_submit(cmd, argv[1] if len(argv) > 1 else None)


if __name__ == "__main__":
    main(sys.argv[1:])
