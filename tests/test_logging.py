#!/usr/bin/env python3
"""Labeled logging: text and JSON, plus parity with the shell helper."""

import io
import json
import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "api"))

import cloudphone_logging as cpl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_SH = os.path.join(ROOT, "scripts", "lib", "log.sh")

TEXT_LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \[([A-Z]{3})\] \[(\w+)\s*\] (.*)$")


class FormatTests(unittest.TestCase):
    def capture(self, log_type="API", fmt="text", level="INFO"):
        stream = io.StringIO()
        logger = cpl.configure(
            f"test.{log_type}.{fmt}", log_type=log_type, level=level, stream=stream
        )
        return logger, stream

    def test_text_format_carries_type_and_level(self):
        logger, stream = self.capture()
        logger.info("container started")
        match = TEXT_LINE.match(stream.getvalue().strip())
        self.assertIsNotNone(match, stream.getvalue())
        self.assertEqual(match.group(1), "API")
        self.assertEqual(match.group(2), "INFO")
        self.assertEqual(match.group(3), "container started")

    def test_json_format(self):
        stream = io.StringIO()
        logger = cpl.configure("test.json", log_type="ORC", stream=stream)
        for handler in logger.logger.handlers:
            handler.setFormatter(cpl.JsonFormatter("ORC"))
        logger.warning("lease expired owner=%s", "alice")
        payload = json.loads(stream.getvalue().strip())
        self.assertEqual(payload["type"], "ORC")
        self.assertEqual(payload["level"], "WARNING")
        self.assertEqual(payload["msg"], "lease expired owner=alice")
        self.assertIn("ts", payload)

    def test_unknown_type_degrades_to_sys(self):
        self.assertEqual(cpl.normalize_type("nope"), "SYS")
        self.assertEqual(cpl.normalize_type(None), "SYS")
        self.assertEqual(cpl.normalize_type("adb"), "ADB")

    def test_bind_switches_the_label(self):
        logger, stream = self.capture(log_type="ORC")
        logger.bind("RDR").info("container up")
        self.assertIn("[RDR]", stream.getvalue())

    def test_level_filtering(self):
        logger, stream = self.capture(level="WARNING")
        logger.info("chatty")
        logger.error("real problem")
        self.assertNotIn("chatty", stream.getvalue())
        self.assertIn("real problem", stream.getvalue())

    def test_log_file_failure_does_not_raise(self):
        """A bad LOG_FILE must not take the Control API down at boot."""
        logger = cpl.configure(
            "test.badfile", log_type="API", stream=io.StringIO(),
            log_file="/proc/definitely/not/writable/x.log",
        )
        logger.info("still alive")

    def test_every_label_is_documented(self):
        for label in cpl.TYPES:
            self.assertRegex(label, r"^[A-Z]{3}$")
            self.assertTrue(cpl.TYPES[label])


class ShellParityTests(unittest.TestCase):
    def run_sh(self, script, env=None):
        full_env = dict(os.environ)
        full_env.update(env or {})
        return subprocess.run(
            ["bash", "-c", f"source {LOG_SH}\n{script}"],
            capture_output=True, text=True, env=full_env,
        )

    def test_shell_text_line_matches_python_shape(self):
        result = self.run_sh('LOG_TYPE=RDR log_info "container started"')
        self.assertEqual(result.returncode, 0, result.stderr)
        match = TEXT_LINE.match(result.stderr.strip())
        self.assertIsNotNone(match, result.stderr)
        self.assertEqual(match.group(1), "RDR")
        self.assertEqual(match.group(2), "INFO")

    def test_shell_json(self):
        result = self.run_sh(
            'LOG_TYPE=GAP log_error "zip empty"', env={"LOG_FORMAT": "json"}
        )
        payload = json.loads(result.stderr.strip())
        self.assertEqual(payload["type"], "GAP")
        self.assertEqual(payload["level"], "ERROR")

    def test_shell_unknown_type_degrades(self):
        result = self.run_sh('LOG_TYPE=bogus log_info "hi"')
        self.assertIn("[SYS]", result.stderr)

    def test_shell_respects_level(self):
        result = self.run_sh('log_debug "quiet"', env={"LOG_LEVEL": "INFO"})
        self.assertEqual(result.stderr.strip(), "")

    def test_shell_logs_to_stderr_so_json_stdout_stays_clean(self):
        result = self.run_sh('log_info "noise"; echo "{\\"ok\\":true}"')
        self.assertEqual(json.loads(result.stdout.strip()), {"ok": True})
        self.assertIn("noise", result.stderr)

    def test_shell_and_python_share_the_label_set(self):
        with open(LOG_SH) as handle:
            declared = re.search(r'_log_types="([^"]+)"', handle.read()).group(1).split()
        self.assertEqual(sorted(declared), sorted(cpl.TYPES))


if __name__ == "__main__":
    unittest.main()
