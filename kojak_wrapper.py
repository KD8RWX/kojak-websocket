import ctypes
import os
import sys
import time
import logging
from ctypes import *

# Configure logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("KojakWrapper")

# --- Constants & Enums ---
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

ENUM_IBSU_ROLL_SINGLE_FINGER = 1
ENUM_IBSU_FLAT_SINGLE_FINGER = 2
ENUM_IBSU_FLAT_TWO_FINGERS = 3
ENUM_IBSU_FLAT_FOUR_FINGERS = 4
ENUM_IBSU_FLAT_THREE_FINGERS = 5

IBSU_IMG_RES_500 = 500

IBSU_OPTION_AUTO_CONTRAST = 1
IBSU_OPTION_AUTO_CAPTURE = 2
IBSU_OPTION_IGNORE_FINGER_COUNT = 4

# --- Event Enums ---
ENUM_IBSU_ESSENTIAL_EVENT_TAKING_ACQUISITION = 3
ENUM_IBSU_ESSENTIAL_EVENT_COMPLETE_ACQUISITION = 4
ENUM_IBSU_ESSENTIAL_EVENT_RESULT_IMAGE_EX = 12

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

# --- Callback Types ---
CALLBACK_RESULT_IMAGE_EX_TYPE = WINFUNCTYPE(None, 
                                        c_int, c_void_p, c_int, 
                                        IBSU_ImageData, c_int, c_int, c_int, 
                                        POINTER(IBSU_ImageData), POINTER(IBSU_SegmentPosition))

CALLBACK_ROLL_EVENT_TYPE = WINFUNCTYPE(None, c_int, c_void_p, c_int)

def load_library():
    cwd = os.getcwd()
    lib_name = "IBScanUltimate.dll"
    found_path = None
    for root, dirs, files in os.walk(cwd):
        if lib_name in files:
            found_path = os.path.join(root, lib_name)
            break
    if not found_path:
        return None
    try:
        return CDLL(found_path, mode=RTLD_GLOBAL)
    except OSError:
        return None

