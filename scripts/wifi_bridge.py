"""Fetch readings from the Pico over Wi-Fi and forward them to the local API.

Usage:  python scripts/wifi_bridge.py [PICO_URL]      (default http://192.168.4.1)
Normally started together with the API by `npm run live`.

The PC needs two connections: one to the Pico's Wi-Fi (Cutebot-Pico) and one with
internet for the API. The script polls the Pico every 2 seconds, so readings reach
the dashboard by themselves while a run is going. It only forwards readings taken
after it started. If the API is briefly unreachable they are queued and retried;
if the Pico restarts, it starts following it again.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from serial_bridge import load_env, post_reading

POLL_SECONDS = 2


def fetch_readings(base_url, since):
    with urllib.request.urlopen(f"{base_url}/readings?since={since}", timeout=4) as response:
        return json.load(response)


def main():
    load_env(Path(__file__).resolve().parent.parent / ".env")
    token = os.environ.get("API_TOKEN")
    if not token:
        raise SystemExit("Set API_TOKEN in .env first.")
    api_url = f"http://127.0.0.1:{os.environ.get('API_PORT', '8001')}/api/readings"
    pico_url = (sys.argv[1] if len(sys.argv) > 1 else "http://192.168.4.1").rstrip("/")

    since = None  # number of the last reading fetched; set on the first poll so old readings are skipped
    queue = []
    pico_ok = True
    print(f"Polling {pico_url}, posting to {api_url}. Press Ctrl+C to stop.")
    try:
        while True:
            try:
                data = fetch_readings(pico_url, since or 0)
                if not pico_ok:
                    print("Pico connected.")
                    pico_ok = True
                if since is None:
                    since = data["seen"]  # start from the current count; older readings are skipped
                elif data["seen"] < since:
                    since = 0  # the Pico restarted; fetch again from the start
                else:
                    queue.extend(data["readings"])
                    since = data["seen"]
            except (OSError, ValueError, KeyError) as error:
                if pico_ok:
                    print(f"Waiting for the Pico at {pico_url} ({error}).")
                    pico_ok = False

            while queue:
                try:
                    result = post_reading(api_url, token, queue[0])
                    print(f"Posted {queue[0]} (sample {result['sample']} of {result['of']})")
                    queue.pop(0)
                except urllib.error.HTTPError as error:
                    print(f"API error HTTP {error.code}; {len(queue)} reading(s) queued, will retry.")
                    break
                except OSError as error:
                    print(f"Could not reach the API ({error}); {len(queue)} queued, will retry.")
                    break
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
