from flask import Flask, render_template, request, jsonify, current_app
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy import select
import os
import random
import logging
import json

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)

image_pairs = []


# DB settings

uri = os.environ.get("DATABASE_URL")
if uri and uri.startswith("postgres://"):
    uri = uri.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = uri

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

class UserSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    img_sequence = db.Column(db.JSON, nullable=False)
    next_pair = db.Column(db.Integer, nullable=False, default=0)
    evaluations = db.relationship(
        "Evaluation",
        backref="session",
        cascade="all, delete-orphan"
    )

class Evaluation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer,
                           db.ForeignKey("usersession.id"),
                           nullable=False,
                           index=True)
    image_a = db.Column(db.String, nullable=False)
    image_b = db.Column(db.String, nullable=False)
    winner_image = db.Column(db.String, nullable=False)
    winner_position = db.Column(db.String, nullable=False)
    timestamp = db.Column(db.DateTime, server_default=db.func.now())
    __table_args__ = (
        db.UniqueConstraint("session_id", "image_a", "image_b",
                            name="uq_session_imagepair"),
    )


def list_images(path):
    return {
        f for f in os.listdir(path)
           if os.path.isfile(os.path.join(path, f))
           and f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
    }

def initialize_image_pairs():
    SYSTEMS_DIRS = ["cocos_images", "images"]
    global image_pairs

    pairs = dict()

    dir0 = list_images(os.path.join(current_app.root_path, "static", SYSTEMS_DIRS[0]))
    dir1 = list_images(os.path.join(current_app.root_path, "static", SYSTEMS_DIRS[1]))
    common = dir0.intersection(dir1) 

    for img in common:
        img_path_0 = f"/static/{SYSTEMS_DIRS[0]}/{img}"
        img_path_1 = f"/static/{SYSTEMS_DIRS[1]}/{img}"
        basename = os.path.splitext(os.path.basename(img))[0]
        pairs[basename] = (img_path_0, img_path_1)

    image_pairs = pairs

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/get_session')
def get_new_session():
        global image_pairs

        # initialize random image pairs sequence
        basenames = list(image_pairs.keys())
        random.shuffle(basenames)

        u_session = UserSession(img_sequence=basenames)
        db.session.add(u_session)
        db.session.commit()
        new_session = u_session.id
        
        app.logger.info(f"New session {new_session}: {basenames}")

        return jsonify({'session': new_session})
    
@app.route('/get_images', methods=['POST'])
def get_images():
    data = request.json
    total_pairs = len(image_pairs)

    # read user session data
    u_session_id = data['sessionId']
    stmt = select(UserSession).where(UserSession.id == u_session_id)
    user_session = db.session.scalars(stmt).first()
    if user_session is None:
        return jsonify({"error": f"session number {u_session_id} not found"}), 400
    session_sequence = user_session.img_sequence
    session_curr_pair = user_session.next_pair


    if session_curr_pair >= len(session_sequence):
        return jsonify({'end': "Thank you for evaluating all the images in our dataset!",
                        'progress': {
                            'current': session_curr_pair,
                            'total': total_pairs
                        }})

    # read next pair from the preordered list for this session
    img1, img2 = image_pairs[session_sequence[session_curr_pair]]

    img_name = os.path.splitext(os.path.basename(img1))[0]
    descr_filename = os.path.join(current_app.root_path,
                                  "static", "refined-jsons", f"{img_name}.json")
    img_info = None

    try:
        with open(descr_filename, encoding="utf-8") as f:
            img_info = json.load(f)
    except FileNotFoundError:
        return jsonify({"error": f"Missing description for {img_name}"}), 500
    
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


@app.route('/update_scores', methods=['POST'])
def update_scores():

    data = request.json
    image1 = data['image1']
    image2 = data['image2']
    winner = data['winner']
    u_session_id = data['sessionId']

    img_a, img_b = sorted([image1, image2])
    winner_img, winner_pos = None
    if winner == "image1":
        winner_img = image1
        winner_pos = "left"
    elif winner == "image2":
        winner_img = image2
        winner_pos = "right"
    else:
        return jsonify({"error": f"winner {winner} not valid"}), 500

    # get user session data
    stmt = select(UserSession).where(UserSession.id == u_session_id)
    user_session = db.session.scalars(stmt).first()
    if user_session is None:
        return jsonify({"error": f"session number {u_session_id} not found"}), 400
    
    try:
        # save the new evaluation
        db.session.add(Evaluation(
            session_id = u_session_id,
            image_a = image1,
            image_b = image2,
            winner_image = winner_img,
            winner_position = winner_pos
        ))

        # update user session data
        user_session.next_pair += 1

        # commit to db
        db.session.commit()

    except IntegrityError:
        db.session.rollback()
        return jsonify({"status": "already_submitted"}), 200
    
    except SQLAlchemyError as e:
        db.session.rollback()
        app.logger.error(f"DB error: {e}")
        return jsonify({"error": "Database error"}), 500

    app.logger.info(f"Updated session {u_session_id}: next pair {user_session.next_pair}")
    
    return jsonify({"status": "ok"}), 200


@app.route("/health")
def health():
    return "OK"


with app.app_context():
    initialize_image_pairs()

    if os.environ.get("INIT_DB") == "1":
        db.create_all()