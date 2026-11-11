"""Temporary HTTP connectivity probe. Never executes commands or reads AWS credentials."""
import argparse
from flask import Flask, jsonify, request

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 4096

@app.get("/health")
def health():
    return jsonify(ok=True, service="studio-connectivity-probe")

@app.post("/echo")
def echo():
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or set(data) != {"message"} or not isinstance(data["message"], str) or len(data["message"]) > 256:
        return jsonify(error="Expected JSON object with a message string (max 256 characters)"), 400
    return jsonify(ok=True, message=data["message"])

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Harmless Studio HTTP connectivity probe")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=False)
