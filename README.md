# AGDrone — Autonomous Greenhouse Drone Monitor

An autonomous drone system that flies a DJI Tello through a two-shelf greenhouse rack, captures images of plant bins at each stop, and uses **Claude AI (claude-opus-4-7)** to assess plant health in real time. Designed to scan a 355 cm greenhouse row across two shelf heights, logging a full health report after every flight.

---

## Table of Contents

- [Overview](#overview)
- [Hardware Requirements](#hardware-requirements)
- [Software Requirements](#software-requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Project Files](#project-files)
- [Mission Flow](#mission-flow)
- [Running the Drone](#running-the-drone)
- [AI Analysis](#ai-analysis)
- [Output and Reports](#output-and-reports)
- [Safety Guidelines](#safety-guidelines)
- [Troubleshooting](#troubleshooting)

---

## Overview

AGDrone automates plant health monitoring in a two-shelf greenhouse rack. On each flight the drone:

1. Takes off and rises to the first shelf height (45 inches / 114 cm)
2. Flies **left** across the full 355 cm greenhouse row, stopping at **3 rack positions**
3. At each stop, captures an image of the plant bin and sends it to Claude for analysis
4. Rises to the second shelf height (70 inches / 178 cm)
5. Flies **right** back across the 355 cm row, stopping at the same 3 rack positions on the upper shelf
6. Lands at the starting position
7. Saves a full `report.json` and `report.txt` to a timestamped session folder

There are two scripts in this repo targeting different use cases:

| Script | Purpose |
|---|---|
| `flight_test.py` | Full two-shelf pass with **live Claude analysis at each stop** during flight |
| `greenhouse_monitor.py` | Captures frames during flight, analyzes with Claude **after landing** (non-blocking flight loop) |

---

## Hardware Requirements

| Item | Details |
|---|---|
| **DJI Tello drone** | Any standard Tello or Tello EDU |
| **Charged Tello battery** | Minimum 40% recommended before flight (enforced in code) |
| **Wi-Fi capable laptop/PC** | Must connect to the Tello's Wi-Fi network before running |
| **Clear flight area** | 355 cm horizontal clearance + at least 180 cm vertical clearance |

> **Note:** The Tello has a typical flight time of 13 minutes. A full two-shelf mission takes approximately 3–5 minutes depending on hover time. Always start with a fully charged battery.

---

## Software Requirements

| Package | Version | Purpose |
|---|---|---|
| Python | 3.8 or higher | Runtime |
| `djitellopy` | latest | Tello SDK — flight commands and video stream |
| `anthropic` | latest | Claude API client for AI plant analysis |
| `opencv-python` | latest | Frame capture and image processing |

All Python dependencies are listed in `requirements.txt`.

You also need a valid **Anthropic API key**. Get one at [console.anthropic.com](https://console.anthropic.com).

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/FahimT123/AGDrone.git
cd AGDrone
```

### 2. Install Python dependencies

```bash
pip install -r requirements.txt
```

Or install manually:

```bash
pip install djitellopy anthropic opencv-python
```

### 3. Set your Anthropic API key

**Windows:**
```cmd
set ANTHROPIC_API_KEY=sk-ant-...
```

**Mac / Linux:**
```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

> To make this permanent on Windows, add it to your System Environment Variables. On Mac/Linux, add the export line to your `~/.bashrc` or `~/.zshrc`.

### 4. Connect to the Tello

Power on your Tello drone. On your computer, connect to the Tello's Wi-Fi network (named something like `TELLO-XXXXXX`) before running any script. The drone communicates over this Wi-Fi — your internet connection will drop while connected.

---

## Configuration

Both scripts share the same set of config constants at the top of the file. Edit these to match your physical greenhouse before flying.

```python
RACK_STOPS        = 3     # number of rack positions per shelf pass
GREENHOUSE_LENGTH = 355   # cm — total one-way length of the rack row
SHELF1_HEIGHT     = 114   # cm from ground (45 inches) — first shelf
SHELF2_HEIGHT     = 178   # cm from ground (70 inches) — second shelf
HOVER_SECONDS     = 3     # seconds to stabilize at each stop before capture
BATTERY_THRESHOLD = 25    # % — drone aborts mission if battery drops below this
```

**Derived value (calculated automatically):**
```python
STOP_DISTANCE = GREENHOUSE_LENGTH // RACK_STOPS  # = 118 cm between stops
```

> Walk the greenhouse path manually with a tape measure before flying. Verify that `SHELF1_HEIGHT` and `SHELF2_HEIGHT` actually clear your shelf structures.

---

## Project Files

### `flight_test.py` — Live Analysis Harness

The primary script. Performs the full two-shelf mission and calls the Claude API **in real time at each stop** while the drone is still in the air.

**Key functions:**

| Function | Description |
|---|---|
| `preflight_check()` | Connects to Tello, reads battery, prints mission plan, asks for `yes` confirmation |
| `capture_frame()` | Grabs the current video frame and saves it as a JPEG |
| `analyze_image()` | Sends the JPEG to Claude and returns a plant health assessment |
| `capture_and_analyze()` | Orchestrates capture → analysis → result at a single stop |
| `fly_pass()` | Flies one full pass (left or right), calling `capture_and_analyze` at each rack stop |
| `fly_mission()` | Runs both shelf passes in sequence; handles return-home on abort |
| `save_report()` | Writes `report.json` and `report.txt` to the session folder |

**Session output structure:**
```
captures/
└── 2025-06-01_14-30-00/
    ├── shelf_1_stop_1.jpg
    ├── shelf_1_stop_2.jpg
    ├── shelf_1_stop_3.jpg
    ├── shelf_2_stop_1.jpg
    ├── shelf_2_stop_2.jpg
    ├── shelf_2_stop_3.jpg
    ├── report.json
    └── report.txt
```

---

### `greenhouse_monitor.py` — Post-Landing Analysis Monitor

An alternative pipeline designed to keep the flight loop as clean and fast as possible. Frames are captured and stored in memory during the mission, and **all Claude API calls happen after the drone has landed safely**.

**Key design difference from `flight_test.py`:** The flight loop has zero network I/O. This keeps hover timing predictable and eliminates any risk of an API delay affecting drone behavior mid-flight.

**Key functions:**

| Function | Description |
|---|---|
| `capture_best_frame()` | Samples 5 frames per stop and returns the sharpest one (scored by Laplacian variance) |
| `analyze_plant()` | Sends a stored frame to Claude after landing |
| `fly_and_capture()` | Flies the full rack path collecting one `StopCapture` per stop |
| `analyze_all()` | Sends every queued frame to Claude in sequence after landing |
| `log_result()` | Appends each result to `greenhouse_log.json` (JSONL format) |

> Use `greenhouse_monitor.py` if you want the safest possible flight behavior, or if your network connection is slow. Use `flight_test.py` if you want to see live analysis printed during the flight.

---

## Mission Flow

```
[Power on Tello] → [Connect PC to Tello Wi-Fi] → [Run script]

Preflight:
  Connect → Read battery → Print mission plan → Confirm 'yes'

Flight:
  Takeoff
  │
  ├─ Rise to 114 cm (45 in) ──────────────────────────────────────────────────┐
  │                                                                             │
  │  PASS 1 — Shelf 1 (fly LEFT)                                              │
  │  ┌─────────────┬─────────────┬─────────────┐                              │
  │  │  Stop 1     │  Stop 2     │  Stop 3     │  ← 118 cm between each       │
  │  │  Hover 3s   │  Hover 3s   │  Hover 3s   │                              │
  │  │  Capture    │  Capture    │  Capture    │                              │
  │  │  Analyze    │  Analyze    │  Analyze    │                              │
  │  └─────────────┴─────────────┴─────────────┘                              │
  │                                                                             │
  ├─ Rise +64 cm → 178 cm (70 in) ────────────────────────────────────────────┤
  │                                                                             │
  │  PASS 2 — Shelf 2 (fly RIGHT, returning to start)                         │
  │  ┌─────────────┬─────────────┬─────────────┐                              │
  │  │  Stop 3     │  Stop 2     │  Stop 1     │  ← mirrors Pass 1 path       │
  │  │  Hover 3s   │  Hover 3s   │  Hover 3s   │                              │
  │  │  Capture    │  Capture    │  Capture    │                              │
  │  │  Analyze    │  Analyze    │  Analyze    │                              │
  │  └─────────────┴─────────────┴─────────────┘                              │
  │                                                                             │
  Land ←──────────────────────────────────────────────────────────────────────┘

Post-flight:
  Save report.json + report.txt → Print mission summary
```

**Low battery abort:** If battery drops below `BATTERY_THRESHOLD` (25%) at any point before a move, the drone skips the remaining stops and flies back to the home position before landing.

---

## Running the Drone

### Using `flight_test.py` (recommended for most cases)

```bash
python flight_test.py
```

You will see the mission plan printed and be asked to type `yes` to confirm:

```
Mission plan:
  Greenhouse length: 355 cm  (3.55 m)
  Rack stops/pass:   3  (118 cm apart)
  Shelf 1 height:    114 cm  (45 in) — fly LEFT
  Shelf 2 height:    178 cm  (70 in) — fly RIGHT
  Hover/stop:        3 sec + capture + AI analysis
  AI model:          claude-opus-4-7
  RTH threshold:     25%

Drone will fly LEFT at shelf-1 height, then RIGHT at shelf-2 height back to start.

Ready to fly? Type 'yes' to proceed:
```

To abort at any time during flight, press **Ctrl+C**. The script will attempt an emergency landing.

### Using `greenhouse_monitor.py`

```bash
python greenhouse_monitor.py
```

Same preflight, but analysis prints to the terminal after the drone has landed. Results are appended to `greenhouse_log.json` in JSONL format.

---

## AI Analysis

Both scripts use the **Anthropic Claude API** with the `claude-opus-4-7` model (vision-capable).

At each stop, the drone's current video frame is:
1. Captured via the Tello's onboard camera (720p)
2. Encoded as a base64 JPEG
3. Sent to Claude along with this prompt:

> *"You are analyzing a greenhouse plant bin at [Shelf X Stop Y]. Assess: (1) overall health — healthy / stressed / diseased, (2) any visible issues such as yellowing, spots, wilting, or pests, (3) estimated growth stage, (4) recommended action if any. Be concise — 3 to 5 sentences."*

**Example Claude response:**
```
The plants appear healthy overall with vibrant green coloration and upright posture.
No visible signs of pest damage or disease lesions on the observed leaves.
Growth stage appears to be mid-vegetative based on leaf development.
Moisture levels look adequate — no wilting or curling observed.
No immediate action required; continue current watering and light schedule.
```

---

## Output and Reports

### `flight_test.py` output

Each run creates a timestamped folder under `captures/`:

```
captures/2025-06-01_14-30-00/
├── shelf_1_stop_1.jpg    ← captured frame at Shelf 1, Stop 1
├── shelf_1_stop_2.jpg
├── shelf_1_stop_3.jpg
├── shelf_2_stop_1.jpg
├── shelf_2_stop_2.jpg
├── shelf_2_stop_3.jpg
├── report.json           ← full structured data
└── report.txt            ← human-readable summary
```

**`report.json` structure:**
```json
{
  "shelf1_steps": 3,
  "shelf1_aborted": false,
  "shelf1_results": [
    {
      "label": "Shelf 1 Stop 1",
      "image": "captures/2025-06-01_14-30-00/shelf_1_stop_1.jpg",
      "analysis": "Plants appear healthy..."
    }
  ],
  "shelf2_steps": 3,
  "shelf2_aborted": false,
  "shelf2_results": [...],
  "final_battery": 62
}
```

### `greenhouse_monitor.py` output

Results are appended to `greenhouse_log.json` in JSONL format (one JSON object per line):

```json
{"timestamp": "2025-06-01T14:35:22", "rack_position": 1, "battery_at_capture": 78, "analysis": "..."}
{"timestamp": "2025-06-01T14:35:45", "rack_position": 2, "battery_at_capture": 74, "analysis": "..."}
```

---

## Safety Guidelines

- **Always do a manual walkthrough** of the drone path before the first flight. Verify that `SHELF1_HEIGHT`, `SHELF2_HEIGHT`, and `STOP_DISTANCE` match your actual greenhouse.
- **Never fly with a battery below 40%** — the preflight check enforces a minimum of `BATTERY_THRESHOLD + 15%` (40% by default).
- **Keep a hand near the Tello** during early test flights. Press Ctrl+C to trigger emergency landing at any time.
- **Remove obstacles** from the flight path. The Tello has no collision avoidance — it will fly into objects.
- **The return-home is dead reckoning** — it moves back exactly as far as it flew forward, assuming perfectly straight flight. Verify this visually on early flights before trusting it fully.
- **Never fly over people or animals.**

---

## Troubleshooting

| Problem | Likely cause | Fix |
|---|---|---|
| `Connection refused` on startup | PC not connected to Tello Wi-Fi | Connect to `TELLO-XXXXXX` Wi-Fi before running |
| `ANTHROPIC_API_KEY not set` | Environment variable missing | Run `set ANTHROPIC_API_KEY=sk-...` in your terminal |
| All captured frames are black | Video stream not ready | Increase the `time.sleep()` after `drone.streamon()` |
| Drone drifts left/right | Indoor air currents or floor surface | Run on a hard flat surface; block drafts |
| `Battery too low` on preflight | Battery not charged enough | Charge Tello battery to at least 40% before flying |
| Analysis says `capture failed` | Frame was `None` from stream | Give the stream more time to stabilize; increase initial sleep |
| Drone overshoots rack positions | `STOP_DISTANCE` is too large | Measure the actual distance between racks and update config |

---

## License

This project is for personal and research use. The DJI Tello SDK is subject to DJI's terms of service. The Claude API is subject to Anthropic's usage policies.
