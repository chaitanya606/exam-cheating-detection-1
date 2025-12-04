// Establish Socket.IO connection
const socket = io();

// Elements
const imgFeed = document.getElementById('video_feed');
const vioList = document.getElementById('violations_list');
const statusFace = document.getElementById('face_status');
const statusGaze = document.getElementById('gaze_status');
const statusEyes = document.getElementById('eyes_status');
const statusMouth = document.getElementById('mouth_status');
const statusTime = document.getElementById('timestamp_status');

// Receive video frames
socket.on('frame', (data) => {
    if (data.image) {
        imgFeed.src = "data:image/jpeg;base64," + data.image;
    }

    const r = data.results;
    if (!r) return;

    statusFace.innerText = r.face_present ? "Present" : "Absent";
    statusGaze.innerText = r.gaze_direction;
    statusEyes.innerText = r.eye_ratio > 0.25 ? "Open" : "Closed";
    statusMouth.innerText = r.mouth_moving ? "Moving" : "Still";
    statusTime.innerText = r.timestamp;
});

// Receive violation alerts
socket.on('violation', (data) => {
    const li = document.createElement("li");
    li.textContent = `${data.timestamp} — ${data.type}`;
    vioList.prepend(li);
});

// Detect tab switching
document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
        socket.emit("focus_warning", { type: "TAB_SWITCHED" });
    }
});






// -----------------------------------------
// // Establish Socket.IO connection
// const socket = io();

// // Elements
// const imgFeed = document.getElementById('video_feed');
// const vioList = document.getElementById('violations_list');
// const statusFace = document.getElementById('face_status');
// const statusGaze = document.getElementById('gaze_status');
// const statusEyes = document.getElementById('eyes_status');
// const statusMouth = document.getElementById('mouth_status');
// const statusTime = document.getElementById('timestamp_status');

// // Receive video frames
// socket.on('frame', (data) => {
//     if (data.image) {
//         imgFeed.src = "data:image/jpeg;base64," + data.image;
//     }

//     const r = data.results;
//     if (!r) return;

//     statusFace.innerText = r.face_present ? "Present" : "Absent";
//     statusGaze.innerText = r.gaze_direction;
//     statusEyes.innerText = r.eye_ratio > 0.25 ? "Open" : "Closed";
//     statusMouth.innerText = r.mouth_moving ? "Moving" : "Still";
//     statusTime.innerText = r.timestamp;
// });

// // Receive violation alerts
// socket.on('violation', (data) => {
//     const li = document.createElement("li");
//     li.textContent = `${data.timestamp} — ${data.type}`;
//     vioList.prepend(li);
// });

// // Detect tab switching
// document.addEventListener("visibilitychange", () => {
//     if (document.hidden) {
//         socket.emit("focus_warning", { type: "TAB_SWITCHED" });
//     }
// });
