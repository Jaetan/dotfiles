"""The heavy matcher: the [[heavy]] row of a declaration (declarations/schema.py) a Command runs heavy.

heavy(command, facts) reads ``command`` against facts.project.heavy, then KIT_ROWS, the kit's own. Of the rows that
run it, the one with the most words of its own (an argv row's, one for any other) decides, the first at equal count,
and heavy() gives it unless its run is light; UNKNOWN where the command's name is not known and no word past it is
--help, -h, --version or -V; None otherwise. The name is the base name of the first word's value or, where that is not known, of its text as written less its quotes, where
that base name holds no expansion or glob character. A row is run:
  - program: by a command whose name is the program, alone or followed by `-` and a version of digits and dots, or
    by a Python interpreter's words running as their script a file of such a base name;
  - argv: by a command whose name is the first word and whose leading operands are the others in order, the word
    after an option of valued_options skipped;
  - module a.b: by a Python interpreter's words (dispatcher/python.py's invoked()) running -m a.b, or a file whose path
    is a/b.py or ends in `/` and it; or by such a file run by its path;
  - script: by the file run by its path, as bash, sh or dash's operand (dispatcher/scripts.py's operand(), none
    under -c, -n or -o noexec) or read by `source` or `.`, whose path is the script's own beneath facts.work_root or
    ends in `/` and it, or which placed from the command's directory is the script.
A word the call does not make known matches any word or module, and as a path any file whose base name its written
base name is, or any file where that is not known. An option is a word led by `-` or `+`. The run is light where a word
past the row's own is one of its light_with, alone or with `=VALUE`, or --help, -h, --version or -V where
verbose_flags does not name it; where it has operands, every one known and in light_targets; or, under targeted_by,
where it has operands, every one known and under catch2-operands the only one, under pytest-operands holding no glob
character and ending in `.py` or holding `::`, under python-files holding no glob character and ending in `.py`. The
word after an option of valued_options, or of its targeted_by kind's valued options, is neither. No other key is read.
"""

import os
import re
from collections import deque
from collections.abc import Sequence
from itertools import islice
from typing import TYPE_CHECKING, Literal

from declarations.schema import Heavy, PathText, ProgramName, TargetedBy
from dispatcher.heavy_table import ROWS
from dispatcher.model import CARRIERS, PYTHON_NAME, Command, Word, options
from dispatcher.python import invoked
from dispatcher.scripts import operand
from dispatcher.tree import Count, ShellText

if TYPE_CHECKING:
    from declarations.lookup import Facts

Unknown = Literal["unknown"]
type Matched = Heavy | Unknown | None
type Run = tuple[Heavy, Sequence[Word]]

