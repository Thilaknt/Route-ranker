# RouteRanker Milestone 3: Road-Network Preparation & Optimization Design
**Engineering Architecture Document**

**Project:** RouteRanker — AI-Powered Public Transit Network Optimization  
**Repository:** `Powered-Public-Transit-Route-Optimization`  
**Milestone Focus:** Road-Network Preparation, Stop-to-Road Snapping, and Multi-Objective Evaluation Architecture  
**Status:** Design & Specification Only (No full optimizer or package installation executed)

---

## 1. Executive Summary & Objective

Milestone 2 established a validated, deterministic transit corridor graph ($9,669$ nodes, $17,118$ directed edges) and defensible baseline metrics ($1,091,488.6\text{ km/day}$ VKT, average circuity $1.54$, peak headway $39.4\text{ min}$).

However, **the existing transit corridor graph connects bus stops using direct straight-line chords**. It is an abstraction of published schedules, **NOT a physical road network**. Generating candidate bus routes directly on this stop graph would propose illegal, physically impossible paths (e.g. crossing divided barriers, driving against one-way streets, or traversing unpaved pedestrian alleys).

This document establishes the rigorous engineering specification to:
1. Ingest and extract a Bengaluru-specific drivable road network from OpenStreetMap.
2. Snap GTFS stops onto legal, bus-accessible directed road segments with direction awareness.
3. Generate candidate route alignments on physical streets using constrained $K$-shortest paths.
4. Formulate the multi-objective evaluation framework comparing candidate networks against the verified Milestone 2 baseline.

---

## 2. OpenStreetMap Dataset Investigation & Extraction Boundary

### 2.1 Investigation of `dataset/india-261024.osm.pbf`
* **File Size:** $1,711,953,012\text{ bytes}$ ($1.59\text{ GB}$).
* **Format:** Compressed binary protocol buffer (`OSMHeader`).
* **Environment Tooling Audit:** No native OSM CLI binaries (`osmium-tool`, `osmconvert`, `osmosis`) and no Python OSM bindings (`pyosmium`, `pyrosm`, `osmnx`, `geopandas`) are currently installed in the Python 3.11 environment.
* **Safety Constraint:** Attempting to parse a $1.6\text{ GB}$ national PBF file using standard Python file reading would exhaust system RAM and cause execution failure.

### 2.2 Extraction Strategy & Geographic Extent
Rather than processing the entire country of India, the extraction must be scoped to cover all retained metropolitan transit operations plus highway approaches to satellite terminals:

```
                          13.55° N (North Bound: Nandi Hills / Chikkaballapura approach)
                             ┌──────────────────────────────────────────────┐
                             │                                              │
                             │         Devanahalli (KIA Airport)            │
                             │                                              │
                             │    Nelamangala                Hoskote        │
        77.10° E             │          ┌─────────────────┐                 │        78.02° E
     (West Bound:            │          │  KBS (Majestic) │                 │     (East Bound:
     Dabaspete /             │  Magadi  │   City Center   │   Whitefield    │     Malur / Hoskote
     Bidadi Road)            │          └─────────────────┘                 │     Perimeter)
                             │                                              │
                             │       Kengeri          Silk Board            │
                             │                                              │
                             │        Bidadi           Attibele             │
                             │                                              │
                             └──────────────────────────────────────────────┘
                          12.50° N (South Bound: Harohalli / Anekal / Attibele)
```

* **Minimum Bounding Polygon (WGS 84 / EPSG:4326):**
  * **Latitude:** $[12.5000^\circ\text{ N}, 13.5500^\circ\text{ N}]$
  * **Longitude:** $[77.1000^\circ\text{ E}, 78.0200^\circ\text{ E}]$
* **Validation of Boundary:** This bounding box encompasses 100% of the $9,669$ retained urban stops (Lat $[12.6120, 13.4079]$, Lon $[77.1418, 77.9586]$), all 12 borderline suburban satellite routes (Channapatna, Tumakuru, Kanakapura), and provides a $5\text{ km}$ buffer for highway ramps, ring-road interchanges, and terminal turning loops.

