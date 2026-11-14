"""The wrapper table: each command that runs the command after it, one row a wrapper, every field read from that
wrapper's --help or man page on this machine (coreutils 9.10, findutils 4.10, util-linux 2.42, sudo's man page,
bubblewrap 0.11.2, strace 6.18, gdb 17.2, procps-ng watch 4.0.4, uv 0.11.18, rtk 0.43.0, systemd-run 262, bash 5.3's
help for its builtins).

A row says how the wrapper's own words are read, so the model's peeler (dispatcher/model.py) names no wrapper: options
taking values, the first given after `=` or attached to a short letter or as the next word, the others as the next
words; options whose value is optional, given only after `=` or attached; options after which it runs no command the kit
follows; operands before the command; a subcommand; NAME=VALUE words; the shell text it runs, with the shell that runs
it; and the command it runs where its words name none, the words it reads from its input added to it. An option the row
does not name is read as a flag; a word whose value is not known is an option where its known prefix starts with `-`,
read as far as that prefix spells it, its value not known; the first word that is not an option ends the options, as
`--` does.
"""

from collections.abc import Mapping
from typing import Literal, NamedTuple, NewType

from dispatcher.tree import Count, ShellText

Dialect = Literal["bash", "sh"]
Runner = NewType("Runner", str)
"""The shell a wrapper's text runs in: a program's name or path, `$NAME` for the one the environment variable NAME
names, or empty where the call cannot make it known (a user's login shell)."""

UNKNOWN_SHELL = Runner("")
ONE = Count(1)


def named(spelled: ShellText) -> frozenset[ShellText]:
    """The options a space-separated list names."""
    return frozenset(ShellText(option) for option in spelled.split())


def taking(spelled: ShellText, count: Count = ONE) -> dict[ShellText, Count]:
    """The options a space-separated list names, each taking ``count`` values."""
    return dict.fromkeys(named(spelled), count)


def running[R: (Runner, Runner | None)](spelled: ShellText, runner: R) -> dict[ShellText, R]:
    """The options a space-separated list names, each running its text in ``runner``."""
    return dict.fromkeys(named(spelled), runner)


class Wrapper(NamedTuple):
    """How a wrapper runs the command after it.

    valued: each option taking values, with their count; optional: options whose value is optional, given only after `=`
    or attached to a short letter; stops: options after which it runs no command the kit follows; ends: options after
    which its options end and the command follows, read among its options or right after its operands; needs: options or
    a subcommand of which it runs a command only past one, none needed where empty; subcommand: a word that may follow
    its options, its options read again after it; operands: the operands before the command; numbered: whether a decimal
    number first among its operands is one more; assigns: whether NAME=VALUE words may come before the command; clears:
    options that empty the environment of the command, a lone `-` after the options among them; unsets: options that
    remove the name they take from it; long_only: whether a single-dash word of more than one letter is a long option,
    spelled so or with `--`; texts: options whose value is shell text it runs, each with the shell that runs it, read
    among its options or right after its operands; joins: the shell its command words run in, joined by spaces, None
    where it runs them as a command; switches: options that change joins; escapes: whether each character of a joined
    word but a letter, digit, `_`, `-` and `$` is escaped with a backslash; builtin: the dialects whose shell runs it as
    a builtin, empty for a program on disk, which a Python start may run too; appends: the command it runs where its
    words name none, where it adds the words it reads from its input to its command, None where it adds none."""

    valued: Mapping[ShellText, Count] = {}
    optional: frozenset[ShellText] = frozenset()
    stops: frozenset[ShellText] = frozenset()
    ends: frozenset[ShellText] = frozenset()
    needs: frozenset[ShellText] = frozenset()
    subcommand: ShellText | None = None
    operands: Count = Count(0)
    numbered: bool = False
    assigns: bool = False
    clears: frozenset[ShellText] = frozenset()
    unsets: frozenset[ShellText] = frozenset()
    long_only: bool = False
    texts: Mapping[ShellText, Runner] = {}
    joins: Runner | None = None
    switches: Mapping[ShellText, Runner | None] = {}
    escapes: bool = False
    builtin: frozenset[Dialect] = frozenset()
    appends: ShellText | None = None


GNU_STOPS = named(ShellText("--help --version"))
UTIL_STOPS = named(ShellText("-h --help -V --version"))
NAMESPACES = ShellText("-m --mount -u --uts -i --ipc -n --net -p --pid -U --user -C --cgroup -T --time")
UV_VALUED = ShellText(
    "--extra --no-extra --group --no-group --only-group --no-editable-package --env-file -w --with --with-editable "
    "--with-requirements --package --python-platform --index --default-index -i --index-url --extra-index-url -f "
    "--find-links --index-strategy --keyring-provider -P --upgrade-package --upgrade-group --resolution --prerelease "
    "--fork-strategy --exclude-newer --exclude-newer-package --no-sources-package --reinstall-package --link-mode -C "
    "--config-setting --config-settings-package --no-build-isolation-package --no-build-package "
    "--no-binary-package --cache-dir --refresh-package -p --python --color --allow-insecure-host --directory "
    "--project --config-file")
