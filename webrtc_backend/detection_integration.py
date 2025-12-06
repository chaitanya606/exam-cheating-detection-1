import asyncio
import json
import os
import sys
from datetime import datetime
import traceback
import numpy as np
import yaml

# Path setup
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# Load config
def load_config():
    """Load config.yaml from project root"""
    config_path = os.path.join(PROJECT_ROOT, "config", "config.yaml")
    
    if not os.path.exists(config_path):
        print(f"✗ Config file not found at: {config_path}")
        return None
    
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        print(f"✓ Loaded config from: {config_path}")
        return config
    except Exception as e:
        print(f"✗ Failed to load config: {e}")
        return None

# Try importing real detectors
DETECTORS_AVAILABLE = False
detectors = {}
config = load_config()

if config:
    try:
        from detection import (
            FaceDetector,
            EyeTracker,
            MouthMonitor,
            MultiFaceDetector,
            ObjectDetector
        )
        
        print("✓ Imported detector classes")
        
        # Initialize detectors with loaded config
        detectors = {
            'face': FaceDetector(config),
            'eye': EyeTracker(config),
            'mouth': MouthMonitor(config),
            'multi_face': MultiFaceDetector(config),
            'object': ObjectDetector(config)
        }
        
        DETECTORS_AVAILABLE = True
        print("✓ All detectors initialized successfully")
        
    except Exception as e:
        print(f"✗ Could not initialize detectors: {e}")
        traceback.print_exc()
        DETECTORS_AVAILABLE = False
else:
    print("✗ Running without detectors (no config loaded)")


class ViolationTracker:
    """Track violations to avoid spam and implement thresholds"""
    def __init__(self):
        self.violation_counts = {}
        self.last_violation_time = {}
        self.thresholds = {
            'FACE_DISAPPEARED': 3,      # 3 consecutive frames
            'GAZE_AWAY': 5,              # 5 consecutive frames
            'MOUTH_MOVING': 10,          # 10 consecutive frames
            'MULTIPLE_FACES': 1,         # immediate
            'OBJECT_DETECTED': 2         # 2 consecutive frames
        }
        self.cooldown_seconds = 10.0  # match your config alert_cooldown
    
    def check_violation(self, violation_type):
        """Return True if violation threshold met and not in cooldown"""
        if not violation_type:
            # No violation - reset counts
            self.violation_counts.clear()
            return False
        
        # Increment violation count
        self.violation_counts[violation_type] = self.violation_counts.get(violation_type, 0) + 1
        
        # Check if threshold met
        threshold = self.thresholds.get(violation_type, 1)
        if self.violation_counts[violation_type] < threshold:
            return False
        
        # Check cooldown period
        now = datetime.now().timestamp()
        last_time = self.last_violation_time.get(violation_type, 0)
        if now - last_time < self.cooldown_seconds:
            return False
        
        # Violation triggered - reset and record
        self.last_violation_time[violation_type] = now
        self.violation_counts[violation_type] = 0
        return True
    
    def reset(self):
        """Reset all violation tracking"""
        self.violation_counts.clear()
        self.last_violation_time.clear()


violation_tracker = ViolationTracker()


