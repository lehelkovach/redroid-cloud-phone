"""In-process Control API stand-ins for Redroid (GApps) vs Cuttlefish (ingest)."""

from flask import Flask, jsonify, request


def make_control_app(runtime="redroid"):
    """Return (app, call_log) for a fake phone Control API.

    Redroid reports Play/GMS ready. Cuttlefish reports ingest endpoints and no GApps.
    """
    app = Flask(f"fake-control-{runtime}-{id(runtime)}")
    log = []
    gapps_ready = runtime == "redroid"

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({
            "status": "healthy",
            "adb_connected": True,
            "runtime": runtime,
            "gapps": {
                "gms": gapps_ready,
                "play_store": gapps_ready,
                "gsf": gapps_ready,
                "ready": gapps_ready,
            },
            "ingest": None if gapps_ready else {
                "nginx_rtmp": True,
                "webrtc_port": 8443,
            },
        })

    @app.route("/status", methods=["GET"])
    def status():
        return jsonify({
            "connected": True,
            "runtime": runtime,
            "device": {"model": "Redroid" if gapps_ready else "Cuttlefish"},
            "gapps": {"ready": gapps_ready},
        })

    @app.route("/apps/<package>/start", methods=["POST"])
    def start_app(package):
        log.append({"endpoint": "start_app", "package": package, "runtime": runtime})
        return jsonify({"success": True, "message": "started", "package": package})

    @app.route("/device/input", methods=["POST"])
    def device_input():
        data = request.get_json() or {}
        log.append({"endpoint": "device_input", "data": data, "runtime": runtime})
        return jsonify({"success": True})

    @app.route("/device/screenshot/base64", methods=["GET"])
    def screenshot_base64():
        log.append({"endpoint": "screenshot", "runtime": runtime})
        return jsonify({"success": True, "image_base64": "AAAA"})

    @app.route("/jobs", methods=["POST"])
    def jobs():
        payload = request.get_json() or {}
        log.append({"endpoint": "jobs", "data": payload, "runtime": runtime})
        return jsonify({"job_id": "job1", "status": "queued"}), 202

    @app.route("/jobs/job1", methods=["GET"])
    def job_poll():
        return jsonify({"id": "job1", "status": "done", "result": {"success": True}})

    return app, log
