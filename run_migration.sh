#!/bin/bash
set -e
set -x

DBS=(
"superhero"
"student_club"
"toxicology"
"thrombosis_prediction"
"formula_1"
"debit_card_specializing"
)

# Mapping for Spanner Short Names
get_spanner_name() {
  local db=$1
  if [[ "$db" == "debit_card_specializing" ]]; then
    echo "debit_card_spec"
  elif [[ "$db" == "thrombosis_prediction" ]]; then
    echo "thrombosis_pred"
  else
    echo "$db"
  fi
}

CONFIGS=(
"datasets/db_configs/postgres.yaml"
"datasets/db_configs/mysql.yaml"
"datasets/db_configs/spanner_gsql.yaml"
"datasets/db_configs/spanner_pg.yaml"
)

echo "Starting Migration for 11 Databases to 4 Engines..."

for db in "${DBS[@]}"; do
  sqlite_path="datasets/bird/db_connections/bird/${db}.sqlite"
  
  if [[ ! -f "$sqlite_path" ]]; then
    echo "Warning: $sqlite_path not found!"
    continue
  fi

  for config in "${CONFIGS[@]}"; do
    # Determine target DB name
    target_name=""
    if [[ "$config" == *"spanner"* ]]; then
        short_name=$(get_spanner_name "$db")
        if [[ "$config" == *"gsql"* ]]; then
            target_name="bird_${short_name}_gsql"
        else
            target_name="bird_${short_name}_pg"
        fi
    else
        # Local DBs use full name
        target_name="bird_${db}"
    fi
    
    echo "---------------------------------------------------"
    echo "Migrating $db -> $target_name (Config: $config)"
    echo "---------------------------------------------------"
    
    python3 utils/migrate_bird.py \
      --sqlite_path "$sqlite_path" \
      --db_config "$config" \
      --target_db_name "$target_name"
      
  done
done

echo "All migrations complete."
