# AI-Powered CI/CD Pipeline

Automated PR code review with **OpenAI** (`gpt-4o-mini`), then (later phases) auto-merge and
server deployment via GitHub Actions.

## Architecture (current)

```
Developer → feature branch → Open PR → main
                ↓
     Workflow: ai-review.yml  (Phase 2)
                ↓
     checkout → git diff(base...head) → review/ (Phase 1)
                ↓
     OpenAI API
                ↓
          PASS / FAIL
                ↓
     PR comment + Actions summary + logs
```

## LLM provider: OpenAI

| Variable | Default |
|----------|---------|
| `OPENAI_API_KEY` | _(required)_ |
| `OPENAI_MODEL` | `gpt-4o-mini` |

Runs on **GitHub-hosted** `ubuntu-latest` — no self-hosted runner needed.

## Local usage

```bash
pip install -e ".[dev]"
pytest -q
export OPENAI_API_KEY=sk-...
python -m review.reviewer --diff-file tests/sample_diffs/sql_injection.diff --json
```

## GitHub Secrets

| Secret | Purpose |
|--------|---------|
| `OPENAI_API_KEY` | OpenAI API key |

## How to trigger AI review

1. Create a feature branch and push a change
2. Open a Pull Request targeting `main`
3. Workflow **AI Code Review** runs automatically
4. AI posts a PASS/FAIL comment on the PR
