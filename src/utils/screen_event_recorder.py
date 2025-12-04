# src/utils/screen_event_recorder.py

import os
import cv2
import datetime
from collections import deque


class ScreenEventRecorder:
    """
    Keeps only the last N frames in memory.
    When trigger_save() is called, it saves:
      - All buffered frames (before event)
      - Next M frames (after event)
    and then resets.
    """

    def __init__(self, buffer_size=5, after_frames=10, base_dir="screen_events"):
        """
        buffer_size: how many frames to keep BEFORE event
        after_frames: how many frames to keep AFTER event
        base_dir: where to store event clips
        """
        self.buffer = deque(maxlen=buffer_size)
        self.after_frames = after_frames
        self.saving = False
        self.frames_left_after = 0
        self.current_folder = None

        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def add_frame(self, frame):
        """
        Call this on EVERY captured screen frame.
        It maintains the rolling buffer and, if saving is active,
        also saves the current frame to disk.
        """
        # Always keep last N frames in memory
        self.buffer.append(frame)

        # If we're in "saving after event" mode, save this frame
        if self.saving and self.current_folder is not None:
            self._save_frame(frame, prefix="after")
            self.frames_left_after -= 1

            # Done capturing post-event frames -> stop saving & reset buffer
            if self.frames_left_after <= 0:
                self.saving = False
                self.buffer.clear()

    def trigger_save(self, violation_type):
        """
        Call this when a window-switch (or any violation) is detected.
        It will:
          1. Create a folder for this event
          2. Save all buffered frames as "before" frames
          3. Start recording the next `after_frames` frames
        """
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        folder_name = f"{violation_type}_{timestamp}"
        self.current_folder = os.path.join(self.base_dir, folder_name)
        os.makedirs(self.current_folder, exist_ok=True)

        # 1) Save the buffered frames (context before event)
        for i, frame in enumerate(self.buffer):
            self._save_frame(frame, prefix=f"before_{i}")

        # 2) Start saving the next M frames
        self.saving = True
        self.frames_left_after = self.after_frames

    def _save_frame(self, frame, prefix="frame"):
        """
        Internal helper to save a single frame as a JPEG.
        """
        existing_files = os.listdir(self.current_folder)
        idx = len(existing_files)
        filename = f"{prefix}_{idx}.jpg"
        path = os.path.join(self.current_folder, filename)
        cv2.imwrite(path, frame)
