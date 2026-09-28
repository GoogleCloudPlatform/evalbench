#!/bin/bash
# Manages the dedicated GKE node pools that containerized eval cases run on.
#
# Each eval case becomes a short-lived Kubernetes Job placed on one of these
# pools (round-robin), so cases get real isolation instead of sharing the eval
# server pod's sandbox home. The pools are tainted so only eval-case Jobs --
# which tolerate the taint -- land on them; the eval server itself keeps
# scheduling on the cluster's default pool.
#
# Usage:
#   ./worker_pools.sh create            # create all pools
#   ./worker_pools.sh delete            # delete all pools
#   ./worker_pools.sh list              # show pools and their nodes
#
# Override any of the variables below via the environment, e.g.
#   POOL_COUNT=4 MACHINE_TYPE=e2-standard-8 ./worker_pools.sh create
#   NODE_LOCATIONS=us-central1-a,us-central1-b,us-central1-f ./worker_pools.sh create
#   DRY_RUN=1 ./worker_pools.sh create   # print the commands only

set -euo pipefail

PROJECT="${PROJECT:-cloud-db-nl2sql}"
CLUSTER="${CLUSTER:-evalbench-directpath-cluster}"
ZONE="${ZONE:-us-central1-c}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-evalbench@cloud-db-nl2sql.iam.gserviceaccount.com}"

# Namespace the eval-case Jobs are created in, and the KSA that creates them.
# Override both together to dispatch from the test deployment.
NAMESPACE="${NAMESPACE:-evalbench-namespace}"
KSA="${KSA:-evalbench-ksa}"

# Pool naming: evalbench-worker-pool-1 .. -N. Keep these names in sync with
# `containerization.worker_pools` in the run config.
POOL_PREFIX="${POOL_PREFIX:-evalbench-worker-pool}"
POOL_COUNT="${POOL_COUNT:-2}"

MACHINE_TYPE="${MACHINE_TYPE:-e2-standard-8}"
DISK_SIZE="${DISK_SIZE:-200}"
DISK_TYPE="${DISK_TYPE:-pd-balanced}"

# Autoscaling bounds per pool. MIN_NODES=0 keeps an idle pool free of charge;
# the first eval case of a run then pays a node cold start.
MIN_NODES="${MIN_NODES:-0}"
MAX_NODES="${MAX_NODES:-10}"
NUM_NODES="${NUM_NODES:-0}"

# Zones to spread each pool over, comma-separated, e.g.
#   NODE_LOCATIONS=us-central1-a,us-central1-b,us-central1-f
# Empty keeps the pool in the cluster's own zone. Spreading matters because a
# single zone can stock out of a machine type (n2-standard-64 did in
# us-central1-c) and then the autoscaler cannot add a node at all. With
# several zones the bounds become pool-wide totals rather than per zone, and
# LOCATION_POLICY=ANY lets the autoscaler take capacity wherever it exists
# instead of insisting on balance.
NODE_LOCATIONS="${NODE_LOCATIONS:-}"
LOCATION_POLICY="${LOCATION_POLICY:-ANY}"
TOTAL_MIN_NODES="${TOTAL_MIN_NODES:-${MIN_NODES}}"
TOTAL_MAX_NODES="${TOTAL_MAX_NODES:-${MAX_NODES}}"

# Must match `containerization.taint_key` / `taint_value` (defaults in
# evalbench/container/config.py).
TAINT_KEY="${TAINT_KEY:-evalbench.io/dedicated}"
TAINT_VALUE="${TAINT_VALUE:-eval-worker}"

# DRY_RUN=1 prints the gcloud/kubectl commands instead of running them.
DRY_RUN="${DRY_RUN:-0}"

run() {
  if [[ "${DRY_RUN}" == "1" ]]; then
    printf '%q ' "$@"
    echo
  else
    "$@"
  fi
}

pool_name() {
  echo "${POOL_PREFIX}-$1"
}

pool_exists() {
  [[ "${DRY_RUN}" == "1" ]] && return 1
  gcloud container node-pools describe "$1" \
      --cluster="${CLUSTER}" --zone="${ZONE}" --project="${PROJECT}" \
      >/dev/null 2>&1
}