UNKNOWN: Unknown = "unknown"
KIT_ROWS: tuple[Heavy, ...] = ROWS
LIGHT_FLAGS = options(ShellText("--help -h --version -V"))
SOURCES = options(ShellText("source ."))
VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)*")
GLOB = re.compile(r"[*?\[]")
DYNAMIC = re.compile(r"[$`\\*?\[]")
VALUED_BY: dict[TargetedBy | None, frozenset[ShellText]] = {
    None: frozenset(), "catch2-operands": options(ShellText(
    "--order --rng-seed -r --reporter -o --out -c --section -d --durations --verbosity --warn -x --abortx --colour-mode "
    "--benchmark-samples --benchmark-resamples --benchmark-confidence-interval --benchmark-warmup-time --shard-count "
    "--shard-index --wait-for-keypress --min-duration")),
    "pytest-operands": options(ShellText(
    "--assert --basetemp -c --cache-show --capture --code-highlight --color --confcutdir --config-file --cov "
    "--cov-config --cov-context --cov-fail-under --cov-precision --cov-report --debug --deselect --doctest-glob "
    "--doctest-report --durations --durations-min --hypothesis-profile --hypothesis-seed --hypothesis-verbosity "
    "--ignore --ignore-glob --import-mode --junit-prefix --junit-xml --junitprefix --junitxml -k "
    "--last-failed-no-failures --lfnf --log-auto-indent --log-cli-date-format --log-cli-format --log-cli-level "
    "--log-date-format --log-disable --log-file --log-file-date-format --log-file-format --log-file-level "
    "--log-file-mode --log-format --log-level -m --markdown-docs-syntax --max-warnings --maxfail -o --override-ini "
    "-p --pastebin --pdbcls --pythonwarnings -r --random-order-bucket --random-order-seed --report-chars --rootdir "
    "--show-capture --tb --verbosity -W")),
    "python-files": options(ShellText(
    "--additional-builtins --allow-any-import-level --allow-global-unused-variables --allow-reexport-from-package "
    "--allow-wildcard-with-all --allowed-redefined-builtins --analyse-fallback-blocks --argument-naming-style "
    "--argument-rgx --attr-naming-style --attr-rgx --bad-names --bad-names-rgxs --callbacks "
    "--check-fixme-in-docstring --check-protected-access-in-special-methods --check-quote-consistency "
    "--check-str-concat-over-line-jumps --class-attribute-naming-style --class-attribute-rgx "
    "--class-const-naming-style --class-const-rgx --class-naming-style --class-rgx --clear-cache-post-run "
    "--confidence --const-naming-style --const-rgx --contextmanager-decorators -d --defining-attr-methods "
    "--deprecated-modules --disable --docstring-min-length --dummy-variables-rgx -e --enable "
    "--evaluation --exclude-protected --exclude-too-few-public-methods "
    "--expected-line-ending-format --ext-import-graph --extension-pkg-allow-list --extension-pkg-whitelist -f "
    "--fail-on --fail-under --function-naming-style --function-rgx --generated-members --good-names "
    "--good-names-rgxs --help-msg --ignore --ignore-comments --ignore-docstrings --ignore-imports "
    "--ignore-long-lines --ignore-mixin-members --ignore-none --ignore-on-opaque-inference --ignore-paths "
    "--ignore-pattern-in-long-lines --ignore-patterns --ignore-signatures --ignored-argument-names "
    "--ignored-checks-for-mixins --ignored-classes --ignored-modules --ignored-parents --import-graph "
    "--include-naming-hint --indent-after-paren --indent-string --init-hook --init-import --inlinevar-naming-style "
    "--inlinevar-rgx --int-import-graph -j --jobs --known-first-party --known-side-effects-only-functions "
    "--known-standard-library --known-third-party --limit-inference-results --load-plugins --logging-format-style "
    "--logging-modules --max-args --max-attributes --max-bool-expr --max-branches --max-line-length --max-locals "
    "--max-module-lines --max-nested-blocks --max-parents --max-positional-arguments --max-public-methods "
    "--max-returns --max-spelling-suggestions --max-statements --method-naming-style --method-rgx "
    "--min-public-methods --min-similarity-lines --missing-member-hint --missing-member-hint-distance "
    "--missing-member-max-choices --mixin-class-rgx --module-naming-style --module-rgx --msg-template --name-group "
    "--never-returning-functions --no-docstring-rgx --notes --notes-rgx --output --output-format "
    "--overgeneral-exceptions --paramspec-rgx --persistent --prefer-stubs --preferred-modules --property-classes "
    "--py-version -r --rcfile --recursive --redefining-builtins-modules --reports -s --score --signature-mutators "
    "--single-line-class-stmt --single-line-if-stmt --source-roots --spelling-dict "
    "--spelling-ignore-comment-directives --spelling-ignore-words --spelling-private-dict-file "
    "--spelling-store-unknown-words --suggest-join-with-non-empty-separator --timeout-methods --typealias-rgx "
    "--typevar-rgx --typevartuple-rgx --valid-classmethod-first-arg --valid-metaclass-classmethod-first-arg "
    "--variable-naming-style --variable-rgx"))}


def base(path: ShellText | PathText) -> ShellText:
    """The base name of ``path``."""
    return ShellText(path.rsplit("/", 1)[-1])


def written(word: Word) -> ShellText | None:
    """The base name of ``word``'s value, or where that is not known of its text less its quotes; None where that
    holds an expansion or glob character."""
    if word.value is not None:
        return base(word.value)
    name = base(ShellText(word.text.replace('"', "").replace("'", "")))
    return None if DYNAMIC.search(name) else name


def option(value: ShellText) -> bool:
    """Whether ``value`` is an option: a word led by `-` or `+`."""
    return value.startswith(("-", "+"))


def names(word: Word, file: PathText) -> bool:
    """Whether the path ``word`` names ``file``: its value is ``file`` or ends in `/` and it; where not known,
    its written base name is ``file``'s or is not known."""
    if word.value is None:
        return written(word) in (None, base(file))
    return word.value == file or word.value.endswith(f"/{file}")


def ran(row: Heavy, word: Word, command: Command, facts: Facts) -> bool:
    """Whether the path ``word`` names the file ``row`` runs: a module's a/b.py, or its script beneath facts.work_root
    or placed from ``command``'s directory."""
    if row.script is None:
        return names(word, PathText(f"{str(row.module).replace('.', '/')}.py"))
    placed = (word.value is not None and command.cwd is not None
              and os.path.normpath(os.path.join(command.cwd, word.value)) == row.script)
    root = facts.work_root
    return placed or names(word, row.script if root is None else PathText(os.path.relpath(row.script, root)))


