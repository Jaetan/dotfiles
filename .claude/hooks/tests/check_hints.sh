#!/usr/bin/env bash
# Hold the kit's Python type hints to its record, PYTHON_IMPRECISE_HINTS.yaml,
# with aletheia's gate, tools/check_precise_hints.py: the one checker, so the
# kit is judged the way aletheia's own code is. Every tracked Python file under
# the kit is read. Arguments are passed to the gate (--print-record prints the
# kit's rows in the record's spelling).
# The gate is found in $ALETHEIA_REPO, or ~/dev/agda/aletheia, the way
# precise-hints-guard.py finds it.
# Exit 0: every hint recorded. 1: a hint or a row out of step with the record.
# 2: the gate or the record cannot be read.
set -u
kit=$(cd "$(dirname "$0")/.." && pwd) || exit 2
gate=${ALETHEIA_REPO:-$HOME/dev/agda/aletheia}
if [ ! -x "$gate/python/.venv/bin/python" ] || [ ! -f "$gate/tools/check_precise_hints.py" ]; then
	echo "check_hints: no gate at $gate (tools/check_precise_hints.py and python/.venv)"
	exit 2
fi
cd "$gate" || exit 2
exec python/.venv/bin/python -m tools.check_precise_hints --root "$kit" --record "$kit/PYTHON_IMPRECISE_HINTS.yaml" "$@"
