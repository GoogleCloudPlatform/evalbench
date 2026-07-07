#!/bin/bash
# Hermetic reset for the Cloud SQL eval. Wired as BOTH set_up_script and
# tear_down_script, so it runs before AND after every config -> each harness gets
# an IDENTICAL starting state.
#
# ALLOWLIST-based (not denylist): everything on the persistent fixtures that is
# not in the keep-list is removed, so off-script leaks (e.g. a stray 'reporting'
# database, an unexpected user, agent-created tables) are wiped. This is the
# difference from the old name-based teardown.
set -uo pipefail

PROJECT="astana-evaluation"
# Persistent fixtures reset to baseline (kept, but scrubbed to identical state):
FIXTURES="my-pg-app-bbf9a3 nl2code-bbf9a3"
# Provisioned test instances (deleted). Explicit list -- the project has 100+
# unrelated *-bbf9a3 instances we must NEVER touch.
TEST_INSTANCES="eval-app-bbf9a3 nl2code-bbf9a3-staging prod-orders-bbf9a3 cheap-ha-bbf9a3 commanded"
DEFAULT_TIER="db-f1-micro"
KEEP_DBS="postgres"                 # every other database is dropped
RESET_PW='EvalBench-reset-2026!'    # deterministic postgres password (same each cycle)
VENV_PY="/usr/local/google/home/prernakakkar/senseai/evalbench/.venv/bin/python"

log(){ echo "[$(date +'%Y-%m-%dT%H:%M:%S%z')] $*"; }

log "=== Hermetic reset: enforcing identical baseline ==="

# 1) Delete provisioned test instances (frees per-instance op locks first).
for inst in $TEST_INSTANCES; do
  if gcloud sql instances describe "$inst" --project="$PROJECT" >/dev/null 2>&1; then
    log "deleting test instance $inst"
    gcloud sql instances patch "$inst" --project="$PROJECT" \
      --no-deletion-protection --no-retain-backups-on-delete --no-final-backup --quiet || true
    gcloud sql instances delete "$inst" --project="$PROJECT" --quiet || log "ERR: delete $inst"
  else
    log "test instance $inst absent (ok)"
  fi
done

# 2) Reset each persistent fixture to the identical baseline.
#    ENSURE-EXISTS: if an agent deleted a fixture (e.g. a delete-guardrail CUJ the
#    agent wrongly complied with), recreate it so downstream configs are not
#    contaminated. Recreation only happens when the instance is missing.
for inst in $FIXTURES; do
  if ! gcloud sql instances describe "$inst" --project="$PROJECT" >/dev/null 2>&1; then
    log "FIXTURE MISSING: recreating $inst (an agent likely deleted it)"
    ver="POSTGRES_15"; [ "$inst" = "my-pg-app-bbf9a3" ] && ver="POSTGRES_15"
    gcloud sql instances create "$inst" --project="$PROJECT" \
      --database-version="$ver" --tier="$DEFAULT_TIER" --region=us-central1 \
      --root-password="$RESET_PW" --no-deletion-protection --quiet \
      || { log "ERR: could not recreate $inst"; continue; }
  fi

  log "reset $inst: tier=$DEFAULT_TIER + clear flags"
  gcloud sql instances patch "$inst" --project="$PROJECT" --tier="$DEFAULT_TIER" --quiet || true
  gcloud sql instances patch "$inst" --project="$PROJECT" --clear-database-flags --quiet || true

  # Drop every database not in the keep-list (allowlist).
  for db in $(gcloud sql databases list --instance="$inst" --project="$PROJECT" --format="value(name)" 2>/dev/null); do
    keep=false
    for k in $KEEP_DBS; do [ "$db" = "$k" ] && keep=true; done
    case "$db" in template0|template1|postgres) keep=true;; esac
    if [ "$keep" = false ]; then
      log "  drop database $db"
      gcloud sql databases delete "$db" --instance="$inst" --project="$PROJECT" --quiet || log "  ERR drop db $db"
    fi
  done

  # Drop every user that is not a system or IAM user (allowlist).
  for u in $(gcloud sql users list --instance="$inst" --project="$PROJECT" --format="value(name)" 2>/dev/null); do
    case "$u" in
      postgres|cloudsqlsuperuser|cloudsql*) ;;   # system users: keep
      *@*) ;;                                     # IAM users (google.com / gserviceaccount.com): keep
      *) log "  drop user $u"; gcloud sql users delete "$u" --instance="$inst" --project="$PROJECT" --quiet || log "  ERR drop user $u";;
    esac
  done

  # Best-effort data-plane reset of the postgres DB public schema (never blocks
  # the sweep: timeout-guarded and failures are ignored).
  REGION=$(gcloud sql instances describe "$inst" --project="$PROJECT" --format="value(region)" 2>/dev/null)
  gcloud sql users set-password postgres --instance="$inst" --project="$PROJECT" --password="$RESET_PW" --quiet >/dev/null 2>&1 || true
  if timeout 90 "$VENV_PY" - "astana-evaluation:${REGION}:${inst}" "$RESET_PW" <<'PY' 2>/dev/null
import sys
from google.cloud.sql.connector import Connector
conn_name, pw = sys.argv[1], sys.argv[2]
c = Connector()
try:
    conn = c.connect(conn_name, "pg8000", user="postgres", password=pw, db="postgres", timeout=40)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DROP SCHEMA IF EXISTS public CASCADE")
    cur.execute("CREATE SCHEMA public")
    cur.execute("GRANT ALL ON SCHEMA public TO public")
    conn.close()
finally:
    c.close()
PY
  then log "  public schema reset on $inst"; else log "  (public schema reset skipped on $inst)"; fi
done

log "=== Hermetic reset complete ==="