create_pool() {
  local name="$1"
  if pool_exists "${name}"; then
    echo "Node pool ${name} already exists; skipping."
    return 0
  fi

  local scaling=(--enable-autoscaling)
  if [[ -n "${NODE_LOCATIONS}" ]]; then
    scaling+=(
      --node-locations="${NODE_LOCATIONS}"
      --location-policy="${LOCATION_POLICY}"
      --total-min-nodes="${TOTAL_MIN_NODES}"
      --total-max-nodes="${TOTAL_MAX_NODES}"
    )
  else
    scaling+=(--min-nodes="${MIN_NODES}" --max-nodes="${MAX_NODES}")
  fi

  echo "Creating node pool ${name}${NODE_LOCATIONS:+ across ${NODE_LOCATIONS}}..."
  run gcloud container node-pools create "${name}" \
    --cluster="${CLUSTER}" \
    --zone="${ZONE}" \
    --project="${PROJECT}" \
    --machine-type="${MACHINE_TYPE}" \
    --disk-size="${DISK_SIZE}" \
    --disk-type="${DISK_TYPE}" \
    --num-nodes="${NUM_NODES}" \
    "${scaling[@]}" \
    --service-account="${SERVICE_ACCOUNT}" \
    --workload-metadata=GKE_METADATA \
    --node-taints="${TAINT_KEY}=${TAINT_VALUE}:NoSchedule" \
    --node-labels="evalbench.io/worker-pool=${name}" \
    --scopes=https://www.googleapis.com/auth/cloud-platform
}

delete_pool() {
  local name="$1"
  if [[ "${DRY_RUN}" != "1" ]] && ! pool_exists "${name}"; then
    echo "Node pool ${name} does not exist; skipping."
    return 0
  fi
  echo "Deleting node pool ${name}..."
  run gcloud container node-pools delete "${name}" \
    --cluster="${CLUSTER}" --zone="${ZONE}" --project="${PROJECT}" --quiet
}

cmd_create() {
  for i in $(seq 1 "${POOL_COUNT}"); do
    create_pool "$(pool_name "${i}")"
  done

  echo
  echo "Applying RBAC in ${NAMESPACE} for service account ${KSA}..."
  # The pools themselves are cluster-wide, but the dispatcher's Role is
  # namespaced: re-target it so the same script works against the test
  # namespace (NAMESPACE=evalbench-test-namespace KSA=evalbench-test-ksa).
  local rbac
  rbac="$(sed -e "s/evalbench-namespace/${NAMESPACE}/g" \
      -e "s/name: evalbench-ksa/name: ${KSA}/g" \
      "$(dirname "$0")/worker_rbac.yaml")"
  if [[ "${DRY_RUN}" == "1" ]]; then
    echo "kubectl apply -f - <<EOF"
    echo "${rbac}"
    echo "EOF"
  else
    echo "${rbac}" | kubectl apply -f -
  fi

  echo
  echo "Done. Add this to your run config:"
  echo
  echo "containerization:"
  echo "  enabled: true"
  echo "  image: us-central1-docker.pkg.dev/${PROJECT}/evalbench/eval_server:latest"
  echo "  namespace: ${NAMESPACE}"
  echo "  service_account: ${KSA}"
  echo "  worker_pools:"
  for i in $(seq 1 "${POOL_COUNT}"); do
    echo "    - $(pool_name "${i}")"
  done
}

cmd_delete() {
  for i in $(seq 1 "${POOL_COUNT}"); do
    delete_pool "$(pool_name "${i}")"
  done
}

cmd_list() {
  gcloud container node-pools list \
    --cluster="${CLUSTER}" --zone="${ZONE}" --project="${PROJECT}"
  echo
  kubectl get nodes -L cloud.google.com/gke-nodepool
}

case "${1:-}" in
  create) cmd_create ;;
  delete) cmd_delete ;;
  list)   cmd_list ;;
  *)
    echo "Usage: $0 {create|delete|list}" >&2
    exit 1
    ;;
esac
