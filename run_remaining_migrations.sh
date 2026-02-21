#!/bin/bash
set -e

# Disable Spanner OpenTelemetry metrics to prevent exit crashes
export GOOGLE_CLOUD_DISABLE_OPENTELEMETRY="true"
# Force unbuffered output for Python
export PYTHONUNBUFFERED=1

# List of ALL 11 BIRD databases to migrate/verify
DBS=(
"california_schools"
"superhero"
"student_club"
"toxicology"
"thrombosis_prediction"
"formula_1"
"debit_card_specializing"
"financial"
"card_games"
"codebase_community"
"european_football_2"
)

# Target configurations
CONFIGS=(
"datasets/db_configs/postgres.yaml"
"datasets/db_configs/mysql.yaml"
"datasets/db_configs/spanner_gsql.yaml"
"datasets/db_configs/spanner_pg.yaml"
)

echo "Ensuring local database infrastructure..."
python3 ensure_local_dbs.py

echo "Starting BIRD Migration Verification and Catch-up..."
echo "Checking status for ALL ${#DBS[@]} databases across 4 engines..."

for db in "${DBS[@]}"; do
  sqlite_path="datasets/bird/db_connections/bird/${db}.sqlite"
  
  if [[ ! -f "$sqlite_path" ]]; then
    echo "Warning: Source DB $sqlite_path not found. Skipping."
    continue
  fi

  for config in "${CONFIGS[@]}"; do
    # Determine target DB name mapping
    if [[ "$db" == "debit_card_specializing" ]]; then
      target_base="debit_card_spec"
    elif [[ "$db" == "thrombosis_prediction" ]]; then
      target_base="thrombosis_pred"
    elif [[ "$db" == "european_football_2" ]]; then
      target_base="european_football_2"
    else
      target_base="$db"
    fi

    # Append suffix based on config
    target_name="bird_${target_base}"
    if [[ "$config" == *"spanner_gsql"* ]]; then
      target_name="${target_name}_gsql"
    elif [[ "$config" == *"spanner_pg"* ]]; then
      target_name="${target_name}_pg"
    fi

    echo "Checking $target_name..."
    
    set +e
    V_ERROR=$(python3 utils/check_migration_status.py \
        --sqlite_path "$sqlite_path" \
        --db_config "$config" \
        --target_db_name "$target_name" 2>&1)
    RET_CODE=$?
    set -e
    
    if [[ $RET_CODE -eq 0 ]]; then
      echo "  [OK] Verified."
      continue
    fi

    # RET_CODE 2: DB/Tables missing (Empty) -> Safe to migrate
    # RET_CODE 3: Schema valid but Data missing (Empty) -> Safe to migrate
    if [[ $RET_CODE -eq 2 ]]; then
      echo "  [EMPTY] Target DB/Tables missing. Proceeding with initial migration..."
    elif [[ $RET_CODE -eq 3 ]]; then
      echo "  [EMPTY_DATA] Schema valid but tables are empty. Proceeding with data load..."
    else
      echo "  [FAIL] Validation failed for $target_name."
      echo "  Reason: $V_ERROR"
      echo ""
      echo "  WARNING: Migration for $target_name will DROP and RECREATE all existing tables."
      read -p "  Continue with migration for $target_name? [y/N] " confirm
      if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
        echo "  Skipping migration as requested."
        continue
      fi
    fi

    echo "---------------------------------------------------"
    echo "Migrating $db -> $target_name (Config: $config)"
    echo "---------------------------------------------------"
    
    # Run migration
    python3 utils/migrate_bird.py \
      --sqlite_path "$sqlite_path" \
      --db_config "$config" \
      --target_db_name "$target_name"
      
    echo "---------------------------------------------------"
    echo ""
  done
done

echo "Done. All 11 migrations verified or completed."
