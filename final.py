"""
Scanner Bridge Application - COM Threading Fixed
This runs on the workstation and communicates with the web interface
"""

import asyncio
import errno
import websockets
import json
import base64
import logging
import logging.handlers
import sys
import urllib.parse
from pathlib import Path
from typing import Optional, Set, List
from datetime import datetime
from PIL import Image
import io
import ctypes
import os
import subprocess
import shutil
import threading
import time

def ensure_installed(app_name="ScannerBridge"):
    """Copy this exe to a permanent folder in AppData if not already there."""
    target_dir = os.path.join(os.getenv("LOCALAPPDATA"), "Programs", app_name)
    os.makedirs(target_dir, exist_ok=True)
    
    # Use a fixed name for the installed exe
    exe_name = os.path.basename(sys.executable)
    if not exe_name.endswith('.exe'):
        exe_name = f"{app_name}.exe"
    target_path = os.path.join(target_dir, exe_name)

    current_path = os.path.abspath(sys.executable)
    target_path_abs = os.path.abspath(target_path)
    
    if current_path != target_path_abs:
        try:
            # If target exists, check if it's the same file
            if os.path.exists(target_path):
                # Compare file sizes and modification times
                current_stat = os.stat(current_path)
                target_stat = os.stat(target_path)
                if (current_stat.st_size == target_stat.st_size and
                    current_stat.st_mtime <= target_stat.st_mtime):
                    # Target is up to date, just relaunch from there
                    logger.info(f"[Install] Using existing installation at {target_path}")
                    os.startfile(target_path)
                    sys.exit(0)

            # Copy to target location
            shutil.copy2(sys.executable, target_path)
            logger.info(f"[Install] Copied self to {target_path}")
            
            # Small delay to ensure file is fully written
            time.sleep(0.5)
            
            # Relaunch from new location with admin privileges
            # Use ShellExecute to ensure it runs with proper privileges
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", target_path, "", None, 1
            )
            sys.exit(0)
        except Exception as e:
            logger.error(f"[Install] Failed to copy self: {e}")
            # Continue anyway - might already be in the right place

def run_as_admin():
    """Relaunch the script with admin privileges if not already elevated."""
    try:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        is_admin = False

    if not is_admin:
        # Relaunch as admin
        params = ' '.join([f'"{arg}"' for arg in sys.argv])
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1
        )
        sys.exit(0)

