import asyncio
import json
import logging
import base64
import sys
import os

# Allow importing from parent/systemutils
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from aiohttp import web, WSMsgType
from kojak_wrapper import (
    KojakScanner,
    ENUM_IBSU_ROLL_SINGLE_FINGER,
    ENUM_IBSU_FLAT_SINGLE_FINGER,
    ENUM_IBSU_FLAT_TWO_FINGERS,
    ENUM_IBSU_FLAT_FOUR_FINGERS
)
from systemutils.wifi_manager import WifiManager

# Configure logging
from logging.handlers import TimedRotatingFileHandler

log_file = "/var/log/portaprint.log"
if not os.path.exists(log_file):
    if not os.access("/var/log", os.W_OK):
        log_file = "portaprint_server.log"

logger = logging.getLogger("KojakServer")
logger.setLevel(logging.INFO)

try:
    file_handler = TimedRotatingFileHandler(log_file, when="midnight", interval=1, backupCount=30)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    logger.addHandler(file_handler)
except Exception as e:
    print(f"Failed to setup file logging to {log_file}: {e}")

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
logger.addHandler(stream_handler)


class PortaPrintServer:
    def __init__(self):
        self.scanner = None
        self.wifi = WifiManager()
        self.connected_clients = set()

        # Persisted hardware configuration across scanner re-initializations
        self.hardware_config = {
            "sound_enabled": True,
            "beep_tone": 2,
            "beep_duration": 2,
            "led_mode": "auto",
            "live_preview_enabled": True,
            "roll_boxes_1_to_10": True
        }

        self.app = web.Application()
        self.setup_routes()
        self.app.on_cleanup.append(self.cleanup)

    def setup_routes(self):
        base_path = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(base_path)
        ui_path = os.path.join(project_root, 'userview')

        self.app.router.add_get('/', self.handle_index)
        self.app.router.add_get('/ws', self.handle_websocket)
        self.app.router.add_get('/preview', self.handle_preview)
        self.app.router.add_static('/css', os.path.join(ui_path, 'css'))
        self.app.router.add_static('/js', os.path.join(ui_path, 'js'))
        self.app.router.add_static('/assets', os.path.join(ui_path, 'assets'))

    async def handle_index(self, request):
        # EFTSuite's legacy scanner helper connects to ws://host:8888/.
        # Serve WebSockets at the root only for valid upgrade requests,
        # while preserving the existing HTML page for ordinary HTTP GETs.
        if web.WebSocketResponse().can_prepare(request).ok:
            return await self.handle_websocket(request)

        base_path = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(base_path)
        return web.FileResponse(os.path.join(project_root, 'userview', 'index.html'))

    async def handle_preview(self, request):
        """HTTP endpoint returning the latest live preview frame as a BMP image."""
        if self.scanner and self.scanner.last_preview_bmp:
            return web.Response(
                body=self.scanner.last_preview_bmp,
                content_type='image/bmp',
                headers={'Cache-Control': 'no-store, no-cache, must-revalidate'}
            )
        return web.Response(status=204)

    async def cleanup(self, app):
        logger.info("Cleaning up resources...")
        if self.scanner:
            self.scanner.close()

    async def broadcast(self, message_dict):
        """Send a message to all connected clients."""
        if not self.connected_clients:
            return

        json_msg = json.dumps(message_dict)
        to_remove = set()

        for ws in self.connected_clients:
            try:
                await ws.send_str(json_msg)
            except Exception:
                to_remove.add(ws)

        for ws in to_remove:
            self.connected_clients.remove(ws)

    async def init_scanner(self):
        if self.scanner is None:
            try:
                self.scanner = KojakScanner()
                self.scanner.update_config(self.hardware_config)
                cnt = self.scanner.get_device_count()
                if cnt > 0:
                    self.scanner.open(0)
                    self.scanner.update_config(self.hardware_config)
                    logger.info("Scanner initialized and opened.")
                    await self.broadcast({"type": "status", "message": "Device Initialized"})
                else:
                    logger.warning("No devices found on connection.")
                    await self.broadcast({"type": "status", "message": "No Device Found"})
            except Exception as e:
                logger.error(f"Failed to init scanner: {e}")
                self.scanner = None
                await self.broadcast({"type": "status", "message": f"Init Error: {str(e)}"})
        elif self.scanner and not self.scanner.is_open:
            if self.scanner.open(0):
                self.scanner.update_config(self.hardware_config)
                await self.broadcast({"type": "status", "message": "Device Initialized"})
            else:
                await self.broadcast({"type": "status", "message": "Failed to open device"})

    async def handle_websocket(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        client_addr = request.remote
        logger.info(f"Client connected: {client_addr}")
        self.connected_clients.add(ws)

        try:
            await ws.send_str(json.dumps({
                "type": "log",
                "message": "Connected to PortaPrint Server"
            }))
            await ws.send_str(json.dumps({
                "type": "config",
                "config": self.hardware_config
            }))

            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await self.process_message(ws, data)
                    except json.JSONDecodeError:
                        logger.error("Invalid JSON received")
                elif msg.type == WSMsgType.ERROR:
                    logger.error('ws connection closed with exception %s', ws.exception())

        except Exception as e:
            logger.error(f"WebSocket Error: {e}")
        finally:
            logger.info("Client disconnected")
            if ws in self.connected_clients:
                self.connected_clients.remove(ws)

        return ws

    async def process_message(self, ws, data):
        action = data.get("action")
        param = data.get("param")
        logger.debug(f"Received Action: {action}")

        if action == "GET_SYSTEM_INFO":
            ip = self.wifi.get_ip_address()
            wifi_needed = (ip == "127.0.0.1" or ip.startswith("169.254"))
            await ws.send_str(json.dumps({
                "type": "system_info",
                "ip": ip,
                "wifi_needed": wifi_needed,
                "connected": not wifi_needed
            }))

        elif action == "GET_CONFIG":
            await ws.send_str(json.dumps({
                "type": "config",
                "config": self.hardware_config
            }))

        elif action in ("UPDATE_CONFIG", "update_config"):
            cfg = data.get("config", data)
            for key in ("sound_enabled", "beep_tone", "beep_duration", "led_mode", "live_preview_enabled", "roll_boxes_1_to_10"):
                if key in cfg:
                    self.hardware_config[key] = cfg[key]
            if self.scanner:
                self.scanner.update_config(self.hardware_config)
            logger.info(f"Updated hardware config: {self.hardware_config}")
            await self.broadcast({
                "type": "config",
                "config": self.hardware_config,
                "message": "Hardware settings updated"
            })

        elif action == "TEST_BEEP":
            if not self.scanner or not self.scanner.is_open:
                await self.init_scanner()
            if self.scanner and self.scanner.is_open:
                self.scanner.play_beep_sequence(count=1)

        elif action == "WIFI_SCAN":
            await ws.send_str(json.dumps({"type": "status", "message": "Scanning Networks..."}))
            networks = self.wifi.scan_networks()
            await ws.send_str(json.dumps({
                "type": "wifi_list",
                "networks": networks
            }))
            await ws.send_str(json.dumps({"type": "status", "message": "Scan Complete"}))

        elif action == "WIFI_CONNECT":
            ssid = data.get("ssid")
            password = data.get("password")
            await ws.send_str(json.dumps({"type": "status", "message": f"Connecting to {ssid}..."}))

            success = self.wifi.connect_network(ssid, password)
            if success:
                ip = self.wifi.get_ip_address()
                await ws.send_str(json.dumps({
                    "type": "wifi_status",
                    "connected": True,
                    "message": "Connected Successfully"
                }))
                await self.broadcast({
                    "type": "system_info",
                    "ip": ip,
                    "wifi_needed": False,
                    "connected": True
                })
            else:
                await ws.send_str(json.dumps({
                    "type": "wifi_status",
                    "connected": False,
                    "message": "Connection Failed"
                }))

        elif action == "EXIT_APP":
            logger.info("Exit command received.")
            os._exit(0)

        elif action == "INIT":
            await self.init_scanner()

        elif action in ("START_CAPTURE", "scan"):
            await self.handle_capture(param)

        elif action == "CANCEL":
            if self.scanner and self.scanner.is_open:
                self.scanner.lib.IBSU_CancelCaptureImage(self.scanner.device_handle)
                await self.broadcast({"type": "status", "message": "Capture Cancelled"})

    async def handle_capture(self, param):
        logger.info(f"Starting capture with param: {param}")
        await self.broadcast({"type": "status", "message": "Starting Capture...", "scan_param": param})

        if self.scanner:
            try:
                logger.info("Forcing scanner close/re-init sequence...")
                self.scanner.close()
            except Exception as e:
                logger.error(f"Error closing scanner during re-init: {e}")
            finally:
                self.scanner = None

        await self.init_scanner()

        if self.scanner and self.scanner.is_open:
            self.scanner.last_bmp_data = None
            self.scanner.captured_image_data = None
            self.scanner.last_preview_bmp = None
            self.scanner.roll_state = None

            # Map FD-258 / EFT box numbers to IBSU_ImageType:
            # Boxes 1-10 -> Rolled Single Finger (1) by default (configurable)
            # Boxes 13-14 -> 4-Finger Slap (4)
            # Box 15 -> 2-Finger Flat Thumbs (3)
            roll_single = ENUM_IBSU_ROLL_SINGLE_FINGER if self.hardware_config.get("roll_boxes_1_to_10", True) else ENUM_IBSU_FLAT_SINGLE_FINGER
            scan_type = ENUM_IBSU_FLAT_SINGLE_FINGER
            if param is not None:
                try:
                    p_int = int(param)
                    if p_int in (13, 14):
                        scan_type = ENUM_IBSU_FLAT_FOUR_FINGERS
                    elif p_int == 15:
                        scan_type = ENUM_IBSU_FLAT_TWO_FINGERS
                    elif 1 <= p_int <= 10:
                        scan_type = roll_single
                    else:
                        scan_type = ENUM_IBSU_FLAT_SINGLE_FINGER
                except ValueError:
                    if param in ("L_SLAP", "R_SLAP"):
                        scan_type = ENUM_IBSU_FLAT_FOUR_FINGERS
                    elif param == "THUMBS":
                        scan_type = ENUM_IBSU_FLAT_TWO_FINGERS
                    elif param == "ROLL":
                        scan_type = ENUM_IBSU_ROLL_SINGLE_FINGER

            logger.info(f"Mapped Param {param} to ScanType {scan_type}")

            if self.scanner.start_capture(scan_type):
                logger.info(f"Capture started successfully for Type {scan_type}")
                last_sent_roll_state = None
                last_sent_preview = None

                # Allow up to 25 seconds for rolled/slap captures
                for i in range(250):
                    # Stream live roll state transitions to UI
                    if self.scanner.roll_state and self.scanner.roll_state != last_sent_roll_state:
                        last_sent_roll_state = self.scanner.roll_state
                        if last_sent_roll_state == "taking_acquisition":
                            await self.broadcast({
                                "type": "roll_state",
                                "state": "taking_acquisition",
                                "message": "Roll finger smoothly across the platen now..."
                            })
                        elif last_sent_roll_state == "complete_acquisition":
                            await self.broadcast({
                                "type": "roll_state",
                                "state": "complete_acquisition",
                                "message": "Roll complete! Processing image..."
                            })

                    # Stream live preview frames to WebSocket clients if enabled
                    if (
                        self.hardware_config.get("live_preview_enabled", True) and
                        self.scanner.last_preview_bmp and
                        self.scanner.last_preview_bmp is not last_sent_preview
                    ):
                        last_sent_preview = self.scanner.last_preview_bmp
                        b64_prev = base64.b64encode(last_sent_preview).decode('utf-8')
                        await self.broadcast({
                            "type": "preview",
                            "image": b64_prev
                        })

                    # Check for final cropped result image
                    if self.scanner.last_bmp_data:
                        logger.info(f"Image captured! Size: {len(self.scanner.last_bmp_data)}")
                        b64_img = base64.b64encode(self.scanner.last_bmp_data).decode('utf-8')
                        await self.broadcast({
                            "type": "result",
                            "image": b64_img,
                            "finger": str(param) if param is not None else "Unknown",
                            "quality": "Good"
                        })
                        logger.info("Sent result image.")
                        self.scanner.last_bmp_data = None
                        return

                    await asyncio.sleep(0.1)

                logger.warning("Capture timed out - no image received.")
                await self.broadcast({"type": "status", "message": "Capture Timed Out"})

                logger.info("Sending Cancel command due to timeout.")
                self.scanner.lib.IBSU_CancelCaptureImage(self.scanner.device_handle)
            else:
                logger.error(f"Failed to start capture for Type {scan_type}. Device open: {self.scanner.is_open}")
                await self.broadcast({"type": "status", "message": "Failed to start capture"})
        else:
            logger.error("Scanner not connected or null.")
            await self.broadcast({"type": "status", "message": "Scanner not connected"})

    async def handle_control_restart(self, request):
        """Handle restart request on port 8889"""
        logger.info("Received RESTART command on Control Server (8889)")
        asyncio.create_task(self.restart_server())
        return web.Response(text="Restarting...")

    async def restart_server(self):
        logger.info("Restarting server process...")
        await asyncio.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    async def start_control_server(self):
        control_app = web.Application()
        control_app.router.add_post('/restart', self.handle_control_restart)
        runner = web.AppRunner(control_app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', 8889)
        logger.info("Starting Control Server on port 8889...")
        await site.start()
        while True:
            await asyncio.sleep(3600)


async def main():
    server = PortaPrintServer()

    runner = web.AppRunner(server.app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8888)
    logger.info("Starting Main Server on port 8888...")
    await site.start()

    asyncio.create_task(server.start_control_server())

    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
