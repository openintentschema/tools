# syntax=docker/dockerfile:1
# Every ois-validator command in one image, for projects in any language:
#   docker run --rm --network none -v "$PWD:/work" --user "$(id -u):$(id -g)" \
#     ghcr.io/openintentschema/ois-validator ois-validator bundle.json
# Base images are pinned by digest; update them by hand like every other dependency.

FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

# Build the wheel from source and export the locked runtime dependencies with their hashes.
FROM python:3.15.2-slim-trixie@sha256:c4158543ecb725b99e57d129dfd43d761d4c43e4577b5e23326aabb223cf7f4e AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv build --wheel --out-dir /dist \
 && uv export --frozen --no-dev --no-emit-project --output-file /dist/requirements.txt

FROM python:3.15.2-slim-trixie@sha256:c4158543ecb725b99e57d129dfd43d761d4c43e4577b5e23326aabb223cf7f4e
LABEL org.opencontainers.image.title="ois-validator" \
      org.opencontainers.image.description="Reference validator of the Open Intent Schema (OIS) and the commands that write OIS records" \
      org.opencontainers.image.source="https://github.com/openintentschema/validator" \
      org.opencontainers.image.documentation="https://openintentschema.io/develop/validator/" \
      org.opencontainers.image.licenses="Apache-2.0"

# git is read by ois-junit and ois-record. The mounted checkout belongs to the host user, so git
# must trust it whatever user the container runs as.
RUN apt-get update \
 && apt-get install --yes --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/* \
 && git config --system --add safe.directory '*'

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 PIP_ROOT_USER_ACTION=ignore
RUN --mount=type=bind,from=build,source=/dist,target=/dist \
    pip install --require-hashes --requirement /dist/requirements.txt \
 && pip install --no-deps /dist/*.whl

# Any user may run it (docker run --user), so HOME must not depend on one.
ENV HOME=/tmp
USER 65532:65532
WORKDIR /work
CMD ["ois-validator", "--help"]
