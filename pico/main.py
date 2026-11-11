"""Pico Cutebot: Wi-Fi drive page + MQ135 / DHT22 air readings.

Wiring: Cutebot I2C on GP4 (SDA) / GP5 (SCL), MQ135 AO -> GP26 (ADC0) through a
10k/20k divider (5 V -> 3.3 V), DHT22 data -> GP16 (3.3 V supply).

The Pico joins the Wi-Fi named in config.py; if WIFI_SSID is empty or the join
fails, it becomes an access point (SSID Cutebot-Pico, http://192.168.4.1). Open the
page to drive (W/A/S/D or the on-screen buttons) and to press "Start reading".
A run takes READINGS_PER_RUN readings, one every 2 seconds. The Pico keeps the
latest readings and serves them at /readings?since=N; scripts/wifi_bridge.py on
the PC fetches them and posts them to the dashboard API. Each reading is also
printed over USB as `mq135_raw,temp_c,humidity` (temp/humidity are empty when
the DHT22 read fails), followed by DONE, so scripts/serial_bridge.py still works.
"""

import json
import network
import socket
import time
import dht
from machine import ADC, I2C, Pin

try:
    import config
except ImportError:
    config = None

MAX_STORED = 100  # latest readings kept for the PC to fetch

# --- Pin Configuration ---
SDA_PIN = 4
SCL_PIN = 5
DHT_PIN = 16
MQ135_PIN = 26  # ADC0 pin connected to MQ135 voltage divider

CUTEBOT_ADDR = 0x10

# --- Drive settings ---
DRIVE_SPEED = 100                                                                                                                                                                       
TURN_SPEED = 40
WATCHDOG_TIMEOUT_MS = 1000  # stop if no command arrives (the page re-sends every 300 ms)

# --- Run settings ---
READINGS_PER_RUN = 30
READING_INTERVAL_MS = 2000
WARMUP_S = 10  # the MQ135 heater needs time to stabilise; longer is better
SAMPLES_PER_READING = 8  # ADC samples averaged into each reading
SAMPLE_DELAY_MS = 20

led = Pin("LED", Pin.OUT)
i2c = I2C(0, sda=Pin(SDA_PIN), scl=Pin(SCL_PIN), freq=100000)
dht_sensor = dht.DHT22(Pin(DHT_PIN, Pin.IN, Pin.PULL_UP))
mq135_adc = ADC(Pin(MQ135_PIN))

boot_ms = time.ticks_ms()


def set_motors(left, right):
    left = max(-100, min(100, left))
    right = max(-100, min(100, right))
    l_dir = 0x02 if left >= 0 else 0x01
    r_dir = 0x02 if right >= 0 else 0x01
    try:
        i2c.writeto(CUTEBOT_ADDR, bytes([0x01, l_dir, abs(left), 0x00]))
        i2c.writeto(CUTEBOT_ADDR, bytes([0x02, r_dir, abs(right), 0x00]))
    except OSError:
        pass


def stop_motors():
    set_motors(0, 0)


def read_mq135():
    # read_u16() returns 0-65535; average several samples to reduce noise
    total = 0
    for _ in range(SAMPLES_PER_READING):
        total += mq135_adc.read_u16()
        time.sleep_ms(SAMPLE_DELAY_MS)
    return total // SAMPLES_PER_READING


def read_dht():
    # Returns (temperature_c, humidity), or (None, None) when the read fails.
    try:
        dht_sensor.measure()
        return dht_sensor.temperature(), dht_sensor.humidity()
    except OSError:
        return None, None


