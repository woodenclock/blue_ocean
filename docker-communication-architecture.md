# Blue Ocean Stack — Docker Communication Architecture

Single compose file: `docker-compose.yml`. All services share the **`rmf2-net`** bridge network (`rmf2_demo_gametl_rmf2-net`). Inside Docker, services reach each other by **DNS name** (`vda5050`, `scheduler`, `amqp-broker`, …). The browser/UI runs on the **host** and uses **localhost ports** mapped from containers.

---

## Container inventory

| Service | Role | Host port | Internal port | Headless? |
|---------|------|-----------|---------------|-----------|
| **mqtt-broker** | Mosquitto — VDA5050 robot I/O | `1883` | `1883` | No |
| **amqp-broker** | RabbitMQ — workflow + MAPF messaging | `5672`, `15672` (mgmt UI) | same | No |
| **vda5050** | VDA5050 master — map authority, orders, live state | `8000` | `8000` | No |
| **scheduler** | Demo FastAPI gateway (`/demo/*`) | `8089` | `8089` | No |
| **task-orchestrator** | Crossflow workflow executor (Rust/Bevy) | `2727` | `2727` | No |
| **mapf-planner** | CBS multi-agent path planner | *(none)* | `8092` preview only | Yes |
| **map-server** | LIF editor persistence + map upload | `8008` | `8008` | No |
| **autoxing-1** | AutoXing VDA5050 adapter | *(none)* | — | Yes |
| **reeman-2-blue** | Reeman VDA5050 adapter | *(none)* | — | Yes |
| **ui** | Dashboard (nginx static SPA) | `3000→80` | — | **Commented out** |

**Not in stack:** PostgreSQL, Redis, gRPC, stack-level API gateway.

**Outside Docker:** physical robots on the LAN (`maps/<VDA5050_MAP_ID>/robots.yaml` → `endpoint` per robot).

---

## Protocols used

| Protocol | Where | Purpose |
|----------|-------|---------|
| **HTTP REST** | UI ↔ services, inter-service control | Maps, navigation, missions, health |
| **WebSocket** | UI ↔ VDA5050 master | Live robot poses, map, MQTT debug logs |
| **MQTT** | Master ↔ adapters (Mosquitto) | VDA5050 orders + state |
| **AMQP** | Scheduler, orchestrator, MAPF, master (RabbitMQ) | Schedules, task requests, task status |
| **Filesystem** | `./maps` bind mount → `vda5050` | LIF layout, robots.yaml, map images |

---

## High-level topology

```mermaid
flowchart TB
    subgraph Host["Host machine"]
        Browser["Browser / Dashboard UI<br/>pnpm start :3000 or Docker UI :3000"]
        Robots["Physical robots<br/>AutoXing / Reeman REST"]
    end

    subgraph Docker["Docker network: rmf2-net"]
        MQTT["mqtt-broker<br/>:1883 MQTT"]
        AMQP["amqp-broker<br/>:5672 AMQP"]
        
        VDA["vda5050 master<br/>:8000 HTTP + WS"]
        SCH["scheduler<br/>:8089 HTTP"]
        RTO["task-orchestrator<br/>:2727 HTTP"]
        MAPF["mapf-planner<br/>:8092 preview only"]
        MS["map-server<br/>:8008 HTTP"]
        
        AX["autoxing-1 adapter"]
        RE["reeman-2-blue adapter"]
    end

    Maps["./maps bind mount<br/>real.layout.lif.json, robots.yaml"]

    Browser -->|"HTTP REST + WebSocket"| VDA
    Browser -->|"HTTP REST /demo/*"| SCH
    Browser -->|"HTTP REST /api/lif, /newmap"| MS
    Browser -->|"HTTP REST (health/editor)"| RTO

    SCH -->|"HTTP GET /state, /map, /robots"| VDA
    SCH -->|"AMQP Schedule → @RECEIVE@"| AMQP
    SCH -->|"HTTP POST :8092/preview"| MAPF

    RTO -->|"AMQP consume @RECEIVE@-rmf_schedule"| AMQP
    RTO -->|"AMQP publish TaskRequest"| AMQP
    RTO -->|"MQTT (demo fixtures only)"| MQTT

    MAPF -->|"HTTP GET /map/grid, /robots"| VDA
    MAPF -->|"HTTP POST /plans/mapf"| VDA
    MAPF -->|"AMQP consume @RECEIVE@-mapf-planner"| AMQP
    MAPF -->|"AMQP publish TaskStatus"| AMQP

    VDA -->|"MQTT VDA5050 orders"| MQTT
    VDA -->|"AMQP TaskStatus on order done"| AMQP
    VDA --- Maps

    MS -->|"HTTP GET /map, POST /map/lif"| VDA
    MS -->|"HTTP POST /demo/demo-reset"| SCH

    MQTT --> AX
    MQTT --> RE
    AX -->|"HTTP REST spellbook"| Robots
    RE -->|"HTTP REST spellbook"| Robots
    SCH -->|"HTTP REST direct-nav / pick-rack"| Robots

    AMQP --> RTO
    AMQP --> MAPF
    AMQP --> VDA
```