### 2.3 Recommended Next Step for Extract Acquisition
1. **Option A (Clipping existing local file via Osmium CLI):** Authorize downloading/installing the standalone portable `osmium-tool` executable to slice `dataset/india-261024.osm.pbf` using the exact bounding box above into `dataset/bengaluru_roads.osm.pbf` (~$60\text{--}90\text{ MB}$).
2. **Option B (Regional Metro Extract):** If local binary installation is constrained, obtain the pre-clipped official GeoFabrik / BBBike Karnataka or Bengaluru metropolitan extract.

---

## 3. Road Graph Data Model & Schema

The road network must be modeled as a **directed spatial multigraph** $G_{\text{road}} = (V_{\text{road}}, E_{\text{road}})$ preserving physical roadway hierarchy:

### 3.1 Node Specification ($V_{\text{road}}$)
* `node_id`: OSM node identifier (integer string).
* `lat`, `lon`: Geographic coordinates (WGS 84 decimal degrees).
* `elevation_m`: Elevation in meters (if available; otherwise 0.0).
* `is_intersection`: Boolean indicating whether $\ge 2$ drivable edges meet at this node.

### 3.2 Edge Specification ($E_{\text{road}}$)
* `edge_id`: Unique identifier `(u, v, key)`.
* `osm_way_id`: OSM way identifier.
* `name`: Street name (e.g., "Outer Ring Road", "Hosur Road").
* `highway`: Road functional classification (`trunk`, `primary`, `secondary`, `tertiary`, `unclassified`, `residential`).
* `length_m`: Accurate geodesic length along roadway geometry in meters.
* `geometry`: List of intermediate coordinate vertices `[(lat, lon), ...]`.
* `oneway`: Boolean indicating forward-only traversal.
* `lanes`: Number of lanes (integer; default based on functional class if missing).
* `maxspeed_kmh`: Speed limit (km/h; default based on functional class if missing).
* `bus_permitted`: Boolean access flag.

### 3.3 Bus Access & Road Filtering Rules
| Highway Classification | Bus Permitted? | Rationale & Default Assumptions |
| :--- | :---: | :--- |
| `trunk`, `trunk_link` | **Yes** | Major arterial expressways permitting heavy commercial buses. |
| `primary`, `primary_link` | **Yes** | Key urban radial and ring corridors (e.g., Old Airport Rd, Bellary Rd). |
| `secondary`, `secondary_link` | **Yes** | Collector arterials connecting neighborhoods. |
| `tertiary`, `tertiary_link` | **Yes** | Local transit feeders; suitable for standard 12m or midi-buses. |
| `residential` | **Conditional** | Permitted only if width $\ge 6.5\text{ m}$ or currently traversed by GTFS shapes. |
| `motorway` | **Conditional** | Restricted. Excluded unless tagged `bus=yes` or `toll=yes` with public transit clearance (e.g. Bangalore Elevated Tollway). |
| `pedestrian`, `footway`, `cycleway`, `steps`, `service`, `track` | ❌ **No** | Strictly prohibited for bus routing. |

### 3.4 Handling Uncertain or Incomplete OSM Data
* **Missing `bus` tags:** Inferred from `highway` hierarchy. Arterials default to `True`; footpaths/alleys default to `False`.
* **Missing `oneway` tags:** Default to bidirectional (`False`) unless tagged `junction=roundabout` or designated as a dual-carriageway divider.
* **Turn Restrictions:** Derived from OSM relation records (`type=restriction`, `restriction=no_right_turn`, `no_u_turn`, `only_straight_on`). Disallowed turns are encoded as prohibited edge transitions in the routing graph.

---

## 4. Stop-to-Road Snapping Design

To make GTFS stops accessible to road-based routing, each stop must be projected onto the nearest legally valid road edge.

```
       [Stop S (GTFS)]
              │
              │  Orthogonal / Geodesic Distance d_perp
              ▼
   (Node u) ──*──────────────────── (Node v)  [Road Edge e: Primary, Oneway]
          Projection Point P
```

### 4.1 Direction-Aware Snapping Algorithm
1. **Spatial Index Query:** Using `scipy.spatial.KDTree` (already installed in the environment), query candidate road edge bounding boxes within $R_{\max} = 50\text{ meters}$ (fallback threshold $100\text{ meters}$).
2. **Access Filter:** Discard candidate edges where `bus_permitted == False`.
3. **Dual-Carriageway & One-Way Disambiguation:**
   * On divided roads (e.g. Hosur Road), there are two parallel one-way edges traveling in opposite directions.
   * Compute the empirical stop departure direction $\vec{V}_{\text{transit}} = (s_{i+1} - s_i)$ from trips serving stop $s$.
   * Match against road edge heading $\vec{V}_{\text{road}} = (v - u)$. Select the edge minimizing angular deviation $|\theta| < 45^\circ$.
