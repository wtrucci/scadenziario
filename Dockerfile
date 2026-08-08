FROM python:3.12-slim-bookworm

# tzdata is needed for Europe/Rome to resolve correctly (slim images ship
# without a timezone database); the scheduler (app/scheduler.py) and any
# "today" comparisons in the occurrence engine depend on the container clock
# being right, not just the TZ env var.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Europe/Rome \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# Overwritten by the docker-compose volume in normal use; kept so the image
# also runs standalone (docker run) without a pre-existing bind mount.
RUN mkdir -p /app/data

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