HTML_PAGE = """<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
  <title>Cutebot Wireless</title>
  <style>
    body { font-family: sans-serif; text-align: center; background: #111; color: #fff; user-select: none; margin: 0; padding: 20px; }
    h1 { color: #00e5ff; }
    .grid { display: grid; grid-template-columns: 90px 90px 90px; gap: 12px; justify-content: center; margin-top: 30px; }
    .btn { width: 90px; height: 90px; font-size: 28px; font-weight: bold; background: #222; color: #00e5ff; border: 2px solid #00e5ff; border-radius: 16px; touch-action: manipulation; outline: none; }
    .btn:active { background: #00e5ff; color: #000; }
    .empty { visibility: hidden; }
    #start { margin-top: 30px; padding: 14px 28px; font-size: 20px; font-weight: bold; background: #00c853; color: #000; border: 0; border-radius: 12px; }
    #start:disabled { background: #444; color: #999; }
    #status { margin-top: 16px; font-size: 18px; line-height: 1.6; }
  </style>
</head>
<body>
  <h1>Cutebot Drive</h1>
  <div class="grid">
    <div class="empty"></div>
    <button class="btn" id="btn-w" tabindex="-1">W</button>
    <div class="empty"></div>
    <button class="btn" id="btn-a" tabindex="-1">A</button>
    <button class="btn" id="btn-s" tabindex="-1">S</button>
    <button class="btn" id="btn-d" tabindex="-1">D</button>
  </div>
  <button id="start">Start reading</button>
  <div id="status">Idle</div>

  <script>
    const KEYS = ['w', 'a', 's', 'd'];
    let activeKey = null;
    let heartbeat = null;

    function sendCmd(cmd) {
      fetch('/' + cmd + '?t=' + Date.now()).catch(() => {});
    }

    // The Pico stops on its own if commands stop arriving, so keep re-sending while held.
    function startDrive(k) {
      if (activeKey === k) return;
      activeKey = k;
      sendCmd(k);
      clearInterval(heartbeat);
      heartbeat = setInterval(() => sendCmd(k), 300);
    }

    function stopDrive() {
      if (activeKey === null) return;
      activeKey = null;
      clearInterval(heartbeat);
      sendCmd('stop');
      setTimeout(() => { if (activeKey === null) sendCmd('stop'); }, 150);
    }

    document.addEventListener('keydown', (e) => {
      const k = e.key.toLowerCase();
      if (KEYS.includes(k)) {
        e.preventDefault();
        if (!e.repeat) startDrive(k);
      }
    });

    document.addEventListener('keyup', (e) => {
      const k = e.key.toLowerCase();
      if (k === activeKey) {
        e.preventDefault();
        stopDrive();
      }
    });

    window.addEventListener('blur', stopDrive);
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) stopDrive();
    });

    KEYS.forEach(k => {
      const btn = document.getElementById('btn-' + k);
      const down = (e) => { e.preventDefault(); startDrive(k); };
      const up = (e) => { e.preventDefault(); if (activeKey === k) stopDrive(); };
      btn.addEventListener('mousedown', down);
      btn.addEventListener('mouseup', up);
      btn.addEventListener('mouseleave', up);
      btn.addEventListener('touchstart', down);
      btn.addEventListener('touchend', up);
      btn.addEventListener('touchcancel', up);
    });

    const startBtn = document.getElementById('start');
    const statusBox = document.getElementById('status');
    startBtn.addEventListener('click', () => {
      startBtn.disabled = true;
      fetch('/start?t=' + Date.now()).catch(() => {});
    });

    function render(s) {
      startBtn.disabled = s.state === 'warmup' || s.state === 'reading';
      let text = s.state === 'idle' ? 'Idle' :
                 s.state === 'warmup' ? 'Warming up sensor...' :
                 s.state === 'reading' ? 'Reading ' + s.count + '/' + s.total :
                 'Done - ' + s.total + ' readings taken';
      if (s.mq !== null) {
        const v = s.mq / 65535 * 3.3 * 1.5;
        text += '<br>Air (MQ135): ' + v.toFixed(2) + ' V';
        text += '<br>Temperature: ' + (s.temp === null ? 'error' : s.temp.toFixed(1) + ' &deg;C');
        text += '<br>Humidity: ' + (s.hum === null ? 'error' : s.hum.toFixed(1) + ' %');
      }
      statusBox.innerHTML = text;
    }

    setInterval(() => {
      fetch('/status?t=' + Date.now()).then(r => r.json()).then(render).catch(() => {});
    }, 1000);
  </script>
</body>
</html>
"""

HTML_BYTES = HTML_PAGE.encode("utf-8")
OK = b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n"

DRIVES = {
    "/w": (DRIVE_SPEED, DRIVE_SPEED),
    "/s": (-DRIVE_SPEED, -DRIVE_SPEED),
    "/a": (-TURN_SPEED, TURN_SPEED),
    "/d": (TURN_SPEED, -TURN_SPEED),
}


def connect_wifi():
    # Returns True when joined to the configured network.
    if config is None or not getattr(config, "WIFI_SSID", ""):
        return False
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
    for _ in range(30):
        if wlan.isconnected():
            print("Wi-Fi joined, open http://%s" % wlan.ifconfig()[0])
            return True
        led.toggle()
        time.sleep_ms(500)
    wlan.active(False)
    return False


