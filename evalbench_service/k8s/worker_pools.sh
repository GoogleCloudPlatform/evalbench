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

# Must match `containerization.taint_key` / `taint_value` (defaults in
# evalbench/container/config.py).
TAINT_KEY="${TAINT_KEY:-evalbench.io/dedicated}"
TAINT_VALUE="${TAINT_VALUE:-eval-worker}"

pool_name() {
  echo "${POOL_PREFIX}-$1"
}

create_pool() {
  local name="$1"
  if gcloud container node-pools describe "${name}" \
      --cluster="${CLUSTER}" --zone="${ZONE}" --project="${PROJECT}" \
      >/dev/null 2>&1; then
    echo "Node pool ${name} already exists; skipping."
    return 0
  fi

  echo "Creating node pool ${name}..."
  gcloud container node-pools create "${name}" \
    --cluster="${CLUSTER}" \
    --zone="${ZONE}" \
    --project="${PROJECT}" \
    --machine-type="${MACHINE_TYPE}" \
    --disk-size="${DISK_SIZE}" \
    --disk-type="${DISK_TYPE}" \
    --num-nodes="${NUM_NODES}" \
    --enable-autoscaling \
    --min-nodes="${MIN_NODES}" \
    --max-nodes="${MAX_NODES}" \
    --service-account="${SERVICE_ACCOUNT}" \
    --workload-metadata=GKE_METADATA \
    --node-taints="${TAINT_KEY}=${TAINT_VALUE}:NoSchedule" \
    --node-labels="evalbench.io/worker-pool=${name}" \
    --scopes=https://www.googleapis.com/auth/cloud-platform
}

delete_pool() {
  local name="$1"
  if ! gcloud container node-pools describe "${name}" \
      --cluster="${CLUSTER}" --zone="${ZONE}" --project="${PROJECT}" \
      >/dev/null 2>&1; then
    echo "Node pool ${name} does not exist; skipping."
    return 0
  fi
  echo "Deleting node pool ${name}..."
  gcloud container node-pools delete "${name}" \
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
  sed -e "s/evalbench-namespace/${NAMESPACE}/g" \
      -e "s/name: evalbench-ksa/name: ${KSA}/g" \
      "$(dirname "$0")/worker_rbac.yaml" | kubectl apply -f -

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
