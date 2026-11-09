import ctypes
import os
import sys
import time
import logging
import threading
from ctypes import *
from collections import deque
import numpy as np

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("KojakWrapper")

# --- Constants & Enums (from IBScanUltimateApi_defs.h) ---
IBSU_STATUS_OK = 0
IBSU_ERR_INVALID_PARAM_VALUE = -1
IBSU_ERR_MEM_ALLOC = -2
IBSU_ERR_NOT_SUPPORT = -3
IBSU_ERR_HEX_DUMP_FAILED = -4
IBSU_ERR_DEVICE_BUSY = -5
IBSU_ERR_DEVICE_NOT_FOUND = -6
IBSU_ERR_DEVICE_NOT_INITIALIZED = -7
IBSU_ERR_DEVICE_INVALID_STATE = -8
IBSU_ERR_DEVICE_IO = -9
IBSU_ERR_DEVICE_NOT_AUTHORIZED = -207

# Image Types
ENUM_IBSU_TYPE_NONE = 0
ENUM_IBSU_ROLL_SINGLE_FINGER = 1
ENUM_IBSU_FLAT_SINGLE_FINGER = 2
ENUM_IBSU_FLAT_TWO_FINGERS = 3
ENUM_IBSU_FLAT_FOUR_FINGERS = 4
ENUM_IBSU_FLAT_THREE_FINGERS = 5

# Image Resolutions
IBSU_IMG_RES_500 = 500

# Capture Options
IBSU_OPTION_AUTO_CONTRAST = 1
IBSU_OPTION_AUTO_CAPTURE = 2
IBSU_OPTION_IGNORE_FINGER_COUNT = 4

# --- Event Enums (from IBScanUltimateApi_defs.h) ---
ENUM_IBSU_ESSENTIAL_EVENT_DEVICE_COUNT = 0
ENUM_IBSU_ESSENTIAL_EVENT_COMMUNICATION_BREAK = 1
ENUM_IBSU_ESSENTIAL_EVENT_PREVIEW_IMAGE = 2
ENUM_IBSU_ESSENTIAL_EVENT_TAKING_ACQUISITION = 3
ENUM_IBSU_ESSENTIAL_EVENT_COMPLETE_ACQUISITION = 4
ENUM_IBSU_ESSENTIAL_EVENT_RESULT_IMAGE = 5
ENUM_IBSU_OPTIONAL_EVENT_FINGER_QUALITY = 6
ENUM_IBSU_OPTIONAL_EVENT_FINGER_COUNT = 7
ENUM_IBSU_ESSENTIAL_EVENT_INIT_PROGRESS = 8
ENUM_IBSU_OPTIONAL_EVENT_CLEAR_PLATEN_AT_CAPTURE = 9
ENUM_IBSU_ESSENTIAL_EVENT_ASYNC_OPEN_DEVICE = 10
ENUM_IBSU_OPTIONAL_EVENT_NOTIFY_MESSAGE = 11
ENUM_IBSU_ESSENTIAL_EVENT_RESULT_IMAGE_EX = 12

# --- Beeper & LED Enums / Defines ---
ENUM_IBSU_BEEPER_TYPE_NONE = 0
ENUM_IBSU_BEEPER_TYPE_MONOTONE = 1

ENUM_IBSU_BEEP_PATTERN_GENERIC = 0
ENUM_IBSU_BEEP_PATTERN_REPEAT = 1

ENUM_IBSU_LE_OPERATION_AUTO = 0
ENUM_IBSU_LE_OPERATION_ON = 1
ENUM_IBSU_LE_OPERATION_OFF = 2

ENUM_IBSU_LED_TYPE_NONE = 0
ENUM_IBSU_LED_TYPE_TSCAN = 1
ENUM_IBSU_LED_TYPE_FSCAN = 2

IBSU_LED_NONE = 0x00000000
IBSU_LED_ALL = 0xFFFFFFFF
IBSU_LED_F_BLINK_GREEN = 0x10000000
IBSU_LED_F_BLINK_RED = 0x20000000

