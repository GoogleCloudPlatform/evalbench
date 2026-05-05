#!/bin/bash
# evalbench_service/entrypoint.sh

if [[ -n "$KUBERNETES_SERVICE_HOST" ]]; then
    echo "GKE detected. Starting supervisord for evalbench server..."
    exec /usr/bin/supervisord -c /evalbench/supervisord_evalbench.conf
elif [[ "$CLOUD_RUN" == "True" ]]; then
    echo "Cloud Run detected. Starting supervisord for frontend and precompute..."
    exec /usr/bin/supervisord -c /evalbench/supervisord_cloudrun.conf
else
    echo "Nothing detected. Starting combined supervisord for server, frontend, and precompute..."
    export PRECOMPUTE_INTERVAL=30
    exec /usr/bin/supervisord -c /evalbench/supervisord_combined.conf
fi