4. **Edge Splitting:**
   * If stop $s$ projects onto interior point $P$ of edge $(u, v)$, edge $(u, v)$ is split into $(u, P)$ and $(P, v)$ with proportional lengths and runtimes.
   * Stop $s$ is connected to $P$ via an access link with $d_{\text{perp}}$.
5. **Off-Street Terminal Handling:**
   * Major terminals (KBS Majestic, Banashankari TTMC, Domlur TTMC) have platforms located off the public street network.
   * Rather than snapping platforms to random perimeter alleyways, designated terminal centroid nodes are linked to arterial ingress/egress gate nodes via explicit terminal connector edges.

### 4.2 Matching Quality & Confidence Scoring
Every stop match is tagged with an audit record:
$$\text{Confidence}(s) = \max\left(0.0, 1.0 - \frac{d_{\text{perp}}}{50\text{ m}}\right) \times \cos(\theta_{\text{heading}})$$
* **High Confidence ($\ge 0.8$):** Distance $< 15\text{ m}$, heading aligned within $20^\circ$.
* **Borderline ($0.4 \le \text{Conf} < 0.8$):** Distance $15\text{--}50\text{ m}$; requires manual or geometry-trace confirmation.
* **Unmatched ($\text{Conf} < 0.4$):** Logged in `outputs/unmatched_stops.json` for human inspection; never silently forced onto an incorrect roadway.

---

## 5. Candidate Route Generation Architecture

Candidate route generation must operate on the **physical road graph**, connecting verified major transit terminals.

### 5.1 Terminal Selection Strategy
Terminals are **NOT chosen arbitrarily**. They are extracted from the observed GTFS endpoint distribution:
1. Identify high-frequency origin-destination hub pairs from GTFS:
   * Top radial corridors (e.g., Attibele $\leftrightarrow$ KBS Majestic, Kadugodi $\leftrightarrow$ KBS Majestic).
   * Key circumferential ring corridors (e.g., Kengeri $\leftrightarrow$ Banashankari $\leftrightarrow$ Silk Board $\leftrightarrow$ Kadugodi).
2. Group terminals by TTMC and regional depot facilities where bus layover and crew relief infrastructure exist.

