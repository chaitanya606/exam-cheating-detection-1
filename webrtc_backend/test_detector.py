import sys
import os
import cv2
import yaml

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
sys.path.insert(0, SRC_DIR)

# Load config
with open(os.path.join(PROJECT_ROOT, "config", "config.yaml"), 'r') as f:
    config = yaml.safe_load(f)

# Import and test
from detection import FaceDetector

detector = FaceDetector(config)

# Capture frame from webcam
cap = cv2.VideoCapture(0)
ret, frame = cap.read()

if ret:
    result = detector.detect_face(frame)
    print(f"Face detected: {result}")
else:
    print("Failed to capture frame")

cap.release()