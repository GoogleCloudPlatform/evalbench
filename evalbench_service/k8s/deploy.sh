#!/bin/bash
gcloud compute networks create evalbench-directpath --enable-ula-internal-ipv6  \
  --subnet-mode=custom --project=cloud-db-nl2sql
gcloud compute networks subnets create evalbench-directpath-subnet \
  --network evalbench-directpath \
  --range 10.10.10.0/24 \
  --ipv6-access-type=INTERNAL \
  --stack-type=IPV4_IPV6 \
  --region us-central1 --project=cloud-db-nl2sql
gcloud container clusters create evalbench-directpath-cluster \
  --network=evalbench-directpath \
  --subnetwork=evalbench-directpath-subnet \
  --stack-type=ipv4-ipv6 \
  --enable-ip-alias \
  --enable-dataplane-v2 \
  --tags=allow-health-checks \
  --release-channel=stable \
  --num-nodes=1 \
  --enable-autoscaling --min-nodes=1 --total-max-nodes=3 \
  --machine-type=n2-standard-64 \
  --disk-size=500GB \
  --zone=us-central1-c \
  --enable-alts \
  --workload-pool cloud-db-nl2sql.svc.id.goog \
  --service-account=evalbench@cloud-db-nl2sql.iam.gserviceaccount.com \
  --project=cloud-db-nl2sql
gcloud container clusters get-credentials evalbench-directpath-cluster \
    --zone=us-central1-c --project=cloud-db-nl2sql
kubectl apply -f cloud/databases/nl2sql/eval/sqlgen/service/configs/gke_deployment.yaml
gcloud compute health-checks create tcp evalbench-directpath-health-check \
  --global \
  --enable-logging \
  --use-serving-port --project=cloud-db-nl2sql
gcloud compute firewall-rules create gke-evalbench-directpath-firewall \
  --network=evalbench-directpath \
  --action=allow \
  --direction=ingress \
  --source-ranges="2001:4860:8040::/42,2600:2d00:1:b029::/64" \
  --target-tags=allow-health-checks \
  --rules=tcp:50051 --project=cloud-db-nl2sql
gcloud compute backend-services create evalbench-directpath-bs \
  --ip-address-selection-policy IPV6_ONLY \
  --load-balancing-scheme=INTERNAL_SELF_MANAGED \
  --protocol=GRPC \
  --global \
  --health-checks=evalbench-directpath-health-check \
  --session-affinity=HEADER_FIELD \
  --custom-request-header='client-rpc-id' \
  --locality-lb-policy=RING_HASH \
  --connection-draining-timeout=60 \
  --project=cloud-db-nl2sql
gcloud compute backend-services add-backend evalbench-directpath-bs \
  --network-endpoint-group evalbench-directpath-neg \
  --network-endpoint-group-zone us-central1-c \
  --balancing-mode RATE \
  --max-rate-per-endpoint 100 \
  --global --project=cloud-db-nl2sql
gcloud network-services meshes import direct-google-access-evalbench-mesh \
    --source=./mesh.yaml \
    --location=global --project=cloud-db-nl2sql
gcloud network-services grpc-routes import evalbench-grpc-route \
    --source=./evalbench_service/k8s/grpc_route.yaml \
    --location=global --project=cloud-db-nl2sql