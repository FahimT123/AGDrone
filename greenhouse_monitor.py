"""
Greenhouse Monitor — Full Pipeline
------------------------------------
Flies the rack path, queues frames at each stop, then analyzes everything
with Claude Vision after the drone has landed.

The flight loop never blocks on the API — frames are stored in memory during
the mission and sent to Claude only once the drone is safely on the ground.

Requires:
    pip install djitellopy opencv-python anthropic

Usage:
    python greenhouse_monitor.py
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import base64
import json
import sys
import time

import anthropic
import cv2
from djitellopy import Tello

# ── Config — edit these to match your greenhouse layout ──────────────────────
RACK_STOPS        = 3     # number of rack positions to visit
STOP_DISTANCE     = 50    # cm between each rack stop
RISE_HEIGHT       = 60    # cm to rise after takeoff
HOVER_SECONDS     = 3     # seconds to hover at each stop before capturing
BATTERY_THRESHOLD = 25    # % — triggers RTH if reached before any move
FRAMES_PER_STOP   = 5     # frames sampled per stop; sharpest one is kept
LOG_FILE          = "greenhouse_log.json"
CLAUDE_MODEL      = "claude-sonnet-4-6"
MAX_TOKENS        = 500
# ─────────────────────────────────────────────────────────────────────────────


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class StopCapture:
    """A single frame captured at one rack stop, waiting for analysis."""
    rack_position: int
    timestamp: str
    battery_at_capture: int
    frame: object          # numpy ndarray — kept in memory, never written to disk


@dataclass
class AnalysisResult:
    """The Claude response for one rack stop, ready to log."""
    rack_position: int
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

        # Stream can return None briefly on startup or connection hiccup
        if frame is None:
            time.sleep(0.1)
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        score = float(cv2.Laplacian(gray, cv2.CV_64F).var())

        if score > best_score:
            best_score = score
            # .copy() is critical — without it the array is a view into the
            # frame buffer and will be overwritten by the next frame
            best_frame = frame.copy()

        time.sleep(0.1)   # stay within stream update rate (~30 fps)

    if best_frame is None:
        print("  WARNING: all frames were None — stream may not be ready yet")
    else:
        print(f"  Captured frame (sharpness: {best_score:.1f})")

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
                    "text": ANALYSIS_PROMPT,
                },
            ],
        }],
    )
    return response.content[0].text


# ── Logging ───────────────────────────────────────────────────────────────────

def log_result(result: AnalysisResult) -> None:
    """Append one result to the JSONL log file and print a summary to stdout."""
    record = {
        "timestamp":          result.timestamp,
        "rack_position":      result.rack_position,
        "battery_at_capture": result.battery_at_capture,
        "analysis":           result.analysis,
    }
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(record) + "\n")

    print(f"\n── Rack {result.rack_position}  (battery at capture: {result.battery_at_capture}%) ──")
    print(result.analysis)


# ── Flight loop ───────────────────────────────────────────────────────────────

def fly_and_capture(drone: Tello) -> list[StopCapture]:
    """
    Fly the full rack path and collect one frame per stop.

    Deliberately does NOT call the Claude API — keeping the flight loop free
    of network I/O means the drone spends the minimum time in the air and the
    hover timing stays predictable. All analysis happens post-landing.

    Returns a list of StopCapture objects (one per stop reached).
    """
    captures: list[StopCapture] = []
    steps_taken = 0

    print("\nTaking off...")
    drone.takeoff()
    time.sleep(1)

    print(f"Rising {RISE_HEIGHT} cm to rack height...")
    drone.move_up(RISE_HEIGHT)
    time.sleep(1)

    for i in range(RACK_STOPS):
        battery = drone.get_battery()
        print(f"\n[Stop {i + 1}/{RACK_STOPS}]  Battery: {battery}%")

        if battery < BATTERY_THRESHOLD:
            print(f"  Low battery ({battery}%) — aborting mission before this move")
            break

        print(f"  Moving forward {STOP_DISTANCE} cm...")
        drone.move_forward(STOP_DISTANCE)
        steps_taken += 1

        # Let the drone fully stabilize before capturing — reduces motion blur
        print(f"  Stabilizing {HOVER_SECONDS} sec...")
        time.sleep(HOVER_SECONDS)

        frame = capture_best_frame(drone)
        if frame is not None:
            captures.append(StopCapture(
                rack_position=i + 1,
                timestamp=datetime.now().isoformat(),
                battery_at_capture=drone.get_battery(),
                frame=frame,
            ))

    # ── Return home ───────────────────────────────────────────────────────────
    if steps_taken > 0:
        return_distance = steps_taken * STOP_DISTANCE
        print(f"\nReturning home — moving back {return_distance} cm...")
        drone.move_back(return_distance)
        time.sleep(1)

    print("Landing...")
    drone.land()
    drone.streamoff()

    return captures


# ── Post-landing analysis ─────────────────────────────────────────────────────

def analyze_all(captures: list[StopCapture]) -> list[AnalysisResult]:
    """
    Send every queued frame to Claude in order and log each result.
    Called only after the drone has landed — no time pressure here.
    """
    if not captures:
        print("\nNo frames captured — nothing to analyze.")
        return []

    print(f"\nDrone landed. Sending {len(captures)} frame(s) to Claude...")
    client = anthropic.Anthropic()
    results: list[AnalysisResult] = []

    for capture in captures:
        print(f"  Analyzing rack {capture.rack_position}...")
        analysis_text = analyze_plant(capture, client)
        result = AnalysisResult(
            rack_position=capture.rack_position,
            timestamp=capture.timestamp,
            battery_at_capture=capture.battery_at_capture,
            analysis=analysis_text,
        )
        log_result(result)
        results.append(result)

    return results


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    drone = Tello()

    print("Connecting to Tello...")
    drone.connect()

    battery = drone.get_battery()
    print(f"Battery: {battery}%")

    min_safe = BATTERY_THRESHOLD + 15
    if battery < min_safe:
        print(f"Battery too low to safely fly ({battery}% < {min_safe}%). Charge first.")
        sys.exit(1)

    drone.streamon()
    # Give the stream 2 seconds to stabilize so the first frame isn't garbage
    time.sleep(2)

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

    # Drone is on the ground — now we can take as long as we need
    results = analyze_all(captures)

    print("\n── Mission Complete ─────────────────────────")
    print(f"  Stops captured:   {len(captures)} / {RACK_STOPS}")
    print(f"  Analyses logged:  {len(results)}")
    print(f"  Log file:         {LOG_FILE}")
    print(f"  Final battery:    {drone.get_battery()}%")
    print("─────────────────────────────────────────────")


if __name__ == "__main__":
    main()
