FROM ubuntu:25.10
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV DEBIAN_FRONTEND=noninteractive
ENV PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
ENV PYTHONPATH=/evalbench/evalbench/evalproto:.
ENV HOME=/root
ENV VIRTUAL_ENV=/evalbench/.venv
ENV PATH=/evalbench/.venv/bin:$PATH
ENV CLOUDSDK_CONFIG=/root/.config/gcloud
ENV GCLOUD_CONFIG=/root/.config/gcloud

RUN \
  apt-get update -y && \
  apt-get install -y unzip git wget apt-transport-https ca-certificates gnupg curl vim jq supervisor make python3 python3-pip sudo && \
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
  apt-get install -y nodejs && \
  npm install -g @dataform/cli

RUN curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
  && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | tee -a /etc/apt/sources.list.d/google-cloud-sdk.list && \
  apt-get update -y && \
  apt-get install -y google-cloud-cli

# agy is not installed at build time; the harness installs it per session at
# runtime (AgyCliGenerator._ensure_agy_installed). This env var has no install
# to pair with here on purpose: it propagates into the runtime agy subprocess
# and disables its background self-update so a binary can't swap versions
# mid-eval.
ENV AGY_CLI_DISABLE_AUTO_UPDATE=true

WORKDIR /evalbench
COPY pyproject.toml uv.lock ./
COPY viewer/pyproject.toml viewer/
RUN uv sync --frozen --all-packages
COPY . .
RUN uv sync --frozen --all-packages

# Create a non-root user for Claude Code. It refuses
# --dangerously-skip-permissions when running as root.
RUN useradd -m -s /bin/bash claudeuser

RUN ln -s /usr/bin/python3 /usr/bin/python
RUN uv run make proto -f ./Makefile
# /tmp_sessions holds the agents' per-session sandboxes (PVC in GKE);
# /var/lib/evalbench/sessions holds the evaluator-only resources (configs,
# scenarios, rubrics, validators) that agents must not be able to reach from
# their sandbox. Keep them on separate trees (see util/sessionmgr.py).
RUN mkdir -p /tmp_session_files /tmp_sessions /var/lib/evalbench/sessions /evalbench/results
RUN cp /evalbench/evalbench_service/supervisord_evalbench.conf /evalbench/supervisord_evalbench.conf
RUN cp /evalbench/evalbench_service/supervisord_cloudrun.conf /evalbench/supervisord_cloudrun.conf
RUN cp /evalbench/evalbench_service/supervisord_combined.conf /evalbench/supervisord_combined.conf
RUN cp /evalbench/evalbench_service/entrypoint.sh /evalbench/entrypoint.sh && chmod +x /evalbench/entrypoint.sh
RUN chmod +x /evalbench/evalbench/run.sh
RUN chmod +x /evalbench/viewer/run_frontend.sh

WORKDIR /evalbench
CMD ["/evalbench/entrypoint.sh"]
EXPOSE 50051 3000
