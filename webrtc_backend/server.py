import asyncio
import json
import logging
from av import VideoFrame
import os
import sys
from aiohttp import web
from datetime import datetime

# Path setup
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# aiortc imports
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaBlackhole

# local helpers
from screen_event_recorder import ScreenEventRecorder
from detection_integration import run_detection_pipeline, detect_window_switch

# logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webrtc_server")

ROOT = PROJECT_ROOT
STATIC = os.path.join(CURRENT_DIR, "static")

# keep track of peerconnections
pcs = set()

# Global screen recorder
screen_recorder = ScreenEventRecorder(
    buffer_size=5, 
    after_frames=10, 
    base_dir=os.path.join(PROJECT_ROOT, "recordings", "screen_events")
)


def avframe_to_bgr(frame: VideoFrame):
    """Convert av.VideoFrame to OpenCV BGR numpy array"""
    return frame.to_ndarray(format="bgr24")


class VideoConsumer:
    """
    Consume incoming video track and run detection pipeline.
    Handles both camera and screen tracks.
    """
    def __init__(self, track, kind, pc_id, data_channel=None):
        self.track = track
        self.kind = kind  # 'camera' or 'screen'
        self.pc_id = pc_id
        self.data_channel = data_channel
        self.prev_screen_frame = None
        self.frame_count = 0
        self._task = asyncio.create_task(self._run())
    
    async def _run(self):
        logger.info("[%s] VideoConsumer started for %s", self.pc_id, self.kind)
        
        try:
            while True:
                # Receive frame
                frame = await self.track.recv()  # av.VideoFrame
                self.frame_count += 1
                
                # Convert to BGR
                bgr = avframe_to_bgr(frame)
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
                
                # Handle based on track type
                if self.kind == "screen":
                    await self._handle_screen_frame(bgr, ts)
                elif self.kind == "camera":
                    await self._handle_camera_frame(bgr, ts)
                
        except asyncio.CancelledError:
            logger.info("[%s] VideoConsumer cancelled for %s", self.pc_id, self.kind)
        except Exception as e:
            logger.exception("[%s] Error in VideoConsumer %s: %s", self.pc_id, self.kind, e)
    
    async def _handle_camera_frame(self, bgr, ts):
        """Process camera frame for face/eye/mouth detection"""
        # Run detection every frame (or skip frames if needed)
        if self.frame_count % 2 == 0:  # process every 2nd frame
            asyncio.create_task(
                run_detection_pipeline(
                    bgr, 
                    kind="camera", 
                    timestamp=ts, 
                    data_channel=self.data_channel
                )
            )
    
    async def _handle_screen_frame(self, bgr, ts):
        """Process screen frame for window switching"""
        # Add to recorder buffer
        try:
            screen_recorder.add_frame(bgr)
        except Exception as e:
            logger.exception("screen_recorder.add_frame failed: %s", e)
        
        # Check for window switch (every 5 frames to reduce CPU)
        if self.frame_count % 5 == 0:
            if self.prev_screen_frame is not None:
                is_switch = await detect_window_switch(self.prev_screen_frame, bgr)
                
                if is_switch:
                    logger.warning("[%s] WINDOW SWITCH detected!", self.pc_id)
                    
                    # Save screen recording
                    screen_recorder.trigger_save("WINDOW_SWITCH")
                    
                    # Send violation to client
                    if self.data_channel and self.data_channel.readyState == "open":
                        try:
                            payload = {
                                "type": "VIOLATION",
                                "violation": "WINDOW_SWITCH",
                                "timestamp": ts,
                                "details": {"frame_count": self.frame_count}
                            }
                            self.data_channel.send(json.dumps(payload))
                            logger.info("[%s] Sent WINDOW_SWITCH violation", self.pc_id)
                        except Exception as e:
                            logger.error("Failed to send window switch violation: %s", e)
            
            # Update previous frame
            self.prev_screen_frame = bgr.copy()


async def index(request):
    """Serve test.html"""
    path = os.path.join(STATIC, "test.html")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return web.Response(text=content, content_type="text/html")


async def status(request):
    """Check server and detector status"""
    from detection_integration import DETECTORS_AVAILABLE, config, detectors
    
    return web.json_response({
        "status": "running",
        "detectors_available": DETECTORS_AVAILABLE,
        "config_loaded": config is not None,
        "active_connections": len(pcs),
        "detectors": list(detectors.keys()) if DETECTORS_AVAILABLE else []
    })


