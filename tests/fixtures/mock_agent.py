"""Mock OSLO agent: lease a phone, VLM-box a screenshot, POST a fill procedure."""

from __future__ import annotations

try:
    from orchestrator import vlm_boxes
except ImportError:
    import vlm_boxes  # type: ignore

from .fake_vlm import FakeVlm, LOGIN_BOXES


class MockAgent:
    """Stand-in for the OSLO agent talking to the orchestrator over HTTP."""

    def __init__(self, orch, vlm=None, owner="mock-agent"):
        self.orch = orch
        self.vlm = vlm if vlm is not None else FakeVlm()
        self.owner = owner
        self.session = None

    def deploy_phone(self, purpose="automation"):
        resp = self.orch.post("/sessions", json={
            "owner_user_id": self.owner,
            "purpose": purpose,
        })
        resp.raise_for_status()
        body = resp.json()
        self.session = body["session"]
        return self.session

    def detect_form(self, instance_id=None):
        instance_id = instance_id or (self.session or {}).get("instance_id")
        shot = self.orch.get(f"/phones/{instance_id}/screenshot").json()
        vnc = self.orch.get(f"/phones/{instance_id}/vnc").json()
        dump = self.orch.get(f"/phones/{instance_id}/dump").json()
        screen = {
            "width": int(vnc.get("width") or shot.get("width") or 1280),
            "height": int(vnc.get("height") or shot.get("height") or 720),
        }
        detection = vlm_boxes.detect_form_boxes(
            image_b64=shot.get("image_base64"),
            screen=screen,
            ui_dump=dump,
            vlm=self.vlm,
        )
        detection["screenshot_source"] = shot.get("source") or "adb-screencap"
        detection["vnc"] = {
            "width": vnc.get("width"),
            "height": vnc.get("height"),
            "port": vnc.get("port"),
            "protocol": vnc.get("protocol"),
        }
        return detection

    def fill_form(self, values, package="com.android.vending", include_submit=False):
        if not self.session:
            self.deploy_phone()
        instance_id = self.session["instance_id"]
        detection = self.detect_form(instance_id)
        plan = vlm_boxes.plan_fill_steps(
            detection.get("fields") or [],
            values,
            include_submit=include_submit,
        )
        steps = [
            {"action": "open", "package": package, "target": package},
            *plan["steps"],
        ]
        resp = self.orch.post("/procedures", json={
            "sync": True,
            "instance_id": instance_id,
            "steps": steps,
            "approve": bool(include_submit),
        })
        body = resp.json()
        return {
            "session": self.session,
            "detection": detection,
            "plan": plan,
            "procedure": body,
        }


__all__ = ["MockAgent", "FakeVlm", "LOGIN_BOXES"]
