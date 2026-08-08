#!/bin/sh
# Applies pending Alembic migrations, then starts the app. Runs on every
# container start: a no-op when the schema is already current, so it is safe
# to leave in place rather than requiring a separate manual migration step.
set -e

alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