async def offer(request):
    """
    Handle WebRTC offer from client.
    Expects JSON: { sdp: ..., type: "offer" }
    """
    params = await request.json()
    offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
    
    pc = RTCPeerConnection()
    pcs.add(pc)
    pc_id = f"PC-{id(pc)}"
    logger.info("%s - New RTCPeerConnection", pc_id)
    
    media_blackhole = MediaBlackhole()
    pc._video_consumers = []
    pc._data_channels = []
    
    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        logger.info("%s - Connection state: %s", pc_id, pc.connectionState)
        if pc.connectionState == "failed" or pc.connectionState == "closed":
            await pc.close()
            pcs.discard(pc)
    
    @pc.on("datachannel")
    def on_datachannel(channel):
        logger.info("%s - DataChannel opened: %s", pc_id, channel.label)
        pc._data_channels.append(channel)
        
        @channel.on("message")
        def on_message(message):
            logger.info("%s - DataChannel message: %s", pc_id, message)
            
            try:
                obj = json.loads(message)
                
                # Handle commands from client
                if obj.get("cmd") == "trigger_screen_save":
                    screen_recorder.trigger_save("MANUAL_TRIGGER")
                    channel.send(json.dumps({
                        "type": "ACK", 
                        "detail": "screen_saved"
                    }))
                    
                elif obj.get("cmd") == "ping":
                    channel.send(json.dumps({
                        "type": "PONG",
                        "timestamp": datetime.now().isoformat()
                    }))
                    
            except json.JSONDecodeError:
                logger.warning("Received non-JSON message: %s", message)
        
        @channel.on("close")
        def on_close():
            logger.info("%s - DataChannel closed", pc_id)
    
    @pc.on("track")
    def on_track(track):
        logger.info("%s - Track received: kind=%s id=%s", pc_id, track.kind, track.id)
        
        if track.kind == "video":
            # Determine if camera or screen based on order
            # First video track = camera, second = screen
            idx = len(pc._video_consumers)
            kind = "camera" if idx == 0 else "screen"
            
            logger.info("%s - Treating video track as: %s", pc_id, kind)
            
            # Get data channel for sending violations
            data_channel = pc._data_channels[0] if pc._data_channels else None
            if not data_channel:
                logger.warning("%s - No data channel available for %s", pc_id, kind)
            
            # Create consumer
            consumer = VideoConsumer(
                track=track, 
                kind=kind, 
                pc_id=pc_id, 
                data_channel=data_channel
            )
            pc._video_consumers.append(consumer)
            
        elif track.kind == "audio":
            # Drop audio or process if needed
            media_blackhole.addTrack(track)
            logger.info("%s - Audio track added to blackhole", pc_id)
        
        @track.on("ended")
        async def on_ended():
            logger.info("%s - Track ended: %s", pc_id, track.id)
    
    # Set remote description
    await pc.setRemoteDescription(offer)
    
    # Create answer
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    
    # Return SDP answer
    response = {
        "sdp": pc.localDescription.sdp, 
        "type": pc.localDescription.type
    }
    logger.info("%s - Answer created and sent", pc_id)
    
    return web.json_response(response)


async def on_shutdown(app):
    """Clean shutdown of all peer connections"""
    logger.info("Shutting down server, closing %d peer connections...", len(pcs))
    
    close_tasks = [pc.close() for pc in pcs]
    await asyncio.gather(*close_tasks, return_exceptions=True)
    
    pcs.clear()
    logger.info("Shutdown complete")


# Create web application
app = web.Application()
app.router.add_get("/", index)
app.router.add_post("/offer", offer)
app.router.add_static("/static/", STATIC, show_index=True)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    logger.info("Starting WebRTC Proctoring Server on port 8080...")
    web.run_app(app, host="0.0.0.0", port=8080)
    
# # webrtc_backend/server.py
# import asyncio
# import json
# import logging
# from av import VideoFrame
# import os
# import sys
# from aiohttp import web
# from datetime import datetime

# # ensure project `src` is on sys.path so we can import detectors as src.detection.*
# CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
# SRC_DIR = os.path.join(PROJECT_ROOT, "src")
# if SRC_DIR not in sys.path:
#     sys.path.insert(0, SRC_DIR)

# # aiortc imports
# from aiortc import RTCPeerConnection, RTCSessionDescription
# from aiortc.contrib.media import MediaBlackhole

# # local helpers
# from screen_event_recorder import ScreenEventRecorder
# from detection_integration import run_detection_pipeline  # implemented below

# # logging
# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger("webrtc_server")

# ROOT = PROJECT_ROOT
# STATIC = os.path.join(CURRENT_DIR, "static")

# # keep track of peerconnections so we can close them on shutdown
# pcs = set()

# # Global screen recorder (you may make per-session if preferred)
# screen_recorder = ScreenEventRecorder(buffer_size=5, after_frames=10, base_dir=os.path.join(PROJECT_ROOT, "recordings", "screen_events"))

