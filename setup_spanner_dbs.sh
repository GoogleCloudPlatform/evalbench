#!/bin/bash
INSTANCE="your-instance-id"
DBS=(
"california_schools"
"card_games"
"codebase_community"
"debit_card_specializing"
"european_football_2"
"financial"
"formula_1"
"student_club"
"superhero"
"thrombosis_prediction"
"toxicology"
)

echo "Creating Spanner Databases..."

for db in "${DBS[@]}"; do
  # GSQL
  gsql_name="bird_${db}_gsql"
  echo "Creating $gsql_name (GOOGLE_STANDARD_SQL)..."
  gcloud spanner databases create "$gsql_name" --instance="$INSTANCE" --database-dialect=GOOGLE_STANDARD_SQL --quiet &
  
  # PG
  pg_name="bird_${db}_pg"
  echo "Creating $pg_name (POSTGRESQL)..."
  gcloud spanner databases create "$pg_name" --instance="$INSTANCE" --database-dialect=POSTGRESQL --quiet &
  
  # Limit concurrency to 4 to avoid rate limits
  if (( $(jobs -r -p | wc -l) >= 4 )); then
     wait -n
  fi
done

wait
echo "Spanner DB creation complete."
