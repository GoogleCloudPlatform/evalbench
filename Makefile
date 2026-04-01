#!/usr/bin/make -f

default:deploy
.PHONY: default

CONTAINER_ENGINE ?= docker

# Check if Podman is preferred or if Docker is not available
# This can be overridden by setting CONTAINER_ENGINE=podman when running make
ifeq ($(CONTAINER_ENGINE), docker)
  # Check if docker command exists, otherwise default to podman
  ifeq ($(shell command -v docker 2>/dev/null),)
    CONTAINER_ENGINE = podman
    $(info Docker not found, defaulting to Podman.)
  endif
endif

# If CONTAINER_ENGINE is explicitly set to podman, use it
ifeq ($(CONTAINER_ENGINE), podman)
  # Ensure podman command exists
  $(info Using Podman as container engine.)
else
  $(info Using Docker as container engine.)
endif

SHELL := /bin/bash
TYPE != awk -F '=' '/GOOGLE_ROLE/ { print $$2 }' /etc/lsb-release

build:
	$(CONTAINER_ENGINE) build  -t evalbench -f evalbench_service/Dockerfile .

build-test:
	$(CONTAINER_ENGINE) build  -t evalbench-test -f evalbench_service/Dockerfile .

container:
	$(CONTAINER_ENGINE) stop evalbench_server || true
	$(CONTAINER_ENGINE) rm evalbench_server || true
	$(CONTAINER_ENGINE) run --rm --name=evalbench_server \
		$(if $(filter podman,$(CONTAINER_ENGINE)),--sysctl net.ipv6.conf.all.disable_ipv6=1) \
		$(if $(filter docker,$(CONTAINER_ENGINE)),--net=host) \
		-v ~/.config/gcloud:/root/.config/gcloud \
		-e GOOGLE_CLOUD_PROJECT=cloud-db-nl2sql \
		-e MESOP_XSRF_CHECK=false \
		--cap-add=SYS_PTRACE	\
		-p 3000:3000 \
		-p 50051:50051 \
		-e TYPE=$(TYPE) evalbench:latest

shell:
	$(CONTAINER_ENGINE) stop evalbench_server || true
	$(CONTAINER_ENGINE) rm evalbench_server || true
	$(CONTAINER_ENGINE) run -ti --rm --name=evalbench_server \
		$(if $(filter podman,$(CONTAINER_ENGINE)),--sysctl net.ipv6.conf.all.disable_ipv6=1) \
		$(if $(filter docker,$(CONTAINER_ENGINE)),--net=host) \
		--cap-add=SYS_PTRACE \
		-v ~/.config/gcloud:/root/.config/gcloud \
		-v $(PWD)/requirements.txt:/evalbench/requirements.txt \
		-v $(PWD)/evalbench:/evalbench/evalbench \
		-v $(PWD)/viewer:/evalbench/viewer \
		-p 3000:3000 \
		-p 50051:50051 \
		-e GOOGLE_CLOUD_PROJECT=cloud-db-nl2sql \
		-e CLOUD_RUN=True \
		-e TYPE=$(TYPE) evalbench:latest bash

push-test:
	$(CONTAINER_ENGINE) image tag evalbench:latest us-central1-docker.pkg.dev/cloud-db-nl2sql/evalbench/eval_server:test
	$(CONTAINER_ENGINE) push us-central1-docker.pkg.dev/cloud-db-nl2sql/evalbench/eval_server:test

push:
	$(CONTAINER_ENGINE) image tag evalbench:latest us-central1-docker.pkg.dev/cloud-db-nl2sql/evalbench/eval_server:latest
	$(CONTAINER_ENGINE) push us-central1-docker.pkg.dev/cloud-db-nl2sql/evalbench/eval_server:latest

push-corprun:
	$(CONTAINER_ENGINE) image tag evalbench:latest us-central1-docker.pkg.dev/evalbench-dev/cr-images/eval_server:latest
	$(CONTAINER_ENGINE) push us-central1-docker.pkg.dev/evalbench-dev/cr-images/eval_server:latest

