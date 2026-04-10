"""
Phase 1+2 Flight Test Harness — Two-Shelf Greenhouse Pass
----------------------------------------------------------
Tests drone connection and dual-shelf rack path — no camera, no API.

Pass 1: Rise to shelf-1 height (45 in), fly LEFT across the greenhouse.
Pass 2: Rise to shelf-2 height (70 in), fly RIGHT back to start.

Walk the intended path and verify distances before flying.
"""

from djitellopy import Tello
import time
import sys

# ── Config — edit these to match your greenhouse layout ──────────────────────
RACK_STOPS        = 3     # 3 rack positions per pass (each shelf)
GREENHOUSE_LENGTH = 355   # cm — one-way length of the rack row
SHELF1_HEIGHT     = 114   # cm from ground (45 in) — first-shelf pass height
SHELF2_HEIGHT     = 178   # cm from ground (70 in) — second-shelf pass height
HOVER_SECONDS     = 3     # seconds to pause at each stop (simulates capture)
BATTERY_THRESHOLD = 25    # % — aborts mission if reached mid-flight
# ─────────────────────────────────────────────────────────────────────────────

STOP_DISTANCE = GREENHOUSE_LENGTH // RACK_STOPS  # cm between rack stops


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
    print(f"  Hover/stop:        {HOVER_SECONDS} sec")
    print(f"  RTH threshold:     {BATTERY_THRESHOLD}%")
    print(f"\nDrone will fly LEFT at shelf-1 height, then RIGHT at shelf-2 height back to start.")

    confirm = input("\nReady to fly? Type 'yes' to proceed: ").strip().lower()
    return confirm == "yes"


def fly_pass(drone: Tello, direction: str, label: str) -> tuple[int, bool]:
    """
    Fly one pass across the greenhouse, stopping at each rack position.

    Args:
        direction: 'left' or 'right'
        label:     display label e.g. 'Shelf 1'

    Returns:
        steps_taken — moves completed before finish or abort
        aborted     — True if mission ended early due to low battery
    """
    move_fn = drone.move_left if direction == "left" else drone.move_right
    steps_taken = 0
    aborted = False

    for i in range(RACK_STOPS):
        battery = drone.get_battery()
        print(f"\n  [{label} | Stop {i + 1}/{RACK_STOPS}]  Battery: {battery}%")

        if battery < BATTERY_THRESHOLD:
            print(f"  Low battery ({battery}%) — aborting before this move")
            aborted = True
            break

        print(f"  Moving {direction} {STOP_DISTANCE} cm...")
        move_fn(STOP_DISTANCE)
        steps_taken += 1

        print(f"  Hovering {HOVER_SECONDS} sec (simulated capture)...")
        time.sleep(HOVER_SECONDS)
        print(f"  Stop {i + 1} complete")

    return steps_taken, aborted


def fly_mission(drone: Tello) -> dict:
    """Execute both shelf passes and return a summary dict."""
    result = {
        "shelf1_steps": 0, "shelf1_aborted": False,
        "shelf2_steps": 0, "shelf2_aborted": False,
    }

    # ── Takeoff and rise to shelf-1 height ───────────────────────────────────
    print("\nTaking off...")
    drone.takeoff()
    time.sleep(1)

    print(f"\nRising to shelf-1 height: {SHELF1_HEIGHT} cm (45 in)...")
    drone.move_up(SHELF1_HEIGHT)
    time.sleep(1)

    # ── Pass 1: fly LEFT at shelf-1 height ───────────────────────────────────
    print(f"\n── Pass 1: Shelf 1 — flying LEFT {GREENHOUSE_LENGTH} cm ──")
    steps1, aborted1 = fly_pass(drone, "left", "Shelf 1")
    result["shelf1_steps"] = steps1
    result["shelf1_aborted"] = aborted1

    # ── Rise additional height to reach shelf-2 level ────────────────────────
    rise_extra = SHELF2_HEIGHT - SHELF1_HEIGHT  # 64 cm
    print(f"\nRising to shelf-2 height: {SHELF2_HEIGHT} cm (70 in)  (+{rise_extra} cm)...")
    drone.move_up(rise_extra)
    time.sleep(1)

    # ── Pass 2: fly RIGHT at shelf-2 height (returns to start) ───────────────
    print(f"\n── Pass 2: Shelf 2 — flying RIGHT {GREENHOUSE_LENGTH} cm ──")
    steps2, aborted2 = fly_pass(drone, "right", "Shelf 2")
    result["shelf2_steps"] = steps2
    result["shelf2_aborted"] = aborted2

    # ── If pass 2 aborted early, cover remaining distance to reach home ───────
    if aborted2:
        remaining = (RACK_STOPS - steps2) * STOP_DISTANCE
        if remaining > 0:
            print(f"\n  Pass 2 aborted — moving right {remaining} cm to reach home position...")
            drone.move_right(remaining)
            time.sleep(1)

    # ── If pass 1 aborted, return home from wherever we stopped ──────────────
    if aborted1:
        return_distance = steps1 * STOP_DISTANCE
        if return_distance > 0:
            print(f"\n  Pass 1 aborted — moving right {return_distance} cm to return home...")
            drone.move_right(return_distance)
            time.sleep(1)

    print("\nLanding...")
    drone.land()

    return result


def main():
    drone = Tello()

    if not preflight_check(drone):
        print("Preflight check failed or cancelled. No flight occurred.")
        sys.exit(0)

    try:
        result = fly_mission(drone)
    except KeyboardInterrupt:
        print("\nInterrupted — attempting emergency land...")
        try:
            drone.land()
        except Exception:
            pass
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR during mission: {e}")
        print("Attempting emergency land...")
        try:
            drone.land()
        except Exception:
            pass
        raise

    # ── Mission summary ───────────────────────────────────────────────────────
    final_battery = drone.get_battery()
    print("\n── Mission Summary ──────────────────────────────────────")
    print(f"  Shelf 1 (LEFT):   {result['shelf1_steps']}/{RACK_STOPS} stops  {'ABORTED' if result['shelf1_aborted'] else 'OK'}")
    print(f"  Shelf 2 (RIGHT):  {result['shelf2_steps']}/{RACK_STOPS} stops  {'ABORTED' if result['shelf2_aborted'] else 'OK'}")
    print(f"  Final battery:    {final_battery}%")
    print("─────────────────────────────────────────────────────────")

    if result["shelf1_aborted"] or result["shelf2_aborted"]:
        print("\nTip: low battery triggered early abort. Charge fully before next flight.")


if __name__ == "__main__":
    main()