async def run_detection_pipeline(frame_bgr, kind="camera", timestamp=None, data_channel=None):
    """
    Run detection pipeline on a frame and send violations if detected.
    
    Args:
        frame_bgr: OpenCV BGR numpy array
        kind: 'camera' or 'screen'
        timestamp: ISO timestamp string
        data_channel: aiortc DataChannel for sending violations
    """
    loop = asyncio.get_running_loop()
    
    def sync_detect():
        """Synchronous detection function (runs in thread pool)"""
        results = {
            "face_present": True,
            "gaze_direction": "CENTER",
            "eye_ratio": 0.45,
            "mouth_moving": False,
            "multiple_faces": False,
            "objects_detected": False,
            "error": None
        }
        
        if not DETECTORS_AVAILABLE:
            # No detectors available
            return results
        
        try:
            # 1. Face Detection
            face_result = detectors['face'].detect_face(frame_bgr)
            results["face_present"] = bool(face_result) if face_result is not None else True
            
            if results["face_present"]:
                # 2. Eye Tracking (only if face detected)
                try:
                    gaze_result = detectors['eye'].track_eyes(frame_bgr)
                    if gaze_result:
                        # Handle different return formats
                        if isinstance(gaze_result, tuple):
                            gaze, eye_ratio = gaze_result
                            results["gaze_direction"] = str(gaze) if gaze else "CENTER"
                            results["eye_ratio"] = float(eye_ratio) if eye_ratio else 0.45
                        else:
                            results["gaze_direction"] = str(gaze_result)
                except Exception as e:
                    print(f"[WARN] Eye tracking failed: {e}")
                
                # 3. Mouth Monitoring
                try:
                    mouth_result = detectors['mouth'].monitor_mouth(frame_bgr)
                    results["mouth_moving"] = bool(mouth_result) if mouth_result is not None else False
                except Exception as e:
                    print(f"[WARN] Mouth monitoring failed: {e}")
            
            # 4. Multi-face Detection
            try:
                multi_faces = detectors['multi_face'].detect_multiple_faces(frame_bgr)
                results["multiple_faces"] = bool(multi_faces) if multi_faces is not None else False
            except Exception as e:
                print(f"[WARN] Multi-face detection failed: {e}")
            
            # 5. Object Detection
            try:
                objects = detectors['object'].detect_objects(frame_bgr)
                results["objects_detected"] = bool(objects) if objects is not None else False
            except Exception as e:
                print(f"[WARN] Object detection failed: {e}")
            
        except Exception as e:
            results["error"] = str(e)
            print(f"[ERROR] Detection pipeline failed: {e}")
            traceback.print_exc()
        
        return results
    
    # Run detection in thread pool (non-blocking)
    try:
        results = await asyncio.wait_for(
            loop.run_in_executor(None, sync_detect),
            timeout=5.0
        )
    except asyncio.TimeoutError:
        print(f"[TIMEOUT] Detection took >5s for {kind}")
        return None
    except Exception as e:
        print(f"[ERROR] Detection execution failed: {e}")
        return None
    
    # Add metadata
    results["timestamp"] = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    results["kind"] = kind
    
    # Check for violations (only for camera feed)
    if kind == "camera":
        violation = check_for_violation(results)
        
        # Use violation tracker to implement thresholds and cooldowns
        if violation and violation_tracker.check_violation(violation):
            print(f"🚨 [VIOLATION] {violation} at {results['timestamp']}")
            
            # Send violation to client via DataChannel
            if data_channel:
                if data_channel.readyState == "open":
                    try:
                        payload = {
                            "type": "VIOLATION",
                            "violation": violation,
                            "timestamp": results["timestamp"],
                            "details": {
                                "face_present": results["face_present"],
                                "gaze_direction": results["gaze_direction"],
                                "mouth_moving": results["mouth_moving"],
                                "multiple_faces": results["multiple_faces"],
                                "objects_detected": results["objects_detected"]
                            }
                        }
                        data_channel.send(json.dumps(payload))
                        print(f"✓ [SENT] Violation to client: {violation}")
                    except Exception as e:
                        print(f"✗ [ERROR] Failed to send violation: {e}")
                else:
                    print(f"⚠ [WARNING] DataChannel not open (state: {data_channel.readyState})")
            else:
                print(f"⚠ [WARNING] No DataChannel available for sending violation")
    
    return results


def check_for_violation(results):
    """
    Check detection results for violations.
    Returns violation type string or None.
    
    Priority order (most severe first):
    1. Multiple faces (immediate concern)
    2. Face disappeared (lost subject)
    3. Suspicious objects
    4. Looking away
    5. Talking/mouth moving
    """
    if results.get("error"):
        return None
    
    # 1. Multiple people detected
    if results.get("multiple_faces", False):
        return "MULTIPLE_FACES"
    
    # 2. Face disappeared from frame
    if not results.get("face_present", True):
        return "FACE_DISAPPEARED"
    
    # 3. Suspicious objects (phone, notes, etc.)
    if results.get("objects_detected", False):
        return "OBJECT_DETECTED"
    
    # 4. Gaze away from screen
    gaze = results.get("gaze_direction", "CENTER").upper()
    if gaze and gaze not in ["CENTER", "UNKNOWN", "NONE"]:
        return "GAZE_AWAY"
    
    # 5. Mouth moving (talking/reading aloud)
    if results.get("mouth_moving", False):
        return "MOUTH_MOVING"
    
    return None


async def detect_window_switch(prev_frame, curr_frame):
    """
    Detect significant screen content change (window switch/tab change).
    
    Args:
        prev_frame: Previous screen frame (BGR numpy array)
        curr_frame: Current screen frame (BGR numpy array)
    
    Returns:
        bool: True if significant change detected
    """
    if prev_frame is None or curr_frame is None:
        return False
    
    try:
        # Ensure same dimensions
        if prev_frame.shape != curr_frame.shape:
            return False
        
        # Compute pixel-wise difference
        diff = np.abs(prev_frame.astype(np.float32) - curr_frame.astype(np.float32))
        mean_diff = np.mean(diff)
        
        # Threshold for "significant" change (tune based on testing)
        # Higher = less sensitive, Lower = more sensitive
        WINDOW_SWITCH_THRESHOLD = 35.0
        
        is_switch = mean_diff > WINDOW_SWITCH_THRESHOLD
        
        if is_switch:
            print(f"🔄 [WINDOW SWITCH] Detected (diff={mean_diff:.1f})")
        
        return is_switch
        
    except Exception as e:
        print(f"✗ [ERROR] Window switch detection failed: {e}")
        return False