# --- Structures ---
class IBSU_ImageData(Structure):
    _fields_ = [
        ("Buffer", c_void_p),
        ("Width", c_uint),
        ("Height", c_uint),
        ("ResolutionX", c_double),
        ("ResolutionY", c_double),
        ("FrameTime", c_double),
        ("Pitch", c_int),
        ("BitsPerPixel", c_ubyte),
        ("Format", c_int),
        ("IsFinal", c_int),
        ("ProcessThres", c_uint)
    ]

class IBSU_SegmentPosition(Structure):
    _fields_ = [
        ("x1", c_short), ("y1", c_short),
        ("x2", c_short), ("y2", c_short),
        ("x3", c_short), ("y3", c_short),
        ("x4", c_short), ("y4", c_short)
    ]

# Use WINFUNCTYPE on Windows and CFUNCTYPE on Linux/macOS
if os.name == 'nt':
    CALLBACK_FUNC_FACTORY = WINFUNCTYPE
else:
    CALLBACK_FUNC_FACTORY = CFUNCTYPE

# Callback Type Definitions
CALLBACK_PREVIEW_IMAGE_TYPE = CALLBACK_FUNC_FACTORY(
    None,
    c_int,          # deviceHandle
    c_void_p,       # pContext
    IBSU_ImageData  # image (passed by value)
)

CALLBACK_ACQUISITION_TYPE = CALLBACK_FUNC_FACTORY(
    None,
    c_int,          # deviceHandle
    c_void_p,       # pContext
    c_int           # imageType
)

CALLBACK_RESULT_IMAGE_EX_TYPE = CALLBACK_FUNC_FACTORY(
    None,
    c_int,                        # deviceHandle
    c_void_p,                     # pContext
    c_int,                        # imageStatus
    IBSU_ImageData,               # image (passed by value)
    c_int,                        # imageType
    c_int,                        # detectedFingerCount
    c_int,                        # segmentImageArrayCount
    POINTER(IBSU_ImageData),      # pSegmentImageArray
    POINTER(IBSU_SegmentPosition) # pSegmentPositionArray
)

# --- Helper to load library (Cross-Platform Windows & Linux) ---
def load_library():
    cwd = os.getcwd()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    logger.info(f"Current working directory: {cwd}")

    if os.name == 'nt':
        lib_names = ["IBScanUltimate64.dll", "IBScanUltimate.dll"]
        search_roots = [
            cwd,
            script_dir,
            r"C:\portaprint\websocket",
            r"C:\Program Files\Integrated Biometrics\IBScanUltimateSDK\bin",
            r"C:\Program Files (x86)\Integrated Biometrics\IBScanUltimateSDK\bin"
        ]
        found_path = None
        for root_path in search_roots:
            if not os.path.exists(root_path):
                continue
            for root, dirs, files in os.walk(root_path):
                for candidate in lib_names:
                    if candidate in files:
                        found_path = os.path.join(root, candidate)
                        break
                if found_path:
                    break
            if found_path:
                break

        if not found_path:
            # Try loading directly from system PATH
            for candidate in lib_names:
                try:
                    lib = WinDLL(candidate)
                    logger.info(f"Loaded {candidate} from system PATH")
                    return lib
                except OSError:
                    pass
            logger.error("Could not find IBScanUltimate.dll in search paths or PATH.")
            return None

        lib_dir = os.path.dirname(found_path)
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(lib_dir)
            except Exception:
                pass
        try:
            logger.info(f"Loading IBScanUltimate DLL from: {found_path}")
            return WinDLL(found_path)
        except OSError as e:
            logger.error(f"Failed to load Windows DLL: {e}")
            return None

    else:
        # Linux / Raspberry Pi
        lib_name = "libIBScanUltimate.so"
        found_path = None

        logger.info(f"Searching for {lib_name}...")
        for search_base in [cwd, script_dir]:
            for root, dirs, files in os.walk(search_base):
                if lib_name in files:
                    found_path = os.path.join(root, lib_name)
                    logger.info(f"Found library at: {found_path}")
                    break
            if found_path:
                break

        if not found_path:
            standards = ["/usr/lib/libIBScanUltimate.so", "/usr/local/lib/libIBScanUltimate.so"]
            for p in standards:
                if os.path.exists(p):
                    found_path = p
                    break

        if not found_path:
            logger.error(f"Could not find {lib_name} in {cwd} or standard paths.")
            return None

        lib_dir = os.path.dirname(found_path)
        dependencies = ["libusb-1.0.so", "libuvc.so", "libAKXUS.so.2"]

        for dep_name in dependencies:
            dep_path = os.path.join(lib_dir, dep_name)
            if not os.path.exists(dep_path):
                if "libAKXUS" in dep_name:
                    for f in os.listdir(lib_dir):
                        if f.startswith("libAKXUS.so"):
                            dep_path = os.path.join(lib_dir, f)
                            break
            if os.path.exists(dep_path):
                try:
                    CDLL(dep_path, mode=RTLD_GLOBAL)
                except Exception as e:
                    logger.warning(f"Failed to load dependency {dep_name}: {e}")

        try:
            logger.info(f"Loading IBScanUltimate from: {found_path}")
            lib = CDLL(found_path, mode=RTLD_GLOBAL)
            logger.info("Library loaded successfully!")
            return lib
        except OSError as e:
            logger.error(f"Failed to load library: {e}")
            return None