def add_to_startup(app_name="ScannerBridge", exe_path=None):
    """Create a Windows Task Scheduler entry for auto-start."""
    if exe_path is None:
        exe_path = sys.executable

    # Normalize path to handle spaces and special characters
    exe_path = os.path.abspath(exe_path)
    
    # Check if task already exists
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/TN", app_name],
            capture_output=True,
            timeout=5
        )
        if result.returncode == 0:
            # Task exists, update it to ensure it uses the correct path
            logger.info(f"[Startup] Task '{app_name}' already exists, updating...")
            try:
                subprocess.run([
                    "schtasks", "/Change", "/TN", app_name,
                    "/TR", exe_path,
                    "/RL", "HIGHEST"
                ], check=True, capture_output=True, timeout=10)
                logger.info(f"[Startup] Task '{app_name}' updated successfully.")
                return True
            except subprocess.CalledProcessError as e:
                logger.warning(f"[Startup] Failed to update task: {e.stderr.decode(errors='ignore', encoding='utf-8')}")
                # Try to delete and recreate
                try:
                    subprocess.run(["schtasks", "/Delete", "/TN", app_name, "/F"],
                                 capture_output=True, timeout=5)
                except Exception:
                    logger.debug("[Startup] Failed to delete existing task", exc_info=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Task doesn't exist, will create it
        pass

    # Create the task with proper settings
    try:
        # Use /RU SYSTEM for highest privileges, or current user
        # /RL HIGHEST ensures it runs with highest privileges
        # /SC ONLOGON runs at user logon
        # /DELAY 0000:30 adds 30 second delay to ensure system is ready
        result = subprocess.run([
            "schtasks", "/Create", "/TN", app_name,
            "/TR", exe_path,
            "/SC", "ONLOGON",
            "/RL", "HIGHEST",
            "/F",
            "/DELAY", "0000:30"  # 30 second delay after logon
        ], check=True, capture_output=True, timeout=10)
        
        # Verify task was created
        verify_result = subprocess.run(
            ["schtasks", "/Query", "/TN", app_name],
            capture_output=True,
            timeout=5
        )
        if verify_result.returncode == 0:
            logger.info(f"[Startup] Task '{app_name}' created and verified successfully.")
            return True
        else:
            logger.warning("[Startup] Task creation may have failed - verification failed.")
            return False

    except subprocess.TimeoutExpired:
        logger.warning("[Startup] Task creation timed out.")
        return False
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.decode(errors='ignore', encoding='utf-8') if e.stderr else str(e)
        logger.warning(f"[Startup] Failed to create scheduled task: {error_msg}")
        return False
def ensure_single_instance(lock_name="scanner_bridge.lock"):
    """Prevent multiple running instances using a file-based lock."""
    lock_path = os.path.join(os.getenv("TEMP", "."), lock_name)

    # Check if lock file exists and process is alive
    if os.path.exists(lock_path):
        try:
            with open(lock_path, "r") as f:
                old_pid = int(f.read().strip())
            
            # Windows-native process check using OpenProcess
            try:
                kernel32 = ctypes.windll.kernel32
                PROCESS_QUERY_INFORMATION = 0x0400
                handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, old_pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    logger.info("[Startup] Instance already running, exiting.")
                    sys.exit(0)
            except Exception:
                pass  # Process doesn't exist or can't access it
        except Exception:
            pass  # stale or corrupt file, ignore

    # Write current PID to lock file
    with open(lock_path, "w") as f:
        f.write(str(os.getpid()))

    # Register cleanup when exiting
    import atexit
    def remove_lock():
        try:
            if os.path.exists(lock_path):
                os.remove(lock_path)
        except Exception:
            pass
    atexit.register(remove_lock)


# Configuration defaults; override via an optional config.json next to the app
DEFAULT_CONFIG = {
    'host': 'localhost',
    'port': 8765,
    'default_dpi': 200,
    'max_clients': 10,
    'allowed_origin_hosts': ['localhost', '127.0.0.1'],
    'log_level': logging.INFO,
    'scan_timeout_seconds': 300,
    'port_retry_seconds': 10,
    'port_retry_attempts': 30,
}

CONFIG = dict(DEFAULT_CONFIG)


def app_dir() -> Path:
    """Directory holding the frozen exe, or this script — where config.json lives."""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    return Path(__file__).parent


def load_config(config_path: Optional[Path] = None) -> dict:
    """Return DEFAULT_CONFIG shallow-merged with config.json, if present and valid."""
    config = dict(DEFAULT_CONFIG)
    if config_path is None:
        config_path = app_dir() / 'config.json'
    try:
        with open(config_path, encoding='utf-8') as f:
            overrides = json.load(f)
        if isinstance(overrides, dict):
            config.update(overrides)
            logger.info(f"Loaded config overrides from {config_path}")
        else:
            logger.warning(f"Ignoring {config_path}: expected a JSON object")
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Ignoring invalid config {config_path}: {e}")
    return config


def origin_allowed(origin: Optional[str]) -> bool:
    """Whether a WebSocket Origin header value may connect.

    Absent or "null" origins are allowed (file:// pages, non-browser
    clients); anything else must have a hostname on the allow-list.
    """
    if origin is None or origin == 'null':
        return True
    try:
        hostname = urllib.parse.urlsplit(origin).hostname
    except ValueError:
        return False
    return hostname in CONFIG['allowed_origin_hosts']


def clamp_dpi(dpi) -> int:
    """Clamp a client-requested DPI to the supported 100-600 range."""
    try:
        dpi = int(dpi)
    except (TypeError, ValueError):
        return DEFAULT_CONFIG['default_dpi']
    return max(100, min(600, dpi))

# Setup logging with UTF-8 encoding for Windows
class UTF8StreamHandler(logging.StreamHandler):
    def __init__(self):
        super().__init__()
        if sys.platform == 'win32' and sys.stdout and hasattr(sys.stdout, "buffer"):
            # Force UTF-8 encoding on Windows, only if stdout exists
            import codecs
            sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')


def default_log_path() -> str:
    """Absolute log location: install dir on Windows, else next to this script."""
    if sys.platform == 'win32':
        log_dir = os.path.join(os.getenv('LOCALAPPDATA', '.'), 'Programs', 'ScannerBridge')
    else:
        log_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, 'scanner_bridge.log')