# # Helper to convert av.VideoFrame to CV2 BGR numpy (handled in detection_integration)
# def avframe_to_bgr(frame: VideoFrame):
#     return frame.to_ndarray(format="bgr24")


# class VideoConsumer:
#     """
#     Consume an incoming track (camera or screen) and call detection pipeline.
#     Accepts an optional data_channel so we can send violations back to the client.
#     """
#     def __init__(self, track, kind, pc_id, data_channel=None):
#         self.track = track
#         self.kind = kind  # 'camera' | 'screen'
#         self.pc_id = pc_id
#         self.data_channel = data_channel
#         self._task = asyncio.create_task(self._run())

#     async def _run(self):
#         logger.info("[%s] VideoConsumer started for %s", self.pc_id, self.kind)
#         try:
#             while True:
#                 frame = await self.track.recv()  # av.VideoFrame
#                 bgr = avframe_to_bgr(frame)
#                 ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")

#                 # If this is a screen frame, push into screen_recorder buffer
#                 if self.kind == "screen":
#                     try:
#                         screen_recorder.add_frame(bgr)
#                     except Exception as e:
#                         logger.exception("screen_recorder.add_frame failed: %s", e)

#                 # call detection pipeline (non-blocking)
#                 # pass data_channel so detection can notify client in realtime
#                 asyncio.create_task(run_detection_pipeline(bgr, kind=self.kind, timestamp=ts, data_channel=self.data_channel))
#         except asyncio.CancelledError:
#             logger.info("[%s] VideoConsumer cancelled for %s", self.pc_id, self.kind)
#         except Exception as e:
#             logger.exception("[%s] Error in VideoConsumer %s: %s", self.pc_id, self.kind, e)


# async def index(request):
#     path = os.path.join(STATIC, "index.html")
#     content = open(path, "r", encoding="utf-8").read()
#     return web.Response(text=content, content_type="text/html")


# async def offer(request):
#     """
#     Handle SDP offer from browser and return an answer.
#     JSON body: { sdp: ..., type: "offer" }
#     """
#     params = await request.json()
#     offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
#     pc = RTCPeerConnection()
#     pcs.add(pc)
#     pc_id = "PC-" + str(id(pc))
#     logger.info("%s - New RTCPeerConnection", pc_id)

#     media_blackhole = MediaBlackhole()
#     pc._video_consumers = []
#     pc._data_channels = []

#     @pc.on("datachannel")
#     def on_datachannel(channel):
#         logger.info("%s - DataChannel received: %s", pc_id, channel.label)
#         pc._data_channels.append(channel)

#         @channel.on("message")
#         def on_message(message):
#             logger.info("%s - DataChannel message: %s", pc_id, message)
#             # Example: client could request a manual screenshot or status
#             # We'll accept JSON messages
#             try:
#                 obj = json.loads(message)
#             except Exception:
#                 obj = None
#             if obj and obj.get("cmd") == "trigger_screen_save":
#                 screen_recorder.trigger_save("MANUAL_TRIGGER")
#                 channel.send(json.dumps({"type": "ACK", "detail": "screen_saved"}))

#     @pc.on("track")
#     def on_track(track):
#         logger.info("%s - Track received kind=%s id=%s", pc_id, track.kind, track.id)
#         if track.kind == "video":
#             # decide camera vs screen by count: first video -> camera, second -> screen
#             idx = len(pc._video_consumers)
#             kind = "camera" if idx == 0 else "screen"
#             # if data channel exists, pass the first one
#             data_channel = pc._data_channels[0] if pc._data_channels else None
#             consumer = VideoConsumer(track, kind=kind, pc_id=pc_id, data_channel=data_channel)
#             pc._video_consumers.append(consumer)
#         elif track.kind == "audio":
#             # drop or handle audio if desired
#             media_blackhole.addTrack(track)

#         @track.on("ended")
#         async def on_ended():
#             logger.info("%s - Track %s ended", pc_id, track.id)

#     # set remote description and create local answer
#     await pc.setRemoteDescription(offer)
#     answer = await pc.createAnswer()
#     await pc.setLocalDescription(answer)

#     response = {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
#     logger.info("%s - Answer created", pc_id)
#     return web.json_response(response)


# async def on_shutdown(app):
#     logger.info("Shutting down, closing peer connections...")
#     coros = [pc.close() for pc in pcs]
#     await asyncio.gather(*coros)
#     pcs.clear()


# # web app wiring
# app = web.Application()
# app.router.add_get("/", index)
# app.router.add_post("/offer", offer)
# app.router.add_static("/static/", STATIC, show_index=True)
# app.on_shutdown.append(on_shutdown)

# if __name__ == "__main__":
#     web.run_app(app, port=8080)
