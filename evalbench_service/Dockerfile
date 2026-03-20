FROM ubuntu:25.10
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
ENV DEBIAN_FRONTEND=noninteractive
ENV PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
ENV PYTHONPATH=/evalbench/evalbench/evalproto:.
ENV HOME=/home/evalbench
ENV CLOUDSDK_CONFIG=/home/evalbench/.config/gcloud
ENV GCLOUD_CONFIG=/home/evalbench/.config/gcloud

RUN \
  apt-get update -y && \
  apt-get install -y unzip git wget apt-transport-https ca-certificates gnupg curl vim jq supervisor make python3 python3-pip && \
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
  apt-get install -y nodejs

RUN curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
  && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | tee -a /etc/apt/sources.list.d/google-cloud-sdk.list && \
  apt-get update -y && \
  apt-get install -y google-cloud-cli 

RUN uv pip install --system --break-system-packages grpcio-tools

# Create home directory and add user to passwd for tools that require it
RUN mkdir -p /home/evalbench && \
    echo "evalbench:x:65532:65532:evalbench:/home/evalbench:/bin/bash" >> /etc/passwd && \
    chown -R 65532:65532 /home/evalbench

COPY . evalbench
WORKDIR evalbench
RUN uv sync

RUN ln -s /usr/bin/python3 /usr/bin/python
RUN make proto -f ./Makefile
RUN mkdir /tmp_session_files /tmp_sessions
RUN cp /evalbench/evalbench_service/supervisord.conf /evalbench/supervisord.conf
RUN chown -R 65532:65532 /evalbench /tmp /tmp_session_files /tmp_sessions /home/evalbench

CMD ["/usr/bin/supervisord", "-c", "/evalbench/supervisord.conf"]
EXPOSE 50051 3000