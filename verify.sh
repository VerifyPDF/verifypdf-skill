#!/usr/bin/env bash
#
# VerifyPDF document verification helper.
#
#   ./verify.sh <file.pdf> [custom_id]   submit a PDF, poll, print the verdict
#   ./verify.sh get    <document_id>     fetch an existing document
#   ./verify.sh delete <document_id>     delete a document
#   ./verify.sh report <document_id> [out.pdf] [lang]   download the PDF report
#
# The API key is read from $VERIFYPDF_API_KEY, or from a `.verifypdf-key` file
# next to this script (one line, the key only). Never pass it on the command
# line or echo it. See SKILL.md.
#
set -euo pipefail

API_BASE="${VERIFYPDF_API_BASE:-https://api.verifypdf.com}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLL_INTERVAL="${VERIFYPDF_POLL_INTERVAL:-3}"
POLL_MAX="${VERIFYPDF_POLL_MAX:-40}"   # 40 * 3s = 120s

die() { printf 'verify.sh: %s\n' "$1" >&2; exit "${2:-1}"; }

for bin in curl jq; do
  command -v "$bin" >/dev/null 2>&1 || die "'$bin' is required but not installed. Use verify.py if you cannot install $bin."
done

resolve_key() {
  if [ -n "${VERIFYPDF_API_KEY:-}" ]; then
    printf '%s' "$VERIFYPDF_API_KEY" | tr -d '[:space:]'
    return 0
  fi
  local f="$SCRIPT_DIR/.verifypdf-key" line
  if [ -f "$f" ]; then
    # First non-empty, non-comment line. Read-loop (no pipe) to stay safe under
    # `set -o pipefail` — a grep -m1 early-close would SIGPIPE its upstream.
    while IFS= read -r line || [ -n "$line" ]; do
      line="${line#"${line%%[![:space:]]*}"}"   # left-trim whitespace
      case "$line" in '' | '#'*) continue ;; esac
      printf '%s' "$line" | tr -d '[:space:]'
      return 0
    done < "$f"
  fi
  die "No API key. Set VERIFYPDF_API_KEY, or copy .verifypdf-key.example to .verifypdf-key and paste your key. Get a key at https://secure.verifypdf.com (Developers)."
}

API_KEY="$(resolve_key)"
[ -n "$API_KEY" ] || die "API key is empty. Check VERIFYPDF_API_KEY or .verifypdf-key."

# Print a one-line human summary to stderr from a verdict JSON on stdin arg $1.
summarize() {
  local json="$1"
  jq -r '
    if .status == "analyzed" then
      "verdict: \(.fraud_risk) (trust_score \(.trust_score)/100), \(.warnings | length) warning(s)"
      + (if (.warnings | length) > 0 then "\n  - " + (.warnings[0:3] | map(.indicator_id) | join("\n  - ")) else "" end)
    else
      "status: \(.status) — no verdict (trust_score/fraud_risk are null)"
    end' <<<"$json" >&2
}

