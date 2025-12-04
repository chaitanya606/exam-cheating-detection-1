
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from flask import Flask, render_template
from flask_socketio import SocketIO
from threading import Lock
from server import main as run_proctoring



app = Flask(__name__, template_folder='templates', static_folder='static')
app.config['SECRET_KEY'] = 'cb0452b08f39c2b940ce52fa6c5095d74c76d797357b6db0ebbeb68217d4e817'

socketio = SocketIO(app)
proctor_thread = None
thread_lock = Lock()

@app.route('/')
def index():
    return render_template('ExamFaceInput.html')

@socketio.on('connect')
def start_proctor():
    global proctor_thread
    print("Client Connected")

    with thread_lock:
        if proctor_thread is None:
            proctor_thread = socketio.start_background_task(
                run_proctoring, socketio
            )

@socketio.on('focus_warning')
def warn_focus(data):
    print("⚠ Focus/TAB Issue:", data)

if __name__ == '__main__':
    socketio.run(app, host="0.0.0.0", port=5050)




# -------------------------------------
# import os, sys
# sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# from flask import Flask, render_template
# from flask_socketio import SocketIO
# from threading import Lock
# import sys
# sys.path.append("..")  # allow import from parent folder

# from server import main as run_proctoring

# app = Flask(__name__, template_folder='../templates', static_folder='static')
# app.config['SECRET_KEY'] = 'cb0452b08f39c2b940ce52fa6c5095d74c76d797357b6db0ebbeb68217d4e817'

# socketio = SocketIO(app)
# proctor_thread = None
# thread_lock = Lock()

# @app.route('/')
# def index():
#     return render_template('ExamFaceInput.html')

# @socketio.on('connect')
# def start_proctor():
#     global proctor_thread
#     print("Client Connected")

#     with thread_lock:
#         if proctor_thread is None:
#             proctor_thread = socketio.start_background_task(
#                 run_proctoring, socketio
#             )

# @socketio.on('focus_warning')
# def warn_focus(data):
#     print("⚠ Focus/TAB Issue:", data)

# if __name__ == '__main__':
#     socketio.run(app, host="0.0.0.0", port=5060)
