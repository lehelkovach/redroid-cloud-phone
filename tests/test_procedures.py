#!/usr/bin/env python3
"""One procedure vocabulary across mobile, web, chrome, and console."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrator import procedures as proc


def recording_driver(log, result=None):
    def drive(action, step):
        log.append((action, step))
        return result if result is not None else {"success": True, "action": action}
    return drive


def mobile_adapter(log, result=None):
    def post(path, payload=None, instance=None):
        log.append((path, payload))
        return result if result is not None else {"success": True, "path": path}
    return proc.MobileAdapter(control_post=post, instance={"api_url": "http://phone"})


def all_surfaces(log):
    return {
        proc.MOBILE: mobile_adapter(log),
        proc.WEB: proc.WebAdapter(recording_driver(log)),
        proc.CHROME: proc.ChromeAdapter(recording_driver(log)),
        proc.CONSOLE: proc.ConsoleAdapter(recording_driver(log)),
    }


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.log = []
        self.adapters = all_surfaces(self.log)

    def test_unknown_action_rejected(self):
        with self.assertRaises(proc.StepValidationError) as ctx:
            proc.validate_procedure([{"action": "teleport"}], self.adapters)
        self.assertIn("teleport", str(ctx.exception))

    def test_unknown_surface_rejected(self):
        with self.assertRaises(proc.UnknownSurfaceError):
            proc.validate_procedure([{"action": "tap", "surface": "hologram"}], self.adapters)

    def test_missing_adapter_rejected(self):
        with self.assertRaises(proc.UnknownSurfaceError):
            proc.validate_procedure(
                [{"action": "tap", "surface": proc.WEB}],
                {proc.MOBILE: mobile_adapter(self.log)},
            )

    def test_empty_procedure_rejected(self):
        with self.assertRaises(proc.StepValidationError):
            proc.validate_procedure([], self.adapters)

    def test_open_requires_a_target(self):
        with self.assertRaises(proc.StepValidationError):
            proc.validate_procedure([{"action": "open"}], self.adapters)

    def test_shell_requires_a_command(self):
        with self.assertRaises(proc.StepValidationError):
            proc.validate_procedure(
                [{"action": "shell", "surface": proc.CONSOLE}], self.adapters
            )

    def test_negative_wait_rejected(self):
        with self.assertRaises(proc.StepValidationError):
            proc.validate_procedure([{"action": "wait", "duration_ms": -5}], self.adapters)

    def test_validation_does_not_mutate_caller_steps(self):
        original = {"action": "tap", "x": 1, "y": 2}
        proc.validate_procedure([original], self.adapters)
        self.assertNotIn("surface", original)

    def test_nothing_executes_when_a_later_step_is_invalid(self):
        """Half-applying a procedure to a live phone is worse than not starting."""
        steps = [
            {"action": "open", "package": "com.example.app"},
            {"action": "teleport"},
        ]
        with self.assertRaises(proc.StepValidationError):
            proc.run_procedure(steps, self.adapters, sleep=lambda s: None)
        self.assertEqual(self.log, [], "no step may reach a surface")


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.log = []
        self.adapters = all_surfaces(self.log)

    def test_console_cannot_tap(self):
        with self.assertRaises(proc.UnsupportedActionError) as ctx:
            proc.validate_procedure(
                [{"action": "tap", "surface": proc.CONSOLE, "x": 1, "y": 2}], self.adapters
            )
        self.assertIn("console", str(ctx.exception))

    def test_chrome_cannot_purchase(self):
        """The helper proposes; the person clicks. No unattended spend in a real tab."""
        with self.assertRaises(proc.UnsupportedActionError):
            proc.validate_procedure(
                [{"action": "purchase", "surface": proc.CHROME}],
                self.adapters, approve=True,
            )

    def test_mobile_cannot_purchase(self):
        with self.assertRaises(proc.UnsupportedActionError):
            proc.validate_procedure(
                [{"action": "purchase", "surface": proc.MOBILE}],
                self.adapters, approve=True,
            )

    def test_web_can_purchase_with_approval(self):
        steps = proc.validate_procedure(
            [{"action": "purchase", "surface": proc.WEB}], self.adapters, approve=True
        )
        self.assertEqual(steps[0]["surface"], proc.WEB)

    def test_surface_capabilities_report(self):
        caps = proc.surface_capabilities(self.adapters)
        self.assertEqual(sorted(caps), [proc.CHROME, proc.CONSOLE, proc.MOBILE, proc.WEB])
        self.assertIn("swipe", caps[proc.MOBILE])
        self.assertNotIn("swipe", caps[proc.CONSOLE])


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.log = []
        self.adapters = all_surfaces(self.log)

    def test_install_needs_approval(self):
        with self.assertRaises(proc.ApprovalRequiredError) as ctx:
            proc.validate_procedure(
                [{"action": "install", "path": "/tmp/app.apk"}], self.adapters
            )
        self.assertEqual(ctx.exception.action, "install")

    def test_submit_needs_approval(self):
        with self.assertRaises(proc.ApprovalRequiredError):
            proc.validate_procedure([{"action": "submit"}], self.adapters)

    def test_approved_install_runs(self):
        outcome = proc.run_procedure(
            [{"action": "install", "path": "/tmp/app.apk"}],
            self.adapters, approve=True, sleep=lambda s: None,
        )
        self.assertEqual(outcome["status"], "done")
        self.assertEqual(self.log[0][0], "/apps/install")

    def test_sensitive_step_blocks_the_whole_run(self):
        steps = [{"action": "tap", "x": 1, "y": 2}, {"action": "submit"}]
        with self.assertRaises(proc.ApprovalRequiredError):
            proc.run_procedure(steps, self.adapters, sleep=lambda s: None)
        self.assertEqual(self.log, [])


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.log = []
        self.adapters = all_surfaces(self.log)

    def test_same_steps_run_on_every_surface(self):
        """The point of the abstraction: one script, four backends."""
        steps = [
            {"action": "open", "target": "example", "package": "example", "url": "example"},
            {"action": "type", "text": "hello"},
        ]
        for surface in (proc.MOBILE, proc.WEB, proc.CHROME):
            with self.subTest(surface=surface):
                self.log.clear()
                outcome = proc.run_procedure(
                    steps, self.adapters, default_surface=surface, sleep=lambda s: None
                )
                self.assertEqual(outcome["status"], "done")
                self.assertEqual(len(outcome["steps"]), 2)
                self.assertTrue(all(s["surface"] == surface for s in outcome["steps"]))

    def test_cross_surface_procedure(self):
        """Read a code on the phone, type it into the browser."""
        steps = [
            {"action": "read", "surface": proc.MOBILE},
            {"action": "type", "text": "123456", "surface": proc.WEB},
        ]
        outcome = proc.run_procedure(steps, self.adapters, sleep=lambda s: None)
        self.assertEqual(outcome["status"], "done")
        self.assertEqual(
            [s["surface"] for s in outcome["steps"]], [proc.MOBILE, proc.WEB]
        )

    def test_failure_halts_and_reports_the_index(self):
        adapters = {proc.MOBILE: mobile_adapter(self.log, {"success": False, "error": "adb gone"})}
        steps = [{"action": "tap", "x": 1, "y": 2}, {"action": "tap", "x": 3, "y": 4}]
        outcome = proc.run_procedure(steps, adapters, sleep=lambda s: None)
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["failed_index"], 0)
        self.assertEqual(outcome["error"], "adb gone")
        self.assertEqual(len(outcome["steps"]), 1, "must not continue past a failure")

    def test_adapter_exception_becomes_a_failed_step(self):
        def boom(action, step):
            raise RuntimeError("driver crashed")
        adapters = {proc.WEB: proc.WebAdapter(boom)}
        outcome = proc.run_procedure(
            [{"action": "tap", "x": 1, "y": 2}], adapters,
            default_surface=proc.WEB, sleep=lambda s: None,
        )
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["steps"][0]["status"], "error")
        self.assertIn("driver crashed", outcome["error"])

    def test_wait_does_not_really_sleep(self):
        slept = []
        outcome = proc.run_procedure(
            [{"action": "wait", "duration_ms": 5000}], self.adapters,
            sleep=slept.append,
        )
        self.assertEqual(outcome["status"], "done")
        self.assertEqual(slept, [5.0])

    def test_steps_are_timed(self):
        outcome = proc.run_procedure(
            [{"action": "tap", "x": 1, "y": 2}], self.adapters, sleep=lambda s: None
        )
        self.assertIn("duration_ms", outcome["steps"][0])
        self.assertGreaterEqual(outcome["steps"][0]["duration_ms"], 0)

    def test_login_procedure_shape(self):
        steps = proc.login_procedure("com.example.app", "u", "p", password_label="Password")
        actions = [s["action"] for s in steps]
        self.assertEqual(actions[0], "open")
        self.assertIn("tap_label", actions)
        self.assertEqual(actions[-1], "key")
        self.assertEqual([s for s in steps if s["action"] == "type"][1]["text"], "p")

    def test_login_procedure_runs_on_web_too(self):
        steps = proc.login_procedure("https://example.com", "u", "p", surface=proc.WEB)
        outcome = proc.run_procedure(steps, self.adapters, sleep=lambda s: None)
        self.assertEqual(outcome["status"], "done")


if __name__ == "__main__":
    unittest.main()
