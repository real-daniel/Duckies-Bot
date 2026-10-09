ARG UPSTREAM_COMMIT=01c61da752aaf382678c43df495949841023c363

FROM rust:1.98-slim-trixie AS builder
ARG UPSTREAM_COMMIT
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential ca-certificates clang cmake gcc git libprotobuf-dev libssl-dev mold pkg-config protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /source
RUN git clone https://github.com/deadlock-api/deadlock-api.git . \
    && git checkout "${UPSTREAM_COMMIT}"
COPY statue-events.patch /tmp/statue-events.patch
RUN git apply --check /tmp/statue-events.patch \
    && git apply /tmp/statue-events.patch
WORKDIR /source/live-events
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    cargo build --locked --release --bin deadlock-live-events

FROM debian:trixie-slim AS runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl libc6 libssl-dev openssl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /source/target/release/deadlock-live-events /usr/local/bin/deadlock-live-events
ENTRYPOINT ["/usr/local/bin/deadlock-live-events"]
