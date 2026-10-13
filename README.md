# Project Blue Ocean — RMF2 demo stack

> **ARES PRIVATE** — internal demo data (robot IPs, map ids, ARTC site
> coordinates are sensitive). Do not paste live credentials or addresses here;
> treat the linked `docs/` pages as confidential.

A two-robot Multi-Agent Path Finding (MAPF) + VDA5050 coordination demo. A
single dispatch schedules two heterogeneous robots (**AutoXing**, **Reeman**) to
their goals; a Conflict-Based Search (CBS) planner resolves their path conflict
and dispatches collision-free VDA5050 orders. The whole system is one
`git clone` + `docker compose up` against physical robots.

AutoXing and Reeman robots on the demo floor

📹 [MS Teams Video of the Demo in June L1](https://itssastar.sharepoint.com/:v:/r/sites/RMF2-Dev-Team/Shared%20Documents/%5BDev%5D%20VDA5050/BlueOceanDemoVideo-GameTL%201-2026-06-15-8000.mp4?csf=1&web=1&e=ukqdqB)

📹 [MS Teams Video of the Demo in July AMAV-X](https://itssastar.sharepoint.com/:v:/r/sites/RMF2-Dev-Team/Shared%20Documents/%5BDev%5D%20VDA5050/Demo-July-Blue-Ocean.mp4?csf=1&web=1&e=uFkZ0m)


## Stack

This stack and all six source repos use `demo-june-blue-ocean` as the default
branch (pinned by `.repos`; see [Branch](#branch-demo-june-blue-ocean)).


| Service                   | Repo                            | Port variable                  |
| ------------------------- | ------------------------------- | ------------------------------ |
| UI dashboard              | `rmf2-ui-gametl`                | `UI_PORT`                      |
| Scheduler API             | `rmf2_scheduler_gametl`         | `SCHEDULER_PORT`               |
| Task orchestrator         | `rmf2_task_orchestrator_gametl` | `TASK_ORCHESTRATOR_PORT`       |
| MAPF planner (CBS)        | `res_mapf_gametl`               | —                              |
| VDA5050 master + adapters | `vda5050_core_gametl`           | `VDA5050_PORT`                 |
| Map Server                | `rmf2_map_server`               | `8008` # see docker compose    |
| MQTT broker               | `eclipse-mosquitto`             | `MQTT_PORT`                    |
| AMQP broker               | `rabbitmq`                      | `AMQP_PORT` / `AMQP_MGMT_PORT` |




## Prerequisites

- Docker + Docker Compose v2
- [vcstool](https://github.com/dirk-thomas/vcstool): `pip install vcstool`
- [uv](https://docs.astral.sh/uv/) — only for running the fixtures and the map
calibrator (both provision their own deps; no `pip install` needed)

---



## Quick Start

```bash
# 1. Clone (lands on demo-june-blue-ocean by default)
git clone git@gitlab.com:ROSI-AP/rmf2/blueocean/rmf2-blue-ocean-stack.git
cd rmf2-blue-ocean-stack

# 2. Pull all source repos (each on demo-june-blue-ocean; paths in .repos are under src/)
vcs import . < .repos

# 3. Local config — copy templates, then edit robot IPs / map id, This is for Step 4
cp .env.example .env
cp src/vda5050_core_gametl/vda5050_core_py/demo-june-blue-ocean/autoxing/autoxing_bridge/spellbook/credentials/CONSTANTS.example.yml \
   src/vda5050_core_gametl/vda5050_core_py/demo-june-blue-ocean/autoxing/autoxing_bridge/spellbook/credentials/CONSTANTS.yml
cp src/vda5050_core_gametl/vda5050_core_py/demo-june-blue-ocean/reeman/reeman_bridge/spellbook/credentials/CONSTANTS.example.yml \
   src/vda5050_core_gametl/vda5050_core_py/demo-june-blue-ocean/reeman/reeman_bridge/spellbook/credentials/CONSTANTS.yml

# 4. If need to map_transform
#! MAKE SURE all robots are online to get the maps from Autoxing and Reeman. 
cd maps && uv run get_map_n_transform.py AMAV-X # or DIR of maps to be tranformed 

# 5. Build and start the stack (first build ~2 min for the ROS images)
docker compose up --build 
# use after build to prevent accidential recompilation
docker compose up
```

Services come up in dependency order. Copy `.env.example` to `.env` and each
spellbook's `CONSTANTS.example.yml` to `CONSTANTS.yml` (gitignored) before
starting — set `VDA5050_MAP_ID` (`AMAV-X` | `l1-artc`) in `.env` and each
robot's `robot_ip` in the spellbook credentials.

## Run the demo (physical robots)

Please see [handover-demo.md](handover-demo.md) on How to physically set up the robots at AMAV-X on L3 of ARTC. 

The adapter `autoxing-1` and `reeman-2-blue` will run and connect to the master regardless. the adapter will continously ping to the robot with get_pose(spellbook) and update the vda5050 `/state`  of each robot, as well as the UI. 

  
  


## Flow



### VDA Visualiser

1. Show the state of each robot
2. Show MAPF by assignign goals to each robot where same node collision is imminent(such as image above)
  1. Before `Apply`, Show `MapF Sim` in **Tabs** which will animation the movement of each robot in each frame.
  2. After `Apply`, the robot should navigate to its goal. The robot deeper in the nodes will go first, unless the other robot is obtructing.
3. In tabs Logs, Show the logs of VDA5050 message such as `/state`, `/order`



### Map LIF Editor

1. Add/Remove Nodes, tap on an exisiting node, add node top-left and add note. `Submit Map` to `map_server`
2. Navigate back to the VDA Visualiser and move the robot to the newly added node.



### Network

1. Show live status and 'fake' uptime of each docker container.



### Scheduler

1. Show 'fake' scheduled jobs assigned to each robot



### Manual Wiki

1. Links to the [https://dev.rmf-industrial.org/latest/](https://dev.rmf-industrial.org/latest/)

---



# Steps to make a new demo

> AMAV-X is the location @ L3 ARTC. if not the same for you. make new foler dir in in `maps/` and Find All and Replace 'AMAV-X' with your new map name

1. Given you have remapped the both of the robot. run the command below. Map will get retrieved. robot at the top of the AMAV-X/robots.yaml will be the master frame.
2. Start to edit/create the real.layout.lif.json, Add Nodes, Edges, Stations. Coordinates are relative to the master frame

```bash
# 4. If need to map_transform
#! MAKE SURE all robots are online to get the maps from Autoxing and Reeman. 
cd maps && uv run get_map_n_transform.py AMAV-X # or DIR of maps to be tranformed 
```



### Frame transforms

Each robot localizes on its own native map; `maps/map_transforms.yaml` declares
the shared `master_frame` plus a per-robot `robot_to_master` similarity (AutoXing
is identity; Reeman is a ~177° + (1.33, 1.99) m offset). The master loads this
file and serves it at `GET /transform`; **each adapter fetches its transform from
the master** at startup — robots only need `ROBOT_ID` + `MASTER_URL`.

## Robots & jack control


| Robot              | Frame                                       | Jack                                                                 | Reference                            |
| ------------------ | ------------------------------------------- | -------------------------------------------------------------------- | ------------------------------------ |
| **AutoXing**       | master (frame = `VDA5050_MAP_ID`), identity | `/services/jack_up` · `_down`; rack pick/drop via the tablet         | [docs/autoxing.md](docs/autoxing.md) |
| **Reeman** FlyBoat | own frame, calibrated to master             | hydraulic lift (`/cmd/hydraulic_up`/`_down`); no jack-state endpoint | [docs/reeman.md](docs/reeman.md)     |


Jack control is **AutoXing-only** in this demo (Reeman `POST /actions/jack`
returns `501`). Manual jack, master-REST jack, and rack pick/drop are all
covered in [docs/how-to-jack-off-and-on.md](docs/how-to-jack-off-and-on.md).

## Service ports

All ports live in `[.env](.env.example)` (auto-loaded by Compose; copy from
`[.env.example](.env.example)`). Fixtures read the same variables when
`--port`/`--host` is omitted.


| Service             | URL / port (default)                                                                                 |
| ------------------- | ---------------------------------------------------------------------------------------------------- |
| UI dashboard        | [http://localhost:3000](http://localhost:3000)                                                       |
| Scheduler API docs  | [http://localhost:8089/docs](http://localhost:8089/docs)                                             |
| Task orchestrator   | [http://localhost:2727](http://localhost:2727)                                                       |
| VDA5050 master docs | [http://localhost:8000/docs](http://localhost:8000/docs) (`/state`, `/map`, `/transform`, `/robots`) |
| MQTT broker         | 1883                                                                                                 |
| RabbitMQ management | [http://localhost:15672](http://localhost:15672) (guest / guest)                                     |




## Demo fixtures

Each fixture declares its own deps (PEP 723), so `uv run` provisions them — no
`pip install`. Use `--host` / `--broker` to target a remote VM.

```bash
uv run fixtures/run_verify_orders.py          # subscribe to VDA5050 orders, assert node sequences
uv run fixtures/run_send_schedule.py          # publish a Schedule to the orchestrator via AMQP
uv run fixtures/run_send_mapf_task_request.py # publish an amr_mapf TaskRequest directly to AMQP
uv run fixtures/run_publish_mqtt_route.py     # publish a raw VDA5050 route order via MQTT
```

See `fixtures/demo_tasks.json` for sample payloads.

## Repo layout

```
rmf2-blue-ocean-stack/
├── .repos                       # vcstool manifest (5 repos, demo-june-blue-ocean)
├── .env.example                 # stack config template (ports, VDA5050_MAP_ID) — copy to .env
├── docker-compose.yml           # full stack (real physical robots)
├── docker/                      # task-orchestrator / res-mapf / vda5050 Dockerfiles
├── config/                      # mosquitto.conf, task_orchestrator.toml
├── maps/                        # per-floor dirs (l1/, l3-amav-x/) + shared map_transform/ tooling
├── fixtures/                    # uv-run demo publishers + order verifier
├── docs/                        # architecture + how-to references (linked above)
└── src/                         # populated by: vcs import . < .repos
```

---



## Branch: `demo-june-blue-ocean`

`demo-june-blue-ocean` is the default branch for this stack and all six source
repos. The demo depends on a coordinated set of changes across them — `vcs import`
reads `.repos` and checks out `demo-june-blue-ocean` automatically.