---

## VDA5050 master — single map authority

Everything that needs topology, robot profiles, transforms, or live poses ultimately talks to **`vda5050`**.

| Source | What it reads/writes |
|--------|----------------------|
| **Filesystem** | `maps/${VDA5050_MAP_ID}/real.layout.lif.json`, `robots.yaml`, `map_transforms.yaml`, `master.image.png` |
| **mapf-planner** | `GET /map/grid`, `GET /robots`, `POST /plans/mapf` |
| **scheduler** | `GET /state`, `GET /map`, `GET /robots` |
| **map-server** | `GET /map` (startup), pushes `POST /map/lif` on upload |
| **adapters** | `GET /robots`, `/transform`, `/map` at startup |
| **UI** | `GET /map`, `/map/image`, `/plans/mapf`, `/actions/*`, `WS /ws/state`, `WS /ws/logs` |

```mermaid
flowchart LR
    subgraph MapsDir["maps/VDA5050_MAP_ID/"]
        LIF["real.layout.lif.json"]
        ROB["robots.yaml"]
        TF["map_transforms.yaml"]
        IMG["master.image.png"]
    end

    VDA["vda5050 master"]

    MapsDir --> VDA
    VDA -->|"GET /map, /map/grid"| MAPF["mapf-planner"]
    VDA -->|"GET /state, /map"| SCH["scheduler"]
    VDA -->|"POST /map/lif"| MS["map-server"]
    VDA -->|"WS /ws/state"| UI["Browser UI"]
    VDA -->|"MQTT orders"| ADP["adapters"]
    ADP -->|"MQTT state"| VDA
```

---

## How MAPF gets maps

MAPF does **not** read `./maps` directly. It pulls topology from the master at startup and on reset:

1. **Startup** — `mapf-planner` waits for `vda5050` healthy, then:
   - `GET http://vda5050:8000/map/grid` → node graph (ids, edges, departure blockers)
   - `GET http://vda5050:8000/robots` → robot profiles (footprint, start nodes)
2. **Runtime** — listens on AMQP queue `@RECEIVE@-mapf-planner` for `TaskRequest` messages (`task_type: amr_mapf`).
3. **Planning** — CBS + `MAPFCoordinator` on the grid graph.
4. **Output** — `POST http://vda5050:8000/plans/mapf` with waypoints per robot.
5. **Reload** — after LIF editor upload → map-server → master → scheduler `POST /demo/demo-reset` → planner re-fetches `/map/grid`.

```mermaid
sequenceDiagram
    participant MS as map-server
    participant VDA as vda5050
    participant SCH as scheduler
    participant MAPF as mapf-planner
    participant AMQP as amqp-broker

    Note over MAPF,VDA: Startup
    MAPF->>VDA: GET /map/grid
    MAPF->>VDA: GET /robots
    MAPF->>MAPF: Build MapGraph in memory

    Note over MS,MAPF: After LIF editor upload
    MS->>VDA: POST /map/lif (new layout)
    MS->>SCH: POST /demo/demo-reset
    SCH->>AMQP: MapfReset / replan signal
    MAPF->>VDA: GET /map/grid (reload)
    MAPF->>MAPF: Rebuild graph
```

---

## AMQP message bus (RabbitMQ)

| Exchange | Type | Queues | Message types |
|----------|------|--------|---------------|
| `@RECEIVE@` | fanout, durable | `@RECEIVE@-rmf_schedule` | `Schedule` (orchestrator) |
| | | `@RECEIVE@-mapf-planner` | `TaskRequest` (`amr_mapf`) |
| | | `@RECEIVE@-task-responses` | `TaskStatus` (orchestrator listener) |

```mermaid
flowchart LR
    SCH["scheduler"] -->|"Schedule<br/>(GoToNode per robot)"| EX["@RECEIVE@ fanout"]
    EX --> Q1["@RECEIVE@-rmf_schedule"]
    EX --> Q2["@RECEIVE@-mapf-planner"]
    EX --> Q3["@RECEIVE@-task-responses"]

    Q1 --> RTO["task-orchestrator"]
    RTO -->|"TaskRequest amr_mapf"| EX
    Q2 --> MAPF["mapf-planner"]
    MAPF -->|"TaskStatus COMPLETED"| EX
    VDA["vda5050 master"] -->|"TaskStatus on order done"| EX
    Q3 --> RTO
```

