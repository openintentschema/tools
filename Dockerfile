# syntax=docker/dockerfile:1
# All ois commands, Sigstore verification included. Base images pinned by digest.

FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

# The wheel and the locked runtime dependencies with their hashes.
FROM python:3.15.2-slim-trixie@sha256:c4158543ecb725b99e57d129dfd43d761d4c43e4577b5e23326aabb223cf7f4e AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN uv build --wheel --out-dir /dist \
 && uv export --frozen --no-dev --extra sigstore --no-emit-project --output-file /dist/requirements.txt

FROM python:3.15.2-slim-trixie@sha256:c4158543ecb725b99e57d129dfd43d761d4c43e4577b5e23326aabb223cf7f4e
LABEL org.opencontainers.image.title="ois-tools" \
      org.opencontainers.image.description="Reference tools for the Open Intent Schema (OIS): validate, record and attest what was required, built and proven" \
      org.opencontainers.image.source="https://github.com/openintentschema/tools" \
      org.opencontainers.image.documentation="https://openintentschema.io/develop/tools/" \
      org.opencontainers.image.licenses="Apache-2.0"

# git for ois junit, ois cucumber and ois record, trusting checkouts of any owner.
RUN apt-get update \
 && apt-get install --yes --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/* \
 && git config --system --add safe.directory '*'

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 PIP_ROOT_USER_ACTION=ignore
RUN --mount=type=bind,from=build,source=/dist,target=/dist \
    pip install --require-hashes --requirement /dist/requirements.txt \
 && pip install --no-deps /dist/*.whl

# HOME for any --user.
ENV HOME=/tmp
USER 65532:65532
WORKDIR /work
# Arguments after the image name go to ois.
ENTRYPOINT ["ois"]
CMD ["--help"]
