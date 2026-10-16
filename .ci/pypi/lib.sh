# Helpers for the steps in pypi.cloudbuild.yaml:
#   . /workspace/.ci/pypi/lib.sh
#
# POSIX sh. Needs BUILD_ID, COMMIT_SHA, LOCATION, PROJECT_ID and TAG_NAME
# from options.env.

# alert_policy.yaml matches this prefix and parses the line format. If you
# change either, update the policy.
PYPI_RESULT_PREFIX="PYPI_RESULT"

# The validate step writes the package version here.
PYPI_VERSION_FILE=/workspace/pypi_version.txt

# A step that ends the run early writes its status here.
PYPI_STOP_FILE=/workspace/pypi_stop.txt

# Prints the result line of the run.
#
# Usage: pypi_result STATUS STAGE DETAIL
#   STATUS  SUCCESS, SKIPPED, DRY_RUN, or FAILED
#   STAGE   the id of the step that ended the run
#   DETAIL  one sentence for the email
#
# Keep detail last, because the alert policy reads it with a greedy regex.
pypi_result() {
  _version=$(cat "$PYPI_VERSION_FILE" 2>/dev/null || echo "unknown")
  _detail=$(printf '%s' "$3" | tr '"\n' "' ")
  _log="https://console.cloud.google.com/cloud-build/builds;region=${LOCATION}/${BUILD_ID}?project=${PROJECT_ID}"
  _commit="https://github.com/GoogleCloudPlatform/evalbench/commit/${COMMIT_SHA}"
  echo "${PYPI_RESULT_PREFIX} status=$1 stage=$2 tag=${TAG_NAME:-none} version=${_version} commit=${_commit} log=${_log} detail=\"${_detail}\""
}

pypi_fail() {
  pypi_result FAILED "$1" "$2"
  exit 1
}

# Ends the run with exit 0. The next steps skip their work.
#
# Usage: pypi_stop STATUS STAGE DETAIL
pypi_stop() {
  pypi_result "$1" "$2" "$3"
  printf '%s\n' "$1" > "$PYPI_STOP_FILE"
  exit 0
}

# Exits 0 if an earlier step ended the run.
skip_if_stopped() {
  if [ -f "$PYPI_STOP_FILE" ]; then
    echo "An earlier step ended the run with status $(cat "$PYPI_STOP_FILE"). Nothing to do."
    exit 0
  fi
}
