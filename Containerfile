FROM docker.io/python:3.11-bookworm AS base-env
RUN pip install --no-cache-dir --upgrade pip

FROM base-env AS protoc-env
RUN pip install --no-cache-dir grpcio-tools
COPY ./evalbench/proto /proto
RUN mkdir proto_out
RUN python -m grpc_tools.protoc --proto_path=./proto --python_out=./proto_out --pyi_out=./proto_out --grpc_python_out=./proto_out --experimental_editions ./proto/*.proto

FROM base-env AS build-env
COPY ./requirements.txt .
RUN pip install --no-cache-dir -r ./requirements.txt

FROM gcr.io/distroless/python3-debian12
COPY --from=build-env /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --chown=nonroot:nonroot . /app
COPY --from=protoc-env --chown=nonroot:nonroot /proto_out /app/evalbench/
WORKDIR /app/evalbench
ENV PYTHONPATH=/usr/local/lib/python3.11/site-packages
CMD ["eval_server.py"]

EXPOSE 50051

# local run steps:
# gcloud auth application-default login
# export EVAL_DB_PASSWORD=xxxx
# podman build . -t evalbench
# podman run -e GOOGLE_CLOUD_PROJECT=cloud-db-nl2sql -e EVAL_DB_PASSWORD -v "$HOME/.config/gcloud/application_default_credentials.json:/tmp/application_default_credentials.json:ro" -e GOOGLE_APPLICATION_CREDENTIALS=/tmp/application_default_credentials.json -p 50051:50051 evalbench eval_server.py --localhost

# push
# gcloud auth configure-docker us-central1-docker.pkg.dev
# podman build . -t evalbench
# podman push evalbench us-central1-docker.pkg.dev/cloud-db-nl2sql/evalbench/eval_server
