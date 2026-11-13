"""Unprivileged, inherited Linux Landlock read allowlist for the research process.

Loaded before applying the rules; then execs Codex without a shared daemon. No
password file is opened here. Missing kernel support fails closed.
"""
import argparse
import ctypes
import os
from pathlib import Path
import platform
import sys

READ_FILE = 1 << 2
READ_DIR = 1 << 3


def restrict_reads(extra_roots):
    if sys.platform != "linux" or platform.machine() not in ("x86_64", "aarch64"):
        raise RuntimeError("live research requires supported Linux Landlock")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    class Ruleset(ctypes.Structure):
        _fields_ = [("handled_access_fs", ctypes.c_uint64)]
    class Beneath(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]
    attr = Ruleset(READ_FILE | READ_DIR)
    fd = libc.syscall(444, ctypes.byref(attr), ctypes.sizeof(attr), 0)
    if fd < 0:
        raise RuntimeError("Landlock unavailable")
    try:
        # Listing directories is harmless; reading files is opt-in below.
        rules = [(Path("/"), READ_DIR)]
        system = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/dev", "/proc", "/sys", "/run")
        for path in [*map(Path, system), *map(Path, extra_roots)]:
            if path.exists():
                rules.append((path.resolve(), READ_FILE | READ_DIR if path.is_dir() else READ_FILE))
        for path, rights in rules:
            target = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = Beneath(rights, target)
                if libc.syscall(445, fd, 1, ctypes.byref(rule), 0) < 0:
                    raise RuntimeError("Landlock read rule failed")
            finally:
                os.close(target)
        if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.syscall(446, fd, 0) != 0:
            raise RuntimeError("Landlock enforcement failed")
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--read-root", action="append", default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not command:
        return 126
    try:
        restrict_reads(args.read_root)
        os.execvpe(command[0], command, os.environ)
    except (OSError, RuntimeError):
        print("research read isolation unavailable", file=sys.stderr)
        return 126


if __name__ == "__main__":
    raise SystemExit(main())
