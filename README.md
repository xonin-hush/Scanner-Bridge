# Scanner Bridge

Scanner Bridge is a two-part solution that lets any modern browser drive a local USB scanner without installing vendor-specific browser plugins. A lightweight Windows service (`final.py`) controls the scanner over TWAIN or WIA APIs and publishes scanned images over a WebSocket API that the included Tailwind-powered single-page app (`index.html`) consumes. The web UI also accepts manual uploads, providing a single document collection workflow whether or not a scanner is available.

## Architecture

```
+----------------------+        WebSocket (JSON/base64)        +-----------------------+
|  Browser (index.html)|  <---------------------------------->  | Windows Scanner Bridge|
|  • Tailwind UI       |                                        |  • TWAIN/WIA drivers  |
|  • Upload fallback   |                                        |  • Auto-start + logs  |
+----------------------+                                        +-----------------------+
```

1. The workstation app runs on Windows, ensures it is installed in `%LOCALAPPDATA%\\Programs\\ScannerBridge`, requests elevation, creates a Task Scheduler entry, and exposes a WebSocket server on `ws://localhost:8765`.
2. The browser app opens `index.html`, connects to the bridge, enables the **Scan Document** button when the socket is ready, and renders thumbnails for either scanned images or uploaded files.

## Repository layout

| Path | Description |
| --- | --- |
| `final.py` | Windows-native scanner service that manages installation, privilege elevation, WIA/TWAIN scanning, image post-processing, logging, and the WebSocket API. |
| `index.html` | TailwindCSS UI that shows connection status, scan/upload actions, document gallery, and simple alerts. |
| `README.md` | This document. |

## Requirements

### Workstation (bridge service)
- Windows 10 or later with an available TWAIN or WIA-compatible scanner.
- Python 3.8+.
- Packages: pinned in `requirements.txt` (`websockets`, `pillow`, plus `python-twain` and `pywin32` on Windows only).
- Optional: `pyinstaller` if you need a standalone executable. Development tools (pytest, ruff) are pinned in `requirements-dev.txt`.

### Web UI host
- Any static file server or HTTP-capable framework (for local testing you can use `python -m http.server 8000`).
- Modern Chromium, Firefox, or Edge browser.

## Installation (Windows scanner host)

