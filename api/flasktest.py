from flask import Flask, jsonify

_app = Flask(__name__)

@_app.route("/api/flasktest")
@_app.route("/")
def hello():
    return jsonify({"flask": "ok", "vercel": True})

handler = _app
