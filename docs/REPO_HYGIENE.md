# Repo Hygiene and Safe Git Publishing

Last updated: 2026-03-22
Status: Active

## Purpose

This repo is safe to push only if code stays in Git and credentials/runtime
artifacts stay local.

## What belongs in Git

- source code
- tests
- docs
- tracked sample fixtures
- `.env.template` with placeholders only

## What must stay local

- `.env`
- `*.pem`
- `secrets/`
- `tmp/core_mm_runs/`
- `tmp/test-run/`
- runtime databases, status dumps, tapes, and local operator notes

## Local secret layout

Use this structure on the machine that runs the bot:

```text
secrets/
  kalshi-private-key.pem
  kalshi-public-key.pem
.env
```

Recommended `.env` entries:

```bash
KALSHI_API_KEY_ID=your_key_id
KALSHI_PRIVATE_KEY_PATH=./secrets/kalshi-private-key.pem
KALSHI_BASE_URL=https://api.elections.kalshi.com
```

## Private remote policy

- Push to a **private** remote first.
- Do not push if credentials or runtime artifacts are still staged or tracked.
- If any credential has ever been committed to history, rotate it before push.

## Pre-push checklist

Run these before `git push`:

```bash
git status --short
git ls-files '*.pem'
git ls-files 'tmp/core_mm_runs/*' 'tmp/test-run/*'
```

Expected result:

- no PEM files tracked
- no runtime artifacts tracked
- only intended code/docs/tests staged

## Same-day live trading rule

For local supervised LIVE sessions:

- keep the bot local
- keep secrets local
- export run artifacts to `tmp/`
- summarize results in Git-tracked docs or Linear, not by committing raw tapes