def send_json(conn, body):
    conn.sendall(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        b"Cache-Control: no-store\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\nConnection: close\r\n\r\n"
        + body
    )


def close_after_client(conn):
    # Let the client close first: a connection the Pico closes itself lingers in
    # TIME_WAIT, and the small TCP table fills up after a few requests.
    try:
        conn.settimeout(0.3)
        conn.recv(1)
    except OSError:
        pass
    conn.close()


def main():
    online = connect_wifi()
    if not online:
        ap = network.WLAN(network.AP_IF)
        ap.config(essid="Cutebot-Pico", password="password123")
        ap.active(True)
        print("Access point Cutebot-Pico, http://192.168.4.1")
    led.on()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("", 80))
    server.listen(2)
    server.settimeout(0.05)

    last_cmd_time = time.ticks_ms()
    is_moving = False

    state = "idle"  # idle -> warmup -> reading -> done
    count = 0
    next_reading = 0
    seen = 0  # total readings taken since boot
    stored = []  # the latest MAX_STORED readings
    last = (None, None, None)  # mq135_raw, temp_c, humidity

    while True:
        conn = None
        try:
            conn, _ = server.accept()
            conn.settimeout(0.2)
            request = conn.recv(512).decode("utf-8")
            parts = request.split(" ", 2)
            path = parts[1].split("?")[0] if len(parts) > 1 and parts[0] == "GET" else ""

            if path in DRIVES:
                set_motors(*DRIVES[path])
                is_moving = True
                last_cmd_time = time.ticks_ms()
                conn.sendall(OK)
            elif path == "/stop":
                stop_motors()
                is_moving = False
                conn.sendall(OK)
            elif path == "/start":
                if state in ("idle", "done"):
                    state = "warmup"
                    count = 0
                    last = (None, None, None)
                conn.sendall(OK)
            elif path == "/status":
                body = json.dumps(
                    {
                        "state": state,
                        "count": count,
                        "total": READINGS_PER_RUN,
                        "mq": last[0],
                        "temp": last[1],
                        "hum": last[2],
                    }
                ).encode("utf-8")
                send_json(conn, body)
            elif path == "/readings":
                # Readings numbered from 1; return those after `since`.
                since = 0
                if "since=" in request.split(" ", 2)[1]:
                    try:
                        since = int(request.split(" ", 2)[1].split("since=")[1].split("&")[0])
                    except (ValueError, OverflowError):
                        pass
                first = seen - len(stored) + 1  # number of stored[0]
                start = min(len(stored), max(0, since + 1 - first))
                send_json(conn, json.dumps({"seen": seen, "readings": stored[start:]}).encode("utf-8"))
            elif path in ("/", "/index.html"):
                header = (
                    "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                    "Content-Length: %d\r\nConnection: close\r\n\r\n" % len(HTML_BYTES)
                )
                conn.sendall(header.encode("utf-8"))
                conn.sendall(HTML_BYTES)
            else:
                conn.sendall(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n")
        except Exception as error:  # a bad request must never stop the drive loop
            if not isinstance(error, OSError):
                print("Request error:", error)
        finally:
            if conn is not None:
                close_after_client(conn)

        # Watchdog fail-safe: stop if commands stop arriving (Wi-Fi lost, page closed).
        if is_moving and time.ticks_diff(time.ticks_ms(), last_cmd_time) > WATCHDOG_TIMEOUT_MS:
            stop_motors()
            is_moving = False

        if state == "warmup":
            if time.ticks_diff(time.ticks_ms(), boot_ms) >= WARMUP_S * 1000:
                state = "reading"
                next_reading = time.ticks_ms()
                print("Warm-up done, reading...")
        elif state == "reading" and time.ticks_diff(time.ticks_ms(), next_reading) >= 0:
            next_reading = time.ticks_add(next_reading, READING_INTERVAL_MS)
            raw_adc = read_mq135()
            temp_c, humidity = read_dht()
            last = (raw_adc, temp_c, humidity)
            count += 1
            if temp_c is None:
                print("%d,," % raw_adc)
            else:
                print("%d,%.1f,%.1f" % (raw_adc, temp_c, humidity))
            seen += 1
            stored.append({"value": raw_adc, "temperature": temp_c, "humidity": humidity})
            del stored[:-MAX_STORED]
            if count >= READINGS_PER_RUN:
                state = "done"
                print("DONE")


try:
    main()
except KeyboardInterrupt:
    stop_motors()
    led.off()