# Log initial state
print("\n" + "="*60)
print("Detection Integration Status:")
print("="*60)
print(f"Config loaded: {'✓' if config else '✗'}")
print(f"Detectors available: {'✓' if DETECTORS_AVAILABLE else '✗'}")
if DETECTORS_AVAILABLE:
    print(f"Loaded detectors: {list(detectors.keys())}")
print("="*60 + "\n")

# # webrtc_backend/detection_integration.py
# import asyncio
# import json
# import os
# import sys
# from datetime import datetime
# import os, sys
# from detection import (
#     FaceDetector,
#     EyeTracker,
#     MouthMonitor,
#     MultiFaceDetector,
#     ObjectDetector
# )

# # 1️⃣ Ensure src is importable BEFORE anything else
# PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# SRC_DIR = os.path.join(PROJECT_ROOT, "src")
# if SRC_DIR not in sys.path:
#     sys.path.insert(0, SRC_DIR)

# # 2️⃣ Now safely import detectors
# try:
#     from detection import (
#         FaceDetector,
#         EyeTracker,
#         MouthMonitor,
#         MultiFaceDetector,
#         ObjectDetector
#     )

#     config = {}

#     face_detector = FaceDetector(config)
#     eye_tracker = EyeTracker(config)
#     mouth_monitor = MouthMonitor(config)
#     multi_face_detector = MultiFaceDetector(config)
#     object_detector = ObjectDetector(config)

#     DETECTORS_AVAILABLE = True
#     print("Loaded REAL detectors.")

# except Exception as e:
#     DETECTORS_AVAILABLE = False
#     print("Could not import real detectors – using stubs. Error:", e)


# # 3️⃣ Run detection in threadpool
# async def run_detection_pipeline(frame_bgr, kind="camera", timestamp=None, data_channel=None):
#     loop = asyncio.get_running_loop()

#     def sync_detect():
#         results = {
#             "face_present": False,
#             "gaze_direction": "CENTER",
#             "eye_ratio": 0,
#             "mouth_moving": False,
#             "multiple_faces": False,
#             "objects_detected": False,
#         }

#         if DETECTORS_AVAILABLE:
#             try:
#                 results["face_present"] = bool(face_detector.detect_face(frame_bgr))
#                 results["gaze_direction"], results["eye_ratio"] = eye_tracker.track_eyes(frame_bgr)
#                 results["mouth_moving"] = bool(mouth_monitor.monitor_mouth(frame_bgr))
#                 results["multiple_faces"] = bool(multi_face_detector.detect_multiple_faces(frame_bgr))
#                 results["objects_detected"] = bool(object_detector.detect_objects(frame_bgr))
#             except Exception as e:
#                 results["error"] = str(e)

#         return results

#     results = await loop.run_in_executor(None, sync_detect)
#     results["timestamp"] = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")

#     violation = check_for_violation(results)
#     if violation and data_channel:
#         data_channel.send(json.dumps({
#             "type": "VIOLATION",
#             "violation": violation,
#             "timestamp": results["timestamp"],
#             "kind": kind,
#         }))

#     return results


# def check_for_violation(results):
#     if not results.get("face_present", True):
#         return "FACE_DISAPPEARED"
#     if results.get("multiple_faces", False):
#         return "MULTIPLE_FACES"
#     if results.get("objects_detected", False):
#         return "OBJECT_DETECTED"
#     if results.get("mouth_moving", False):
#         return "MOUTH_MOVING"
#     if results.get("gaze_direction", "").upper() != "CENTER":
#         return "GAZE_AWAY"
#     return None


# # # webrtc_backend/detection_integration.py
# # import asyncio
# # import json
# # import os
# # import sys
# # from datetime import datetime
# # # TEMP DEBUG - START
# # import os, sys, traceback
# # print("=== DETECTION DEBUG START ===")
# # print("cwd:", os.getcwd())
# # SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
# # print("expected SRC:", SRC, "exists:", os.path.exists(SRC))
# # print("first sys.path entries:", sys.path[:6])
# # # ensure src is in sys.path
# # if SRC not in sys.path:
# #     sys.path.insert(0, SRC)
# # try:
# #     import importlib
# #     m = importlib.import_module("detection")
# #     print("detection package loaded:", m, getattr(m, "__file__", None))
# #     print("detection contents:", dir(m)[:50])
# # except Exception as e:
# #     print("failed to import 'detection':", type(e), e)
# #     traceback.print_exc()
# # print("=== DETECTION DEBUG END ===")
# # # TEMP DEBUG - END


