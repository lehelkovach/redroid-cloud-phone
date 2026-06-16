#!/usr/bin/env python3
"""Unit tests for orchestrator.launch_config."""

import base64
import json
import unittest

from orchestrator.launch_config import (
    LaunchConfig,
    build_launch_config,
    DEFAULT_CONFIG_PATH,
)


class LaunchConfigTests(unittest.TestCase):
    def test_build_and_roundtrip(self):
        cfg = build_launch_config(
            "phone-1",
            golden_image_id="ocid1.image.oc1..xyz",
            proxy={"enabled": True, "type": "socks5", "host": "1.2.3.4", "port": 1080},
            startup_tasks=[{"type": "adb_shell", "payload": {"command": "echo hi"}}],
            labels={"role": "dev"},
        )
        data = cfg.to_dict()
        self.assertEqual(data["instance_id"], "phone-1")
        self.assertEqual(data["proxy"]["host"], "1.2.3.4")
        again = LaunchConfig.from_json(cfg.to_json())
        self.assertEqual(again.to_dict(), data)

    def test_unknown_fields_preserved_in_extra(self):
        cfg = LaunchConfig.from_dict({
            "instance_id": "p2",
            "some_future_field": {"a": 1},
        })
        self.assertEqual(cfg.extra["some_future_field"], {"a": 1})

    def test_validation_requires_instance_id(self):
        with self.assertRaises(ValueError):
            LaunchConfig.from_dict({"proxy": {}})

    def test_validation_rejects_bad_task_type(self):
        with self.assertRaises(ValueError):
            build_launch_config("p3", startup_tasks=[{"type": "not_a_real_task"}])

    def test_cloud_init_userdata_contains_payload_and_apply(self):
        cfg = build_launch_config("p4", labels={"k": "v"})
        ud = cfg.to_cloud_init_userdata()
        self.assertTrue(ud.startswith("#!/bin/bash"))
        self.assertIn(DEFAULT_CONFIG_PATH, ud)
        self.assertIn("/launch-config/apply", ud)
        # The embedded base64 should decode back to the same config JSON.
        b64 = [tok for tok in ud.split() if tok.endswith("=") or len(tok) > 40]
        decoded = None
        for tok in b64:
            try:
                candidate = base64.b64decode(tok).decode("utf-8")
                if "instance_id" in candidate:
                    decoded = json.loads(candidate)
                    break
            except Exception:
                continue
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded["instance_id"], "p4")


if __name__ == "__main__":
    unittest.main()
