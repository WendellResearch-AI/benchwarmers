# Benchwarmers in one container: the dashboard, the gateway's routes on the
# same port, and the scheduler the dashboard starts. See compose.yaml, and
# HOSTING.md for running it on a hosting platform or a VPS.
#
#   docker build -t benchwarmers .
#   docker run -p 127.0.0.1:8122:8122 -v benchwarmers-data:/data \
#     -e BENCHWARMERS_ADMIN_TOKEN=<16+ characters> benchwarmers
#
# It listens on 0.0.0.0 inside the container, so it refuses to start without
# an admin token. No llm CLI: every model call goes through the built-in
# client, which reads keys from BENCHWARMERS_KEY_<NAME> first, then from
# /data/llm/keys.json (where a key typed or signed in on the dashboard goes).
#
# It starts as root only for deploy/container.py, which takes a platform's
# PORT and public address, hands /data to the app's user (a platform volume
# is mounted owned by root) and drops to that user before serve.py runs.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 PYTHONDONTWRITEBYTECODE=1 \
    BENCHWARMERS_HOME=/data LLM_USER_PATH=/data/llm \
    BENCH_BIND=0.0.0.0 BENCH_PORT=8122 BENCH_CLIENT=builtin

RUN useradd --create-home --uid 1000 bench && mkdir /data && chown bench /data
WORKDIR /app
# Owned by root: the code is not the app user's to change.
COPY . .
VOLUME /data
EXPOSE 8122

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % (os.environ.get('PORT') or os.environ['BENCH_PORT']), timeout=4)"

CMD ["python", "deploy/container.py"]
