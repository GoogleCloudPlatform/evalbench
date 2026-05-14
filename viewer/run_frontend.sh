#!/bin/bash
exec gunicorn -w 12 -k sync main:me --bind :${PORT:-3000} --forwarded-allow-ips="*" --timeout 120
