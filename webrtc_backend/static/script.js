// webrtc_backend/static/script.js
const pc = new RTCPeerConnection({
  iceServers: [{ urls: "stun:stun.l.google.com:19302" }]
});

const localVideo = document.getElementById("localVideo");
const startBtn = document.getElementById("startBtn");
const shareScreenBtn = document.getElementById("shareScreenBtn");
const triggerBtn = document.getElementById("triggerBtn");
const status = document.getElementById("status");
const violationsList = document.getElementById("violations_list");

let localStream = null;
let screenStream = null;
let dataChannel = null;

// create DataChannel (client-created)
dataChannel = pc.createDataChannel("client-data");
dataChannel.onopen = () => {
  console.log("DataChannel open");
  status.innerText = "DataChannel open";
};
dataChannel.onmessage = (evt) => {
  try {
    const msg = JSON.parse(evt.data);
    if (msg.type === "VIOLATION") {
      const li = document.createElement("li");
      li.textContent = `${msg.timestamp} — ${msg.violation} (${msg.kind})`;
      violationsList.prepend(li);
    } else if (msg.type === "ACK") {
      console.log("Server ACK:", msg);
    }
  } catch (e) {
    console.log("DataChannel message:", evt.data);
  }
};

// handle server-created datachannels (rare in this flow)
pc.ondatachannel = (evt) => {
  const ch = evt.channel;
  console.log("Received datachannel from server:", ch.label);
  ch.onmessage = (e) => console.log("Server datachannel message:", e.data);
};

pc.oniceconnectionstatechange = () => {
  status.innerText = "ICE: " + pc.iceConnectionState;
  console.log("ICE state:", pc.iceConnectionState);
};

async function start() {
  startBtn.disabled = true;
  try {
    localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    localVideo.srcObject = localStream;

    // add camera tracks
    localStream.getTracks().forEach((track) => pc.addTrack(track, localStream));

    // create offer
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    // send offer to server
    const res = await fetch("/offer", {
      method: "POST",
      body: JSON.stringify({ sdp: pc.localDescription.sdp, type: pc.localDescription.type }),
      headers: { "Content-Type": "application/json" }
    });

    const answer = await res.json();
    await pc.setRemoteDescription(answer);
    status.innerText = "Connected. Sending camera track.";
  } catch (err) {
    console.error("Start failed:", err);
    status.innerText = "Error: " + err;
    startBtn.disabled = false;
  }
}

async function shareScreen() {
  try {
    screenStream = await navigator.mediaDevices.getDisplayMedia({ video: true });
    screenStream.getTracks().forEach((track) => pc.addTrack(track, screenStream));
    status.innerText = "Screen shared.";
  } catch (err) {
    console.error("Screen share failed:", err);
  }
}

triggerBtn.onclick = () => {
  // ask server (via dataChannel) to save current buffered screen frames
  if (dataChannel && dataChannel.readyState === "open") {
    const msg = { cmd: "trigger_screen_save" };
    dataChannel.send(JSON.stringify(msg));
  } else {
    alert("Data channel not open");
  }
};

startBtn.onclick = start;
shareScreenBtn.onclick = shareScreen;
