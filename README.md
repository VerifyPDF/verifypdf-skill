# VerifyPDF agent skill

Point your AI agent at a PDF and get back a verdict: is the document authentic, or was it forged, AI-generated or edited? This skill wraps the VerifyPDF forensic API so any agent that reads a `SKILL.md` and runs a shell command can answer that in one step.

It works with Claude Code, Cursor and other coding agents. The two helper scripts also run fine on their own as a CLI.

## What is in the pack

| File                     | Purpose                                                     |
|--------------------------|-------------------------------------------------------------|
| `SKILL.md`               | The skill an agent loads to verify PDFs.                    |
| `verify.sh`              | Bash helper (needs `curl` and `jq`).                        |
| `verify.py`              | Python helper (standard library only, no `pip install`).    |
| `.verifypdf-key.example` | Template for saving your API key. Copy to `.verifypdf-key`. |

## Setup (about a minute)

1. **Get an API key.** Sign in at https://secure.verifypdf.com, open the
   **Developers** section and create a key. API access is on the Professional
   and Corporate plans. Grab a **test key** (`key_test_...`) too. It is free and
   lets you exercise the flow without spending quota.

2. **Save the key.** Either:
   - export it (recommended for production and CI):
     ```bash
     export VERIFYPDF_API_KEY="key_live_..."
     ```
   - or copy `.verifypdf-key.example` to `.verifypdf-key` and paste your key in.
     That file is git-ignored so it will not be committed.

3. **Install the skill for your agent.** For Claude Code, clone this repo straight
   into your project (or user) skills directory as `verifypdf/`:
   ```bash
   git clone https://github.com/VerifyPDF/verifypdf-skill .claude/skills/verifypdf
   ```
   Or, if you have the `skills` CLI: `npx skills add VerifyPDF/verifypdf-skill`.
   For other agents, point them at `SKILL.md`.

## Try it

```bash
# Free smoke test with a test key. The verdict comes from the filename:
cp some.pdf test-high.pdf
./verify.sh test-high.pdf        # -> fraud_risk: high

# Real document with a live key:
./verify.sh ./bank-statement.pdf
```

You get the full verdict JSON on stdout and a one-line summary on stderr.
See `SKILL.md` for the response shape, exit codes and the test-mode filename
table.

## Security

- Never commit `.verifypdf-key` or paste your key into a chat prompt.
- Prefer the `VERIFYPDF_API_KEY` environment variable in shared or CI
  environments so the secret never touches disk.
- Rotate a key immediately in the Developers section if it leaks.

Full reference and FAQ: https://verifypdf.com/agents/
