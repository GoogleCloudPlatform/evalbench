#!/bin/bash
# evalbench_service/entrypoint.sh

if [[ "$CLOUD_RUN" == "True" ]]; then
    echo "Cloud Run detected. Starting only gunicorn frontend on port ${PORT:-3000}..."
    # Ensure we are in the viewer directory for gunicorn to find main:me
    cd /evalbench/viewer
    exec gunicorn -w 4 -k gevent main:me --bind :${PORT:-3000} --forwarded-allow-ips="*"
else
    echo "Starting supervisord to manage multiple processes..."
    exec /usr/bin/supervisord -c /evalbench/supervisord.conf
fi
