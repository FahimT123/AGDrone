"""
Greenhouse Drone — Vision Analysis Harness
-------------------------------------------
At each rack stop the drone captures a frame and sends it to Claude
for plant-health analysis. Results are saved to a timestamped session folder.

Requires:
    pip install djitellopy anthropic opencv-python

Set your Anthropic API key before running:
    Windows:   set ANTHROPIC_API_KEY=sk-...
    Mac/Linux: export ANTHROPIC_API_KEY=sk-...
"""

from djitellopy import Tello
import anthropic
import cv2
import base64
import json
import os
import time
import sys
from datetime import datetime
from pathlib import Path

# ── Config — edit these to match your greenhouse layout ──────────────────────
RACK_STOPS        = 3     # 3 rack positions per pass (each shelf)
GREENHOUSE_LENGTH = 355   # cm — one-way length of the rack row
SHELF1_HEIGHT     = 114   # cm from ground (45 in) — first-shelf pass height
SHELF2_HEIGHT     = 178   # cm from ground (70 in) — second-shelf pass height
HOVER_SECONDS     = 3     # seconds to hover before capture at each stop
BATTERY_THRESHOLD = 25    # % — aborts mission if reached mid-flight
CAPTURES_DIR      = "captures"  # root folder for saved images and reports
AI_MODEL          = "claude-opus-4-7"
# ─────────────────────────────────────────────────────────────────────────────

STOP_DISTANCE = GREENHOUSE_LENGTH // RACK_STOPS  # 118 cm between stops


def preflight_check(drone: Tello) -> bool:
    """Connect, read battery, print mission plan, ask for confirmation."""
    print("Connecting to Tello...")
    drone.connect()

    battery = drone.get_battery()
    print(f"Battery: {battery}%")

    min_safe = BATTERY_THRESHOLD + 15
    if battery < min_safe:
        print(f"Battery too low ({battery}% < {min_safe}%). Charge first.")
        return False

    print(f"\nMission plan:")
    print(f"  Greenhouse length: {GREENHOUSE_LENGTH} cm  ({GREENHOUSE_LENGTH / 100:.2f} m)")
    print(f"  Rack stops/pass:   {RACK_STOPS}  ({STOP_DISTANCE} cm apart)")
    print(f"  Shelf 1 height:    {SHELF1_HEIGHT} cm  (45 in) — fly LEFT")
    print(f"  Shelf 2 height:    {SHELF2_HEIGHT} cm  (70 in) — fly RIGHT")
    print(f"  Hover/stop:        {HOVER_SECONDS} sec + capture + AI analysis")
    print(f"  AI model:          {AI_MODEL}")
    print(f"  RTH threshold:     {BATTERY_THRESHOLD}%")
    print(f"\nDrone will fly LEFT at shelf-1 height, then RIGHT at shelf-2 height back to start.")

    confirm = input("\nReady to fly? Type 'yes' to proceed: ").strip().lower()
    return confirm == "yes"


def capture_frame(drone: Tello, save_path: str) -> bool:
    """Grab current video frame and save it as a JPEG."""
    frame_reader = drone.get_frame_read()
    frame = frame_reader.frame
    if frame is None:
        return False
    success, _ = cv2.imwrite(save_path, frame)
    return success


def analyze_image(client: anthropic.Anthropic, image_path: str, label: str) -> str:
    """Send a captured frame to Claude and return a plant-health assessment."""
    with open(image_path, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")

    response = client.messages.create(
        model=AI_MODEL,
        max_tokens=512,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": (
                            f"You are analyzing a greenhouse plant bin at {label}. "
                            "Assess: (1) overall health — healthy / stressed / diseased, "
                            "(2) any visible issues such as yellowing, spots, wilting, or pests, "
                            "(3) estimated growth stage, "
                            "(4) recommended action if any. "
                            "Be concise — 3 to 5 sentences."
                        ),
                    },
                ],
            }
        ],
    )
    return response.content[0].text


