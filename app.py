from flask import Flask, render_template, request, jsonify, send_file, Response
import os
import random
import csv
import logging
import threading
import json

import app_config as cfg

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)

image_pairs = []
sessions_info = dict()

last_session = 0

results_lock = threading.Lock()
session_lock = threading.Lock()



def initialize_image_pairs():
    global image_pairs

    pairs = list()
    for img in os.listdir(cfg.SYSTEMS_DIRS[0]):
        img_path_0 = f"{cfg.SYSTEMS_DIRS[0]}/{img}"
        img_path_1 = f"{cfg.SYSTEMS_DIRS[1]}/{img}"
        pairs.append((img_path_0, img_path_1))

    image_pairs = pairs
    random.shuffle(image_pairs)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_session')
def get_new_session():
    with session_lock:
        global last_session
        global sessions_info
        global image_pairs

        last_session += 1

        # initialize random image pairs sequence
        indices = list(range(len(image_pairs)))
        random.shuffle(indices)
        sessions_info[last_session] = {"img_sequence": indices,
                                       "next_pair": 0}
        
        app.logger.info(f"New session {last_session}: {sessions_info[last_session]}")

        return jsonify({'session': last_session})
    
@app.route('/get_images', methods=['POST'])
def get_images():
    data = request.json
    total_pairs = len(image_pairs)

    # read session data
    session_id = data['sessionId']
    session_sequence = sessions_info[session_id]["img_sequence"]
    session_curr_pair = sessions_info[session_id]["next_pair"]

    if session_curr_pair >= len(session_sequence):
        return jsonify({'end': "Thank you for evaluating all the images in our dataset!",
                        'progress': {
                            'current': session_curr_pair,
                            'total': total_pairs
                        }})

    # read next pair from the preordered list for this session
    img1, img2 = image_pairs[session_sequence[session_curr_pair]]

    img_name = os.path.splitext(os.path.basename(img1))[0]
    descr_filename = f"{cfg.DESCR_DIR}/{img_name}.json"
    img_info = None
    with open(descr_filename, encoding="utf-8") as f:
        img_info = json.loads(f.read())
    
    # randomize presentation order of images
    if random.choice([True, False]):
        img1, img2 = img2, img1

    return jsonify({
        'image1':  img1,
        'image2':  img2,
        'descriptions': img_info["descriptions"],
        'progress': {
            'current': session_curr_pair,
            'total': total_pairs
        }
    })

@app.route('/serve_image')
def serve_image():
    image_path = request.args.get('path')
    if image_path.startswith('/serve_image'):
        image_path = image_path.split('=', 1)[1]
    file_extension = os.path.splitext(image_path)[1].lower()
    if file_extension == '.webp':
        mimetype = 'image/webp'
    else:
        mimetype = 'image/jpeg'
    return send_file(image_path, mimetype=mimetype)

def save_comparison(img_1, img_2, winner, session_id):
    comparisons_filename = os.path.join(cfg.SAVE_DIR, f'comparisons_autosave.csv')
    
    with results_lock:
        if not os.path.isfile(comparisons_filename):
            with open(comparisons_filename, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['Image 1', 'Image 2', 'Winner', 'Session ID'])
        
        with open(comparisons_filename, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([img_1, img_2, winner, session_id])

    app.logger.info(f"Comparison saved in {comparisons_filename}")

@app.route('/update_scores', methods=['POST'])
def update_scores():
    global sessions_info

    data = request.json
    image1 = data['image1']
    image2 = data['image2']
    winner = data['winner']
    session_id = data['sessionId']

    # update session data
    sessions_info[session_id]["next_pair"] += 1
    app.logger.info(f"Updated session {session_id}: {sessions_info[session_id]}")
    
    save_comparison(image1, image2, winner, session_id)
    return jsonify({'success': True})


if __name__ == '__main__':
    initialize_image_pairs()
    app.run(debug=False, threaded=True)