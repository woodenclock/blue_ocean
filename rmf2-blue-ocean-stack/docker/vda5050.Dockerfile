# syntax=docker/dockerfile:1
FROM ros:jazzy-ros-base

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-colcon-common-extensions \
        python3-rosdep \
        ros-jazzy-rmw-cyclonedds-cpp \
        cmake \
        clang \
        libssl-dev \
        curl \
        ca-certificates \
  && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV VDA_WS=/vda_ws
ENV VDA5050_PY=vda5050_core_gametl/vda5050_core_py
WORKDIR ${VDA_WS}/src

# --- VDA5050 LIBRARY (C++ + python core) --------------------------------------
# Copy ONLY the library sources here, so editing the blue-ocean app (further
# down) does NOT invalidate this layer or the colcon C++ build below — Docker
# reuses the cached _core.so unless these inputs actually change. This is the
# "only recompile the C++ when the VDA itself changes" behaviour.
#
#   vda5050_core/        : C++ library; examples + tests are built (BUILD_EXAMPLES
#                          / BUILD_TESTING default ON), so the whole dir is needed.
#   vda5050_core_py/...  : pybind11 extension + python package. Needs src/ (the
#                          bindings), test/ (pytest tests registered by CMake),
#                          the module dir, and the colcon/uv packaging files.
#
# Deliberately EXCLUDES vda5050_core_py/demo-june-blue-ocean/ — the app is copied
# below so its edits never touch these layers. (Also skips examples/, README,
# build artifacts: not inputs to the build.)
COPY src/vda5050_core_gametl/vda5050_core/                   vda5050_core_gametl/vda5050_core/
COPY src/vda5050_core_gametl/vda5050_core_py/CMakeLists.txt  ${VDA5050_PY}/CMakeLists.txt
COPY src/vda5050_core_gametl/vda5050_core_py/package.xml     ${VDA5050_PY}/package.xml
COPY src/vda5050_core_gametl/vda5050_core_py/pyproject.toml  ${VDA5050_PY}/pyproject.toml
COPY src/vda5050_core_gametl/vda5050_core_py/uv.lock         ${VDA5050_PY}/uv.lock
COPY src/vda5050_core_gametl/vda5050_core_py/src/            ${VDA5050_PY}/src/
COPY src/vda5050_core_gametl/vda5050_core_py/test/           ${VDA5050_PY}/test/
COPY src/vda5050_core_gametl/vda5050_core_py/vda5050_core_py/ ${VDA5050_PY}/vda5050_core_py/

WORKDIR ${VDA_WS}
RUN apt-get update \
  && rosdep install --from-paths src --ignore-src --rosdistro=$ROS_DISTRO -y \
  && rm -rf /var/lib/apt/lists/*

ENV MAKEFLAGS=-j2
ENV CMAKE_BUILD_PARALLEL_LEVEL=2
RUN . /opt/ros/$ROS_DISTRO/setup.sh \
  && nice -n 19 ionice -c 3 colcon build --symlink-install

# Ensure the colcon-built extension module is present for uv packaging (.so only;
# pyi/__init__ already live in the source tree; symlink-install shares paths).
RUN . /vda_ws/install/setup.sh \
  && VDA5050_PKG="$(python3 -c "import pathlib, vda5050_core_py; print(pathlib.Path(vda5050_core_py.__file__).parent)")" \
  && DEST=${VDA_WS}/src/${VDA5050_PY}/vda5050_core_py \
  && for so in "${VDA5050_PKG}"/_core.cpython-*.so; do \
       [ -e "$so" ] || continue; \
       [ "$so" -ef "$DEST/$(basename "$so")" ] || cp -a "$so" "$DEST/"; \
     done

# --- BLUE-OCEAN APP (fast-changing) -------------------------------------------
# Everything below re-runs on an app edit; the C++ build above stays cached.
# map_transform is a runtime import path only (not a C++ build input), so it sits
# here to keep the library layers stable when the maps/ tree changes.
COPY maps/map_transform/ /opt/gametl/maps/map_transform/
ENV PYTHONPATH=/opt/gametl/maps:${PYTHONPATH}

# Absolute dest: WORKDIR is ${VDA_WS} here (set before rosdep), so a relative
# path would land under /vda_ws, not /vda_ws/src.
ENV APP_DIR=${VDA_WS}/src/${VDA5050_PY}/demo-june-blue-ocean

# Dependency manifests ONLY. This layer + the sync below bust only when the app's
# deps or the vda5050-core-py library change — NOT on app source edits. The library
# lives at ".." (copied above), so its wheel builds here and is cached.
COPY src/vda5050_core_gametl/vda5050_core_py/demo-june-blue-ocean/pyproject.toml \
     src/vda5050_core_gametl/vda5050_core_py/demo-june-blue-ocean/uv.lock \
     ${APP_DIR}/
WORKDIR ${APP_DIR}
# Persistent uv cache: downloaded/built wheels survive across builds (no more
# re-downloading numpy/opencv/etc). --no-install-project installs deps + the
# vda5050-core-py path dep, but not the app itself.
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project

# App source (fast-changing) — copied AFTER deps are cached, so edits here reuse
# the cached deps layer and never rebuild the library.
COPY src/vda5050_core_gametl/vda5050_core_py/demo-june-blue-ocean/ ${APP_DIR}/
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen

COPY docker/vda5050-master-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ARG VDA5050_PORT=8000
ENV VDA5050_PORT=${VDA5050_PORT}
EXPOSE ${VDA5050_PORT}
ENTRYPOINT ["/entrypoint.sh"]