# --- Wrapper Class ---
class KojakScanner:
    def __init__(self):
        self.lib = load_library()
        if not self.lib:
            raise Exception("Failed to load IBScanUltimate library")

        self.device_handle = c_int(-1)
        self.is_open = False

        # Keep callback references alive to prevent garbage collection
        self.image_callback = None
        self.preview_callback = None
        self.taking_acq_callback = None
        self.complete_acq_callback = None

        self.captured_image_data = None
        self.last_bmp_data = None
        self.last_preview_bmp = None
        self.last_preview_time = 0.0
        self.roll_state = None
        self.current_image_type = ENUM_IBSU_FLAT_SINGLE_FINGER

        # Hardware capabilities & configurable settings
        self.has_beeper = False
        self.has_leds = False
        self.sound_enabled = True
        self.beep_tone = 2       # 0 = Low, 1 = Med, 2 = High
        self.beep_duration = 2   # In 25ms increments (2 = 50ms)
        self.led_mode = "auto"   # "auto", "on", "off"
        self.live_preview_enabled = True
        self.roll_boxes_1_to_10 = True

        self._setup_function_signatures()

    def _setup_function_signatures(self):
        self.lib.IBSU_GetDeviceCount.argtypes = [POINTER(c_int)]
        self.lib.IBSU_GetDeviceCount.restype = c_int

        self.lib.IBSU_OpenDevice.argtypes = [c_int, POINTER(c_int)]
        self.lib.IBSU_OpenDevice.restype = c_int

        self.lib.IBSU_CloseDevice.argtypes = [c_int]
        self.lib.IBSU_CloseDevice.restype = c_int

        self.lib.IBSU_BeginCaptureImage.argtypes = [c_int, c_int, c_int, c_uint]
        self.lib.IBSU_BeginCaptureImage.restype = c_int

        self.lib.IBSU_CancelCaptureImage.argtypes = [c_int]
        self.lib.IBSU_CancelCaptureImage.restype = c_int

        self.lib.IBSU_IsCaptureActive.argtypes = [c_int, POINTER(c_int)]
        self.lib.IBSU_IsCaptureActive.restype = c_int

        self.lib.IBSU_RegisterCallbacks.argtypes = [c_int, c_int, c_void_p, c_void_p]
        self.lib.IBSU_RegisterCallbacks.restype = c_int

        # Optional Beeper APIs
        if hasattr(self.lib, "IBSU_GetOperableBeeper"):
            self.lib.IBSU_GetOperableBeeper.argtypes = [c_int, POINTER(c_int)]
            self.lib.IBSU_GetOperableBeeper.restype = c_int

        if hasattr(self.lib, "IBSU_SetBeeper"):
            self.lib.IBSU_SetBeeper.argtypes = [c_int, c_int, c_uint, c_uint, c_uint, c_uint]
            self.lib.IBSU_SetBeeper.restype = c_int

        # Optional LED / LE Film APIs
        if hasattr(self.lib, "IBSU_GetOperableLEDs"):
            self.lib.IBSU_GetOperableLEDs.argtypes = [c_int, POINTER(c_int), POINTER(c_int), POINTER(c_uint)]
            self.lib.IBSU_GetOperableLEDs.restype = c_int

        if hasattr(self.lib, "IBSU_SetLEDs"):
            self.lib.IBSU_SetLEDs.argtypes = [c_int, c_uint]
            self.lib.IBSU_SetLEDs.restype = c_int

        if hasattr(self.lib, "IBSU_SetLEOperationMode"):
            self.lib.IBSU_SetLEOperationMode.argtypes = [c_int, c_int]
            self.lib.IBSU_SetLEOperationMode.restype = c_int

    def get_device_count(self):
        count = c_int(0)
        res = self.lib.IBSU_GetDeviceCount(byref(count))
        if res < 0:
            return 0
        return count.value

    def open(self, index=0):
        if self.is_open:
            return True
        handle = c_int(0)
        logger.info(f"Opening device index {index}...")
        res = self.lib.IBSU_OpenDevice(index, byref(handle))
        if res == IBSU_STATUS_OK:
            self.device_handle = handle
            self.is_open = True
            self._detect_hardware_features()
            self.apply_led_mode(self.led_mode)
            self.register_callbacks()
            logger.info(f"Device opened! Handle: {handle.value}")
            return True
        else:
            logger.error(f"OpenDevice failed: {res}")
            return False

    def _detect_hardware_features(self):
        if hasattr(self.lib, "IBSU_GetOperableBeeper"):
            beeper_type = c_int(0)
            res = self.lib.IBSU_GetOperableBeeper(self.device_handle, byref(beeper_type))
            if res == IBSU_STATUS_OK and beeper_type.value != ENUM_IBSU_BEEPER_TYPE_NONE:
                self.has_beeper = True
                logger.info(f"Hardware beeper detected (Type: {beeper_type.value})")
            else:
                # Still allow attempting IBSU_SetBeeper on Kojak units
                self.has_beeper = True
                logger.info(f"IBSU_GetOperableBeeper returned res={res}, type={beeper_type.value}; enabling beeper calls")

        if hasattr(self.lib, "IBSU_GetOperableLEDs"):
            led_type = c_int(0)
            led_count = c_int(0)
            operable_leds = c_uint(0)
            res = self.lib.IBSU_GetOperableLEDs(
                self.device_handle, byref(led_type), byref(led_count), byref(operable_leds)
            )
            if res == IBSU_STATUS_OK and led_count.value > 0:
                self.has_leds = True
                logger.info(f"Hardware LEDs detected: count={led_count.value}, mask=0x{operable_leds.value:08X}")

    def play_beep_sequence(self, count=1, tone=None, duration=None, gap=0.08):
        """
        Triggers hardware buzzer tone sequences asynchronously:
        - 1 beep: Start capture / Taking roll acquisition
        - 2 beeps: Roll acquisition complete
        - 3 beeps: Final image processed and saved
        """
        if not self.is_open or not self.sound_enabled or not hasattr(self.lib, "IBSU_SetBeeper"):
            return

        use_tone = self.beep_tone if tone is None else int(tone)
        use_dur = self.beep_duration if duration is None else int(duration)
        use_tone = max(0, min(2, use_tone))
        use_dur = max(1, min(200, use_dur))

        def _beep_worker():
            for i in range(count):
                if not self.is_open:
                    break
                try:
                    self.lib.IBSU_SetBeeper(
                        self.device_handle,
                        ENUM_IBSU_BEEP_PATTERN_GENERIC,
                        c_uint(use_tone),
                        c_uint(use_dur),
                        c_uint(0),
                        c_uint(0)
                    )
                except Exception as e:
                    logger.debug(f"SetBeeper error: {e}")
                if i < count - 1:
                    time.sleep(use_dur * 0.025 + gap)

        threading.Thread(target=_beep_worker, daemon=True).start()

    def apply_led_mode(self, mode="auto"):
        self.led_mode = mode
        if not self.is_open:
            return
        if hasattr(self.lib, "IBSU_SetLEOperationMode"):
            le_val = ENUM_IBSU_LE_OPERATION_AUTO
            if mode == "on":
                le_val = ENUM_IBSU_LE_OPERATION_ON
            elif mode == "off":
                le_val = ENUM_IBSU_LE_OPERATION_OFF
            try:
                self.lib.IBSU_SetLEOperationMode(self.device_handle, le_val)
            except Exception as e:
                logger.debug(f"SetLEOperationMode error: {e}")

        if hasattr(self.lib, "IBSU_SetLEDs"):
            try:
                if mode == "on":
                    self.lib.IBSU_SetLEDs(self.device_handle, c_uint(IBSU_LED_ALL))
                elif mode == "off":
                    self.lib.IBSU_SetLEDs(self.device_handle, c_uint(IBSU_LED_NONE))
                else:
                    self.lib.IBSU_SetLEDs(self.device_handle, c_uint(IBSU_LED_NONE))
            except Exception as e:
                logger.debug(f"SetLEDs error: {e}")

    def update_config(self, config_dict):
        if "sound_enabled" in config_dict:
            self.sound_enabled = bool(config_dict["sound_enabled"])
        if "beep_tone" in config_dict:
            self.beep_tone = max(0, min(2, int(config_dict["beep_tone"])))
        if "beep_duration" in config_dict:
            self.beep_duration = max(1, min(50, int(config_dict["beep_duration"])))
        if "led_mode" in config_dict:
            self.apply_led_mode(str(config_dict["led_mode"]))
        if "live_preview_enabled" in config_dict:
            self.live_preview_enabled = bool(config_dict["live_preview_enabled"])
        if "roll_boxes_1_to_10" in config_dict:
            self.roll_boxes_1_to_10 = bool(config_dict["roll_boxes_1_to_10"])

    def get_config(self):
        return {
            "sound_enabled": self.sound_enabled,
            "beep_tone": self.beep_tone,
            "beep_duration": self.beep_duration,
            "led_mode": self.led_mode,
            "live_preview_enabled": self.live_preview_enabled,
            "roll_boxes_1_to_10": self.roll_boxes_1_to_10
        }

    def close(self):
        if self.is_open:
            logger.info("Closing device...")
            try:
                self.lib.IBSU_CloseDevice(self.device_handle)
            except Exception:
                pass
            self.is_open = False

    def get_bmp_bytes(self, data, width, height):
        # Pad row stride to 4-byte boundary for valid BMP format
        row_pad = (4 - (width % 4)) % 4
        if row_pad > 0:
            arr = np.frombuffer(data, dtype=np.uint8).reshape((height, width))
            padded = np.pad(arr, ((0, 0), (0, row_pad)), mode='constant', constant_values=255)
            data = padded.tobytes()

        file_size = 14 + 40 + 1024 + len(data)
        offset = 14 + 40 + 1024

        header = (
            b'BM' +
            file_size.to_bytes(4, 'little') +
            (0).to_bytes(4, 'little') +
            offset.to_bytes(4, 'little')
        )
        info = (
            (40).to_bytes(4, 'little') +
            width.to_bytes(4, 'little', signed=True) +
            height.to_bytes(4, 'little', signed=True) +
            (1).to_bytes(2, 'little') +
            (8).to_bytes(2, 'little') +
            (0).to_bytes(4, 'little') +
            len(data).to_bytes(4, 'little') +
            (19685).to_bytes(4, 'little') * 2 +  # 500 DPI = ~19685 pixels/meter
            (0).to_bytes(4, 'little') * 2
        )

        palette = b''.join([bytes([i, i, i, 0]) for i in range(256)])
        return header + info + palette + data

    def save_bmp(self, filename, data, width, height):
        bmp_data = self.get_bmp_bytes(data, width, height)
        with open(filename, 'wb') as f:
            f.write(bmp_data)

    def crop_to_fingerprint(self, raw_data, width, height, pitch, image_type):
        """
        Filters out isolated platen smudges/dust via 8x downsampled connected-component
        density masking, corrects 4-finger slap horizontal orientation via np.fliplr,
        and crops/pads to standard target dimensions:
          - Rolled Single Finger (type 1): 800x750
          - Flat Single Finger (type 2):   800x1000
          - 2-Finger / 4-Finger Slap (3/4): 1600x1000
        """
        if image_type == ENUM_IBSU_ROLL_SINGLE_FINGER:
            target_w, target_h = 800, 750
        elif image_type == ENUM_IBSU_FLAT_SINGLE_FINGER:
            target_w, target_h = 800, 1000
        elif image_type in (ENUM_IBSU_FLAT_FOUR_FINGERS, ENUM_IBSU_FLAT_TWO_FINGERS):
            target_w, target_h = 1600, 1000
        else:
            target_w, target_h = 800, 1000

        abs_pitch = abs(pitch)
        img = np.frombuffer(raw_data, dtype=np.uint8).reshape((height, abs_pitch))[:, :width].copy()

        # Fix 4-finger slap horizontal orientation so slaps are not processed backwards
        if image_type == ENUM_IBSU_FLAT_FOUR_FINGERS:
            img = np.fliplr(img)

        # 1. Platen Defect Filtering via 8x grid connected components
        grid = 8
        gh, gw = height // grid, width // grid
        if gh > 0 and gw > 0:
            cropped_for_grid = img[:gh * grid, :gw * grid]
            dark_mask = (cropped_for_grid < 230).reshape(gh, grid, gw, grid).mean(axis=(1, 3)) > 0.12

            visited = np.zeros((gh, gw), dtype=bool)
            components = []

            for r in range(gh):
                for c in range(gw):
                    if dark_mask[r, c] and not visited[r, c]:
                        comp = []
                        queue = deque([(r, c)])
                        visited[r, c] = True
                        while queue:
                            cr, cc = queue.popleft()
                            comp.append((cr, cc))
                            for dr in (-1, 0, 1):
                                for dc in (-1, 0, 1):
                                    if dr == 0 and dc == 0:
                                        continue
                                    nr, nc = cr + dr, cc + dc
                                    if 0 <= nr < gh and 0 <= nc < gw and dark_mask[nr, nc] and not visited[nr, nc]:
                                        visited[nr, nc] = True
                                        queue.append((nr, nc))
                        components.append(comp)

            if components:
                max_len = max(len(c) for c in components)
                # Keep cluster threshold lower (15%) for 4-finger slaps so all 4 fingers stay intact
                min_ratio = 0.15 if image_type in (ENUM_IBSU_FLAT_FOUR_FINGERS, ENUM_IBSU_FLAT_TWO_FINGERS) else 0.25
                min_size = max(3, int(max_len * min_ratio))

                valid_grid = np.zeros((gh, gw), dtype=bool)
                for comp in components:
                    if len(comp) >= min_size:
                        for r, c in comp:
                            # Dilate by 1 grid cell to preserve faint ridge edges
                            r0, r1 = max(0, r - 1), min(gh, r + 2)
                            c0, c1 = max(0, c - 1), min(gw, c + 2)
                            valid_grid[r0:r1, c0:c1] = True

                full_mask = np.repeat(np.repeat(valid_grid, grid, axis=0), grid, axis=1)
                pad_h = height - full_mask.shape[0]
                pad_w = width - full_mask.shape[1]
                if pad_h > 0 or pad_w > 0:
                    full_mask = np.pad(full_mask, ((0, pad_h), (0, pad_w)), mode='edge')

                # Wipe isolated background platen defects to pure white (255)
                img[~full_mask] = 255

        # 2. Dimension-Aware Bounding Box Cropping
        ys, xs = np.where(img < 230)
        if len(xs) > 0 and len(ys) > 0:
            cx = int((xs.min() + xs.max()) // 2)
            cy = int((ys.min() + ys.max()) // 2)
        else:
            cx, cy = width // 2, height // 2

        canvas = np.full((target_h, target_w), 255, dtype=np.uint8)

        src_x0 = cx - target_w // 2
        src_y0 = cy - target_h // 2
        src_x1 = src_x0 + target_w
        src_y1 = src_y0 + target_h

        dst_x0 = max(0, -src_x0)
        dst_y0 = max(0, -src_y0)
        dst_x1 = target_w - max(0, src_x1 - width)
        dst_y1 = target_h - max(0, src_y1 - height)

        clamp_x0 = max(0, src_x0)
        clamp_y0 = max(0, src_y0)
        clamp_x1 = min(width, src_x1)
        clamp_y1 = min(height, src_y1)

        if clamp_x1 > clamp_x0 and clamp_y1 > clamp_y0:
            canvas[dst_y0:dst_y1, dst_x0:dst_x1] = img[clamp_y0:clamp_y1, clamp_x0:clamp_x1]

        return canvas.tobytes(), target_w, target_h

    def on_preview_callback(self, deviceHandle, pContext, image):
        if not self.live_preview_enabled:
            return
        now = time.time()
        # Throttle live preview encoding to ~10 FPS
        if now - self.last_preview_time < 0.10:
            return
        if image.Buffer and image.Width > 0 and image.Height > 0:
            try:
                self.last_preview_time = now
                pitch = abs(image.Pitch)
                buffer_size = image.Height * pitch
                raw_data = string_at(image.Buffer, buffer_size)
                arr = np.frombuffer(raw_data, dtype=np.uint8).reshape((image.Height, pitch))[:, :image.Width]
                # Downsample by 2x for fast, lightweight WebSocket streaming
                ds = arr[::2, ::2].copy()
                if self.current_image_type == ENUM_IBSU_FLAT_FOUR_FINGERS:
                    ds = np.fliplr(ds)
                self.last_preview_bmp = self.get_bmp_bytes(ds.tobytes(), ds.shape[1], ds.shape[0])
            except Exception as e:
                logger.debug(f"Preview frame error: {e}")

    def on_taking_acquisition_callback(self, deviceHandle, pContext, imageType):
        logger.info(f"Roll State: TAKING ACQUISITION (imageType={imageType})")
        self.roll_state = "taking_acquisition"
        # Single beep when finger placement is locked and rolling begins
        self.play_beep_sequence(count=1)

    def on_complete_acquisition_callback(self, deviceHandle, pContext, imageType):
        logger.info(f"Roll State: COMPLETE ACQUISITION (imageType={imageType})")
        self.roll_state = "complete_acquisition"
        # Double beep when roll across platen completes
        self.play_beep_sequence(count=2)

    def on_image_callback(self, deviceHandle, pContext, imageStatus, image, imageType, fingerCount, segmentCount, pSegmentArray, pSegmentPos):
        if imageStatus >= 0:
            if image.IsFinal:
                logger.info(f"FINAL IMAGE RECEIVED! Size: {image.Width}x{image.Height}, Pitch: {image.Pitch}, Type: {imageType}")
                try:
                    pitch = abs(image.Pitch)
                    buffer_size = image.Height * pitch

                    if buffer_size > 0 and image.Buffer:
                        data = string_at(image.Buffer, buffer_size)
                        cropped_bytes, out_w, out_h = self.crop_to_fingerprint(
                            data, image.Width, image.Height, image.Pitch, imageType
                        )
                        self.captured_image_data = cropped_bytes
                        self.last_bmp_data = self.get_bmp_bytes(cropped_bytes, out_w, out_h)
                        self.roll_state = "result_image"

                        # Triple beep on final image save
                        self.play_beep_sequence(count=3)

                        # Save debug BMP
                        self.save_bmp(f"capture_{int(time.time())}.bmp", cropped_bytes, out_w, out_h)
                except Exception as e:
                    logger.error(f"Error processing final image: {e}")
        else:
            logger.warning(f"Callback Error Status: {imageStatus}")

    def register_callbacks(self):
        self.image_callback = CALLBACK_RESULT_IMAGE_EX_TYPE(self.on_image_callback)
        res = self.lib.IBSU_RegisterCallbacks(
            self.device_handle,
            ENUM_IBSU_ESSENTIAL_EVENT_RESULT_IMAGE_EX,
            self.image_callback,
            None
        )
        if res != IBSU_STATUS_OK:
            logger.error(f"Failed to register RESULT_IMAGE_EX callback: {res}")

        self.preview_callback = CALLBACK_PREVIEW_IMAGE_TYPE(self.on_preview_callback)
        self.lib.IBSU_RegisterCallbacks(
            self.device_handle,
            ENUM_IBSU_ESSENTIAL_EVENT_PREVIEW_IMAGE,
            self.preview_callback,
            None
        )

        self.taking_acq_callback = CALLBACK_ACQUISITION_TYPE(self.on_taking_acquisition_callback)
        self.lib.IBSU_RegisterCallbacks(
            self.device_handle,
            ENUM_IBSU_ESSENTIAL_EVENT_TAKING_ACQUISITION,
            self.taking_acq_callback,
            None
        )

        self.complete_acq_callback = CALLBACK_ACQUISITION_TYPE(self.on_complete_acquisition_callback)
        self.lib.IBSU_RegisterCallbacks(
            self.device_handle,
            ENUM_IBSU_ESSENTIAL_EVENT_COMPLETE_ACQUISITION,
            self.complete_acq_callback,
            None
        )
        logger.info("All scanner callbacks (Result, Preview, TakingAcquisition, CompleteAcquisition) registered.")

    def start_capture(self, image_type=ENUM_IBSU_FLAT_SINGLE_FINGER):
        if not self.is_open:
            return False
        self.current_image_type = image_type
        self.roll_state = None
        self.last_preview_bmp = None
        opts = IBSU_OPTION_AUTO_CONTRAST | IBSU_OPTION_AUTO_CAPTURE | IBSU_OPTION_IGNORE_FINGER_COUNT
        logger.info(f"Starting capture with Type: {image_type}")
        res = self.lib.IBSU_BeginCaptureImage(self.device_handle, image_type, IBSU_IMG_RES_500, opts)
        if res == IBSU_STATUS_OK:
            logger.info("Capture started successfully")
            # Single beep to prompt user that platen is ready
            self.play_beep_sequence(count=1)
            return True
        else:
            logger.error(f"BeginCaptureImage failed: {res}")
            return False

    def is_capturing(self):
        if not self.is_open:
            return False
        active = c_int(0)
        res = self.lib.IBSU_IsCaptureActive(self.device_handle, byref(active))
        return active.value != 0 if res == IBSU_STATUS_OK else False


if __name__ == "__main__":
    scanner = KojakScanner()
    cnt = scanner.get_device_count()
    logger.info(f"Devices found: {cnt}")

    if cnt > 0 and scanner.open(0):
        try:
            if scanner.start_capture():
                logger.info("\n=== PLEASE PLACE FINGER ON SCANNER ===\n")
                for _ in range(150):
                    if scanner.captured_image_data:
                        logger.info("Capture completed and data received!")
                        break
                    time.sleep(0.1)
        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        finally:
            scanner.close()
