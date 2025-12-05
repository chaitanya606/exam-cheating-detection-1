# webrtc_backend/detection_integration.py
import asyncio
import json
import os
import sys
from datetime import datetime
# TEMP DEBUG - START
import os, sys, traceback
print("=== DETECTION DEBUG START ===")
print("cwd:", os.getcwd())
SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
print("expected SRC:", SRC, "exists:", os.path.exists(SRC))
print("first sys.path entries:", sys.path[:6])
# ensure src is in sys.path
if SRC not in sys.path:
    sys.path.insert(0, SRC)
try:
    import importlib
    m = importlib.import_module("detection")
    print("detection package loaded:", m, getattr(m, "__file__", None))
    print("detection contents:", dir(m)[:50])
except Exception as e:
    print("failed to import 'detection':", type(e), e)
    traceback.print_exc()
print("=== DETECTION DEBUG END ===")
# TEMP DEBUG - END


# ensure src path is importable
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# Try to import your detectors from src/detection; if not found, use stubs.
try:
    from detection.face_detection import FaceDetector
    from detection.eye_tracking import EyeTracker
    from detection.mouth_detection import MouthMonitor
    from detection.object_detection import ObjectDetector
    from detection.multi_face import MultiFaceDetector
    # If you have a config loader, you can load it here
    config = {}
    # initialize detectors once (heavy loads only once)
    face_detector = FaceDetector(config) if 'FaceDetector' in globals() else None
    eye_tracker = EyeTracker(config) if 'EyeTracker' in globals() else None
    mouth_monitor = MouthMonitor(config) if 'MouthMonitor' in globals() else None
    object_detector = ObjectDetector(config) if 'ObjectDetector' in globals() else None
    multi_face_detector = MultiFaceDetector(config) if 'MultiFaceDetector' in globals() else None
    DETECTORS_AVAILABLE = True
    print("Loaded detectors:", {
    "face": bool(face_detector),
    "eye": bool(eye_tracker),
    "mouth": bool(mouth_monitor),
    "object": bool(object_detector),
    "multi": bool(multi_face_detector)
    })

except Exception as e:
    print("Could not import real detectors – using stubs. Error:", e)
    DETECTORS_AVAILABLE = False
    face_detector = eye_tracker = mouth_monitor = object_detector = multi_face_detector = None

# For CPU-heavy sync detection, we delegate to threadpool
async def run_detection_pipeline(frame_bgr, kind="camera", timestamp=None, data_channel=None):
    """
    frame_bgr: OpenCV BGR numpy array
    kind: 'camera' or 'screen'
    data_channel: aiortc DataChannel to send violation messages
    """
    loop = asyncio.get_running_loop()

    def sync_detect():
        # placeholder detection logic
        results = {
            "face_present": False,
            "gaze_direction": "CENTER",
            "eye_ratio": 0.0,
            "mouth_moving": False,
            "multiple_faces": False,
            "objects_detected": False,
        }

        if DETECTORS_AVAILABLE:
            try:
                # Face detection
                results["face_present"] = bool(face_detector.detect_face(frame_bgr))
                # Eye tracking
                gaze, eye_ratio = eye_tracker.track_eyes(frame_bgr)
                results["gaze_direction"], results["eye_ratio"] = gaze, eye_ratio
                # Mouth
                results["mouth_moving"] = bool(mouth_monitor.monitor_mouth(frame_bgr))
                # Multi-face
                results["multiple_faces"] = bool(multi_face_detector.detect_multiple_faces(frame_bgr))
                # Objects (YOLO)
                results["objects_detected"] = bool(object_detector.detect_objects(frame_bgr))
            except Exception as e:
                # fallback if detectors error
                results["error"] = str(e)
        else:
            # Simple heuristic stub for demo:
            results["face_present"] = True
            results["gaze_direction"] = "CENTER"
            results["eye_ratio"] = 0.45
            results["mouth_moving"] = False
            results["multiple_faces"] = False
            results["objects_detected"] = False

        return results

    results = await loop.run_in_executor(None, sync_detect)
    # add timestamp info
    results["timestamp"] = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
    results["kind"] = kind

    # If a violation occurs, notify via datachannel (real-time)
    violation = check_for_violation(results)
    if violation and data_channel:
        try:
            payload = {"type": "VIOLATION", "violation": violation, "timestamp": results["timestamp"], "kind": kind}
            # data_channel is an aiortc RTCDataChannel object; .send accepts str or bytes
            data_channel.send(json.dumps(payload))
        except Exception as e:
            print("Failed to send datachannel message:", e)

    # Additionally, you can log violations into your existing logger here
    # e.g., violation_logger.log_violation(...)

    # For debugging: print short summary
    # print(f"[DETECT] {results['timestamp']} kind={kind} face={results['face_present']} gaze={results['gaze_direction']}")

    return results


def check_for_violation(results):
    """
    Simple rule-based violation detection.
    Replace with your domain logic / thresholds.
    """
    # Example: if face disappears or multiple faces or object detected or mouth moving
    if not results.get("face_present", True):
        return "FACE_DISAPPEARED"
    if results.get("multiple_faces", False):
        return "MULTIPLE_FACES"
    if results.get("objects_detected", False):
        return "OBJECT_DETECTED"
    if results.get("mouth_moving", False):
        return "MOUTH_MOVING"
    # Example gaze away rule:
    if results.get("gaze_direction") and results["gaze_direction"].upper() != "CENTER":
        # small tolerance: ignore "SLIGHT" etc. Adapt to your gaze labels.
        return "GAZE_AWAY"
    return None
