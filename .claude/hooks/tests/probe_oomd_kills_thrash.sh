#!/usr/bin/env bash
# Claim: systemd-oomd kills a scope that thrashes in reclaim under claude.slice, by the slice's memory pressure.
# A scope capped at 1G runs 32 workers, each holding 20M of anonymous memory and reading its own 60M file over and
# over: 2.5G of working set, so file pages are reclaimed and faulted back without end, nothing is OOM-killed by the
# kernel, and the slice's "full" memory pressure goes past 60% (measured 2026-10-10: 68% after 20 s; one worker alone
# stays under 10%, since refaults are served fast). oomd must SIGKILL the scope once the pressure stays past
# ManagedOOMMemoryPressureLimit for ManagedOOMMemoryPressureDurationSec.
# The files live on a disk-backed directory under ~/.cache, never tmpfs: a tmpfs file stays in memory charged to
# its writer and is never reclaimed from the reader. They are synced and dropped from the cache before the workers
# read them, so their pages are charged to the scope.
# Reads: claude.slice's ManagedOOM properties, oomctl, systemd-oomd's journal since the start.
# Writes: 32 files of 60M in a mktemp directory under ~/.cache, removed at exit. The workers read the disk for up to
# PROBE_LIMIT seconds (120), each stopping at that deadline. PROBE_RED=1 skips the setup checks, so the thrash alone
# decides: with nothing watching the slice it runs the whole window, which is the probe shown red.
# Exit 0: oomd killed the scope. 1: the thrash ran the window unkilled, or ended another way. 2: oomd is not set up,
# or the directory is not disk-backed.
set -u
limit=${PROBE_LIMIT:-120}
[ -n "${PROBE_RED:-}" ] || command -v oomctl > /dev/null || { echo "no oomctl: systemd-oomd is not installed"; exit 2; }
[ -n "${PROBE_RED:-}" ] || systemctl is-active -q systemd-oomd || { echo "systemd-oomd is not running"; exit 2; }
[ -n "${PROBE_RED:-}" ] || [ "$(systemctl --user show -p ManagedOOMMemoryPressure --value claude.slice)" = kill ] \
    || { echo "claude.slice does not set ManagedOOMMemoryPressure=kill (systemctl --user daemon-reload?)"; exit 2; }
# oomctl may need root to reach oomd; when it answers, claude.slice must be among the cgroups it monitors.
if [ -z "${PROBE_RED:-}" ] && monitored=$(oomctl 2> /dev/null); then
    grep -q 'claude\.slice' <<< "$monitored" || { echo "claude.slice is not among the cgroups oomd monitors"; exit 2; }
fi
cache=${XDG_CACHE_HOME:-$HOME/.cache}
mkdir -p "$cache"
d=$(mktemp -d -p "$cache" oomd-probe.XXXXXX)
trap 'rm -rf "$d"' EXIT
fs=$(stat -f -c %T "$d")
case "$fs" in tmpfs | ramfs) echo "$d is on $fs: its pages are never reclaimed from the reader"; exit 2 ;; esac
for k in $(seq 0 31); do
    dd if=/dev/urandom of="$d/f$k" bs=1M count=60 status=none
done
sync
since=$(date '+%Y-%m-%d %H:%M:%S')
start=$(date +%s)
timeout $((limit + 30)) systemd-run --user --scope --quiet --collect --slice=claude.slice -p MemoryMax=1G \
    -p MemorySwapMax=0 -- python3 -c '
import mmap, os, sys, time
prefix, limit = sys.argv[1], float(sys.argv[2])
kids = []
for k in range(32):
    pid = os.fork()
    if pid == 0:
        anon = bytearray(20 << 20)
        for i in range(0, len(anon), 4096):
            anon[i] = 1
        t0 = time.monotonic()
        with open(f"{prefix}{k}", "rb") as f:
            os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
            m = mmap.mmap(f.fileno(), 0, prot=mmap.PROT_READ)
            while time.monotonic() - t0 < limit:
                for off in range(0, len(m), 4096):
                    m[off]
        os._exit(0)
    kids.append(pid)
for pid in kids:
    os.waitpid(pid, 0)
' "$d/f" "$limit"
rc=$?
took=$(( $(date +%s) - start ))
killed=$(journalctl -q -u systemd-oomd --since "$since" 2> /dev/null | grep 'due to memory pressure' | tail -1)
if [ -n "$killed" ]; then
    echo "PASS: oomd killed the thrashing scope after ${took} s (exit ${rc}):"
    echo "$killed"
    exit 0
fi
if [ "$rc" -eq 0 ]; then
    echo "FAIL: the thrash ran ${took} s and oomd did not kill it"
else
    echo "FAIL: the thrash ended after ${took} s with exit ${rc}, and oomd's journal records no kill"
fi
exit 1
