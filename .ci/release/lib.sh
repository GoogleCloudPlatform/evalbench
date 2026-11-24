# Helpers for the steps in release.cloudbuild.yaml:
#   . /release/ci/lib.sh
#
# POSIX sh, so the busybox shell in the gcrane image can source it. Needs
# BUILD_ID, COMMIT_SHA, LOCATION and PROJECT_ID from options.env.

# alert_policy.yaml matches this prefix and parses the line format. If you
# change either, update the policy.
RELEASE_RESULT_PREFIX="RELEASE_RESULT"

# The steps keep their state in the release-state volume at /release. The
# smoke gate runs the candidate image and does not mount the volume, so it
# reads the copies from share_result_files.
if [ -d /release ]; then
  RELEASE_STATE_DIR=/release
else
  RELEASE_STATE_DIR=/workspace
fi

# Copies the files of the result line to /workspace for the smoke gate. No
# other step reads the copies. --remove-destination replaces a symlink and
# does not write through it.
#
# Usage: share_result_files
share_result_files() {
  for _file in release_tag.txt release_prs.txt release_compare.txt; do
    [ -f "$RELEASE_STATE_DIR/$_file" ] \
      && cp --remove-destination "$RELEASE_STATE_DIR/$_file" "/workspace/$_file"
  done
  return 0
}

# Prints the result line of the run.
#
# Usage: release_result STATUS STAGE DETAIL
#   STATUS  SUCCESS, FAILED, ROLLED_BACK, or ROLLBACK_FAILED
#   STAGE   the id of the step that ended the run
#   DETAIL  one sentence for the email
#
# Reads the tag, PR list, and compare link from RELEASE_STATE_DIR. Keep detail
# last, because the alert policy reads it with a greedy regex.
release_result() {
  _tag=$(cat "$RELEASE_STATE_DIR/release_tag.txt" 2>/dev/null || echo "none")
  _detail=$(printf '%s' "$3" | tr '"\n' "' ")
  _prs=$(cat "$RELEASE_STATE_DIR/release_prs.txt" 2>/dev/null \
    || echo "PR list not computed before this step ended the run.")
  _prs=$(printf '%s' "$_prs" | tr '"\n' "' ")
  _compare=$(cat "$RELEASE_STATE_DIR/release_compare.txt" 2>/dev/null || echo "unavailable")
  _log="https://console.cloud.google.com/cloud-build/builds;region=${LOCATION}/${BUILD_ID}?project=${PROJECT_ID}"
  _commit="https://github.com/GoogleCloudPlatform/evalbench/commit/${COMMIT_SHA}"
  echo "${RELEASE_RESULT_PREFIX} status=$1 stage=$2 tag=${_tag} commit=${_commit} log=${_log} compare=${_compare} prs=\"${_prs}\" detail=\"${_detail}\""
}

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

# Prints "POD DIGEST" for each pod that is Running and not Terminating.
# Skips Evicted pods, and old pods that stay Terminating after a rollout.
#
# Usage: serving_pods NAMESPACE SELECTOR CONTAINER
serving_pods() {
  kubectl get pods -n "$1" -l "$2" \
    -o jsonpath="{range .items[*]}{.metadata.name}{'|'}{.status.phase}{'|'}{.metadata.deletionTimestamp}{'|'}{.status.containerStatuses[?(@.name=='$3')].imageID}{'\n'}{end}" \
    | awk -F'|' '$2 == "Running" && $3 == "" && $4 != "" { sub(/^.*@/, "", $4); print $1, $4 }'
}

# Prints the unique digests from serving_pods.
#
# Usage: serving_digests NAMESPACE SELECTOR CONTAINER
serving_digests() {
  serving_pods "$1" "$2" "$3" | cut -d' ' -f2 | sort -u
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
