# RouteRanker: Baseline Transit Network Audit & Metrics Report

**Milestone:** Milestone 2 — GTFS Ingestion, Outstation Filtering & Baseline Modeling  
**Target City:** Bengaluru Metropolitan Area  
**Transit Agency:** Bengaluru Metropolitan Transport Corporation (BMTC)  
**Filtering Mode:** `STRICT_URBAN`  

---

## 1. Data Ingestion & Quality Findings

* **Agencies:** 1
* **Declared Routes:** 4,434 (Routes with 0 trips: 18)
* **Total Trips:** 57,836 (Trips without stop_times: 0)
* **Total Stops:** 9,960
* **Scheduled Stop Times:** 1,540,686
* **Distinct Shape Polylines:** 7,355 (2,492,304 vertices)
* **Total Validation Errors:** 0
* **Total Validation Warnings:** 1

---

## 2. Urban vs. Outstation Service Partitioning

To ensure route optimization operates strictly on Bengaluru's urban and suburban transit network, long-distance intercity services were identified and filtered:

* **Routes Before Filtering:** 4,434
* **Routes Retained (Urban Scope):** 4,382 (52 excluded)
* **Trips Retained:** 57,256 (580 excluded)
* **Stops in Urban Graph:** 9,669 (291 excluded)

### Exclusion Breakdown by Rule:
* **Extreme Distance (> 75 km from city center):** 39 routes
* **Outstation Operator Keyword Markers (e.g. KSRTC stands):** 1 routes
* **Suburban / Peri-Urban Excluded (under Strict Urban mode):** 12 routes

---

## 3. Defensible Baseline Metrics

| Metric Category | Metric Name | Value | Methodology / Epistemic Basis |
| :--- | :--- | :---: | :--- |
| **Operational Volume** | Daily Scheduled VKT (Shape-based) | 1,091,488.6 km | **[B] Calculated:** Sum of polyline lengths for all scheduled trips |
| **Operational Volume** | Daily Scheduled VKT (Stop chord) | 962,556.4 km | **[B] Calculated:** Direct stop-to-stop great-circle chord sum |
| **Operational Volume** | Shape-to-Chord VKT Ratio | 1.134 | **[B] Calculated:** Measures roadway sinuosity relative to stop chords |
| **Cost Proxy** | Benchmark CPKM | Rs 83.05 / km | **[A] Observed:** BMTC FY 2024-25 aggregate operational reporting |
| **Cost Proxy** | Estimated Daily Operating Cost | Rs 90,648,128.23 | **[C] Estimated:** Daily VKT × Benchmark CPKM (Statistical proxy only) |
| **Cost Proxy** | Estimated Annual Operating Cost | Rs 33,086,566,803.95 | **[C] Estimated:** Daily cost × 365 days |
| **Route Geometry** | Average Route Circuity | 1.54 | **[B] Calculated:** Shape length / Euclidean terminal distance |
| **Route Geometry** | Median Route Circuity | 1.41 | **[B] Calculated:** Median ratio across non-loop routes |
| **Route Geometry** | Circular / Loop Routes Count | 72 | **[A] Observed:** Routes with origin-destination distance < 100m |
| **Frequency** | Average Morning Peak Headway | 39.4 min | **[B] Calculated:** Mean departure interval (07:00–10:00) |
| **Frequency** | Average Off-Peak Headway | 72.2 min | **[B] Calculated:** Mean departure interval (10:00–16:00) |
| **Network Density** | Max Routes on Single Stop | 833 | **[B] Calculated:** Majestic/KBS central terminal concentration |
| **Network Density** | Average Routes per Stop | 21.62 | **[B] Calculated:** Network-wide average stop sharing |
| **Network Density** | Max Routes on Single Corridor | 508 | **[B] Calculated:** High-concurrency trunk corridors |
| **Topology** | Graph Nodes (Transit Stops) | 9,669 | **[A] Observed:** Active urban transit stops |
| **Topology** | Graph Directed Edges | 17,118 | **[B] Calculated:** Distinct scheduled stop-to-stop corridors |
| **Topology** | Weakly Connected Components | 29 | **[B] Calculated:** Connected subgraphs |
| **Topology** | Giant Component Ratio | 99.1% | **[B] Calculated:** Fraction of stops in main connected network |

---

## 4. Key Limitations & Assumptions

1. **No Passenger Demand:** Scheduled timetables do not establish passenger boardings, overcrowding, or origin-destination travel demand.
2. **Scheduled vs Actual Times:** Dwell times and trip runtimes reflect published schedules, not real-time GPS traffic congestion.
3. **Operational Cost Proxy:** The cost figure is an aggregate statistical estimate (CPKM = Rs 83.05/km). Exact per-route fleet accounting requires depot-level telemetry not present in public GTFS.
4. **Urban Boundary:** The urban cutoff radius (50 km from Majestic) is a project assumption aligning with BMRDA boundaries that can be reconfigured.