def setup_logging():
    """Configure rotating file + console logging. Called from main(), not at import."""
    handlers = [logging.handlers.RotatingFileHandler(
        default_log_path(), maxBytes=1_000_000, backupCount=3, encoding='utf-8'
    )]
    if sys.stderr is not None:  # absent in pyinstaller --noconsole builds
        handlers.append(UTF8StreamHandler())
    logging.basicConfig(
        level=CONFIG['log_level'],
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers
    )


logger = logging.getLogger(__name__)

# For Windows TWAIN support
try:
    import twain
    TWAIN_AVAILABLE = True
except ImportError:
    TWAIN_AVAILABLE = False

# For Windows WIA support (alternative to TWAIN)
WIA_AVAILABLE = False
if sys.platform == 'win32':
    try:
        import win32com.client  # noqa: F401 - availability probe
        import pythoncom  # noqa: F401 - availability probe
        WIA_AVAILABLE = True
    except ImportError:
        pass

class ScannerBridge:
    def __init__(self):
        self.scanner: Optional[object] = None
        self.scanner_name: Optional[str] = None
        self.scanner_type: str = 'none'  # 'twain', 'wia', or 'none'
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
        self.is_scanning: bool = False
        self.scanner_needs_reinit: bool = False
        
    def release_scanner(self):
        """Release the current scanner handle, if any."""
        if self.scanner is not None and self.scanner_type == 'twain':
            try:
                self.scanner.destroy()
            except Exception as e:
                logger.debug(f"Error releasing TWAIN scanner: {e}")
        self.scanner = None
        self.scanner_name = None
        self.scanner_type = 'none'
        self.scanner_needs_reinit = False

    def initialize_scanner(self) -> bool:
        """Initialize scanner - try TWAIN first, then WIA"""

        # Try TWAIN first
        if TWAIN_AVAILABLE:
            logger.info("Attempting TWAIN scanner initialization...")
            if self.initialize_twain_scanner():
                return True
        
        # Try WIA as fallback
        if WIA_AVAILABLE:
            logger.info("Attempting WIA scanner initialization...")
            if self.initialize_wia_scanner():
                return True
        
        logger.error("No scanner could be initialized")
        return False
    
    def initialize_twain_scanner(self) -> bool:
        """Initialize TWAIN scanner"""
        try:
            sm = twain.SourceManager(0)
            
            # List available sources
            sources = sm.GetSourceList()
            if not sources:
                logger.warning("No TWAIN sources found")
                return False
            
            logger.info(f"Found TWAIN sources: {sources}")
            
            # Try to open the first available source
            try:
                ss = sm.OpenSource(sources[0])
                if ss:
                    self.scanner = ss
                    self.scanner_name = sources[0]
                    self.scanner_type = 'twain'
                    logger.info(f"TWAIN scanner initialized: {self.scanner_name}")
                    return True
            except Exception as e:
                logger.warning(f"Could not open TWAIN source '{sources[0]}': {e}")
                # Try default
                try:
                    ss = sm.OpenSource()
                    if ss:
                        self.scanner = ss
                        self.scanner_name = ss.GetSourceName()
                        self.scanner_type = 'twain'
                        logger.info(f"TWAIN scanner initialized (default): {self.scanner_name}")
                        return True
                except Exception as e2:
                    logger.warning(f"Could not open default TWAIN source: {e2}")
            
            return False
                
        except Exception as e:
            logger.error(f"TWAIN initialization failed: {e}")
            return False
    
    def initialize_wia_scanner(self) -> bool:
        """Initialize WIA scanner (Windows Image Acquisition)"""
        try:
            import win32com.client
            import pythoncom

            # Ref-counted and safe to repeat. Deliberately not paired with
            # CoUninitialize: the cached device object must outlive this call,
            # and re-initialization may run on a fresh scan thread (scan
            # threads set up COM for themselves in scan_with_wia).
            pythoncom.CoInitialize()

            device_manager = win32com.client.Dispatch("WIA.DeviceManager")
            devices = device_manager.DeviceInfos
            
            if devices.Count == 0:
                logger.warning("No WIA devices found")
                return False
            
            # Get first scanner device
            for i in range(1, devices.Count + 1):
                device_info = devices.Item(i)
                if device_info.Type == 1:  # Scanner type
                    device = device_info.Connect()
                    self.scanner = device
                    self.scanner_name = device_info.Properties('Name').Value
                    self.scanner_type = 'wia'
                    logger.info(f"WIA scanner initialized: {self.scanner_name}")
                    return True
            
            logger.warning("No WIA scanner devices found (cameras excluded)")
            return False
            
        except Exception as e:
            logger.error(f"WIA initialization failed: {e}")
            return False
    
    def configure_scanner(self, dpi: int = 200) -> bool:
        """Configure scanner settings"""
        if not self.scanner:
            return False
            
        try:
            # Set X resolution
            self.scanner.SetCapability(
                twain.ICAP_XRESOLUTION,
                twain.TWTY_FIX32,
                [float(dpi)]
            )
            
            # Set Y resolution
            self.scanner.SetCapability(
                twain.ICAP_YRESOLUTION,
                twain.TWTY_FIX32,
                [float(dpi)]
            )
            
            # Set pixel type to RGB
            self.scanner.SetCapability(
                twain.ICAP_PIXELTYPE,
                twain.TWTY_UINT16,
                [twain.TWPT_RGB]
            )
            
            logger.info(f"Scanner configured: {dpi} DPI, RGB mode")
            return True
            
        except Exception as e:
            logger.error(f"Failed to configure scanner: {e}")
            return False
    
    def scan_document(self, dpi: int = 200, color_mode: str = 'RGB') -> Optional[str]:
        """
        Scan a document and return as base64 encoded image
        Routes to TWAIN or WIA based on scanner type
        """
        if self.scanner is None or self.scanner_needs_reinit:
            # Heals a scanner plugged in after startup, a stale handle after
            # a USB unplug, and the aftermath of a timed-out scan.
            logger.info("(Re)initializing scanner before scan...")
            self.release_scanner()
            if not self.initialize_scanner():
                logger.error("Scanner not initialized")
                return None

        if self.scanner_type == 'twain':
            return self.scan_with_twain(dpi, color_mode)
        elif self.scanner_type == 'wia':
            return self.scan_with_wia()
        else:
            logger.error(f"Unknown scanner type: {self.scanner_type}")
            return None
    
    def scan_with_twain(self, dpi: int = 200, color_mode: str = 'RGB') -> Optional[str]:
        """Scan using TWAIN"""
        handle = None
        
        try:
            # Validate DPI
            dpi = clamp_dpi(dpi)

            # Configure scanner
            if not self.configure_scanner(dpi):
                return None
            
            # Request scan
            self.scanner.RequestAcquire(0, 0)
            logger.info("TWAIN scan requested, waiting for user...")
            
            # Get image
            rv = self.scanner.XferImageNatively()
            
            if rv:
                (handle, count) = rv
                logger.info("Image acquired, processing...")
                
                # Convert to PIL Image
                try:
                    from PIL import ImageWin
                    dib = ImageWin.Dib(handle)
                    pil_image = dib.image
                    
                    return self.process_image(pil_image)
                    
                except Exception as e:
                    logger.error(f"Image processing error: {e}", exc_info=True)
                    return None
            else:
                logger.warning("No image acquired - user may have cancelled")
                return None
                
        except AttributeError as e:
            logger.error(f"TWAIN attribute error: {e}")
            self.scanner_needs_reinit = True
            return None
        except Exception as e:
            logger.error(f"TWAIN scan error: {e}", exc_info=True)
            self.scanner_needs_reinit = True
            return None
        finally:
            # Clean up handle
            if handle:
                try:
                    twain.GlobalFree(handle)
                except Exception:
                    pass
    
    def scan_with_wia(self) -> Optional[str]:
        """Scan using WIA - MUST be called from a thread with COM initialized"""
        tmp_path = None
        try:
            import win32com.client
            import pythoncom
            import tempfile
            import os
            
            # Initialize COM for this thread
            pythoncom.CoInitialize()
            
            try:
                logger.info("WIA scan requested...")
                
                # Show scanner dialog for WIA
                common_dialog = win32com.client.Dispatch("WIA.CommonDialog")
                image = common_dialog.ShowAcquireImage()
                
                if not image:
                    logger.warning("No image acquired - user may have cancelled")
                    return None
                
                # Create temp file path (but don't create the file yet)
                # WIA SaveFile requires the file to NOT exist
                fd, tmp_path = tempfile.mkstemp(suffix='.bmp')
                os.close(fd)  # Close the file descriptor
                os.unlink(tmp_path)  # Delete the file so WIA can create it
                
                # Now WIA can save to this path
                image.SaveFile(tmp_path)
                logger.info(f"Image saved to temporary file: {tmp_path}")
                
                # Load with PIL
                from PIL import Image
                pil_image = Image.open(tmp_path)
                
                # Process before cleanup
                result = self.process_image(pil_image)
                
                return result
            
            finally:
                # Uninitialize COM for this thread
                pythoncom.CoUninitialize()
                
                # Clean up temp file
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                        logger.info(f"Cleaned up temporary file: {tmp_path}")
                    except Exception as cleanup_error:
                        logger.warning(f"Failed to cleanup temp file: {cleanup_error}")
            
        except Exception as e:
            logger.error(f"WIA scan error: {e}", exc_info=True)
            return None
    
    def process_image(self, pil_image) -> Optional[str]:
        """Process and encode scanned image"""
        try:
            # Optimize image size
            max_dimension = 2000
            if max(pil_image.size) > max_dimension:
                ratio = max_dimension / max(pil_image.size)
                new_size = tuple(int(dim * ratio) for dim in pil_image.size)
                pil_image = pil_image.resize(new_size, Image.LANCZOS)
                logger.info(f"Image resized to {new_size}")
            
            # Convert to base64
            buffer = io.BytesIO()
            pil_image.save(buffer, format='PNG', optimize=True)
            img_bytes = buffer.getvalue()
            
            # Check size (max 5MB)
            if len(img_bytes) > 5 * 1024 * 1024:
                logger.warning("Image too large, compressing further")
                buffer = io.BytesIO()
                pil_image.save(buffer, format='JPEG', quality=85, optimize=True)
                img_bytes = buffer.getvalue()
                img_format = 'jpeg'
            else:
                img_format = 'png'
            
            img_str = base64.b64encode(img_bytes).decode()
            logger.info(f"Scan complete: {len(img_bytes)} bytes ({img_format})")
            
            return f"data:image/{img_format};base64,{img_str}"
            
        except Exception as e:
            logger.error(f"Image processing error: {e}", exc_info=True)
            return None
    
    async def run_scan_with_timeout(self, dpi, color_mode) -> Optional[str]:
        """Run the blocking scan in a daemon thread, bounded by scan_timeout_seconds.

        A hung TWAIN/WIA driver call (e.g. an acquire dialog nobody dismisses)
        cannot be killed from Python. A daemon thread is abandoned on timeout:
        the driver may still hold the physical scanner until its dialog is
        closed, but the bridge stays responsive and can exit cleanly.
        """
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def deliver(setter, value):
            if not future.done():
                setter(value)

        def worker():
            try:
                result = self.scan_document(dpi, color_mode)
            except Exception as e:
                callback, payload = future.set_exception, e
            else:
                callback, payload = future.set_result, result
            try:
                loop.call_soon_threadsafe(deliver, callback, payload)
            except RuntimeError:
                pass  # event loop already closed

        threading.Thread(target=worker, daemon=True, name='scan').start()
        return await asyncio.wait_for(future, timeout=CONFIG['scan_timeout_seconds'])

    async def handle_client(self, websocket: websockets.WebSocketServerProtocol):
        """Handle WebSocket connection from web interface"""

        # Reject cross-site pages; any origin could otherwise trigger scans
        # and read the resulting documents.
        origin = websocket.request_headers.get('Origin')
        if not origin_allowed(origin):
            logger.warning(f"Rejected connection from disallowed origin: {origin}")
            await websocket.send(json.dumps({
                'type': 'error',
                'message': 'Origin not allowed'
            }))
            await websocket.close()
            return

        # Check client limit
        if len(self.clients) >= CONFIG['max_clients']:
            await websocket.send(json.dumps({
                'type': 'error',
                'message': 'Maximum clients reached'
            }))
            await websocket.close()
            return
        
        self.clients.add(websocket)
        client_id = id(websocket)
        logger.info(f"Client {client_id} connected. Total clients: {len(self.clients)}")
        
        try:
            # Send connection confirmation
            await websocket.send(json.dumps({
                'type': 'connected',
                'scanner_available': self.scanner is not None,
                'scanner_name': self.scanner_name,
                'timestamp': datetime.now().isoformat()
            }))
            
            async for message in websocket:
                try:
                    data = json.loads(message)
                    msg_type = data.get('type')

                    if msg_type == 'scan':
                        logger.info(f"Scan requested by client {client_id}")

                        if self.is_scanning:
                            await websocket.send(json.dumps({
                                'type': 'error',
                                'message': 'Scan already in progress'
                            }))
                            continue

                        # Claim the scanner before the first await so two
                        # clients can't pass the check together (the check
                        # and set run atomically on the event loop).
                        self.is_scanning = True
                        try:
                            await websocket.send(json.dumps({
                                'type': 'scanning',
                                'message': 'Initializing scanner...'
                            }))

                            image_data = await self.run_scan_with_timeout(
                                data.get('dpi', CONFIG['default_dpi']),
                                data.get('color_mode', 'RGB')
                            )
                        except asyncio.TimeoutError:
                            logger.error(
                                f"Scan timed out after {CONFIG['scan_timeout_seconds']}s; "
                                "scanner will be re-initialized on the next scan"
                            )
                            self.scanner_needs_reinit = True
                            await websocket.send(json.dumps({
                                'type': 'error',
                                'message': 'Scan timed out. Close any scanner dialogs and try again.'
                            }))
                        else:
                            if image_data:
                                await websocket.send(json.dumps({
                                    'type': 'scan_complete',
                                    'image': image_data,
                                    'timestamp': datetime.now().isoformat(),
                                    'dpi': data.get('dpi', CONFIG['default_dpi'])
                                }))
                                logger.info(f"Scan completed for client {client_id}")
                            else:
                                await websocket.send(json.dumps({
                                    'type': 'error',
                                    'message': 'Scan failed. Check scanner connection or try again.'
                                }))
                        finally:
                            self.is_scanning = False

                    elif msg_type == 'get_scanners':
                        # List available scanners
                        scanners = self.list_scanners()
                        await websocket.send(json.dumps({
                            'type': 'scanners_list',
                            'scanners': scanners
                        }))
                        logger.info(f"Scanner list sent to client {client_id}")
                        
                    elif msg_type == 'ping':
                        await websocket.send(json.dumps({
                            'type': 'pong',
                            'timestamp': datetime.now().isoformat()
                        }))

                    else:
                        logger.warning(f"Unknown message type: {msg_type}")
                        await websocket.send(json.dumps({
                            'type': 'error',
                            'message': f"Unknown message type: {msg_type}"
                        }))
                        
                except json.JSONDecodeError as e:
                    logger.error(f"Invalid JSON from client {client_id}: {e}")
                    await websocket.send(json.dumps({
                        'type': 'error',
                        'message': 'Invalid message format'
                    }))
                except Exception as e:
                    logger.error(f"Error handling message from client {client_id}: {e}", exc_info=True)
                    await websocket.send(json.dumps({
                        'type': 'error',
                        'message': 'Internal server error'
                    }))
                    
        except websockets.exceptions.ConnectionClosed:
            logger.info(f"Client {client_id} disconnected")
        except Exception as e:
            logger.error(f"Unexpected error with client {client_id}: {e}", exc_info=True)
        finally:
            self.clients.discard(websocket)
            logger.info(f"Client {client_id} removed. Total clients: {len(self.clients)}")
    
    def list_scanners(self) -> List[str]:
        """List available scanners from both TWAIN and WIA"""
        scanners = []
        
        # Check TWAIN
        if TWAIN_AVAILABLE:
            try:
                sm = twain.SourceManager(0)
                sources = sm.GetSourceList()
                if sources:
                    scanners.extend([f"TWAIN: {s}" for s in sources])
            except Exception as e:
                logger.error(f"Error listing TWAIN scanners: {e}")
        
        # Check WIA
        if WIA_AVAILABLE:
            try:
                import win32com.client
                import pythoncom
                
                # Initialize COM for this thread
                pythoncom.CoInitialize()
                try:
                    device_manager = win32com.client.Dispatch("WIA.DeviceManager")
                    devices = device_manager.DeviceInfos
                    
                    for i in range(1, devices.Count + 1):
                        device_info = devices.Item(i)
                        if device_info.Type == 1:  # Scanner
                            name = device_info.Properties('Name').Value
                            scanners.append(f"WIA: {name}")
                finally:
                    pythoncom.CoUninitialize()
                    
            except Exception as e:
                logger.error(f"Error listing WIA scanners: {e}")
        
        logger.info(f"Found {len(scanners)} scanner(s)")
        return scanners
    
    async def start_server(self, host: str = None, port: int = None):
        """Start WebSocket server"""
        host = host or CONFIG['host']
        port = port or CONFIG['port']
        
        logger.info("=" * 60)
        logger.info("Scanner Bridge v2.1 (COM Threading Fixed)")
        logger.info("=" * 60)
        logger.info(f"Starting Scanner Bridge on ws://{host}:{port}")
        logger.info("Initializing scanner...")
        
        scanner_initialized = self.initialize_scanner()
        
        if scanner_initialized:
            logger.info(f"[OK] Scanner ready: {self.scanner_name} ({self.scanner_type.upper()})")
        else:
            logger.warning("[WARNING] No scanner detected - file upload mode only")
        
        # A crashed predecessor may still hold the port for a few seconds
        # (or a stale instance may be shutting down) - retry instead of dying.
        addr_in_use = (errno.EADDRINUSE, getattr(errno, 'WSAEADDRINUSE', errno.EADDRINUSE))
        server = None
        for attempt in range(1, CONFIG['port_retry_attempts'] + 1):
            try:
                server = await websockets.serve(
                    self.handle_client,
                    host,
                    port,
                    ping_interval=30,
                    ping_timeout=10
                )
                break
            except OSError as e:
                if e.errno not in addr_in_use:
                    raise
                if attempt >= CONFIG['port_retry_attempts']:
                    logger.error(f"Port {port} still in use after {attempt} attempts; giving up")
                    raise
                logger.warning(
                    f"Port {port} in use; retrying in {CONFIG['port_retry_seconds']}s "
                    f"({attempt}/{CONFIG['port_retry_attempts']})"
                )
                await asyncio.sleep(CONFIG['port_retry_seconds'])

        if server is None:  # only reachable with port_retry_attempts <= 0
            raise OSError(f"Could not bind ws://{host}:{port}")

        try:
            logger.info("[OK] Scanner Bridge is running")
            logger.info("[OK] Open your web application to start scanning")
            logger.info("Press Ctrl+C to stop")

            # Keep server running
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                logger.info("Server shutdown initiated")
        finally:
            server.close()
            await server.wait_closed()