deploy:
	gcloud container clusters get-credentials evalbench-directpath-cluster --zone us-central1-c --project cloud-db-nl2sql
	kubectl apply -f evalbench_service/k8s/namespace.yaml
	kubectl apply -f evalbench_service/k8s/pvc.yaml
	kubectl apply -f evalbench_service/k8s/ksa.yaml
	kubectl apply -f evalbench_service/k8s/service.yaml
	kubectl apply -f evalbench_service/k8s/evalbench.yaml
	kubectl apply -f evalbench_service/k8s/hpa.yaml
	kubectl apply -f evalbench_service/k8s/vertical-autoscale.yaml

deploy-test:
	gcloud container clusters get-credentials evalbench-directpath-cluster --zone us-central1-c --project cloud-db-nl2sql
	kubectl apply -f evalbench_service/k8s/namespace-test.yaml
	kubectl apply -f evalbench_service/k8s/ksa-test.yaml
	kubectl apply -f evalbench_service/k8s/service-test.yaml
	kubectl apply -f evalbench_service/k8s/evalbench-test.yaml
	kubectl apply -f evalbench_service/k8s/vertical-autoscale-test.yaml

deploy-corprun:
	gcloud run deploy evalbench \
		--project=evalbench-dev \
		--region=us-central1 \
		--image=us-central1-docker.pkg.dev/evalbench-dev/cr-images/eval_server:latest \
		--port=3000 \
		--memory=2Gi \
		--service-account=crsvc-evalbench@evalbench-dev.iam.gserviceaccount.com \
		--set-env-vars CLOUD_RUN=True,GOOGLE_CLOUD_PROJECT=evalbench-dev,MESOP_XSRF_CHECK=false \
		--ingress=internal-and-cloud-load-balancing \
		--network=cr-infra-vpc-network \
		--subnet=cr-infra-subnetwork \
		--vpc-egress=all-traffic \
		--add-volume=name=session-files,type=cloud-storage,bucket=evalbench-sessions-cloud-db-nl2sql \
		--add-volume-mount=volume=session-files,mount-path=/tmp_session_files

undeploy:
	gcloud container clusters get-credentials evalbench-directpath-cluster --zone us-central1-c --project cloud-db-nl2sql
	kubectl delete -f evalbench_service/k8s/evalbench.yaml
	kubectl delete -f evalbench_service/k8s/service.yaml

undeploy-test:
	gcloud container clusters get-credentials evalbench-directpath-cluster --zone us-central1-c --project cloud-db-nl2sql
	kubectl delete -f evalbench_service/k8s/namespace-test.yaml
	kubectl delete -f evalbench_service/k8s/ksa-test.yaml
	kubectl delete -f evalbench_service/k8s/service-test.yaml
	kubectl delete -f evalbench_service/k8s/evalbench-test.yaml

redeploy:
	gcloud container clusters get-credentials evalbench-directpath-cluster --zone us-central1-c --project cloud-db-nl2sql
	kubectl rollout restart deployment/evalbench-eval-server-deploy -n evalbench-namespace

redeploy-test:
	gcloud container clusters get-credentials evalbench-directpath-cluster --zone us-central1-c --project cloud-db-nl2sql
	kubectl rollout restart deployment/evalbench-test-eval-server-deploy -n evalbench-test-namespace

pod-shell:
	gcloud container clusters get-credentials evalbench-directpath-cluster --zone us-central1-c --project cloud-db-nl2sql
	kubectl exec -it deployment/evalbench-eval-server-deploy -n evalbench-namespace -c evalbench-eval -- /bin/bash

pod-shell-test:
	gcloud container clusters get-credentials evalbench-directpath-cluster --zone us-central1-c --project cloud-db-nl2sql
	kubectl exec -it deployment/evalbench-test-eval-server-deploy -n evalbench-test-namespace -c evalbench-test-eval -- /bin/bash

proto:
	@python -m grpc_tools.protoc \
		--proto_path=evalbench/evalproto \
		--python_out=evalbench/evalproto \
		--pyi_out=evalbench/evalproto \
		--grpc_python_out=evalbench/evalproto \
		--experimental_editions evalbench/evalproto/*.proto

clean:
	@rm -fr evalbench/evalproto/*.py
	@rm -fr evalbench/evalproto/*.pyi

test:
	@nox

style:
	@pycodestyle --exclude=evalbench/lib,evalbench/lib64,evalproto evalbench

run:
	@./run_service.sh

binary:
	uv pip install pyinstaller
	uv run pyinstaller pyinstaller.spec
