"""Gemini-shaped UI boxes → CPMS-like roles → mobile procedure steps.

The live OSLO agent asks Gemini VLM for bounding boxes on an ADB screenshot
(not VNC pixels). This module is the orchestrator-side contract the test runner
exercises with a fake VLM: parse boxes, bind login/form roles, skip captcha/SMS,
and emit tap-centre + type steps. Submit stays approval-gated.

Prefer a labeled UIAutomator dump when it already has identity + password
(or other required roles). Fall back to VLM when the dump is empty, unlabeled,
or half-rendered — the same trigger as OSLO `needsVisionFallback`.
"""

from __future__ import annotations

# Role families match OSLO `src/cpms/ui_form.mjs` ROLE_FAMILY (keep in sync).
ROLE_FAMILY = {
    "email": "username",
    "user": "username",
    "login": "username",
    "username": "username",
    "password": "password",
    "pass": "password",
    "pwd": "password",
    "submit": "submit",
    "button": "submit",
    "sign_in": "submit",
    "signin": "submit",
    "log_in": "submit",
    "login_button": "submit",
}

# Never fill these from VLM (Watch / Needs-you — do not solve).
REFUSED_ROLES = frozenset({
    "captcha", "recaptcha", "hcaptcha", "sms", "otp", "totp", "2fa",
    "verification_code", "sms_code", "mfa", "pin",
})

DEFAULT_SCREEN = {"width": 1280, "height": 720}


def normalize_role(role):
    key = str(role or "").strip().lower().replace(" ", "_").replace("-", "_")
    return ROLE_FAMILY.get(key, key)


