import os


MOUNT_WORKER_MODES = {"mount_worker", "macfuse_worker"}

# Identifies one AutoOrtho run; the parent passes it to its mount workers.
SESSION_ENV = "AO_SESSION_ID"


def is_mount_worker_mode(mode=None):
    if mode is None:
        mode = os.environ.get("AO_RUN_MODE")
    return mode in MOUNT_WORKER_MODES