class KojakScanner:
    def __init__(self):
        self.lib = load_library()
        if not self.lib:
            raise Exception("Failed to load IBScanUltimate library")
        
        self.device_handle = c_int(-1)
        self.is_open = False
        self.image_callback = None
        self.taking_acq_callback = None
        self.complete_acq_callback = None
        self.captured_image_data = None
        
        # Function Signatures
        self.lib.IBSU_GetDeviceCount.argtypes = [POINTER(c_int)]
        self.lib.IBSU_GetDeviceCount.restype = c_int
        
        self.lib.IBSU_OpenDevice.argtypes = [c_int, POINTER(c_int)]
        self.lib.IBSU_OpenDevice.restype = c_int
        
        self.lib.IBSU_CloseDevice.argtypes = [c_int]
        self.lib.IBSU_CloseDevice.restype = c_int
        
        self.lib.IBSU_BeginCaptureImage.argtypes = [c_int, c_int, c_int, c_int]
        self.lib.IBSU_BeginCaptureImage.restype = c_int

        try:
            # IBSU_SetBeeper(handle, beepPattern, soundTone (0-2), duration, reserved_1, reserved_2)[cite: 62, 85, 86, 141, 165]
            self.lib.IBSU_SetBeeper.argtypes = [c_int, c_int, c_ulong, c_ulong, c_ulong, c_ulong]
            self.lib.IBSU_SetBeeper.restype = c_int
        except AttributeError:
            pass

        try:
            self.lib.IBSU_GetOperableBeeper.argtypes = [c_int, POINTER(c_int)]
            self.lib.IBSU_GetOperableBeeper.restype = c_int
        except AttributeError:
            pass
        
        self.lib.IBSU_CancelCaptureImage.argtypes = [c_int]
        self.lib.IBSU_CancelCaptureImage.restype = c_int
        
        self.lib.IBSU_IsCaptureActive.argtypes = [c_int, POINTER(c_int)]
        self.lib.IBSU_IsCaptureActive.restype = c_int
        
        self.lib.IBSU_RegisterCallbacks.argtypes = [c_int, c_int, c_void_p, c_void_p]
        self.lib.IBSU_RegisterCallbacks.restype = c_int

    def get_device_count(self):
        count = c_int(0)
        res = self.lib.IBSU_GetDeviceCount(byref(count))
        return count.value if res >= 0 else 0

    def open(self, index=0):
        if self.is_open: return True
        handle = c_int(0)
        res = self.lib.IBSU_OpenDevice(index, byref(handle))
        if res == IBSU_STATUS_OK:
            self.device_handle = handle
            self.is_open = True
            self.register_callbacks()
            return True
        return False

    def close(self):
        if self.is_open:
            try:
                self.lib.IBSU_CloseDevice(self.device_handle)
            except: pass
            self.is_open = False

    def get_bmp_bytes(self, data, width, height):
        file_size = 14 + 40 + 1024 + len(data)
        offset = 14 + 40 + 1024
        header = b'BM' + file_size.to_bytes(4, 'little') + (0).to_bytes(4, 'little') + offset.to_bytes(4, 'little')
        info = (40).to_bytes(4, 'little') + width.to_bytes(4, 'little') + height.to_bytes(4, 'little') + \
               (1).to_bytes(2, 'little') + (8).to_bytes(2, 'little') + (0).to_bytes(4, 'little') + \
               len(data).to_bytes(4, 'little') + (2835).to_bytes(4, 'little') * 2 + (0).to_bytes(4, 'little') * 2
        palette = b''.join([bytes([i, i, i, 0]) for i in range(256)])
        return header + info + palette + data

    def crop_to_fingerprint(self, data, width, height, image_type):
        import numpy as np
        max_w, max_h = (800, 750) if image_type == 1 else ((800, 1000) if image_type == 2 else (1600, 1000))
        img = (np.fliplr(np.frombuffer(data, dtype=np.uint8).reshape((height, width))) if image_type == 4 else np.frombuffer(data, dtype=np.uint8).reshape((height, width))).copy()
        
        mask = img < 230
        ds = 8
        h_ds, w_ds = height // ds, width // ds
        small_mask = mask[:h_ds*ds, :w_ds*ds].reshape(h_ds, ds, w_ds, ds).sum(axis=(1, 3)) >= 8

        visited = np.zeros_like(small_mask, dtype=bool)
        clusters = []
        for r in range(h_ds):
            for c in range(w_ds):
                if small_mask[r, c] and not visited[r, c]:
                    stack = [(r, c)]
                    visited[r, c] = True
                    coords = []
                    while stack:
                        cr, cc = stack.pop()
                        coords.append((cr, cc))
                        for nr in range(max(0, cr - 2), min(h_ds, cr + 3)):
                            for nc in range(max(0, cc - 2), min(w_ds, cc + 3)):
                                if small_mask[nr, nc] and not visited[nr, nc]:
                                    visited[nr, nc] = True
                                    stack.append((nr, nc))
                    clusters.append(coords)

        if not clusters:
            return data, width, height

        clusters.sort(key=len, reverse=True)
        largest_size = len(clusters[0])
        min_valid_size = max(12, int(largest_size * 0.15)) if image_type == 4 else int(largest_size * 0.5)

        valid_mask_ds = np.zeros_like(small_mask, dtype=bool)
        for cl in clusters:
            if len(cl) >= min_valid_size:
                for (r, c) in cl:
                    valid_mask_ds[r, c] = True

        full_valid_mask = np.kron(valid_mask_ds, np.ones((ds, ds), dtype=bool))
        pad_h, pad_w = height - full_valid_mask.shape[0], width - full_valid_mask.shape[1]
        if pad_h > 0 or pad_w > 0:
            full_valid_mask = np.pad(full_valid_mask, ((0, pad_h), (0, pad_w)), mode='edge')

        img[~full_valid_mask] = 255
        ys, xs = np.where(img < 235)
        if len(ys) == 0 or len(xs) == 0:
            return data, width, height

        margin = 35
        y0, y1 = max(0, int(ys.min()) - margin), min(height, int(ys.max()) + margin)
        x0, x1 = max(0, int(xs.min()) - margin), min(width, int(xs.max()) + margin)
        crop_w, crop_h = min(max_w, x1 - x0), min(max_h, y1 - y0)
        crop_w = (crop_w // 4) * 4

        cropped = img[y0 : y0 + crop_h, x0 : x0 + crop_w].copy()
        return cropped.tobytes(), crop_w, crop_h

    def save_bmp(self, filename, data, width, height):
        bmp_data = self.get_bmp_bytes(data, width, height)
        with open(filename, 'wb') as f:
            f.write(bmp_data)

    # --- Callbacks for Hardware Control and Custom Tones ---
    def on_taking_acquisition(self, deviceHandle, pContext, imageType):
        # 1. Start Roll: 1 Beep[cite: 136]
        logger.info(">>> TAKING ACQUISITION (START ROLL) <<<")
        self.sound_scanner_buzzer(pattern=0, tone=0, duration=4)

    def on_complete_acquisition(self, deviceHandle, pContext, imageType):
        # 2. Finished Rolling: Double Beep[cite: 136]
        logger.info(">>> COMPLETE ACQUISITION (ROLL FINISHED) <<<")
        self.sound_scanner_buzzer(pattern=0, tone=1, duration=3)
        time.sleep(0.06)
        self.sound_scanner_buzzer(pattern=0, tone=1, duration=3)

    def on_image_callback(self, deviceHandle, pContext, imageStatus, image, imageType, fingerCount, segmentCount, pSegmentArray, pSegmentPos):
        if imageStatus >= 0 and image.IsFinal:
            # 3. Very End / Final Image Saved: Triple Beep[cite: 137]
            logger.info(f"FINAL IMAGE RECEIVED! Size: {image.Width}x{image.Height}")
            for _ in range(3):
                self.sound_scanner_buzzer(pattern=0, tone=2, duration=3)
                time.sleep(0.06)

            try:
                pitch = abs(image.Pitch)
                buffer_size = image.Height * pitch
                if buffer_size > 0 and image.Buffer:
                    data = string_at(image.Buffer, buffer_size)
                    self.captured_image_data = data
                    cropped_data, cw, ch = self.crop_to_fingerprint(data, image.Width, image.Height, imageType)
                    self.last_bmp_data = self.get_bmp_bytes(cropped_data, cw, ch)
                    self.save_bmp(f"capture_{int(time.time())}.bmp", cropped_data, cw, ch)
            except Exception as e:
                logger.error(f"Error processing image: {e}")

    def register_callbacks(self):
        self.image_callback = CALLBACK_RESULT_IMAGE_EX_TYPE(self.on_image_callback)
        self.lib.IBSU_RegisterCallbacks(self.device_handle, ENUM_IBSU_ESSENTIAL_EVENT_RESULT_IMAGE_EX, self.image_callback, None)

        self.taking_acq_callback = CALLBACK_ROLL_EVENT_TYPE(self.on_taking_acquisition)
        self.lib.IBSU_RegisterCallbacks(self.device_handle, ENUM_IBSU_ESSENTIAL_EVENT_TAKING_ACQUISITION, self.taking_acq_callback, None)

        self.complete_acq_callback = CALLBACK_ROLL_EVENT_TYPE(self.on_complete_acquisition)
        self.lib.IBSU_RegisterCallbacks(self.device_handle, ENUM_IBSU_ESSENTIAL_EVENT_COMPLETE_ACQUISITION, self.complete_acq_callback, None)

        logger.info("All callbacks registered successfully")

    def sound_scanner_buzzer(self, pattern=0, tone=1, duration=4):
        if not self.is_open: return
        try:
            if hasattr(self.lib, "IBSU_SetBeeper"):
                self.lib.IBSU_SetBeeper(self.device_handle, pattern, tone, duration, 0, 0)
        except Exception:
            pass

    def start_capture(self, image_type=ENUM_IBSU_FLAT_SINGLE_FINGER):
        if not self.is_open: return False
        opts = IBSU_OPTION_AUTO_CONTRAST | IBSU_OPTION_AUTO_CAPTURE
        logger.info(f"Starting capture with Type: {image_type}")
        res = self.lib.IBSU_BeginCaptureImage(self.device_handle, image_type, IBSU_IMG_RES_500, opts)
        if res == IBSU_STATUS_OK:
            # 4. Start Capture Sequence: 1 Beep
            self.sound_scanner_buzzer(pattern=0, tone=1, duration=3)
            return True
        return False

    def is_capturing(self):
        if not self.is_open: return False
        active = c_int(0)
        res = self.lib.IBSU_IsCaptureActive(self.device_handle, byref(active))
        return active.value != 0 if res == IBSU_STATUS_OK else False