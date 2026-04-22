"""
Greenhouse Monitor — Post-Landing Analysis Pipeline
-----------------------------------------------------
Flies the two-shelf rack path and queues frames at each stop, then
analyzes everything with Claude Vision after the drone has landed.

The flight loop never blocks on the API — frames are stored in memory
during the mission and sent to Claude only once the drone is safely on
the ground. This keeps hover timing consistent and minimizes air time.

Requires:
    pip install djitellopy opencv-python anthropic python-dotenv

Set your Anthropic API key in .env:
    ANTHROPIC_API_KEY=sk-ant-...

Usage:
    python greenhouse_monitor.py
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import base64
import json
import os
import sys
import time

import anthropic
import cv2
from djitellopy import Tello
from dotenv import load_dotenv

load_dotenv()

# ── Config — edit these to match your greenhouse layout ──────────────────────
RACK_STOPS        = 3     # 3 rack positions per pass (each shelf)
GREENHOUSE_LENGTH = 355   # cm — one-way length of the rack row
SHELF1_HEIGHT     = 114   # cm rise after takeoff to reach shelf 1 (45 in)
SHELF2_HEIGHT     = 178   # cm total rise from ground to shelf 2 (70 in)
HOVER_SECONDS     = 3     # seconds to hover at each stop before capturing
BATTERY_THRESHOLD = 25    # % — aborts mission if reached mid-flight
FRAMES_PER_STOP   = 5     # frames sampled per stop; sharpest one is kept
LOG_FILE          = "greenhouse_log.json"
CLAUDE_MODEL      = "claude-opus-4-7"
MAX_TOKENS        = 500
# ─────────────────────────────────────────────────────────────────────────────

STOP_DISTANCE = GREENHOUSE_LENGTH // RACK_STOPS  # 118 cm between stops


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class StopCapture:
    """A single frame captured at one rack stop, waiting for analysis."""
    shelf: int
    rack_position: int
    label: str
    timestamp: str
    battery_at_capture: int
    frame: object  # numpy ndarray — kept in memory, never written to disk


@dataclass
class AnalysisResult:
    """The Claude response for one rack stop, ready to log."""
    shelf: int
    rack_position: int
    label: str
    timestamp: str
    battery_at_capture: int
    analysis: str


# ── Frame capture ─────────────────────────────────────────────────────────────

def capture_best_frame(drone: Tello) -> Optional[object]:
    """
    Sample FRAMES_PER_STOP frames from the live stream and return the sharpest
    one, scored by Laplacian variance (high variance = sharp edges = good focus).
    Returns None if the stream yields no usable frames.
    """
    frame_reader = drone.get_frame_read()
    best_frame = None
    best_score = -1.0

    for _ in range(FRAMES_PER_STOP):
        frame = frame_reader.frame
        if frame is None:
            time.sleep(0.1)
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if score > best_score:
            best_score = score
            best_frame = frame.copy()  # .copy() prevents overwrite by next frame
        time.sleep(0.1)

    if best_frame is None:
        print("    WARNING: all frames were None — stream may not be ready")
    else:
        print(f"    Captured frame  (sharpness score: {best_score:.1f})")

    return best_frame


# ── Claude analysis ───────────────────────────────────────────────────────────

ANALYSIS_PROMPT = """\
Analyze this greenhouse plant image and provide:
1. Overall health status (Healthy / At Risk / Unhealthy)
2. Leaf color and any discoloration
3. Signs of disease or pests
4. Moisture stress indicators
5. Any recommended actions

