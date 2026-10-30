"""A fake host for `orchestrator/user_volumes.py`: loop devices, LUKS headers, mounts.

The volume manager sends every privileged command (losetup, cryptsetup,
mkfs.ext4, mount, umount) through one injectable runner. `FakeVolumeHost`
is that runner plus the kernel state behind it, so tests can assert both the
exact command sequence and its effect: which image is open under which mapper
name, which key unlocked it, what is mounted where. A wrong key fails the way
cryptsetup does (exit 2), so "B's key cannot open A's image" is checked by
behaviour, not by reading the code.

It keeps its state across an orchestrator "restart" (the harness drops the
orchestrator's dicts, not this object), which is what a real host does: the
kernel's mounts outlive the orchestrator process.

Nothing here runs a real command.
"""

import os
import subprocess
from types import SimpleNamespace

GIB = 1024 ** 3


class FakeVolumeHost:
    def __init__(self, host_total=500 * GIB, host_free=400 * GIB):
        self.calls = []            # [(argv tuple, input bytes or None)]
        self.loops = {}            # "/dev/loopN" -> image path
        self.headers = {}          # image path -> key bytes (the LUKS keyslot)
        self.formatted = set()     # image paths whose plaintext holds ext4
        self.mappers = {}          # mapper name -> image path
        self.mounts = {}           # mount path -> mapper name
        self.used = {}             # mount path -> used bytes (what statvfs reports)
        self.sizes = {}            # image path -> bytes (from the file at luksFormat time)
        self.host_total = host_total
        self.host_free = host_free
        self.fail_on = None        # argv[0:2] tuple that should fail once, for error paths
        self._next_loop = 0

    # -- the runner -----------------------------------------------------------
    def __call__(self, argv, input=None):
        argv = tuple(str(a) for a in argv)
        self.calls.append((argv, input))
        if self.fail_on and tuple(argv[: len(self.fail_on)]) == self.fail_on:
            self.fail_on = None
            return self._done(argv, 1, err="injected failure")
        cmd = argv[0]
        handler = {
            "losetup": self._losetup,
            "cryptsetup": self._cryptsetup,
            "mkfs.ext4": self._mkfs,
            "mount": self._mount,
            "umount": self._umount,
        }.get(cmd)
        if handler is None:
            return self._done(argv, 127, err=f"{cmd}: not faked")
        return handler(argv, input)

    @staticmethod
    def _done(argv, rc, out="", err=""):
        return subprocess.CompletedProcess(list(argv), rc, out, err)

    def _image_of(self, dev):
        return self.loops.get(dev, dev)

    def _losetup(self, argv, _input):
        if argv[1:3] == ("--find", "--show"):
            dev = f"/dev/loop{self._next_loop}"
            self._next_loop += 1
            self.loops[dev] = argv[3]
            return self._done(argv, 0, out=dev + "\n")
        if argv[1] == "-j":
            img = argv[2]
            lines = [f"{d}: [2049]:1 ({i})" for d, i in sorted(self.loops.items()) if i == img]
            return self._done(argv, 0, out="\n".join(lines) + ("\n" if lines else ""))
        if argv[1] == "-d":
            if self.loops.pop(argv[2], None) is None:
                return self._done(argv, 1, err="no such loop")
            return self._done(argv, 0)
        return self._done(argv, 1, err="unsupported losetup form")

    def _cryptsetup(self, argv, key):
        verb = argv[1]
        if verb == "luksFormat":
            target = argv[-1]
            self.headers[self._image_of(target)] = key
            try:
                self.sizes[self._image_of(target)] = os.path.getsize(self._image_of(target))
            except OSError:
                pass
            return self._done(argv, 0)
        if verb == "open" and "--test-passphrase" in argv:
            img = self._image_of(argv[-1])
            if img not in self.headers:
                return self._done(argv, 1, err="Device is not a valid LUKS device.")
            return self._done(argv, 0 if self.headers[img] == key else 2,
                              err="" if self.headers[img] == key else "No key available with this passphrase.")
        if verb == "open":
            dev, name = argv[-2], argv[-1]
            img = self._image_of(dev)
            if img not in self.headers:
                return self._done(argv, 1, err="Device is not a valid LUKS device.")
            if name in self.mappers:
                return self._done(argv, 5, err=f"Device {name} already exists.")
            if self.headers[img] != key:
                return self._done(argv, 2, err="No key available with this passphrase.")
            self.mappers[name] = img
            return self._done(argv, 0)
        if verb == "close":
            if self.mappers.pop(argv[2], None) is None:
                return self._done(argv, 4, err="Device is not active.")
            return self._done(argv, 0)
        if verb == "luksErase":
            self.headers.pop(self._image_of(argv[-1]), None)
            return self._done(argv, 0)
        return self._done(argv, 1, err=f"unsupported cryptsetup {verb}")

    def _mkfs(self, argv, _input):
        dev = argv[-1]
        name = dev.rsplit("/", 1)[-1]
        if name not in self.mappers:
            return self._done(argv, 1, err="no such device")
        self.formatted.add(self.mappers[name])
        return self._done(argv, 0)

    def _mount(self, argv, _input):
        dev, mnt = argv[-2], argv[-1]
        name = dev.rsplit("/", 1)[-1]
        if name not in self.mappers or self.mappers[name] not in self.formatted:
            return self._done(argv, 32, err="wrong fs type")
        if mnt in self.mounts:
            return self._done(argv, 32, err="already mounted")
        self.mounts[mnt] = name
        self.used.setdefault(mnt, 0)
        return self._done(argv, 0)

    def _umount(self, argv, _input):
        if self.mounts.pop(argv[-1], None) is None:
            return self._done(argv, 32, err="not mounted")
        return self._done(argv, 0)

    # -- the other hooks the manager takes ------------------------------------
    def is_mounted(self, path):
        return path in self.mounts

    def mapper_exists(self, name):
        return name in self.mappers

    def statvfs(self, path):
        if path in self.mounts:
            img = self.mappers[self.mounts[path]]
            total = self.sizes.get(img) or os.path.getsize(img)
            used = min(self.used.get(path, 0), total)
            return SimpleNamespace(f_frsize=4096, f_bsize=4096, f_blocks=total // 4096,
                                   f_bfree=(total - used) // 4096, f_bavail=(total - used) // 4096)
        return SimpleNamespace(f_frsize=4096, f_bsize=4096, f_blocks=self.host_total // 4096,
                               f_bfree=self.host_free // 4096, f_bavail=self.host_free // 4096)

    # -- assertions helpers ---------------------------------------------------
    def commands(self):
        """Just the argv tuples, for sequence assertions."""
        return [argv for argv, _ in self.calls]

    def verbs(self):
        """`losetup --find`, `cryptsetup open`, ...: the first two words of each call."""
        out = []
        for argv, _ in self.calls:
            out.append(" ".join(argv[:2]))
        return out

    def mounted_image(self, mnt):
        name = self.mounts.get(mnt)
        return self.mappers.get(name) if name else None

    def hooks(self):
        return dict(runner=self, is_mounted=self.is_mounted, mapper_exists=self.mapper_exists,
                    statvfs=self.statvfs)
