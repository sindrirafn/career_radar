# Career Radar

Career Radar is a private, single-user job recommendation and application
tracker. It imports your job sources into a review queue, enriches and ranks
roles for your profile, and keeps your applications, deadlines and next actions
in one local workspace.

It contains no public job-market archive or public visualization.

## Start

```bash
PYTHONPATH=src .venv/bin/python -m career_radar.cli setup
PYTHONPATH=src .venv/bin/python -m career_radar.cli web
```

Use the private dashboard to review recommendations and manage applications.
See `docs/START_HERE.md` for the private import and review workflow.
