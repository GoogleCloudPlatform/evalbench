#!/bin/bash
# evalbench_service/entrypoint.sh

if [[ "$CLOUD_RUN" == "True" ]]; then
    echo "Cloud Run detected. Starting supervisord for frontend and precompute..."
    exec /usr/bin/supervisord -c /evalbench/supervisord_cloudrun.conf
else
    echo "Starting supervisord for evalbench server..."
    exec /usr/bin/supervisord -c /evalbench/supervisord_evalbench.conf
fi