SUDO_VALUED = ShellText("-C -D -g -p -R -r -t -U -T -u --close-from --chdir --group --host --prompt --chroot --role "
                        "--type --other-user --command-timeout --user")
SUDO_STOPS = ShellText("-e --edit -K --remove-timestamp -l --list -V --version -v --validate --help")
UNSHARE_VALUED = ShellText("-l --load-interp --propagation -R --root -w --wd -S --setuid -G --setgid --map-user "
                           "--map-group --map-users --map-groups --owner --setgroups --monotonic --boottime")
SETPRIV_VALUED = ShellText("--ambient-caps --inh-caps --bounding-set --ruid --euid --rgid --egid --reuid --regid "
                           "--groups --securebits --pdeathsig --ptracer --selinux-label --apparmor-profile "
                           "--landlock-access --landlock-rule --seccomp-filter")
PRLIMIT_RESOURCES = ShellText("-c --core -d --data -e --nice -f --fsize -i --sigpending -l --memlock -m --rss -n "
                              "--nofile -q --msgqueue -r --rtprio -s --stack -t --cpu -u --nproc -v --as -x --locks "
                              "-y --rttime")
BWRAP_ONE = ShellText(
    "--args --argv0 --userns --userns2 --pidns --uid --gid --hostname --chdir --unsetenv --lock-file --sync-fd "
    "--remount-ro --overlay-src --tmp-overlay --ro-overlay --exec-label --file-label --proc --dev --tmpfs --mqueue "
    "--dir --seccomp --add-seccomp-fd --block-fd --userns-block-fd --info-fd --json-status-fd --cap-add --cap-drop "
    "--perms --size")
BWRAP_TWO = ShellText("--setenv --bind --bind-try --dev-bind --dev-bind-try --ro-bind --ro-bind-try --bind-fd "
                      "--ro-bind-fd --file --bind-data --ro-bind-data --symlink --chmod")
STRACE_VALUED = ShellText(
    "-e -E -p -u -b -I -P -a -o -s -X -O -S -U --env --attach --user --detach-on --interruptible --trace --signal "
    "--status --trace-fds --trace-path --columns --abbrev --verbose --raw --read --write --kvm --namespace --output "
    "--string-limit --const-print-style --summary-syscall-overhead --summary-sort-by --summary-columns --inject "
    "--fault --argv0 --stack-trace-frame-limit --syscall-limit --decode-pids")
STRACE_OPTIONAL = ShellText("-D -k -r --daemonize --stack-trace --relative-timestamps --absolute-timestamps "
                            "--syscall-times --strings-in-hex --decode-fds --quiet --tips")
GDB_VALUED = ShellText("--core --exec --pid --directory --se --symbols --command --init-command --eval-command "
                       "--init-eval-command --interpreter --tty --cd --data-directory -x -ix -ex -iex -D -b -l -s -e "
                       "-c -d")
XARGS_VALUED = ShellText("-a --arg-file -d --delimiter -E -I -L --max-lines -n --max-args -P --max-procs "
                         "--process-slot-var -s --max-chars")
SCRIPT_VALUED = ShellText("-I --log-in -O --log-out -B --log-io -T --log-timing -m --logging-format -c --command -E "
                          "--echo -o --output-limit")
SYSTEMD_RUN_VALUED = ShellText(
    "-u --unit -p --property --description --slice --expand-environment --service-type --uid --gid --nice "
    "--working-directory --root-directory -E --setenv --output --on-active --on-boot --on-startup --on-unit-active "
    "--on-unit-inactive --on-calendar --path-property --socket-property --timer-property --job-mode --background -H "
    "--host -M --machine -C --capsule --json")

