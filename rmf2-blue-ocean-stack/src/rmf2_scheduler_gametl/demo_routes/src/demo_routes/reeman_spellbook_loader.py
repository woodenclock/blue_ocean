# Copyright 2024-2026 ROS Industrial Consortium Asia Pacific
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Bootstrap reeman_bridge/spellbook for scheduler demo routes."""

from __future__ import annotations

import importlib
import os
import shutil
import sys
from pathlib import Path
from types import ModuleType
from urllib.parse import urlparse

_IMPORT_MODULES = (
    "credentials._config",
    "credentials",
    "api_client",
    "cancel_move",
    "navigate",
    "ws_helper",
)
_SHARED_MODULES = (
    "credentials._config",
    "credentials",
    "api_client",
    "cancel_move",
    "navigate",
    "ws_helper",
    "pick_rack",
)
_cached_endpoint: str | None = None


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "config" / "config.env").is_file():
            return parent
    vda = os.environ.get("VDA_DEMO_ROOT", "").strip()
    if vda:
        return Path(vda)
    raise RuntimeError(
        "Cannot locate repo root (config/config.env) — set VDA_DEMO_ROOT or "
        "REEMAN_SPELLBOOK_DIR."
    )


def spellbook_dir() -> Path:
    env = os.environ.get("REEMAN_SPELLBOOK_DIR", "").strip()
    if env:
        root = Path(env).expanduser()
        if not root.is_absolute():
            root = _repo_root() / root
        return root.resolve()
    return (
        _repo_root()
        / "src/vda5050_core_gametl/vda5050_core_py/demo-june-blue-ocean"
        / "reeman"
        / "reeman_bridge"
        / "spellbook"
    ).resolve()


def _ensure_constants_yml(cmd_dir: Path) -> None:
    yml = cmd_dir / "credentials" / "CONSTANTS.yml"
    example = cmd_dir / "credentials" / "CONSTANTS.example.yml"
    if not yml.is_file() and example.is_file():
        shutil.copy(example, yml)
    if not yml.is_file():
        raise RuntimeError(
            f"Spellbook robot config not found: {yml} "
            f"(copy CONSTANTS.example.yml to CONSTANTS.yml)."
        )


def _endpoint_host_port(endpoint: str) -> str:
    parsed = urlparse(endpoint if "://" in endpoint else f"http://{endpoint}")
    if not parsed.hostname:
        return endpoint
    if parsed.port:
        return f"{parsed.hostname}:{parsed.port}"
    return parsed.hostname


def _activate_spellbook_path(cmd_dir: Path) -> None:
    demo_root = cmd_dir.parent.parent
    for path in (str(cmd_dir), str(demo_root)):
        while path in sys.path:
            sys.path.remove(path)
    sys.path.insert(0, str(demo_root))
    sys.path.insert(0, str(cmd_dir))


def _purge_spellbook_modules() -> None:
    # AutoXing and Reeman spellbooks share bare module names. Evict them before
    # import so this loader cannot reuse the other robot's credentials/api_client.
    for name in _SHARED_MODULES:
        sys.modules.pop(name, None)


def load_navigate(endpoint: str) -> ModuleType:
    """Point spellbook credentials at ``endpoint`` and return ``navigate`` module."""
    global _cached_endpoint

    cmd_dir = spellbook_dir()
    if not cmd_dir.is_dir():
        raise RuntimeError(f"Spellbook directory not found: {cmd_dir}")

    _activate_spellbook_path(cmd_dir)

    endpoint = endpoint.rstrip("/")
    os.environ["REEMAN_BASE_URL"] = endpoint
    os.environ["REEMAN_ROBOT_IP"] = _endpoint_host_port(endpoint)
    _ensure_constants_yml(cmd_dir)

    endpoint_changed = _cached_endpoint != endpoint
    _cached_endpoint = endpoint

    if endpoint_changed or "navigate" not in sys.modules:
        _purge_spellbook_modules()
        for name in _IMPORT_MODULES:
            importlib.import_module(name)
    else:
        cred_cfg = importlib.import_module("credentials._config")
        cred_cfg.CONSTANTS = cred_cfg.robot_config()
        cred = importlib.import_module("credentials")
        cred.CONSTANTS = cred_cfg.CONSTANTS
        importlib.reload(sys.modules["api_client"])
        importlib.reload(sys.modules["navigate"])

    return sys.modules["navigate"]
