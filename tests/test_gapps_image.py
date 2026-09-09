#!/usr/bin/env python3
"""Contract for baking a Redroid image that already contains GApps.

Why bake at all, when `install-gapps-redroid.sh` exists: that script pushes an
extracted zip into a *running* container. Redroid's `/system` is a read-only
image layer, so the push is lost the moment the container is recreated, and
`adb install` cannot make GmsCore a privileged system app (it needs to sit in
priv-app with a privapp-permissions whitelist before boot). Baking is what
makes Play survive a restart and a fleet redeploy.

Everything here runs offline: no Docker, no OCI, no proprietary zip. The zips
are synthetic and only carry the *shape* the builder inspects.
"""

import os
import platform
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build-redroid-gapps-image.sh"


def run(cmd, env=None, timeout=30):
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(cmd, text=True, capture_output=True, env=merged, timeout=timeout)


def build(*args, env=None):
    return run(["bash", str(BUILDER), *args], env=env)


def make_zip(path, names, comment=b""):
    with zipfile.ZipFile(path, "w") as z:
        for name in names:
            z.writestr(name, "x")
        if comment:
            z.comment = comment
    return str(path)


# A MindTheGapps-shaped tree: the overlay dirs plus the packages the installer
# and `/health` both look for.
MTG_ARM64 = [
    "META-INF/com/google/android/update-binary",
    "system/product/priv-app/PrebuiltGmsCore/PrebuiltGmsCore.apk",
    "system/product/priv-app/Phonesky/Phonesky.apk",
    "system/product/priv-app/GoogleServicesFramework/GoogleServicesFramework.apk",
    "system/product/etc/permissions/privapp-permissions-google.xml",
    "system/product/framework/com.google.android.maps.jar",
    "system/product/lib64/libjni_latinimegoogle.so",
]

MTG_X86_64 = [n.replace("lib64/", "lib64/x86_64/") for n in MTG_ARM64]

HOST_IS_X86 = platform.machine() in ("x86_64", "amd64")
NATIVE_PLATFORM = "linux/amd64" if HOST_IS_X86 else "linux/arm64"
FOREIGN_PLATFORM = "linux/arm64" if HOST_IS_X86 else "linux/amd64"


def zip_for(tmp, plat):
    """A zip whose arch matches the target, so arch checks stay out of the way."""
    if plat.endswith("arm64"):
        return make_zip(Path(tmp) / "MindTheGapps-11.0.0-arm64.zip", MTG_ARM64)
    return make_zip(Path(tmp) / "MindTheGapps-11.0.0-x86_64.zip", MTG_X86_64)