def capture_and_analyze(
    drone: Tello,
    client: anthropic.Anthropic,
    label: str,
    session_dir: Path,
) -> dict:
    """Capture a frame at the current stop, analyze it, and return a result dict."""
    filename = f"{label.replace(' ', '_').lower()}.jpg"
    image_path = session_dir / filename

    print(f"    Capturing image...")
    captured = capture_frame(drone, str(image_path))

    if not captured:
        print(f"    WARNING: frame capture failed — skipping analysis")
        return {"label": label, "image": None, "analysis": "capture failed"}

    print(f"    Analyzing with Claude ({AI_MODEL})...")
    try:
        analysis = analyze_image(client, str(image_path), label)
        print(f"    {analysis}")
    except Exception as e:
        analysis = f"analysis error: {e}"
        print(f"    WARNING: {analysis}")

    return {"label": label, "image": str(image_path), "analysis": analysis}


def fly_pass(
    drone: Tello,
    client: anthropic.Anthropic,
    direction: str,
    shelf_label: str,
    session_dir: Path,
) -> tuple[int, bool, list]:
    """
    Fly one pass across the greenhouse.
    At each stop: hover, capture a frame, send to Claude for analysis.

    Returns:
        steps_taken, aborted, results_list
    """
    move_fn = drone.move_left if direction == "left" else drone.move_right
    steps_taken = 0
    aborted = False
    results = []

    for i in range(RACK_STOPS):
        battery = drone.get_battery()
        stop_label = f"{shelf_label} Stop {i + 1}"
        print(f"\n  [{stop_label}]  Battery: {battery}%")

        if battery < BATTERY_THRESHOLD:
            print(f"  Low battery ({battery}%) — aborting before this move")
            aborted = True
            break

        print(f"  Moving {direction} {STOP_DISTANCE} cm...")
        move_fn(STOP_DISTANCE)
        steps_taken += 1

        print(f"  Hovering {HOVER_SECONDS} sec before capture...")
        time.sleep(HOVER_SECONDS)

        result = capture_and_analyze(drone, client, stop_label, session_dir)
        results.append(result)
        print(f"  Stop {i + 1} complete")

    return steps_taken, aborted, results


def fly_mission(drone: Tello, client: anthropic.Anthropic, session_dir: Path) -> dict:
    """Execute both shelf passes and return a full summary dict."""
    summary = {
        "shelf1_steps": 0, "shelf1_aborted": False, "shelf1_results": [],
        "shelf2_steps": 0, "shelf2_aborted": False, "shelf2_results": [],
    }

    print("\nStarting video stream...")
    drone.streamon()
    time.sleep(1)  # let stream stabilize before first frame

    print("\nTaking off...")
    drone.takeoff()
    time.sleep(1)

    print(f"\nRising to shelf-1 height: {SHELF1_HEIGHT} cm (45 in)...")
    drone.move_up(SHELF1_HEIGHT)
    time.sleep(1)

    # ── Pass 1: fly LEFT at shelf-1 height ───────────────────────────────────
    print(f"\n── Pass 1: Shelf 1 — flying LEFT {GREENHOUSE_LENGTH} cm ──")
    steps1, aborted1, results1 = fly_pass(drone, client, "left", "Shelf 1", session_dir)
    summary["shelf1_steps"] = steps1
    summary["shelf1_aborted"] = aborted1
    summary["shelf1_results"] = results1

    # ── Rise additional height to shelf-2 level ───────────────────────────────
    rise_extra = SHELF2_HEIGHT - SHELF1_HEIGHT  # 64 cm
    print(f"\nRising to shelf-2 height: {SHELF2_HEIGHT} cm (70 in)  (+{rise_extra} cm)...")
    drone.move_up(rise_extra)
    time.sleep(1)

    # ── Pass 2: fly RIGHT at shelf-2 height (returns to start) ───────────────
    print(f"\n── Pass 2: Shelf 2 — flying RIGHT {GREENHOUSE_LENGTH} cm ──")
    steps2, aborted2, results2 = fly_pass(drone, client, "right", "Shelf 2", session_dir)
    summary["shelf2_steps"] = steps2
    summary["shelf2_aborted"] = aborted2
    summary["shelf2_results"] = results2

    # ── Return home if either pass aborted early ──────────────────────────────
    if aborted2:
        remaining = (RACK_STOPS - steps2) * STOP_DISTANCE
        if remaining > 0:
            print(f"\n  Pass 2 aborted — moving right {remaining} cm to reach home...")
            drone.move_right(remaining)
            time.sleep(1)

    if aborted1:
        return_dist = steps1 * STOP_DISTANCE
        if return_dist > 0:
            print(f"\n  Pass 1 aborted — moving right {return_dist} cm to reach home...")
            drone.move_right(return_dist)
            time.sleep(1)

    print("\nLanding...")
    drone.land()
    drone.streamoff()

    return summary