Be concise and practical."""


def analyze_plant(capture: StopCapture, client: anthropic.Anthropic) -> str:
    """Encode a captured frame and send it to Claude. Returns the response text."""
    _, buffer = cv2.imencode(".jpg", capture.frame)
    image_b64 = base64.b64encode(buffer).decode()

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_b64,
                    },
                },
                {
                    "type": "text",
                    "text": f"You are analyzing {capture.label} in a greenhouse. {ANALYSIS_PROMPT}",
                },
            ],
        }],
    )
    return response.content[0].text


# ── Logging ───────────────────────────────────────────────────────────────────

def log_result(result: AnalysisResult) -> None:
    """Append one result to the JSONL log file and print a summary."""
    record = {
        "timestamp":          result.timestamp,
        "shelf":              result.shelf,
        "rack_position":      result.rack_position,
        "label":              result.label,
        "battery_at_capture": result.battery_at_capture,
        "analysis":           result.analysis,
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")

    print(f"\n── {result.label}  (battery at capture: {result.battery_at_capture}%) ──")
    print(result.analysis)


# ── Flight loop ───────────────────────────────────────────────────────────────

def fly_pass_and_capture(
    drone: Tello,
    direction: str,
    shelf: int,
) -> tuple[list[StopCapture], bool]:
    """
    Fly one pass across the greenhouse collecting frames — no API calls.

    Returns:
        captures — list of StopCapture objects for this pass
        aborted  — True if the pass ended early due to low battery
    """
    move_fn = drone.move_left if direction == "left" else drone.move_right
    captures = []
    steps_taken = 0
    aborted = False
    shelf_label = f"Shelf {shelf}"

    for i in range(RACK_STOPS):
        battery = drone.get_battery()
        stop_label = f"{shelf_label} Stop {i + 1}"
        print(f"\n  [{stop_label}]  Battery: {battery}%")

        if battery < BATTERY_THRESHOLD:
            print(f"  Low battery ({battery}%) — aborting pass before this move")
            aborted = True
            break

        print(f"  Moving {direction} {STOP_DISTANCE} cm...")
        move_fn(STOP_DISTANCE)
        steps_taken += 1

        print(f"  Stabilizing {HOVER_SECONDS} sec before capture...")
        time.sleep(HOVER_SECONDS)

        frame = capture_best_frame(drone)
        if frame is not None:
            captures.append(StopCapture(
                shelf=shelf,
                rack_position=i + 1,
                label=stop_label,
                timestamp=datetime.now().isoformat(),
                battery_at_capture=drone.get_battery(),
                frame=frame,
            ))
        print(f"  Stop {i + 1} complete")

    return captures, aborted, steps_taken


def fly_and_capture(drone: Tello) -> list[StopCapture]:
    """
    Fly the full two-shelf path and collect frames at every stop.
    No Claude API calls here — all analysis happens after landing.

    Returns a list of all StopCapture objects collected across both passes.
    """
    all_captures = []

    print("\nTaking off...")
    drone.takeoff()
    time.sleep(1)

    # ── Pass 1: rise to shelf-1 height, fly LEFT ──────────────────────────────
    print(f"\nRising to shelf-1 height: {SHELF1_HEIGHT} cm (45 in)...")
    drone.move_up(SHELF1_HEIGHT)
    time.sleep(1)

    print(f"\n── Pass 1: Shelf 1 — flying LEFT {GREENHOUSE_LENGTH} cm ──")
    captures1, aborted1, steps1 = fly_pass_and_capture(drone, "left", shelf=1)
    all_captures.extend(captures1)

    if aborted1:
        if steps1 > 0:
            print(f"\n  Pass 1 aborted — moving right {steps1 * STOP_DISTANCE} cm to reach home...")
            drone.move_right(steps1 * STOP_DISTANCE)
            time.sleep(1)
        print("\nLanding...")
        drone.land()
        drone.streamoff()
        return all_captures

    # ── Pass 2: rise to shelf-2 height, fly RIGHT (returns to start) ──────────
    rise_extra = SHELF2_HEIGHT - SHELF1_HEIGHT  # 64 cm
    print(f"\nRising to shelf-2 height: {SHELF2_HEIGHT} cm (70 in)  (+{rise_extra} cm)...")
    drone.move_up(rise_extra)
    time.sleep(1)

    print(f"\n── Pass 2: Shelf 2 — flying RIGHT {GREENHOUSE_LENGTH} cm ──")
    captures2, aborted2, steps2 = fly_pass_and_capture(drone, "right", shelf=2)
    all_captures.extend(captures2)

    if aborted2:
        remaining = (RACK_STOPS - steps2) * STOP_DISTANCE
        if remaining > 0:
            print(f"\n  Pass 2 aborted — moving right {remaining} cm to reach home...")
            drone.move_right(remaining)
            time.sleep(1)

    print("\nLanding...")
    drone.land()
    drone.streamoff()

    return all_captures


# ── Post-landing analysis ─────────────────────────────────────────────────────

def analyze_all(captures: list[StopCapture]) -> list[AnalysisResult]:
    """
    Send every queued frame to Claude in order and log each result.
    Called only after the drone has landed — no time pressure here.
    """
    if not captures:
        print("\nNo frames captured — nothing to analyze.")
        return []

    print(f"\nDrone landed. Sending {len(captures)} frame(s) to Claude ({CLAUDE_MODEL})...")
    client = anthropic.Anthropic()
    results: list[AnalysisResult] = []

    for capture in captures:
        print(f"\n  Analyzing {capture.label}...")
        analysis_text = analyze_plant(capture, client)
        result = AnalysisResult(
            shelf=capture.shelf,
            rack_position=capture.rack_position,
            label=capture.label,
            timestamp=capture.timestamp,
            battery_at_capture=capture.battery_at_capture,
            analysis=analysis_text,
        )
        log_result(result)
        results.append(result)

    return results


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set. Fill in your .env file.")
        sys.exit(1)

    drone = Tello()

    print("Connecting to Tello...")
    drone.connect()

    battery = drone.get_battery()
    print(f"Battery: {battery}%")

    min_safe = BATTERY_THRESHOLD + 15
    if battery < min_safe:
        print(f"Battery too low ({battery}% < {min_safe}%). Charge first.")
        sys.exit(1)

    print(f"\nMission plan:")
    print(f"  Greenhouse length: {GREENHOUSE_LENGTH} cm  ({GREENHOUSE_LENGTH / 100:.2f} m)")
    print(f"  Rack stops/pass:   {RACK_STOPS}  ({STOP_DISTANCE} cm apart)")
    print(f"  Shelf 1 height:    {SHELF1_HEIGHT} cm  (45 in) — fly LEFT")
    print(f"  Shelf 2 height:    {SHELF2_HEIGHT} cm  (70 in) — fly RIGHT")
    print(f"  Hover/stop:        {HOVER_SECONDS} sec + {FRAMES_PER_STOP}-frame capture")
    print(f"  AI model:          {CLAUDE_MODEL}  (analysis runs after landing)")
    print(f"  RTH threshold:     {BATTERY_THRESHOLD}%")

    confirm = input("\nReady to fly? Type 'yes' to proceed: ").strip().lower()
    if confirm != "yes":
        print("Cancelled. No flight occurred.")
        sys.exit(0)

    drone.streamon()
    time.sleep(2)  # let stream stabilize before first frame

    captures: list[StopCapture] = []

    try:
        captures = fly_and_capture(drone)
    except KeyboardInterrupt:
        print("\nInterrupted — attempting emergency land...")
        try:
            drone.land()
            drone.streamoff()
        except Exception:
            pass
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR during flight: {e}")
        print("Attempting emergency land...")
        try:
            drone.land()
            drone.streamoff()
        except Exception:
            pass
        raise

    results = analyze_all(captures)

    final_battery = drone.get_battery()
    print("\n── Mission Complete ─────────────────────────────────────")
    print(f"  Stops captured:   {len(captures)} / {RACK_STOPS * 2}")
    print(f"  Analyses logged:  {len(results)}")
    print(f"  Log file:         {LOG_FILE}")
    print(f"  Final battery:    {final_battery}%")
    print("─────────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
