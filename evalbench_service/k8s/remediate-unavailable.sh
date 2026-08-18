#!/bin/bash
#
# Remediation for CI failures of the form:
#
#   StatusCode.UNAVAILABLE
#   "ring hash cannot find a connected endpoint; first failure: UNAVAILABLE:
#    ipv6:[fd20:...]:50051: Handshake read failed (Socket closed)"
#
# Background
# ----------
# The mesh backend service uses RING_HASH + HEADER_FIELD session affinity on
# the client-supplied `client-rpc-id` header. That affinity is deliberate:
# evalbench sessions are stateful and live in a single pod's /tmp_sessions, so
# a client must stay pinned to one backend for the whole run.
#
# The cost of that design is that a client CANNOT fail over. If the endpoint
# its header hashes to is not connectable, ring_hash reports "cannot find a
# connected endpoint" instead of picking another backend. So any single
# transient connection failure is fatal to the whole run.
#
# Three things were making those transient failures common:
#   1. Pods were being evicted for ephemeral-storage (see evalbench.yaml).
#   2. The HPA flapped between 1 and 2 replicas every ~15 min, tearing down
#      backends that were still serving.
#   3. connectionDraining was 0s, so endpoints were cut instantly with no
#      drain window.
#
# (1) and (2) are fixed in evalbench.yaml / hpa.yaml. This script covers the
# GCP-side resources that kubectl cannot manage, plus one-off cleanup.
#
# Run with: bash remediate-unavailable.sh
set -euo pipefail

PROJECT=cloud-db-nl2sql
ZONE=us-central1-c
CLUSTER=evalbench-directpath-cluster
NAMESPACE=evalbench-namespace

echo "==> 1/4 Give the backend service a connection-draining window."
# Was 0s. With 0 there is no grace period at all between a pod being removed
# from the NEG and its connections being cut, which is precisely when a
# ring-hash client sees "Socket closed" mid-handshake. Pairs with the 15s
# preStop hook and terminationGracePeriodSeconds: 60 in evalbench.yaml.
gcloud compute backend-services update evalbench-directpath-bs \
  --global \
  --connection-draining-timeout=60 \
  --project="${PROJECT}"

echo "==> 2/4 Clear evicted pods left behind by the disk-pressure incidents."
# These sit in Init:ContainerStatusUnknown indefinitely. They are not in the
# NEG so they do not serve traffic, but they hold pod IPs and make the real
# state of the deployment hard to read.
kubectl delete pod -n "${NAMESPACE}" \
  --field-selector 'status.phase==Failed' \
  --ignore-not-found

echo "==> 3/4 Report current node disk headroom."
# The node pool is 100GB/node while a single eval pod was observed using 50GB
# of ephemeral storage. evalbench.yaml now redirects scratch to the 1000Gi PVC
# and caps ephemeral-storage, which should be sufficient. Bumping the disk is
# defence in depth -- see step 4 if this still looks tight.
kubectl get nodes -o custom-columns=\
'NAME:.metadata.name,ALLOCATABLE_EPHEMERAL:.status.allocatable.ephemeral-storage'

echo "==> 4/4 OPTIONAL: enlarge the node pool boot disk (recreates nodes)."
# Not run automatically: this recreates every node in the pool and will kill
# any eval in flight. Run it during a quiet window if disk stays tight.
cat <<EOF

  gcloud container node-pools update workers \\
    --cluster=${CLUSTER} --zone=${ZONE} --project=${PROJECT} \\
    --disk-size=500GB

  # GKE cannot resize a node pool's boot disk in place on all versions. If the
  # command above is rejected, create a replacement pool and drain the old one:
  #
  #   gcloud container node-pools create workers-v2 \\
  #     --cluster=${CLUSTER} --zone=${ZONE} --project=${PROJECT} \\
  #     --machine-type=n2-standard-64 --disk-size=500GB \\
  #     --enable-autoscaling --min-nodes=1 --total-max-nodes=3
  #   kubectl drain <old-node> --ignore-daemonsets --delete-emptydir-data
  #   gcloud container node-pools delete workers \\
  #     --cluster=${CLUSTER} --zone=${ZONE} --project=${PROJECT}

EOF

echo "Done. Apply the manifest changes with:"
echo "  kubectl apply -f evalbench.yaml -f hpa.yaml"
