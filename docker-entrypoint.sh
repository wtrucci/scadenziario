#!/bin/sh
# Applies pending Alembic migrations, then starts the app. Runs on every
# container start: a no-op when the schema is already current, so it is safe
# to leave in place rather than requiring a separate manual migration step.
set -e

alembic upgrade head

# --forwarded-allow-ips: behind a reverse proxy that terminates TLS (Traefik,
# nginx, Caddy) the app itself is reached over plain http. Without trusting the
# proxy's X-Forwarded-Proto, url_for() builds "http://..." links for the CSS on
# a page served over https, and the browser blocks them as mixed content — the
# interface then renders completely unstyled.
#
# uvicorn only trusts 127.0.0.1 by default, and a proxy in a Docker network
# arrives from 172.x, so the headers would be ignored. "*" trusts whoever
# connects, which is right when only the proxy can reach the container. If port
# 8000 is exposed directly to untrusted clients, set FORWARDED_ALLOW_IPS to the
# proxy's address instead, so nobody can spoof the headers.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}"
