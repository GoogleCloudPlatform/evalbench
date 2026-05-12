#!/bin/bash
exec uv run gunicorn -w 12 -k sync main:me --bind :${PORT:-3000} --forwarded-allow-ips="*" --timeout 120
