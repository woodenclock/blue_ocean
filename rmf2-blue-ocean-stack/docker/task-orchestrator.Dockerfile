# Base with cargo-chef installed (shared by planner + builder -> cached)
FROM rust:1-bookworm AS chef
RUN cargo install cargo-chef --locked
WORKDIR /build

# Planner: derive the dependency recipe from the manifests
FROM chef AS planner
COPY src/rmf2_task_orchestrator_gametl/ .
RUN cargo chef prepare --recipe-path recipe.json

# Builder: cook deps (cached layer), THEN build our source
FROM chef AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        pkg-config \
        libssl-dev \
  && rm -rf /var/lib/apt/lists/*

# Compile only dependencies from the recipe. This layer is cached and only
# rebuilds when Cargo.toml / Cargo.lock change -- not on source edits.
COPY --from=planner /build/recipe.json recipe.json
RUN cargo chef cook --release --recipe-path recipe.json

COPY src/rmf2_task_orchestrator_gametl/ .
RUN cargo build --release

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /build/target/release/rmf2_task_orchestrator ./rmf2_task_orchestrator
COPY config/task_orchestrator.toml ./config.toml

EXPOSE 2727
CMD ["./rmf2_task_orchestrator"]
