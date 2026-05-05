FROM ubuntu:25.10
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV DEBIAN_FRONTEND=noninteractive
ENV PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
ENV PYTHONPATH=/evalbench/evalbench/evalproto:.
ENV HOME=/root
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

RUN uv pip install --system --break-system-packages grpcio-tools

COPY . evalbench
WORKDIR evalbench
RUN uv pip install --system --break-system-packages -r requirements.txt
RUN uv pip install --system --break-system-packages ./viewer
RUN uv pip install --system --break-system-packages .

# Create a non-root user for Claude Code. It refuses
# --dangerously-skip-permissions when running as root.
RUN useradd -m -s /bin/bash claudeuser

RUN ln -s /usr/bin/python3 /usr/bin/python
RUN make proto -f ./Makefile
RUN mkdir -p /tmp_session_files /tmp_sessions /evalbench/results
RUN cp /evalbench/evalbench_service/supervisord_evalbench.conf /evalbench/supervisord_evalbench.conf
RUN cp /evalbench/evalbench_service/supervisord_cloudrun.conf /evalbench/supervisord_cloudrun.conf
RUN cp /evalbench/evalbench_service/supervisord_combined.conf /evalbench/supervisord_combined.conf
RUN cp /evalbench/evalbench_service/entrypoint.sh /evalbench/entrypoint.sh && chmod +x /evalbench/entrypoint.sh
RUN chmod +x /evalbench/evalbench/run.sh
RUN chmod +x /evalbench/viewer/run_frontend.sh

WORKDIR /evalbench
CMD ["/evalbench/entrypoint.sh"]
EXPOSE 50051 3000