def main():
    """Main entry point"""
    # Set UTF-8 encoding for Windows console early
    if sys.platform == 'win32':
        try:
            if sys.stdout and sys.stdout.encoding != 'utf-8':
                sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    setup_logging()

    CONFIG.update(load_config())
    level = CONFIG['log_level']
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)
    logging.getLogger().setLevel(level)

    if not TWAIN_AVAILABLE:
        logger.warning("TWAIN not available. Install: pip install python-twain")
    if sys.platform == 'win32' and not WIA_AVAILABLE:
        logger.info("WIA not available. Install: pip install pywin32")

    if sys.platform == "win32":
        # First ensure we're installed to a safe location
        ensure_installed("ScannerBridge")
        
        # Then ensure we have admin privileges
        run_as_admin()
        
        # Now we're running from the installed location with admin privileges
        # Ensure startup task is created/updated with the correct path
        installed_path = sys.executable
        startup_success = add_to_startup("ScannerBridge", installed_path)
        if not startup_success:
            logger.warning("[Startup] Failed to create/update startup task. Application may not start after restart.")
        
        # Ensure only one instance is running
        ensure_single_instance()
    
    bridge = ScannerBridge()

    try:
        asyncio.run(bridge.start_server())
    except KeyboardInterrupt:
        logger.info("Shutting down Scanner Bridge...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        bridge.release_scanner()

if __name__ == "__main__":
    main()


"""
INSTALLATION INSTRUCTIONS:
==========================

1. Install Python 3.8+ from python.org

2. Install required packages:
   pip install websockets pillow python-twain pywin32

3. Run the application:
   python scanner_bridge_app.py

4. To make it auto-start on Windows:
   - Create a shortcut to scanner_bridge_app.py
   - Press Win+R, type: shell:startup
   - Place the shortcut in the Startup folder

5. To compile to executable:
   pip install pyinstaller
   pyinstaller --onefile --noconsole scanner_bridge_app.py

CONFIGURATION:
==============
Edit CONFIG dictionary in code:
- host: 'localhost' (server address)
- port: 8765 (WebSocket port)
- default_dpi: 200 (scan quality)
- max_clients: 10 (concurrent connections)

SCANNER SUPPORT:
================
- TWAIN: Traditional scanner interface (requires python-twain)
- WIA: Windows Image Acquisition (requires pywin32)
- The app will try TWAIN first, then fall back to WIA

TROUBLESHOOTING:
================
- Check scanner_bridge.log for detailed error messages
- If scanner not detected, ensure drivers are installed
- On Windows 10+, WIA is often more reliable than TWAIN
- Check Windows Device Manager for scanner status
- Firewall may block localhost:8765 (add exception)
- For "Scan already in progress", wait for current scan to complete

WHAT WAS FIXED IN v2.1:
========================
- Added pythoncom.CoInitialize() and CoUninitialize() to WIA methods
- This fixes the "CoInitialize has not been called" error
- COM objects now work correctly in executor threads
- Scanner dialog will now appear properly when clicking "Scan Document"
"""