class HelpAndDefaults(unittest.TestCase):
    def test_help_names_the_arch_and_the_base(self):
        r = build("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout.lower()
        self.assertIn("arm64", out, "the OCI phone fleet is Ampere; arch must be explicit")
        self.assertIn("redroid/redroid", out)
        self.assertIn("mindthegapps", out)

    def test_dry_run_defaults_to_arm64_because_the_fleet_is_ampere(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = make_zip(Path(tmp) / "MindTheGapps-11.0.0-arm64.zip", MTG_ARM64)
            r = build("--zip", zip_path, "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("linux/arm64", r.stdout)
            self.assertIn("redroid/redroid:11.0.0-latest", r.stdout)


class ZipRefusals(unittest.TestCase):
    """The builder must refuse the same zips the installer refuses."""

    def test_missing_zip_is_refused(self):
        r = build("--zip", "/nonexistent/gapps.zip", "--dry-run")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not found", (r.stdout + r.stderr).lower())

    def test_empty_zip_is_refused_by_name(self):
        # The 0-byte /opt/gapps/gapps.zip is the original lab failure; baking
        # must not be a new way to reintroduce it.
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "gapps.zip"
            empty.write_bytes(b"")
            r = build("--zip", str(empty), "--dry-run")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("empty", (r.stdout + r.stderr).lower())

    def test_zip_without_gapps_packages_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = make_zip(
                Path(tmp) / "not-gapps-arm64.zip",
                ["system/app/Calculator/Calculator.apk"],
            )
            r = build("--zip", zip_path, "--dry-run")
            self.assertNotEqual(r.returncode, 0)
            blob = (r.stdout + r.stderr).lower()
            self.assertTrue("gmscore" in blob or "gapps" in blob, blob)


class ArchitectureMatch(unittest.TestCase):
    """An amd64 GApps zip on an Ampere phone is the failure mode to catch early.

    Every community prebuilt GApps image on Docker Hub is amd64-only, so the
    tempting shortcut silently produces a phone that cannot boot Play.
    """

    def test_x86_zip_is_refused_for_an_arm64_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = make_zip(Path(tmp) / "MindTheGapps-11.0.0-x86_64.zip", MTG_X86_64)
            r = build("--zip", zip_path, "--dry-run")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("arch", (r.stdout + r.stderr).lower())

    def test_x86_zip_is_allowed_when_the_target_is_x86(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = make_zip(Path(tmp) / "MindTheGapps-11.0.0-x86_64.zip", MTG_X86_64)
            r = build("--zip", zip_path, "--platform", "linux/amd64", "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("linux/amd64", r.stdout)

    def test_unknown_arch_is_a_warning_not_a_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = make_zip(Path(tmp) / "gapps-mystery.zip", MTG_ARM64[:5])
            r = build("--zip", zip_path, "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("warn", (r.stdout + r.stderr).lower())

    def test_unknown_arch_is_a_refusal_when_asked_to_be_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = make_zip(Path(tmp) / "gapps-mystery.zip", MTG_ARM64[:5])
            r = build("--zip", zip_path, "--require-arch-match", "--dry-run")
            self.assertNotEqual(r.returncode, 0)


class GeneratedDockerfile(unittest.TestCase):
    def _dockerfile(self, tmp, *extra):
        zip_path = make_zip(Path(tmp) / "MindTheGapps-11.0.0-arm64.zip", MTG_ARM64)
        out = Path(tmp) / "Dockerfile.out"
        r = build("--zip", zip_path, "--dry-run", "--emit-dockerfile", str(out), *extra)
        self.assertEqual(r.returncode, 0, r.stderr)
        return out.read_text()

    def test_it_derives_from_the_same_base_the_compose_file_uses(self):
        with tempfile.TemporaryDirectory() as tmp:
            df = self._dockerfile(tmp)
            self.assertIn("FROM redroid/redroid:11.0.0-latest", df)

    def test_the_product_partition_lands_at_slash_product(self):
        with tempfile.TemporaryDirectory() as tmp:
            df = self._dockerfile(tmp)
            self.assertIn("COPY --chown=0:0 gapps/system/product/ /product/", df)

    def test_a_product_only_zip_does_not_also_get_copied_to_system(self):
        # MindTheGapps ships only `system/product/...`. Treating the presence of
        # `system/` as a payload copies the same tree to /system too, leaving
        # GmsCore at /system/product/priv-app — the wrong partition — alongside
        # the right one, so the guest carries two Play Services.
        with tempfile.TemporaryDirectory() as tmp:
            df = self._dockerfile(tmp)
            self.assertNotIn("gapps/system/ /system/", df)

    def test_a_zip_with_a_real_system_payload_does_copy_to_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = make_zip(
                Path(tmp) / "NikGapps-11.0.0-arm64.zip",
                [
                    "system/priv-app/PrebuiltGmsCore/PrebuiltGmsCore.apk",
                    "system/priv-app/Phonesky/Phonesky.apk",
                    "system/etc/permissions/privapp-permissions-google.xml",
                ],
            )
            out = Path(tmp) / "Dockerfile.out"
            r = build("--zip", zip_path, "--dry-run", "--emit-dockerfile", str(out))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("COPY --chown=0:0 gapps/system/ /system/", out.read_text())

    def test_entrypoint_keeps_redroid_boot_and_skips_the_setup_wizard(self):
        with tempfile.TemporaryDirectory() as tmp:
            df = self._dockerfile(tmp)
            # COPY does not change ENTRYPOINT, but adding the setupwizard prop
            # does, so it has to be restated in full or the guest never boots.
            self.assertIn("androidboot.hardware=redroid", df)
            self.assertIn("ro.setupwizard.mode=DISABLED", df)
            self.assertIn("/init", df)

    def test_the_privapp_permission_whitelist_is_placed(self):
        with tempfile.TemporaryDirectory() as tmp:
            df = self._dockerfile(tmp)
            # Without this, GmsCore installs but is denied its privileged
            # permissions and Play sign-in fails in a way that looks like a
            # network problem.
            self.assertIn("privapp-permissions", df)


class RepoHygiene(unittest.TestCase):
    def test_no_gapps_zip_is_ever_committed(self):
        tracked = run(["git", "-C", str(ROOT), "ls-files"]).stdout.splitlines()
        offenders = [p for p in tracked if p.lower().endswith(".zip")]
        self.assertEqual(offenders, [], f"proprietary zips must not be tracked: {offenders}")

    def test_the_build_context_is_not_written_inside_the_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = make_zip(Path(tmp) / "MindTheGapps-11.0.0-arm64.zip", MTG_ARM64)
            before = set(run(["git", "-C", str(ROOT), "status", "--porcelain"]).stdout.splitlines())
            r = build("--zip", zip_path, "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            after = set(run(["git", "-C", str(ROOT), "status", "--porcelain"]).stdout.splitlines())
            self.assertEqual(before, after, "builder dirtied the working tree")


class HostsItTheSameWay(unittest.TestCase):
    """The bake is only useful if the existing deploy path can serve the result."""

    def test_dry_run_names_the_variable_the_compose_file_already_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = make_zip(Path(tmp) / "MindTheGapps-11.0.0-arm64.zip", MTG_ARM64)
            r = build("--zip", zip_path, "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("REDROID_IMAGE", r.stdout)

    def test_shipping_to_a_host_needs_no_registry(self):
        # The phone VMs have no registry credentials, so the default hosting
        # route is save + scp + load rather than push + pull.
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = make_zip(Path(tmp) / "MindTheGapps-11.0.0-arm64.zip", MTG_ARM64)
            r = build("--zip", zip_path, "--ship-to", "ubuntu@10.0.1.127", "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            plan = r.stdout
            self.assertIn("docker save", plan)
            self.assertIn("scp", plan)
            self.assertIn("docker load", plan)
            self.assertIn("ubuntu@10.0.1.127", plan)

    def test_push_is_available_when_a_registry_is_wanted(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = make_zip(Path(tmp) / "MindTheGapps-11.0.0-arm64.zip", MTG_ARM64)
            r = build("--zip", zip_path, "--tag", "iad.ocir.io/ns/redroid-gapps:11", "--push", "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("docker push", r.stdout)
            self.assertIn("iad.ocir.io/ns/redroid-gapps:11", r.stdout)

    def test_gapps_validation_after_hosting_is_the_existing_checker(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = make_zip(Path(tmp) / "MindTheGapps-11.0.0-arm64.zip", MTG_ARM64)
            r = build("--zip", zip_path, "--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            # Baking does not get to self-certify; the same gapps-check that
            # guards a runtime install must confirm the baked phone.
            self.assertIn("gapps-check", r.stdout)


class CrossArchBuild(unittest.TestCase):
    """Nobody has to own an Ampere box to produce the Ampere image.

    The bake is a COPY plus one small RUN, so qemu emulation builds it on any
    x86 machine at negligible cost. What must not happen is buildx being handed
    a foreign platform with no emulator registered: it fails inside the RUN with
    `exec format error`, which reads like a broken Dockerfile.
    """

    def _run_with_docker(self, tmp, plat, platforms, extra=()):
        bindir = Path(tmp) / "bin"
        bindir.mkdir()
        log = Path(tmp) / "docker.log"
        stub = bindir / "docker"
        stub.write_text(
            "#!/bin/bash\n"
            f'printf "%s\\n" "$*" >> {log}\n'
            'case "$1 $2" in\n'
            '  "buildx version") echo "github.com/docker/buildx v0.14.0" ;;\n'
            f'  "buildx inspect") echo "Platforms: {platforms}" ;;\n'
            'esac\n'
            "exit 0\n"
        )
        stub.chmod(0o755)
        r = build(
            "--zip", zip_for(tmp, plat), "--platform", plat, *extra,
            env={"PATH": f"{bindir}:{os.environ['PATH']}"},
        )
        return r, (log.read_text() if log.exists() else "")

    def test_a_foreign_platform_without_qemu_is_refused_before_the_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, log = self._run_with_docker(tmp, FOREIGN_PLATFORM, NATIVE_PLATFORM)
            self.assertNotEqual(r.returncode, 0, "a build that cannot succeed was started")
            self.assertNotIn("buildx build", log, "buildx was invoked anyway")
            msg = r.stdout + r.stderr
            self.assertIn("binfmt", msg, "the refusal must name the fix, not just the symptom")

    def test_a_foreign_platform_with_qemu_registered_builds(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, log = self._run_with_docker(
                tmp, FOREIGN_PLATFORM, f"{NATIVE_PLATFORM}, {FOREIGN_PLATFORM}"
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("buildx build", log)
            self.assertIn(FOREIGN_PLATFORM, log)

    def test_a_native_build_does_not_consult_the_emulator(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Platforms list deliberately empty: a native build must not be
            # gated on emulation being present.
            r, log = self._run_with_docker(tmp, NATIVE_PLATFORM, "")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("buildx build", log)

    def test_help_points_at_the_x86_route(self):
        out = build("--help").stdout.lower()
        self.assertIn("qemu", out)


class CliIntegration(unittest.TestCase):
    def test_cloud_phone_exposes_the_bake(self):
        r = run(["bash", str(ROOT / "cloud-phone"), "gapps-bake", "--help"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("arm64", r.stdout.lower())

    def test_usage_lists_the_bake(self):
        r = run(["bash", str(ROOT / "cloud-phone")])
        self.assertIn("gapps-bake", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