---

## MQTT (Mosquitto) — robot I/O

| Participant | Role |
|-------------|------|
| **vda5050** | Publishes VDA5050 **orders**; subscribes to **state** |
| **autoxing-1**, **reeman-2-blue** | Subscribe to orders; publish state |
| **task-orchestrator** | MQTT only for demo `MQTTDeviceReqNode` fixtures (not primary nav) |

Topic pattern: `uagv/v2/{manufacturer}/{serial_number}/...`

```mermaid
sequenceDiagram
    participant VDA as vda5050
    participant MQTT as mqtt-broker
    participant ADP as adapter
    participant ROB as physical robot

    VDA->>MQTT: VDA5050 Order
    MQTT->>ADP: order topic
    ADP->>ROB: HTTP REST (navigate, jack, etc.)
    ROB-->>ADP: status
    ADP->>MQTT: VDA5050 State
    MQTT->>VDA: state topic
    VDA->>VDA: Update poses, order queue
```

Adapters resolve identity at startup from master (`ROBOT_ID` env only):

- `GET /robots` → manufacturer, serial, `endpoint`
- `GET /transform` → frame calibration (Reeman → AutoXing)
- `GET /map` → onboard map alignment

---

## Primary navigation flow (MAPF path)

UI → scheduler → AMQP → orchestrator → AMQP → MAPF → master → MQTT → adapter → robot.

```mermaid
sequenceDiagram
    participant UI as Browser UI
    participant SCH as scheduler :8089
    participant AMQP as amqp-broker
    participant RTO as task-orchestrator :2727
    participant MAPF as mapf-planner
    participant VDA as vda5050 :8000
    participant MQTT as mqtt-broker
    participant ADP as adapter
    participant ROB as physical robot

    UI->>SCH: POST /demo/navigate-to (HTTP)
    SCH->>AMQP: Schedule with GoToNode (AMQP)
    AMQP->>RTO: @RECEIVE@-rmf_schedule
    RTO->>AMQP: TaskRequest amr_mapf (AMQP)
    AMQP->>MAPF: @RECEIVE@-mapf-planner
    MAPF->>VDA: POST /plans/mapf (HTTP)
    VDA->>MQTT: VDA5050 Order (MQTT)
    MQTT->>ADP: order
    ADP->>ROB: REST navigate
    ADP->>MQTT: VDA5050 State
    MQTT->>VDA: state
    VDA->>UI: WS /ws/state (live poses)
    VDA->>AMQP: TaskStatus on completion
    AMQP->>MAPF: clear finished task
    AMQP->>RTO: GoToNode unblocks
```

---

## Bypass paths (no MAPF)

| Path | Flow | Protocol |
|------|------|----------|
| **Direct navigate** | UI → scheduler `/demo/direct-navigate` → spellbook REST on robot IP | HTTP only |
| **Pick rack** | UI → scheduler `/demo/pick-rack` → AutoXing spellbook on robot IP | HTTP only |
| **MAPF preview** | UI → scheduler `/demo/mapf-preview` → mapf-planner `:8092/preview` | HTTP only (no dispatch) |
| **Minimal stack** | `docker compose up mqtt-broker vda5050 autoxing-1 reeman-2-blue` | MQTT + master only |

---

## How the UI gets information

The UI service is **commented out** in compose. Typical dev: `pnpm start` on the host at `:3000`. **Nginx serves static files only** — no API reverse proxy. The browser calls each backend **directly on localhost ports**.

### Build-time env (`Dockerfile` / `.env.production`)

| Env var | Default target | Used for |
|---------|----------------|----------|
| `VITE_RTS_BASE` | `http://localhost:8089` | Scheduler `/demo/*` |
| `VITE_BROKER_STATUS_BASE` | `http://localhost:8000` | VDA5050 master + WebSocket |
| `VITE_RTO_BASE` | `http://localhost:2727` | Task orchestrator |
| `VITE_API_BASE_URL` | `http://localhost:8008` | Map server (LIF editor) |

WebSocket URLs are derived from the master base (`use-vda-state.ts`):

```ts
const WS_URL =
  (BrokerStatusConfig.BASE ?? 'http://localhost:8000').replace(/^http/, 'ws') +
  '/ws/state';
```

### UI → backend matrix

