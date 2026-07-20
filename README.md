# AI-Powered CI/CD Pipeline

Automated push-based code review with **OpenAI** and **squash auto-merge on PASS**.

Deploy to server is deferred for later.

## Architecture

```
Developer → push to feature branch (not main)
                ↓
     Workflow: ai-review.yml
                ↓
     git diff vs main → OpenAI review → PASS / FAIL
                ↓
     Workflow logs + step summary
                ↓
     PASS → squash merge into main
     FAIL → stop (no merge)
```

No pull request is required — the pipeline runs on every push to a non-`main` branch.

## Auto-merge (squash)

| Setting | Value |
|---------|--------|
| Strategy | `git merge --squash` into `main`, then push |
| On **PASS** | Branch squash-merged into `main` automatically |
| On **FAIL** | Workflow fails; merge blocked; feedback in logs |
| Infrastructure failure | Never merges (fail-closed) |

## GitHub Secrets

| Secret | Purpose |
|--------|---------|
| `OPENAI_API_KEY` | OpenAI API key |

`GITHUB_TOKEN` is provided by Actions. The review workflow needs `contents: write` to push the squash merge to `main`.

## How to use

1. Push commits to a feature branch (not `main`)
2. AI review runs on the diff against `main`
3. **PASS** → branch squash-merged into `main` automatically
4. **FAIL** → fix code and push again; feedback appears in workflow logs

## Local usage

```bash
pip install -e ".[dev]"
pytest -q
export OPENAI_API_KEY=sk-...
python -m review.reviewer --diff-file tests/sample_diffs/clean_code.diff --json
python scripts/merge_branch.py --result-file artifacts/review_result.json --branch feature-x --dry-run
```

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `OPENAI_API_KEY` | _(required)_ | OpenAI API key |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model id |
| `MAX_DIFF_LINES` | `2000` | Diff size cap |
| `LLM_TIMEOUT_SECONDS` | `90` | LLM timeout |
| `LOG_LEVEL` | `INFO` | Logging level |
