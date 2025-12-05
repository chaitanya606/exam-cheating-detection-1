# webrtc_backend/screen_event_recorder.py
import os
import cv2
import datetime
from collections import deque

class ScreenEventRecorder:
    def __init__(self, buffer_size=5, after_frames=10, base_dir="screen_events"):
        self.buffer = deque(maxlen=buffer_size)
        self.after_frames = after_frames
        self.saving = False
        self.frames_left_after = 0
        self.current_folder = None
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def add_frame(self, frame):
        """
        frame: BGR numpy array
        """
        self.buffer.append(frame)
        if self.saving and self.current_folder:
            self._save_frame(frame, prefix="after")
            self.frames_left_after -= 1
            if self.frames_left_after <= 0:
                self.saving = False
                self.buffer.clear()

    def trigger_save(self, violation_type):
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        folder_name = f"{violation_type}_{ts}"
        self.current_folder = os.path.join(self.base_dir, folder_name)
        os.makedirs(self.current_folder, exist_ok=True)
        # save buffered frames
        for i, frame in enumerate(self.buffer):
            self._save_frame(frame, prefix=f"before_{i}")
        # start saving next after_frames frames
        self.saving = True
        self.frames_left_after = self.after_frames

    def _save_frame(self, frame, prefix="frame"):
        existing = os.listdir(self.current_folder)
        idx = len(existing)
        filename = f"{prefix}_{idx}.jpg"
        path = os.path.join(self.current_folder, filename)
        cv2.imwrite(path, frame)