# # # ensure src path is importable
# # CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# # PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
# # SRC_DIR = os.path.join(PROJECT_ROOT, "src")
# # if SRC_DIR not in sys.path:
# #     sys.path.insert(0, SRC_DIR)

# # # Try to import your detectors from src/detection; if not found, use stubs.
# # try:
# #     from detection import (
# #         FaceDetector,
# #         EyeTracker,
# #         MouthMonitor,
# #         MultiFaceDetector,
# #         ObjectDetector
# #     )

# #     config = {}
# #     face_detector = FaceDetector(config)
# #     eye_tracker = EyeTracker(config)
# #     mouth_monitor = MouthMonitor(config)
# #     object_detector = ObjectDetector(config)
# #     multi_face_detector = MultiFaceDetector(config)

# #     DETECTORS_AVAILABLE = True
# #     print("Loaded REAL detectors.")

# # except Exception as e:
# #     DETECTORS_AVAILABLE = False
# #     print("Could not import real detectors – using stubs. Error:", e)


# # # For CPU-heavy sync detection, we delegate to threadpool
# # async def run_detection_pipeline(frame_bgr, kind="camera", timestamp=None, data_channel=None):
# #     """
# #     frame_bgr: OpenCV BGR numpy array
# #     kind: 'camera' or 'screen'
# #     data_channel: aiortc DataChannel to send violation messages
# #     """
# #     loop = asyncio.get_running_loop()

# #     def sync_detect():
# #         # placeholder detection logic
# #         results = {
# #             "face_present": False,
# #             "gaze_direction": "CENTER",
# #             "eye_ratio": 0.0,
# #             "mouth_moving": False,
# #             "multiple_faces": False,
# #             "objects_detected": False,
# #         }

# #         if DETECTORS_AVAILABLE:
# #             try:
# #                 # Face detection
# #                 results["face_present"] = bool(face_detector.detect_face(frame_bgr))
# #                 # Eye tracking
# #                 gaze, eye_ratio = eye_tracker.track_eyes(frame_bgr)
# #                 results["gaze_direction"], results["eye_ratio"] = gaze, eye_ratio
# #                 # Mouth
# #                 results["mouth_moving"] = bool(mouth_monitor.monitor_mouth(frame_bgr))
# #                 # Multi-face
# #                 results["multiple_faces"] = bool(multi_face_detector.detect_multiple_faces(frame_bgr))
# #                 # Objects (YOLO)
# #                 results["objects_detected"] = bool(object_detector.detect_objects(frame_bgr))
# #             except Exception as e:
# #                 # fallback if detectors error
# #                 results["error"] = str(e)
# #         else:
# #             # Simple heuristic stub for demo:
# #             results["face_present"] = True
# #             results["gaze_direction"] = "CENTER"
# #             results["eye_ratio"] = 0.45
# #             results["mouth_moving"] = False
# #             results["multiple_faces"] = False
# #             results["objects_detected"] = False

# #         return results

# #     results = await loop.run_in_executor(None, sync_detect)
# #     # add timestamp info
# #     results["timestamp"] = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
# #     results["kind"] = kind

# #     # If a violation occurs, notify via datachannel (real-time)
# #     violation = check_for_violation(results)
# #     if violation and data_channel:
# #         try:
# #             payload = {"type": "VIOLATION", "violation": violation, "timestamp": results["timestamp"], "kind": kind}
# #             # data_channel is an aiortc RTCDataChannel object; .send accepts str or bytes
# #             data_channel.send(json.dumps(payload))
# #         except Exception as e:
# #             print("Failed to send datachannel message:", e)

# #     # Additionally, you can log violations into your existing logger here
# #     # e.g., violation_logger.log_violation(...)

# #     # For debugging: print short summary
# #     # print(f"[DETECT] {results['timestamp']} kind={kind} face={results['face_present']} gaze={results['gaze_direction']}")

# #     return results


# # def check_for_violation(results):
# #     """
# #     Simple rule-based violation detection.
# #     Replace with your domain logic / thresholds.
# #     """
# #     # Example: if face disappears or multiple faces or object detected or mouth moving
# #     if not results.get("face_present", True):
# #         return "FACE_DISAPPEARED"
# #     if results.get("multiple_faces", False):
# #         return "MULTIPLE_FACES"
# #     if results.get("objects_detected", False):
# #         return "OBJECT_DETECTED"
# #     if results.get("mouth_moving", False):
# #         return "MOUTH_MOVING"
# #     # Example gaze away rule:
# #     if results.get("gaze_direction") and results["gaze_direction"].upper() != "CENTER":
# #         # small tolerance: ignore "SLIGHT" etc. Adapt to your gaze labels.
# #         return "GAZE_AWAY"
# #     return None
