"""In-process Control API stand-ins for Redroid (GApps) vs Cuttlefish (ingest).

Emits labeled verbose logs for Appium, UI commandlets, and VNC viewports so the
TDD ladder can assert the same stream a live phone would produce.
"""

from flask import Flask, jsonify, request

try:
    from api.cloudphone_logging import configure, recent_logs, redact
    from api import ui_control
    from api import viewport
except ImportError:
    from cloudphone_logging import configure, recent_logs, redact  # type: ignore
    import ui_control  # type: ignore
    import viewport  # type: ignore


# 1x1 PNG — ADB screencap stand-in. Agents must not treat VNC pixels as the frame.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

EMPTY_UI_DUMP = {"success": True, "count": 0, "elements": []}


def make_control_app(runtime="redroid", ui_dump=None):
    """Return (app, call_log) for a fake phone Control API.

    Redroid reports Play/GMS ready. Cuttlefish reports ingest endpoints and no GApps.
    Default UI dump is empty/unlabeled so the VLM fill path has to run.
    """
    app = Flask(f"fake-control-{runtime}-{id(runtime)}")
    log = []
    gapps_ready = runtime == "redroid"
    dump = EMPTY_UI_DUMP if ui_dump is None else ui_dump
    logger = configure(f"fake.{runtime}", log_type="API")
    cmd_logger = logger.bind("CMD")
    apm_logger = logger.bind("APM")
    vnc_logger = logger.bind("VNC")
    size = viewport.default_size()

    @app.route("/health", methods=["GET"])
    def health():
        vnc = viewport.snapshot(runtime=runtime, size=size)
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
            "appium": {"url": "http://127.0.0.1:4723", "ready": gapps_ready, "backend": "adb"},
            "vnc": vnc,
        })

    @app.route("/status", methods=["GET"])
    def status():
        return jsonify({
            "connected": True,
            "runtime": runtime,
            "device": {"model": "Redroid" if gapps_ready else "Cuttlefish"},
            "gapps": {"ready": gapps_ready},
            "vnc": viewport.snapshot(runtime=runtime, size=size),
        })

    @app.route("/apps/<package>/start", methods=["POST"])
    def start_app(package):
        log.append({"endpoint": "start_app", "package": package, "runtime": runtime})
        cmd_logger.info("commandlet start_app package=%s runtime=%s", package, runtime)
        apm_logger.info("activate %s (fake session)", package)
        viewport.frame(vnc_logger, size=size)
        return jsonify({"success": True, "message": "started", "package": package})

    @app.route("/device/input", methods=["POST"])
    def device_input():
        data = request.get_json() or {}
        log.append({"endpoint": "device_input", "data": data, "runtime": runtime})
        payload = redact(data)
        if data.get("type") in {"text", "type"}:
            payload = dict(payload)
            payload["text"] = f"<{len(str(data.get('text', '')))} chars>"
        cmd_logger.info("commandlet %s payload=%s runtime=%s", data.get("type"), payload, runtime)
        viewport.frame(vnc_logger, size=size)
        return jsonify({"success": True})

    @app.route("/device/screenshot/base64", methods=["GET", "POST"])
    def screenshot_base64():
        log.append({"endpoint": "screenshot", "runtime": runtime})
        viewport.frame(vnc_logger, nbytes=4, size=size)
        return jsonify({
            "success": True,
            "image_base64": _TINY_PNG_B64,
            "width": size[0],
            "height": size[1],
            "source": "adb-screencap",
        })

    @app.route("/device/ui", methods=["GET", "POST"])
    def device_ui():
        log.append({"endpoint": "ui_dump", "runtime": runtime})
        cmd_logger.info("uiautomator dump count=%s runtime=%s", dump.get("count"), runtime)
        return jsonify(dump)

    @app.route("/jobs", methods=["POST"])
    def jobs():
        payload = request.get_json() or {}
        log.append({"endpoint": "jobs", "data": payload, "runtime": runtime})
        return jsonify({"job_id": "job1", "status": "queued"}), 202

    @app.route("/jobs/job1", methods=["GET"])
    def job_poll():
        return jsonify({"id": "job1", "status": "done", "result": {"success": True}})

    @app.route("/ui/command", methods=["POST"])
    def ui_command():
        cmd = request.get_json() or {}
        action = cmd.get("action") or cmd.get("type") or "tap"
        backend = (cmd.get("backend") or "adb").lower()
        try:
            w3c = ui_control.build_appium_actions(cmd, size)
        except ui_control.UIError as exc:
            cmd_logger.warning("commandlet rejected: %s", exc)
            return jsonify({"success": False, "error": str(exc)}), 400
        apm_logger.info("w3c action=%s runtime=%s payload=%s", action, runtime, w3c)
        if backend == "appium":
            apm_logger.info("fake appium execute action=%s", action)
            log.append({"endpoint": "ui_command", "backend": "appium", "action": action})
            viewport.frame(vnc_logger, size=size)
            return jsonify({"success": True, "backend": "appium", "w3c": w3c, "size": {"width": size[0], "height": size[1]}})
        shell_cmds = ui_control.build_adb_input(cmd, size)
        cmd_logger.info("commandlet action=%s backend=adb cmds=%s runtime=%s", action, shell_cmds, runtime)
        log.append({"endpoint": "ui_command", "backend": "adb", "action": action, "commands": shell_cmds})
        viewport.frame(vnc_logger, size=size)
        return jsonify({
            "success": True,
            "backend": "adb",
            "commands": shell_cmds,
            "w3c": w3c,
            "size": {"width": size[0], "height": size[1]},
        })

    @app.route("/appium/status", methods=["GET"])
    def appium_status():
        ready = gapps_ready
        apm_logger.info("status ready=%s runtime=%s url=http://127.0.0.1:4723", ready, runtime)
        return jsonify({"url": "http://127.0.0.1:4723", "ready": ready, "backend": "adb", "runtime": runtime})

    @app.route("/vnc/status", methods=["GET"])
    def vnc_status():
        status = viewport.snapshot(runtime=runtime, size=size)
        vnc_logger.info(
            "viewport %sx%s :%s clients=%s frames=%s runtime=%s",
            status["width"], status["height"], status["port"],
            status["clients"], status["frames"], runtime,
        )
        return jsonify(status)

    @app.route("/vnc/attach", methods=["POST"])
    def vnc_attach():
        return jsonify(viewport.attach(vnc_logger, runtime=runtime, size=size)), 201

    @app.route("/logs", methods=["GET"])
    def get_logs():
        log_type = request.args.get("type")
        try:
            n = int(request.args.get("n", "200"))
        except ValueError:
            n = 200
        items = recent_logs(log_type=log_type, n=n)
        return jsonify({"count": len(items), "logs": items})

    return app, log