| UI feature | Endpoint | Protocol |
|------------|----------|----------|
| Live map + robot poses | `ws://localhost:8000/ws/state` | WebSocket |
| MQTT debug log tap | `ws://localhost:8000/ws/logs` | WebSocket |
| Map backdrop image | `GET :8000/map/image/meta`, `/map/image` | HTTP |
| Jack up/down, instant actions | `POST :8000/actions/*` | HTTP |
| Navigate, missions, pick-rack, preview | `POST :8089/demo/*` | HTTP |
| LIF load/save/publish | `GET/POST :8008/api/lif`, `POST /newmap` | HTTP |
| Network health page | `GET :8089/docs`, `:8000/status` | HTTP |
| Orchestrator editor/health | `GET :2727/health_check` | HTTP |

```mermaid
flowchart TB
    Browser["Browser dashboard<br/>localhost:3000"]

    subgraph Backends["Host-mapped ports"]
        VDA["vda5050 :8000<br/>HTTP + WebSocket"]
        SCH["scheduler :8089<br/>HTTP REST"]
        MS["map-server :8008<br/>HTTP REST"]
        RTO["task-orchestrator :2727<br/>HTTP REST"]
    end

    Browser -->|"WS /ws/state, /ws/logs"| VDA
    Browser -->|"GET /map/image, POST /actions/*"| VDA
    Browser -->|"POST /demo/navigate-to, pick-rack, mapf-preview"| SCH
    Browser -->|"GET/POST /api/lif, POST /newmap"| MS
    Browser -->|"GET /health_check"| RTO

    SCH -.->|"internal Docker only"| MAPF["mapf-planner :8092"]
    MS -->|"Docker internal"| VDA
    MS -->|"Docker internal"| SCH
```

---

## LIF map editor upload flow

```mermaid
sequenceDiagram
    participant UI as Browser LIF editor
    participant MS as map-server :8008
    participant VDA as vda5050 :8000
    participant SCH as scheduler :8089
    participant MAPF as mapf-planner

    UI->>MS: POST /newmap (HTTP, layout JSON)
    MS->>MS: Persist to volume map-server-data
    MS->>VDA: POST /map/lif (HTTP)
    MS->>SCH: POST /demo/demo-reset (HTTP, best-effort)
    SCH->>MAPF: Trigger graph reload
    MAPF->>VDA: GET /map/grid (HTTP)
    UI->>VDA: GET /map (refresh visualiser)
```

---

## Service dependency graph (compose `depends_on`)

```mermaid
flowchart BT
    MQTT["mqtt-broker"]
    AMQP["amqp-broker"]
    VDA["vda5050"]
    SCH["scheduler"]
    RTO["task-orchestrator"]
    MAPF["mapf-planner"]
    MS["map-server"]
    AX["autoxing-1"]
    RE["reeman-2-blue"]

    VDA --> MQTT
    RTO --> MQTT
    RTO --> AMQP
    MAPF --> AMQP
    MAPF --> VDA
    MS --> VDA
    MS --> SCH
    AX --> MQTT
    AX --> VDA
    RE --> MQTT
    RE --> VDA
```

---

## Quick reference — who talks to whom

| From | To | Protocol | Data |
|------|-----|----------|------|
| UI | vda5050 | HTTP + WS | Map, poses, actions, plans |
| UI | scheduler | HTTP | Demo missions, nav, pick-rack |
| UI | map-server | HTTP | LIF CRUD, publish map |
| scheduler | vda5050 | HTTP | Live state, topology |
| scheduler | amqp-broker | AMQP | GoTo schedules |
| scheduler | mapf-planner | HTTP | MAPF preview |
| scheduler | robots (LAN) | HTTP | Direct nav, spellbook |
| task-orchestrator | amqp-broker | AMQP | Schedules in, TaskRequests out |
| mapf-planner | vda5050 | HTTP | Grid, robots, plans |
| mapf-planner | amqp-broker | AMQP | TaskRequests in, TaskStatus out |
| map-server | vda5050 | HTTP | Map sync |
| map-server | scheduler | HTTP | Planner reset after upload |
| vda5050 | mqtt-broker | MQTT | VDA5050 orders/state |
| vda5050 | amqp-broker | AMQP | Order completion → MAPF |
| adapters | vda5050 | HTTP | Identity, map, transform |
| adapters | mqtt-broker | MQTT | VDA5050 I/O |
| adapters | robots (LAN) | HTTP | Navigate, jack, pick |

---

## Minimal vs full stack

**Full stack** (default):

```bash
docker compose up --build
```

**VDA5050 only** (no scheduler, MAPF, orchestrator):

```bash
docker compose up --build mqtt-broker vda5050 autoxing-1 reeman-2-blue
```

In the minimal stack, the UI can still connect to `:8000` for live visualisation; navigation via MAPF/scheduler is unavailable.