1. **Clone or download this repository** to the workstation that is connected to the scanner.
2. **Install Python** from [python.org](https://www.python.org/downloads/) and ensure `python` is available on `PATH`.
3. **Create an environment** (optional but recommended):
   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\activate
   ```
4. **Install dependencies**:
   ```powershell
   pip install -r requirements.txt
   ```
5. **Run the bridge**:
   ```powershell
   python final.py
   ```
   - On first launch the script copies itself to `%LOCALAPPDATA%\\Programs\\ScannerBridge`, restarts with administrative rights, creates/updates a Task Scheduler entry so it auto-starts on logon, and enforces a single-running instance.
   - Logs are written to `scanner_bridge.log` in `%LOCALAPPDATA%\Programs\ScannerBridge` (rotated at 1 MB, 3 backups kept). When running from source on other platforms, the log lands next to `final.py`.
6. *(Optional)* For development, `python final.py --fake-scanner` serves generated test images without hardware (works on any OS), and `--no-install` skips the self-install/elevation/scheduler setup.
7. *(Optional)* **Bundle as an executable** if you prefer distribution without Python:
   ```powershell
   pip install pyinstaller
   pyinstaller --onefile --noconsole final.py
   ```

## Running the web client

1. Start a simple static server from the repository root:
   ```bash
   python -m http.server 8000
   ```
2. Navigate to `http://localhost:8000/index.html`.
3. The banner at the top shows whether the page is connected to the bridge. When connected, the **Scan Document** button is enabled; otherwise, users can continue to upload PDFs or images directly.

> **Tip:** The default WebSocket endpoint used by the UI is `ws://localhost:8765`. To point at a bridge on another machine or port, append `?ws=ws://host:port` to the page URL (or edit `WS_URL` near the top of `index.html`).

If the connection drops (bridge restart, sleep/wake, network blip), the page reconnects automatically with exponential backoff (1 s doubling to 30 s, reset once connected) and shows the retry countdown in the status banner — no manual reload needed.

## Configuration

The service starts from the `DEFAULT_CONFIG` dictionary near the top of `final.py` and shallow-merges an optional `config.json` placed next to the executable (or next to `final.py` when running from source). Invalid or missing files are ignored with a logged warning, so a bad config can never keep the bridge from starting. Key options:

| Key | Default | Purpose |
| --- | --- | --- |
| `host` | `localhost` | Interface bound by the WebSocket server. Change to `0.0.0.0` to expose the bridge to the LAN. |
| `port` | `8765` | WebSocket port. Must match the `WS_URL` used by the web UI. |
| `default_dpi` | `200` | DPI used when the UI issues a `scan` command without specifying `dpi`. Requested values are clamped to 100–600. |
| `max_clients` | `10` | Concurrent browser connections permitted. Additional clients receive an error immediately. |
| `allowed_origin_hosts` | `["localhost", "127.0.0.1"]` | Browser origin hostnames allowed to open a socket (extend this list when deploying on an intranet). |
| `log_level` | `INFO` | Minimum level recorded in `scanner_bridge.log` and on the console. Use a string such as `"DEBUG"` in `config.json`. |
| `scan_timeout_seconds` | `300` | How long a single scan may run before the bridge gives up and frees the scanner for new requests. |
| `port_retry_seconds` / `port_retry_attempts` | `10` / `30` | How often and how many times to retry binding the WebSocket port when it is temporarily in use. |

Example `config.json`:

```json
{
  "port": 9000,
  "log_level": "DEBUG",
  "scan_timeout_seconds": 120
}
```

## WebSocket contract

The UI and bridge exchange compact JSON messages:

- **Client → Server**
  - `{ "type": "scan", "dpi": 200 }` – start a scan at the requested DPI. The server rejects requests while another scan is running.
  - `{ "type": "get_scanners" }` – returns `scanners_list` with TWAIN/WIA device names.
  - `{ "type": "ping" }` – requests a `pong` heartbeat.
- **Server → Client**
  - `{ "type": "connected", "scanner_available": true, "scanner_name": "..." }` – sent immediately after a client connects.
  - `{ "type": "scan_complete", "image": "data:image/png;base64,...", "dpi": 200 }` – delivered when the scanner returns an image (TWAIN or WIA). The payload is already browser-ready.
  - `{ "type": "scanners_list", "scanners": [] }` – enumerates available devices.
  - `{ "type": "pong" }` – heartbeat response with ISO timestamp.
  - `{ "type": "error", "message": "..." }` – sent for invalid JSON, unknown commands, or scan failures.

## Image processing pipeline

1. Images are captured either via TWAIN (`twain.SourceManager`) or WIA (`WIA.CommonDialog`).
2. The resulting bitmap is converted to a PIL image, constrained so that the longest edge is at most 2000px, and encoded as PNG. If the encoded payload exceeds ~5 MB it is recompressed as JPEG (~85% quality) before being base64-encoded.
3. The UI directly renders the `data:image/...` URI and stores it in the page’s document gallery.

## Auto-start & deployment notes

- The bridge automatically creates a Task Scheduler job (highest privileges, `ONLOGON`, 30-second delay) named `ScannerBridge`. Use `schtasks /Query /TN ScannerBridge` to verify or delete it manually if needed.
- **Crash recovery is two-layered.** Inside the process, a supervisor loop restarts the WebSocket server after unhandled errors with exponential backoff (1 s doubling to 60 s, reset after 10 minutes of healthy uptime). Outside it, a `ScannerBridgeWatchdog` Task Scheduler job relaunches the executable every 5 minutes; while the bridge is healthy each relaunch exits immediately on the single-instance lock, and after a hard kill the next firing resurrects the service. Remove it with `schtasks /Delete /TN ScannerBridgeWatchdog /F` if you uninstall the bridge.
- An OS-held file lock in `%TEMP%` prevents multiple instances from running simultaneously; it is released automatically however the process ends, so no stale lock can block a restart.
- If the UAC elevation prompt is declined, the bridge keeps running without admin rights (scanning works normally; the auto-start task is created without highest privileges).
- Ensure Windows Defender Firewall allows inbound connections on the configured port if you plan to connect from another machine.

## Troubleshooting checklist

| Symptom | Resolution |
| --- | --- |
| **`TWAIN not available` warning** | Install `python-twain` (32-bit vs 64-bit must match your Python installation) or rely on WIA. |
| **`WIA not available` info message** | Install `pywin32` and ensure Windows Image Acquisition service is running. |
| **Browser shows “ScannerBridge Offline”** | Confirm `final.py` is running, verify port/host values match between `CONFIG` and `index.html`, and check firewalls. |
| **`Origin not allowed` error on connect** | The page is served from a hostname the bridge doesn't trust. Add it to `allowed_origin_hosts` in `config.json`. |
| **`Port ... in use` warnings in the log** | Another process (often a previous instance still shutting down) holds the port. The bridge retries automatically; if it gives up, free the port or change `port` in `config.json`. |
| **`Scan already in progress` log** | Wait for the current scan to finish; the UI will receive either `scan_complete` or `error`. |
| **Large scans fail to display** | Images above ~5 MB are recompressed automatically. If the issue persists, reduce DPI or paper size. |
| **Auto-start skipped** | The bridge falls back to a normal-privilege Task Scheduler entry when elevation is unavailable. If no task exists at all, check `scanner_bridge.log` for `[Startup]` errors. |

## Contributing

1. Fork the repository and create a feature branch.
2. Update `README.md` or inline code comments when you add/modify behavior.
3. Provide clear reproduction steps or screenshots for UI-facing changes.
4. Test on Windows hardware with at least one physical scanner before opening a pull request.

Happy scanning!