def leading(row: Heavy, words: Sequence[Word]) -> list[Word] | None:
    """The words past the name of ``words`` but the argv ``row``'s own, these being its leading operands in order, a
    word not known matching any and the word after an option of valued_options skipped; None where they are not."""
    wanted = deque(row.argv[1:])
    others: list[Word] = []
    rest = iter(words[1:])
    for word in rest:
        if wanted and (word.value is None or not option(word.value)):
            if word.value not in (None, wanted.popleft()):
                return None
            continue
        others.append(word)
        if word.value in row.valued_options:
            others.extend(islice(rest, 1))
    return None if wanted else others


def reads(row: Heavy, command: Command, facts: Facts, name: ShellText) -> Sequence[Word] | None:
    """The words of ``command``, named ``name``, a run of ``row`` reads past its own; None where it does not run it."""
    words = command.words
    if row.argv:
        return leading(row, words) if name == row.argv[0] else None
    if row.program is not None:
        if versioned(name, row.program):
            return words[1:]
        run = invoked([word.value for word in words]) if PYTHON_NAME.fullmatch(name) else None
        if run is None or run.kind != "script":
            return None
        script = written(words[run.index])
        return words[run.index + 1:] if script is None or versioned(script, row.program) else None
    if row.module is not None and PYTHON_NAME.fullmatch(name):
        run = invoked([word.value for word in words])
        if run is not None and run.kind == "module":
            value = words[run.index].value
            return words[run.index + 1:] if value is None or value[run.offset:] == row.module else None
        index = run.index if run is not None and run.kind == "script" else None
    elif row.script is not None and name in CARRIERS:
        index = operand([word.value for word in words])
    elif row.script is not None and name in SOURCES:
        index = Count(1) if len(words) > 1 else None
    else:
        index = Count(0) if "/" in words[0].spelled else None
    return words[index + 1:] if index is not None and ran(row, words[index], command, facts) else None


def versioned(name: ShellText, program: ProgramName) -> bool:
    """Whether the base name ``name`` is ``program``, alone or followed by `-` and a version of digits and dots."""
    stem, _, version = name.rpartition("-")
    return name == program or (stem == program and VERSION.fullmatch(version) is not None)


def targeted(kind: TargetedBy, operands: Sequence[ShellText]) -> bool:
    """Whether ``operands``, a run's known ones under targeted_by ``kind``, name its targets: under catch2-operands
    one; under pytest-operands each holding no glob character and ending in `.py` or holding `::`; under python-files
    each holding no glob character and ending in `.py`."""
    if kind == "catch2-operands":
        return len(operands) == 1
    return all(GLOB.search(value) is None
               and (value.endswith(".py") or (kind == "pytest-operands" and "::" in value)) for value in operands)


def light(row: Heavy, words: Sequence[Word]) -> bool:
    """Whether ``words``, those a run of ``row`` reads past its own, make the run light."""
    flags = frozenset(flag for flag in LIGHT_FLAGS if flag not in row.verbose_flags)
    takes = VALUED_BY[row.targeted_by] | frozenset(row.valued_options)
    operands: list[ShellText] = []
    unknown = False
    rest = iter(word.value for word in words)
    for value in rest:
        if value is None:
            unknown = True
        elif value in takes:
            _ = next(rest, None)
        elif value in flags or value.partition("=")[0] in row.light_with:
            return True
        elif not option(value):
            operands.append(value)
    if unknown or not operands:
        return False
    if all(value in row.light_targets for value in operands):
        return True
    return row.targeted_by is not None and targeted(row.targeted_by, operands)


def heavy(command: Command, facts: Facts) -> Matched:
    """The row deciding ``command``, of facts.project.heavy and then KIT_ROWS, where it runs heavy; UNKNOWN where the
    command's name is not known and no word past it asks for help or the version; None otherwise."""
    if not command.words:
        return None
    if (name := written(command.words[0])) is None:
        return None if any(word.value in LIGHT_FLAGS for word in command.words[1:]) else UNKNOWN
    found: list[Run] = [(row, rest) for row in (*facts.project.heavy, *KIT_ROWS)
                        if (rest := reads(row, command, facts, name)) is not None]
    if not found:
        return None
    row, rest = max(found, key=lambda run: len(run[0].argv) or 1)
    return None if light(row, rest) else row