def save_report(summary: dict, session_dir: Path) -> None:
    """Write a JSON report and a human-readable text summary to the session folder."""
    with open(session_dir / "report.json", "w") as f:
        json.dump(summary, f, indent=2)

    lines = ["Greenhouse Drone Mission Report", "=" * 40, ""]
    for shelf_key, results_key, label in [
        ("shelf1", "shelf1_results", "Shelf 1 (LEFT)"),
        ("shelf2", "shelf2_results", "Shelf 2 (RIGHT)"),
    ]:
        status = "ABORTED" if summary[f"{shelf_key}_aborted"] else "OK"
        lines.append(f"{label} — {summary[f'{shelf_key}_steps']}/{RACK_STOPS} stops — {status}")
        lines.append("-" * 30)
        for r in summary[results_key]:
            lines.append(f"\n{r['label']}:")
            lines.append(r["analysis"])
        lines.append("")

    lines.append(f"Final battery: {summary.get('final_battery', 'N/A')}%")

    with open(session_dir / "report.txt", "w") as f:
        f.write("\n".join(lines))

    print(f"\nReport saved to: {session_dir}/")


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.")
        print("  Windows:   set ANTHROPIC_API_KEY=sk-...")
        print("  Mac/Linux: export ANTHROPIC_API_KEY=sk-...")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    session_dir = Path(CAPTURES_DIR) / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    session_dir.mkdir(parents=True, exist_ok=True)

    drone = Tello()

    if not preflight_check(drone):
        print("Preflight check failed or cancelled. No flight occurred.")
        sys.exit(0)

    try:
        summary = fly_mission(drone, client, session_dir)
    except KeyboardInterrupt:
        print("\nInterrupted — attempting emergency land...")
        try:
            drone.land()
            drone.streamoff()
        except Exception:
            pass
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR during mission: {e}")
        print("Attempting emergency land...")
        try:
            drone.land()
            drone.streamoff()
        except Exception:
            pass
        raise

    final_battery = drone.get_battery()
    print("\n── Mission Summary ──────────────────────────────────────")
    print(f"  Shelf 1 (LEFT):   {summary['shelf1_steps']}/{RACK_STOPS} stops  {'ABORTED' if summary['shelf1_aborted'] else 'OK'}")
    print(f"  Shelf 2 (RIGHT):  {summary['shelf2_steps']}/{RACK_STOPS} stops  {'ABORTED' if summary['shelf2_aborted'] else 'OK'}")
    print(f"  Final battery:    {final_battery}%")
    print("─────────────────────────────────────────────────────────")

    summary["final_battery"] = final_battery
    save_report(summary, session_dir)

    if summary["shelf1_aborted"] or summary["shelf2_aborted"]:
        print("\nTip: low battery triggered early abort. Charge fully before next flight.")


if __name__ == "__main__":
    main()