# Map a verdict to "<action>\t<text>": action is a stable token a caller can
# branch on (accept | review | reject | unverifiable). Mirrors verify.py's
# recommend(). All logic lives in jq so the bash and python helpers agree.
recommend() {
  jq -r '
    def signals: (((.warnings // [])[0:3]) | map(.description // .indicator_id) | join("; "))
      + (if (((.warnings // []) | length) > 3) then "; +\(((.warnings // []) | length) - 3) more" else "" end);
    def edited: ((.text_changes | length) > 0) or ((.change_summary.total_changes // 0) > 0);
    if .status == "password-protected" then
      "unverifiable\tPassword-protected — no verdict possible. Ask the sender for an unlocked copy and re-submit."
    elif .status == "corrupted" then
      "unverifiable\tFile is corrupted/unreadable — no verdict possible. Request a freshly exported PDF and re-submit."
    elif .status != "analyzed" then
      "unverifiable\tstatus \(.status) — no verdict yet."
    elif .fraud_risk == "trusted" then
      "accept\tAuthentic — no fraud signals (trust \(.trust_score)/100). Safe to accept through your normal process."
    elif .fraud_risk == "low" then
      "accept\tLikely authentic (trust \(.trust_score)/100). No special action needed beyond your normal checks."
    elif .fraud_risk == "medium" then
      "review\tReview manually before accepting (trust \(.trust_score)/100). \(.warnings | length) indicator(s) fired: \(signals). Confirm the document against the issuer/source."
    elif .fraud_risk == "high" then
      "reject\tDo not accept as-is (trust \(.trust_score)/100). Strong forgery/tampering signals: \(signals)."
        + (if edited then " The document was edited after creation — inspect text_changes for what was altered." else "" end)
        + " Request the original from the issuer, or reject."
    else
      "review\tUnrecognized risk \(.fraud_risk) (trust \(.trust_score)/100). Treat with caution and review manually."
    end' <<<"$1"
}

# Print the enriched verdict JSON (with a .recommendation object) to stdout, and
# the human summary + recommendation line to stderr.
emit() {
  local json="$1" rec action text
  rec="$(recommend "$json")"
  action="${rec%%$'\t'*}"
  text="${rec#*$'\t'}"
  jq --arg a "$action" --arg t "$text" '. + {recommendation: {action: $a, text: $t}}' <<<"$json"
  summarize "$json"
  printf 'recommendation [%s]: %s\n' "$action" "$text" >&2
}

# Exit code reflects the verdict so callers can branch: 0 trusted/low/processing
# states resolved fine, 3 = high risk, 4 = medium risk, 5 = unanalyzable.
verdict_exit() {
  local json="$1" risk status
  status="$(jq -r '.status' <<<"$json")"
  risk="$(jq -r '.fraud_risk // "null"' <<<"$json")"
  case "$status" in
    analyzed)
      case "$risk" in
        high) return 3 ;;
        medium) return 4 ;;
        *) return 0 ;;
      esac ;;
    password-protected|corrupted) return 5 ;;
    *) return 0 ;;
  esac
}

cmd_submit() {
  local file="$1" custom_id="${2:-}"
  [ -f "$file" ] || die "file not found: $file"

  local args=(-sS -X POST "$API_BASE/v2/document" -H "API-KEY: $API_KEY" -F "file=@$file")
  [ -n "$custom_id" ] && args+=(-F "custom_id=$custom_id")

  local resp http body
  resp="$(curl "${args[@]}" -w $'\n%{http_code}')"
  http="${resp##*$'\n'}"
  body="${resp%$'\n'*}"
  [ "$http" = "202" ] || die "submit failed (HTTP $http): $body" 2

  local doc_id
  doc_id="$(jq -r '.document_id' <<<"$body")"
  [ -n "$doc_id" ] && [ "$doc_id" != "null" ] || die "no document_id in response: $body" 2
  printf 'submitted, document_id=%s — polling for verdict...\n' "$doc_id" >&2

  local i status out
  for ((i = 0; i < POLL_MAX; i++)); do
    sleep "$POLL_INTERVAL"
    out="$(cmd_fetch_raw "$doc_id")"
    status="$(jq -r '.status' <<<"$out")"
    if [ "$status" != "processing" ]; then
      emit "$out"
      verdict_exit "$out"; return $?
    fi
    printf '  still processing (%ds)...\n' "$(( (i + 1) * POLL_INTERVAL ))" >&2
  done
  die "timed out after $((POLL_MAX * POLL_INTERVAL))s still processing. Fetch later: ./verify.sh get $doc_id" 2
}

cmd_fetch_raw() {
  local id="$1" resp http body
  resp="$(curl -sS -G "$API_BASE/v2/document" --data-urlencode "document_id=$id" -H "API-KEY: $API_KEY" -w $'\n%{http_code}')"
  http="${resp##*$'\n'}"
  body="${resp%$'\n'*}"
  [ "$http" = "200" ] || die "get failed (HTTP $http): $body" 2
  printf '%s' "$body"
}

cmd_get() {
  local out; out="$(cmd_fetch_raw "$1")"
  emit "$out"
  verdict_exit "$out"
}

cmd_delete() {
  local resp http body
  resp="$(curl -sS -G -X DELETE "$API_BASE/v2/document" --data-urlencode "document_id=$1" -H "API-KEY: $API_KEY" -w $'\n%{http_code}')"
  http="${resp##*$'\n'}"
  body="${resp%$'\n'*}"
  [ "$http" = "200" ] || die "delete failed (HTTP $http): $body" 2
  printf '%s\n' "$body"
}

cmd_report() {
  local id="$1" out="${2:-report-$1.pdf}" lang="${3:-en}" http
  http="$(curl -sS -G "$API_BASE/v2/document/report" --data-urlencode "document_id=$id" --data-urlencode "lang=$lang" -H "API-KEY: $API_KEY" -o "$out" -w '%{http_code}')"
  [ "$http" = "200" ] || { rm -f "$out"; die "report failed (HTTP $http)" 2; }
  printf 'report saved to %s\n' "$out" >&2
}

main() {
  [ $# -ge 1 ] || die "usage: verify.sh <file.pdf> [custom_id] | get <id> | delete <id> | report <id> [out.pdf] [lang]"
  case "$1" in
    get)    [ $# -ge 2 ] || die "usage: verify.sh get <document_id>"; cmd_get "$2" ;;
    delete) [ $# -ge 2 ] || die "usage: verify.sh delete <document_id>"; cmd_delete "$2" ;;
    report) [ $# -ge 2 ] || die "usage: verify.sh report <document_id> [out.pdf] [lang]"; cmd_report "$2" "${3:-}" "${4:-}" ;;
    *)      cmd_submit "$1" "${2:-}" ;;
  esac
}

main "$@"