### 5.2 Algorithmic Route Synthesis
```
 [Terminal A (Origin)]                                   [Terminal B (Destination)]
          │                                                         ▲
          ▼                                                         │
 ┌──────────────────────────────────────────────────────────────────┴────────┐
 │ 1. Physical Shortest Path (Dijkstra on Road Graph Length / Speed)         │
 └────────────────────────────────┬──────────────────────────────────────────┘
                                  │
                                  ▼
 ┌───────────────────────────────────────────────────────────────────────────┐
 │ 2. Constrained K-Shortest Paths (Yen's Algorithm on Road Graph)           │
 │    - Generates K alternative arterial paths (K = 3 to 5)                  │
 │    - Enforces turn restrictions and bus access constraints                │
 └────────────────────────────────┬──────────────────────────────────────────┘
                                  │
                                  ▼
 ┌───────────────────────────────────────────────────────────────────────────┐
 │ 3. Detour & Sinuosity Pruning                                             │
 │    - Discard paths with Circuity > 1.8                                    │
 │    - Discard paths with Length > (1 + delta) * L_shortest (delta = 25%)   │
 └────────────────────────────────┬──────────────────────────────────────────┘
                                  │
                                  ▼
 ┌───────────────────────────────────────────────────────────────────────────┐
 │ 4. Sequence GTFS Stops Along Candidate Road Polyline                      │
 │    - Stops within 25m buffer of road path are sequenced into timetable    │
 └───────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Multi-Objective Evaluation Framework

Each candidate network configuration $\mathcal{N}_{\text{cand}}$ is evaluated against the baseline $\mathcal{N}_{\text{base}}$ across vector objective function $\mathbf{F}(\mathcal{N}) = [f_1, f_2, f_3, f_4, f_5]$:

### 6.1 Objective Functions

#### 1. Vehicle-Kilometers Traveled (Operator Efficiency)
$$f_1(\mathcal{N}) = \sum_{r \in \mathcal{R}} \text{VKT}(r) = \sum_{r \in \mathcal{R}} \sum_{t \in \mathcal{T}_r} L_{\text{road}}(r) \quad [\text{km/day}]$$
* **Goal:** Minimize wasteful operational mileage while maintaining service.

#### 2. Standardized Operating Cost Proxy
$$f_2(\mathcal{N}) = f_1(\mathcal{N}) \times \text{CPKM}_{\text{benchmark}} \quad [\text{INR/day}]$$
* **Benchmark:** $\text{CPKM} = ₹83.05\text{ / km}$ (authoritative from BMTC FY 2024–25 KPIs).
* **Epistemic Label:** Statistical cost proxy; not an observed route ledger.

#### 3. Average Route Circuity (Passenger Directness)
$$f_3(\mathcal{N}) = \frac{1}{|\mathcal{R}|} \sum_{r \in \mathcal{R}} \frac{L_{\text{road}}(r)}{D_{\text{euclidean}}(\text{orig}_r, \text{dest}_r)} \quad [\text{dimensionless ratio}]$$
* **Goal:** Minimize circuitous winding routes; improve travel time directness.

#### 4. Headway Regularity & Service Balance
$$f_4(\mathcal{N}) = \frac{1}{|\mathcal{R}|} \sum_{r \in \mathcal{R}} \text{StdDev}(\mathbf{h}_{r, \text{peak}}) \quad [\text{minutes}]$$
* **Goal:** Minimize departure headway variance; prevent bus bunching.

#### 5. Corridor Redundancy & Congestion Burden
$$f_5(\mathcal{N}) = \sum_{e \in E_{\text{road}}} \max\left(0, \text{Routes}(e) - \text{Capacity}_{\text{target}}(e)\right)$$
* **Goal:** De-duplicate excessive parallel overlapping routes along saturated corridors (e.g. Majestic approaches).

### 6.2 Hard Constraints
1. **Fleet Availability Bound:**
   $$\text{Active Schedules}(\mathcal{N}) \le B_{\text{fleet}} \quad (\text{Configurable; default } 6,000\text{--}6,281\text{ buses})$$
2. **Stratified Route Length Caps:**
   $$L(r) \le L_{\max}(\text{Class}(r)) \quad (\text{Feeder: } \le 18\text{ km}, \text{ Trunk: } \le 35\text{ km}, \text{ Express: } \le 65\text{ km})$$
3. **Stop Coverage Preservation:**
   $$\text{Stops}(\mathcal{N}_{\text{cand}}) \supseteq \text{Stops}(\mathcal{N}_{\text{base}}) \quad (\text{Zero unserved stops permitted})$$
4. **Physical Traversability:**
   $$\forall (u, v) \in \text{Route}, \quad (u, v) \in E_{\text{road}} \text{ with } \text{bus\_permitted} == \text{True}$$

---

## 7. Unresolved Data Limitations & Risks

1. **Absence of Passenger O-D Matrix:** Public GTFS contains schedules, not ticket transactions. Direct passenger crowding or ridership elasticity cannot be claimed without external smart-card data or a synthetic spatial gravity model.
2. **Published Schedules vs. Real-World Speeds:** Travel times reflect timetable estimates, not live GPS congestion.
3. **Crowdsourced OSM Gaps:** Bus lane and height restriction tagging in OSM may be incomplete in peripheral Bengaluru suburbs; heuristics must be clearly flagged.

---

## 8. Milestone 3 Implementation Roadmap

* [ ] **Task 3.1:** Extract and clip Bengaluru road network into `dataset/bengaluru_roads.osm.pbf` (Bounding Box $[12.50, 77.10]$ to $[13.55, 78.02]$).
* [ ] **Task 3.2:** Implement directed NetworkX physical road graph builder `road_graph.py` with functional classes and turn filters.
* [ ] **Task 3.3:** Implement direction-aware KDTree stop-to-road snapping module `stop_snapper.py` with confidence scoring.
* [ ] **Task 3.4:** Implement constrained $K$-shortest paths route generator `route_generator.py` for selected high-frequency terminal corridors.
* [ ] **Task 3.5:** Implement multi-objective evaluator `evaluator.py` benchmarking candidate routes against Milestone 2 baseline.
