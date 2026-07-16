# AI-Powered CI/CD Pipeline

Automated PR code review with **Ollama** (`granite3.2:latest` — lightweight 8B model), then (later phases) auto-merge and
server deployment via GitHub Actions.

## Architecture (current)

```
Developer → feature branch → Open PR → main
                ↓
     Workflow: ai-review.yml  (Phase 2)
                ↓
     checkout → git diff(base...head) → review/ (Phase 1)
                ↓
     Ollama HTTP (10.28.81.52:11434)
                ↓
          PASS / FAIL
                ↓
     PR comment + Actions summary + logs
```

## LLM provider: Ollama

Reviews call your Ollama server over HTTP:

```python
POST {OLLAMA_BASE_URL}/api/chat
{
  "model": "granite3.2:latest",
  "messages": [{"role": "system", ...}, {"role": "user", ...}],
  "stream": false,
  "format": "json"
}
```

Defaults (override via env):

| Variable | Default |
|----------|---------|
| `OLLAMA_BASE_URL` | `http://10.28.81.52:11434` |
| `OLLAMA_MODEL` | `granite3.2:latest` |

No API key required — the runner must reach the Ollama host on your network.

## Phase 1 — Local review core

```bash
pip install -e ".[dev]"
pytest -q
python -m review.reviewer --diff-file tests/sample_diffs/clean_code.diff --json
```

Live review (Ollama must be running and reachable):

```bash
export OLLAMA_BASE_URL=http://10.28.81.52:11434
export OLLAMA_MODEL=granite3.2:latest
python -m review.reviewer --diff-file tests/sample_diffs/sql_injection.diff --json
```

## Phase 2 — GitHub Actions AI review

Workflow: `.github/workflows/ai-review.yml`

| Script | Role |
|--------|------|
| `scripts/generate_diff.py` | PR diff + changed-file logging |
| `scripts/run_ai_review.py` | Ollama review → `artifacts/review_result.json` |
| `scripts/publish_review.py` | PR comment + step summary |

### GitHub configuration

| Name | Purpose |
|------|---------|
| `OLLAMA_BASE_URL` (secret or repo variable) | e.g. `http://10.28.81.52:11434` |

**Note:** GitHub-hosted runners cannot reach private IPs unless you use a **self-hosted runner** on the same network as Ollama.

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `OLLAMA_BASE_URL` | `http://10.28.81.52:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `granite3.2:latest` | Model name |
| `MAX_DIFF_LINES` | `2000` | Diff size cap |
| `LLM_TIMEOUT_SECONDS` | `60` | HTTP timeout |
| `LLM_MAX_RETRIES` | `2` | Retries after failure |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `REVIEW_IGNORE_EXTRA` | _(empty)_ | Extra ignore globs |

## Layout

```
review/ollama_client.py   # Ollama HTTP client
review/reviewer.py        # orchestration
.github/workflows/ai-review.yml
scripts/
tests/
```
