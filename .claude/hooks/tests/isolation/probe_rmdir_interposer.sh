#!/usr/bin/env bash
# Probe of claude-call-rmdir.c: removing or creating an empty mount point (rm -rf, mkdir, Python's shutil.rmtree)
# succeeds with it preloaded, everything else keeps its real outcome, and ASan binaries run under it (clang's static
# runtime as is; gcc's shared runtime only with verify_asan_link_order=0, which claude-call sets in a view).
# usage: probe_rmdir_interposer.sh   (run on CPUs 0-19; exits non-zero when a check fails)
set -u
here=$(cd "$(dirname "$0")" && pwd)
src="$here/../../claude-call-rmdir.c"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fails=0
check() {  # check LABEL EXPECTED ACTUAL
    if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: expected [$2], got [$3]"; fails=$((fails + 1)); fi
}
cc -shared -fPIC -O2 -Wall -Wextra -Werror -o "$work/rmdir.so" "$src" -ldl || { echo "FAIL build"; exit 1; }
printf 'int main(void){return 0;}\n' > "$work/asan.c"
mkdir -p "$work/real" "$work/view/build" "$work/view/src"
echo s > "$work/view/src/f"
view=(bwrap --dev-bind / / --bind "$work/real" "$work/view/build" --chdir "$work/view" env LD_PRELOAD="$work/rmdir.so")
fill() { echo x > "$work/real/out.o"; }

fill; check "python rmtree of a bound directory" "ok []" \
    "$("${view[@]}" python3 -c 'import shutil, os; shutil.rmtree("build"); print("ok", os.listdir("build"))' 2>&1 | tail -1)"
fill; check "rm -rf then mkdir" "ok" "$("${view[@]}" sh -c 'rm -rf build && mkdir build && echo ok' 2>&1 | tail -1)"
fill; check "rm -rf then mkdir -p below" "ok" "$("${view[@]}" sh -c 'rm -rf build && mkdir -p build/x && echo ok' 2>&1 | tail -1)"
check "the real directory holds what the view made" "x" "$(ls "$work/real")"
fill; check "rmdir of a bound directory with content still refuses" "16" \
    "$("${view[@]}" python3 -c 'import os
try: os.rmdir("build")
except OSError as e: print(e.errno)' 2>&1 | tail -1)"
check "rmdir of a non-empty source directory still refuses" "39" \
    "$("${view[@]}" python3 -c 'import os
try: os.rmdir("src")
except OSError as e: print(e.errno)' 2>&1 | tail -1)"
check "mkdir of an existing source directory still fails" "1" "$("${view[@]}" sh -c 'mkdir src 2>/dev/null; echo $?')"
check "rm -rf of a bound directory without the interposer fails" "1" \
    "$(bwrap --dev-bind / / --bind "$work/real" "$work/view/build" --chdir "$work/view" sh -c 'rm -rf build 2>/dev/null; echo $?')"
if command -v clang >/dev/null; then
    clang -fsanitize=address -o "$work/asan-clang" "$work/asan.c"
    check "a clang ASan binary runs preloaded" "0" "$(LD_PRELOAD="$work/rmdir.so" "$work/asan-clang"; echo $?)"
fi
if gcc -fsanitize=address -o "$work/asan-gcc" "$work/asan.c" 2>/dev/null; then
    check "a gcc ASan binary refuses a preload before its runtime" "1" \
        "$(LD_PRELOAD="$work/rmdir.so" "$work/asan-gcc" 2>/dev/null; echo $?)"
    check "... and runs with verify_asan_link_order=0" "0" \
        "$(LD_PRELOAD="$work/rmdir.so" ASAN_OPTIONS=verify_asan_link_order=0 "$work/asan-gcc"; echo $?)"
fi
echo "$fails failing"
exit $((fails > 0))