WRAPPERS: dict[ShellText, Wrapper] = {
    ShellText("env"): Wrapper(taking(ShellText("-a -u -C --argv0 --unset --chdir")),
                              named(ShellText("--block-signal --default-signal --ignore-signal")),
                              named(ShellText("-S --split-string")) | GNU_STOPS, assigns=True,
                              clears=named(ShellText("- -i --ignore-environment")),
                              unsets=named(ShellText("-u --unset"))),
    ShellText("taskset"): Wrapper(stops=named(ShellText("-p --pid")) | UTIL_STOPS, operands=ONE),
    ShellText("timeout"): Wrapper(taking(ShellText("-k -s --kill-after --signal")), stops=GNU_STOPS, operands=ONE),
    ShellText("nice"): Wrapper(taking(ShellText("-n --adjustment")), stops=GNU_STOPS),
    ShellText("nohup"): Wrapper(stops=GNU_STOPS),
    ShellText("setsid"): Wrapper(stops=UTIL_STOPS),
    ShellText("stdbuf"): Wrapper(taking(ShellText("-i -o -e --input --output --error")), stops=GNU_STOPS),
    ShellText("command"): Wrapper(stops=named(ShellText("-v -V")), builtin=frozenset({"bash", "sh"})),
    ShellText("builtin"): Wrapper(builtin=frozenset({"bash"})),
    ShellText("exec"): Wrapper(taking(ShellText("-a")), clears=named(ShellText("-c")),
                               builtin=frozenset({"bash", "sh"})),
    ShellText("rtk"): Wrapper(stops=UTIL_STOPS, subcommand=ShellText("proxy")),
    ShellText("sudo"): Wrapper(taking(SUDO_VALUED), named(ShellText("-h --preserve-env")), named(SUDO_STOPS),
                               assigns=True, escapes=True,
                               switches={**running(ShellText("-s --shell"), Runner("$SHELL")),
                                         **running(ShellText("-i --login"), UNKNOWN_SHELL)}),
    ShellText("runuser"): Wrapper(taking(ShellText("-u --user -w --whitelist-environment -g --group -G --supp-group "
                                                   "-s --shell -c --command --session-command")),
                                  stops=UTIL_STOPS, needs=named(ShellText("-u --user")),
                                  texts=running(ShellText("-c --command --session-command"), UNKNOWN_SHELL)),
    ShellText("uv"): Wrapper(taking(UV_VALUED), stops=named(ShellText("-m --module -s --script --gui-script"))
                             | UTIL_STOPS, needs=named(ShellText("run")), subcommand=ShellText("run")),
    ShellText("unshare"): Wrapper(taking(UNSHARE_VALUED),
                                  named(ShellText(f"{NAMESPACES} --mount-proc --mount-binfmt --kill-child")),
                                  UTIL_STOPS),
    ShellText("setpriv"): Wrapper(taking(SETPRIV_VALUED), stops=named(ShellText("-d --dump --list-caps")) | UTIL_STOPS,
                                  clears=named(ShellText("--reset-env"))),
    ShellText("prlimit"): Wrapper(taking(ShellText("-p --pid -o --output")), named(PRLIMIT_RESOURCES),
                                  named(ShellText("-p --pid")) | UTIL_STOPS),
    ShellText("bwrap"): Wrapper({**taking(BWRAP_ONE), **taking(BWRAP_TWO, Count(2)),
                                 **taking(ShellText("--overlay"), Count(3))},
                                stops=GNU_STOPS, clears=named(ShellText("--clearenv")),
                                unsets=named(ShellText("--unsetenv"))),
    ShellText("nsenter"): Wrapper(taking(ShellText("-t --target -N --net-socket -W --wdns")),
                                  named(ShellText(f"{NAMESPACES} -S --setuid -G --setgid -r --root -w --wd")),
                                  UTIL_STOPS),
    ShellText("chroot"): Wrapper(taking(ShellText("--groups --userspec")), stops=GNU_STOPS, operands=ONE),
    ShellText("strace"): Wrapper(taking(STRACE_VALUED), named(STRACE_OPTIONAL), UTIL_STOPS),
    ShellText("gdb"): Wrapper(taking(GDB_VALUED), stops=named(ShellText("-h --help --version --configuration")),
                              ends=named(ShellText("--args")), needs=named(ShellText("--args")), long_only=True),
    ShellText("ionice"): Wrapper(taking(ShellText("-c --class -n --classdata")),
                                 stops=named(ShellText("-p --pid -P --pgid -u --uid")) | UTIL_STOPS),
    ShellText("chrt"): Wrapper(taking(ShellText("-T --sched-runtime -P --sched-period -D --sched-deadline")),
                               stops=named(ShellText("-p --pid -m --max")) | UTIL_STOPS, numbered=True),
    ShellText("flock"): Wrapper(taking(ShellText("-w --timeout -E --conflict-exit-code --start --length --wait")),
                                stops=UTIL_STOPS, operands=ONE,
                                texts=running(ShellText("-c --command"), Runner("$SHELL"))),
    ShellText("watch"): Wrapper(taking(ShellText("-q --equexit -n --interval")),
                                named(ShellText("-d --differences")), named(ShellText("-h --help -v --version")),
                                joins=Runner("sh"), switches=running(ShellText("-x --exec"), None)),
    ShellText("systemd-run"): Wrapper(taking(SYSTEMD_RUN_VALUED),
                                      stops=named(ShellText("-S --shell -h --help --version"))),
    ShellText("xargs"): Wrapper(taking(XARGS_VALUED), named(ShellText("-e --eof -i --replace -l")), GNU_STOPS,
                                appends=ShellText("echo")),
    ShellText("script"): Wrapper(taking(SCRIPT_VALUED), named(ShellText("-t --timing")), UTIL_STOPS,
                                 ends=named(ShellText("--")), needs=named(ShellText("--")), operands=ONE,
                                 texts=running(ShellText("-c --command"), Runner("$SHELL")), joins=Runner("$SHELL")),
}
