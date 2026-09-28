import sys

sys.path.insert(0, "/tmp/claude-1000/-home-nicolas-dev-agda-aletheia/025c2552-d9d6-44e5-9753-8eedfd64010c/scratchpad/hooks-new")
import _shell  # noqa: E402

CASES = [
    "echo start\ncargo test",
    "n=${#x}; cargo test",
    "echo a#b > tools/f.txt",
    "(cd rust && cargo test)",
    "for i in 1 2; do cargo test; done",
    "if cargo test; then echo ok; fi",
    "{ cargo test; } > /tmp/x.log 2>&1",
    "echo $(cargo test) `ctest`",
    "cat <<'EOF' > /tmp/s.sh\ncargo test\nEOF",
    "git commit -F - <<'EOF' && git log -1\nci: run go test; ctest now logs\nEOF",
    "python3 - <<'PY'\nfrom pathlib import Path\nPath('CHANGELOG.md').write_text('x')\nPY",
    "S=/tmp/sp; taskset -c 0-19 cargo test > \"$S/x.log\" 2>&1; echo \"EXIT=$?\" >> \"$S/x.log\"",
    "cd /home/nicolas/dev/agda/aletheia && echo x > note.txt",
    "tail -f -n +1 \"$log\" | grep -E --line-buffered \"^FAIL \" &\nwhile kill -0 1 2> /dev/null; do sleep 15; done",
    "f() { cargo test; }; f",
    "case $x in a) cargo test;; b) echo;; esac",
    "cat > f <<'END-NOTES'\nwe don't run cargo test here\nEND-NOTES",
    "bash <<< 'cargo test'",
    "taskset -c 0-19 cargo test >& /tmp/x.log",
    "echo x > >(cat)",
    "[[ $# -gt 0 ]] && cargo test",
]
for c in CASES:
    try:
        cmds = _shell.commands(c, "/home/nicolas/dev/agda/aletheia")
    except _shell.ParseError as e:
        print(f"PARSE ERROR {e!s:30} <- {c!r}")
        continue
    print(repr(c)[:70])
    for cmd in cmds:
        redirs = [(r.fd, r.op, r.target.value if r.target else None, (r.body or "")[:25]) for r in cmd.redirects]
        flags = "".join(f for f, on in (("B", cmd.background), ("<", cmd.pipe_in), (">", cmd.pipe_out)) if on)
        print(f"    {cmd.argv} cwd={cmd.cwd} {flags} {redirs}")