def parse_bbox(raw, screen=None):
    """Accept [x1,y1,x2,y2], {x,y,w,h}, {left,top,right,bottom}, or center-only."""
    screen = screen or DEFAULT_SCREEN
    width = int(screen.get("width") or DEFAULT_SCREEN["width"])
    height = int(screen.get("height") or DEFAULT_SCREEN["height"])

    if raw is None:
        return None
    if isinstance(raw, (list, tuple)) and len(raw) >= 4:
        x1, y1, x2, y2 = (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
    elif isinstance(raw, dict):
        if all(k in raw for k in ("x1", "y1", "x2", "y2")):
            x1, y1, x2, y2 = raw["x1"], raw["y1"], raw["x2"], raw["y2"]
        elif all(k in raw for k in ("left", "top", "right", "bottom")):
            x1, y1, x2, y2 = raw["left"], raw["top"], raw["right"], raw["bottom"]
        elif all(k in raw for k in ("x", "y", "w", "h")):
            x1, y1 = raw["x"], raw["y"]
            x2, y2 = float(raw["x"]) + float(raw["w"]), float(raw["y"]) + float(raw["h"])
        elif "x" in raw and "y" in raw:
            x, y = float(raw["x"]), float(raw["y"])
            x1, y1, x2, y2 = x, y, x, y
        else:
            return None
        x1, y1, x2, y2 = float(x1), float(y1), float(x2), float(y2)
    else:
        return None

    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    x1 = max(0, min(width, int(round(x1))))
    y1 = max(0, min(height, int(round(y1))))
    x2 = max(0, min(width, int(round(x2))))
    y2 = max(0, min(height, int(round(y2))))
    if x2 == x1 and y2 == y1:
        return [x1, y1, x2, y2]
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


def bbox_center(bbox):
    if not bbox or len(bbox) < 4:
        return None
    return (bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2


def _element_role(element):
    if element.get("password"):
        return "password"
    blob = " ".join(
        str(element.get(k) or "")
        for k in ("label", "resource_id", "role", "name", "text", "content_desc")
    ).lower()
    if any(tok in blob for tok in ("captcha", "recaptcha", "hcaptcha")):
        return "captcha"
    if any(tok in blob for tok in ("sms", "otp", "2fa", "mfa", "verification")):
        return "otp"
    if any(tok in blob for tok in ("pass", "pwd")):
        return "password"
    if any(tok in blob for tok in ("email", "user", "login", "account")):
        return "username"
    if any(tok in blob for tok in ("sign in", "signin", "log in", "submit", "continue", "next")):
        return "submit"
    return ""


def fields_from_dump(dump):
    """Map UIAutomator elements onto CPMS-like fields with tap centres."""
    elements = []
    if isinstance(dump, dict):
        elements = dump.get("elements") or dump.get("nodes") or []
    elif isinstance(dump, list):
        elements = dump
    fields = []
    for el in elements:
        if not isinstance(el, dict):
            continue
        bbox = parse_bbox(el.get("bounds") or el.get("bbox"))
        if not bbox:
            x, y = el.get("x"), el.get("y")
            if x is None or y is None:
                continue
            bbox = [int(x), int(y), int(x), int(y)]
        role = normalize_role(el.get("role") or _element_role(el))
        if not role:
            continue
        if role in REFUSED_ROLES:
            fields.append({
                "role": role,
                "bbox": bbox,
                "x": bbox_center(bbox)[0],
                "y": bbox_center(bbox)[1],
                "source": "uiautomator",
                "refused": True,
            })
            continue
        cx, cy = bbox_center(bbox)
        fields.append({
            "role": role,
            "type": role,
            "bbox": bbox,
            "x": cx,
            "y": cy,
            "source": "uiautomator",
            "label": el.get("label") or "",
        })
    return fields


def fields_from_vlm(payload, screen=None):
    """Normalize a Gemini-shaped `{elements:[{role,bbox}]}` payload."""
    screen = screen or DEFAULT_SCREEN
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("elements") or payload.get("fields") or payload.get("boxes") or []
    else:
        items = []
    fields = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        role = normalize_role(item.get("role") or item.get("name") or item.get("type"))
        if not role or role in seen:
            continue
        bbox = parse_bbox(item.get("bbox") or item.get("bounds") or item.get("box"), screen)
        if not bbox:
            continue
        cx, cy = bbox_center(bbox)
        refused = role in REFUSED_ROLES
        fields.append({
            "role": role,
            "type": role,
            "bbox": bbox,
            "x": cx,
            "y": cy,
            "source": "gemini-vision",
            "label": item.get("label") or item.get("text") or "",
            "refused": refused,
        })
        if not refused:
            seen.add(role)
    return fields


def needs_vision_fallback(fields):
    """True when a dump (or prior detection) is not enough to fill a login/form."""
    fields = fields or []
    roles = {f.get("role") or f.get("type") for f in fields}
    if roles & REFUSED_ROLES:
        return False  # dump already named the block; do not VLM-solve it
    usable = [f for f in fields if not f.get("refused")]
    if not usable:
        return True
    usable_roles = {f.get("role") or f.get("type") for f in usable}
    has_id = "username" in usable_roles
    has_pass = "password" in usable_roles
    if has_id and has_pass:
        return False
    only_submit = usable_roles <= {"submit"}
    if only_submit:
        return True
    if has_id != has_pass:
        return True
    return len(usable) == 0


def detect_form_boxes(image_b64=None, screen=None, ui_dump=None, vlm=None):
    """Dump first; VLM when unlabeled. `vlm` is callable(image_b64, screen) -> dict."""
    screen = screen or DEFAULT_SCREEN
    dump_fields = fields_from_dump(ui_dump)
    source = "uiautomator"
    fields = dump_fields
    if needs_vision_fallback(dump_fields):
        if vlm is None:
            return {
                "ok": False,
                "needs_vision": True,
                "fields": dump_fields,
                "source": source,
                "error": "ui dump unlabeled; VLM required",
            }
        payload = vlm(image_b64, screen)
        fields = fields_from_vlm(payload, screen)
        source = "gemini-vision"
    refused = [f for f in fields if f.get("refused")]
    usable = [f for f in fields if not f.get("refused")]
    return {
        "ok": True,
        "needs_vision": source == "gemini-vision",
        "fields": usable,
        "refused": refused,
        "source": source,
        "screen": {
            "width": int(screen.get("width") or DEFAULT_SCREEN["width"]),
            "height": int(screen.get("height") or DEFAULT_SCREEN["height"]),
        },
    }


def plan_fill_steps(fields, values, include_submit=False, surface="mobile"):
    """Tap the box centre then type. Submit is omitted unless include_submit."""
    steps = []
    missing = []
    submit = None
    for field in fields or []:
        role = field.get("role") or field.get("type")
        if field.get("refused") or role in REFUSED_ROLES:
            continue
        if role == "submit":
            submit = field
            continue
        value = values.get(role)
        if value is None or value == "":
            for alias, fam in ROLE_FAMILY.items():
                if fam == role and values.get(alias):
                    value = values[alias]
                    break
        if value is None or value == "":
            missing.append(role)
            continue
        steps.append({
            "action": "tap",
            "x": int(field["x"]),
            "y": int(field["y"]),
            "surface": surface,
            "role": role,
        })
        steps.append({
            "action": "type",
            "text": str(value),
            "surface": surface,
            "role": role,
        })
    if include_submit and submit:
        steps.append({
            "action": "submit",
            "x": int(submit["x"]),
            "y": int(submit["y"]),
            "surface": surface,
            "role": "submit",
        })
    return {
        "ok": not missing,
        "steps": steps,
        "missing": missing,
        "approvalRequired": bool(submit),
        "submit": {"x": submit["x"], "y": submit["y"]} if submit else None,
    }
