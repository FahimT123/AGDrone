"""
test_analysis.py — Test Claude plant-health analysis without a drone.

Verifies the full pipeline: .env loading → API key → image encoding → Claude call → result.
Run this before any real flight to confirm everything is wired up correctly.

Usage:
    python test_analysis.py                        # uses a synthetic test image
    python test_analysis.py path/to/plant.jpg      # uses your own image
"""

import sys
import os
import base64
import anthropic
import cv2
import numpy as np
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

AI_MODEL   = "claude-opus-4-7"
MAX_TOKENS = 512

ANALYSIS_PROMPT = (
    "You are analyzing a greenhouse plant bin. "
    "Assess: (1) overall health — healthy / stressed / diseased, "
    "(2) any visible issues such as yellowing, spots, wilting, or pests, "
    "(3) estimated growth stage, "
    "(4) recommended action if any. "
    "Be concise — 3 to 5 sentences."
)


def analyze_image(image_path: str) -> str:
    """Send an image to Claude and return the plant-health analysis."""
    client = anthropic.Anthropic()

    with open(image_path, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")

    response = client.messages.create(
        model=AI_MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{
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
                {"type": "text", "text": ANALYSIS_PROMPT},
            ],
        }],
    )
    return response.content[0].text


def make_synthetic_image(path: str) -> None:
    """
    Draw a simple plant-like image — green foliage above a brown pot on a dark
    background. Good enough to confirm Claude receives and processes an image.
    """
    img = np.zeros((480, 640, 3), dtype=np.uint8)

    # Background
    img[:] = (25, 25, 25)

    # Pot
    cv2.rectangle(img, (220, 320), (420, 440), (42, 82, 101), -1)

    # Stem
    cv2.rectangle(img, (308, 200), (332, 325), (34, 100, 34), -1)

    # Foliage — several overlapping ellipses for a leafy look
    cv2.ellipse(img, (320, 180), (120, 90), 0, 0, 360, (34, 139, 34), -1)
    cv2.ellipse(img, (240, 220), (80, 60), -20, 0, 360, (50, 160, 50), -1)
    cv2.ellipse(img, (400, 210), (85, 65),  20, 0, 360, (45, 150, 45), -1)
    cv2.ellipse(img, (320, 130), (70, 55),   0, 0, 360, (60, 170, 60), -1)

    # Label
    cv2.putText(img, "SYNTHETIC TEST IMAGE", (140, 475),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)

    cv2.imwrite(path, img)
    print(f"  Created synthetic plant image: {path}")


def main():
    # ── Check API key ─────────────────────────────────────────────────────────
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set.")
        print("  Open your .env file and add:  ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)
    print(f"API key loaded OK  ({api_key[:12]}...)")

    # ── Resolve image path ────────────────────────────────────────────────────
    if len(sys.argv) > 1:
        image_path = sys.argv[1]
        if not Path(image_path).exists():
            print(f"ERROR: File not found: {image_path}")
            sys.exit(1)
        print(f"Using image: {image_path}")
    else:
        image_path = "test_plant.jpg"
        print("No image path provided — generating a synthetic plant image.")
        make_synthetic_image(image_path)

    # ── Run analysis ──────────────────────────────────────────────────────────
    print(f"\nSending to Claude ({AI_MODEL}) for plant-health analysis...")

    try:
        result = analyze_image(image_path)
    except anthropic.AuthenticationError:
        print("\nERROR: API key is invalid or expired.")
        print("  Get a valid key at https://console.anthropic.com")
        sys.exit(1)
    except anthropic.APIConnectionError:
        print("\nERROR: Could not reach the Anthropic API.")
        print("  Check your internet connection (note: Tello Wi-Fi blocks internet — disconnect first).")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)

    print("\n── Claude Analysis ──────────────────────────────────────")
    print(result)
    print("─────────────────────────────────────────────────────────")
    print("\nAll checks passed — API connection and image analysis pipeline are working.")


if __name__ == "__main__":
    main()
