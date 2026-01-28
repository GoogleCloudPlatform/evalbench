#!/usr/bin/make -f

default:apply
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
	$(CONTAINER_ENGINE) run --rm --net=host --name=evalbench_container \
		-v ~/.config/gcloud:/root/.config/gcloud \
		-e GOOGLE_CLOUD_PROJECT=cloud-db-nl2sql \
		--cap-add=SYS_PTRACE	\
		-e OPTION=--localhost \
		-e TYPE=$(TYPE) evalbench:latest /evalbench/run_service.sh 

shell:
	$(CONTAINER_ENGINE) run -ti --rm --net=host --name=evalbench_container \
		--cap-add=SYS_PTRACE	\
		-v ~/.config/gcloud:/root/.config/gcloud \
		-v ~/.gitconfig:/root/.gitconfig \
		-v ~/.gitcookies:/root/.gitcookies \
		-v $(PWD)/requirements.txt:/evalbench/requirements.txt \
		-v $(PWD)/evalbench:/evalbench/evalbench \
		-e GOOGLE_CLOUD_PROJECT=cloud-db-nl2sql \
		-e OPTION=--localhost \
		-e TYPE=$(TYPE) evalbench:latest bash

push-test:
	$(CONTAINER_ENGINE) image tag evalbench:latest us-central1-docker.pkg.dev/cloud-db-nl2sql/evalbench/eval_server:test
	$(CONTAINER_ENGINE) push us-central1-docker.pkg.dev/cloud-db-nl2sql/evalbench/eval_server:test

push:
	$(CONTAINER_ENGINE) image tag evalbench:latest us-central1-docker.pkg.dev/cloud-db-nl2sql/evalbench/eval_server:latest
	$(CONTAINER_ENGINE) push us-central1-docker.pkg.dev/cloud-db-nl2sql/evalbench/eval_server:latest

deploy:
	gcloud container clusters get-credentials evalbench-directpath-cluster --zone us-central1-c --project cloud-db-nl2sql
	kubectl apply -f evalbench_service/k8s/namespace.yaml
	kubectl apply -f evalbench_service/k8s/ksa.yaml
	kubectl apply -f evalbench_service/k8s/service.yaml
	kubectl apply -f evalbench_service/k8s/evalbench.yaml
	kubectl apply -f evalbench_service/k8s/vertical-autoscale.yaml

deploy-test:
	gcloud container clusters get-credentials evalbench-directpath-cluster --zone us-central1-c --project cloud-db-nl2sql
	kubectl apply -f evalbench_service/k8s/namespace-test.yaml
	kubectl apply -f evalbench_service/k8s/ksa-test.yaml
	kubectl apply -f evalbench_service/k8s/service-test.yaml
	kubectl apply -f evalbench_service/k8s/evalbench-test.yaml
	kubectl apply -f evalbench_service/k8s/vertical-autoscale-test.yaml

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
	@pycodestyle --exclude=evalbench/lib,evalbench/lib64 --max-line-length=120 evalbench

run:
	@./run_service.sh
