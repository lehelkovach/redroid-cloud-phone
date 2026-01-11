#!/bin/bash
# Unmount waydroid rootfs after container stop
python3 << 'PYTHON'
import sys
import os
sys.path.insert(0, '/usr/lib/waydroid/tools')
os.chdir('/usr/lib/waydroid')
from tools.helpers import images
import argparse

class Args:
    pass

args = Args()
try:
    images.umount_rootfs(args)
    print("Rootfs unmounted successfully")
except Exception as e:
    print(f"Unmount failed (may already be unmounted): {e}")
PYTHON









