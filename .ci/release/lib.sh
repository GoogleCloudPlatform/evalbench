# Helpers for the steps in release.cloudbuild.yaml:
#   . /workspace/.ci/release/lib.sh
#
# POSIX sh, so the busybox shell in the gcrane image can source it. Needs
# BUILD_ID, COMMIT_SHA, LOCATION and PROJECT_ID from options.env.

# alert_policy.yaml parses this line. Update it if the format changes.
RELEASE_RESULT_PREFIX="RELEASE_RESULT"

# Prints the result line of the run.
#
# Usage: release_result STATUS STAGE DETAIL
#   STATUS  SUCCESS, FAILED, ROLLED_BACK, or ROLLBACK_FAILED
#   STAGE   the id of the step that ended the run
#   DETAIL  one sentence for the email
#
# Reads the tag, PR list, and compare link from /workspace. If a step did not
# write them, it prints a fallback. Double quotes become single quotes. Keep
# detail last, because the alert policy reads it with a greedy regex.
release_result() {
  _tag=$(cat /workspace/release_tag.txt 2>/dev/null || echo "none")
  _detail=$(printf '%s' "$3" | tr '"\n' "' ")
  _prs=$(cat /workspace/release_prs.txt 2>/dev/null \
    || echo "PR list not computed before this step ended the run.")
  _prs=$(printf '%s' "$_prs" | tr '"\n' "' ")
  _compare=$(cat /workspace/release_compare.txt 2>/dev/null || echo "none")
  _log="https://console.cloud.google.com/cloud-build/builds;region=${LOCATION}/${BUILD_ID}?project=${PROJECT_ID}"
  _commit="https://github.com/GoogleCloudPlatform/evalbench/commit/${COMMIT_SHA}"
  echo "${RELEASE_RESULT_PREFIX} status=$1 stage=$2 tag=${_tag} commit=${_commit} log=${_log} compare=${_compare} prs=\"${_prs}\" detail=\"${_detail}\""
}

# Prints a FAILED result line and ends the step with an error.
#
# Usage: release_fail STAGE DETAIL
release_fail() {
  release_result FAILED "$1" "$2"
  exit 1
}

# Prints the ERROR and CRITICAL records and tracebacks in an eval_server log.
# Prints nothing if the log is clean. Matches only at the start of a line,
# because eval payloads can quote a traceback.
#
# Usage: log_problems < LOG
log_problems() {
  _log=$(cat)
  printf '%s\n' "$_log" \
    | grep -nE '^[0-9-]{10} [0-9:,]+ \[[^]]*\] (ERROR|CRITICAL) ' | head -40
  printf '%s\n' "$_log" \
    | grep -n -A8 '^Traceback (most recent call last)' | head -80
}

# Points TAG at DIGEST. 'tags update' moves an existing tag without the
# tags.delete permission. 'docker tags add' creates a new tag.
#
# Usage: move_tag IMAGE TAG DIGEST
#   IMAGE  the image path, e.g. us-central1-docker.pkg.dev/PROJECT/REPO/PKG
move_tag() {
  _host=$(echo "$1" | cut -d/ -f1)
  _project=$(echo "$1" | cut -d/ -f2)
  _repository=$(echo "$1" | cut -d/ -f3)
  _package=$(echo "$1" | cut -d/ -f4-)
  gcloud artifacts tags update "$2" --location="${_host%-docker.pkg.dev}" \
      --project="$_project" --repository="$_repository" \
      --package="$_package" --version="$3" 2>/dev/null \
    || gcloud artifacts docker tags add "$1@$3" "$1:$2"
}
