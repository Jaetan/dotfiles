"""The paths a command writes (dispatcher/writes.py): its streams and own redirections on every descriptor, and its
words through every row of the writer table (dispatcher/write_table.py), each call read with the kit's own shell
reader, bin/shell-reader; the old kit's write cases whose targets these writers decide are carried under their ids."""

import unittest
from collections.abc import Sequence
from pathlib import Path

from dispatcher.model import ReadSettings, build
from dispatcher.tree import ReaderPath, Seconds, ShellText
from dispatcher.words import Directory, VarName
from dispatcher.write_table import WRITERS, Kind
from dispatcher.writes import FilePath, Write, writes

KIT = Path(__file__).resolve().parents[1]
SETTINGS = ReadSettings(ReaderPath(str(KIT / "bin" / "shell-reader")), Seconds(5.0),
                        {VarName("HOME"): ShellText("/home/u")})
T = ShellText
R = Directory("/r")
type Seen = tuple[tuple[FilePath | None, Kind], ...]
type Case = tuple[ShellText, Seen]
type Carried = tuple[ShellText, ShellText, Directory, Seen]


def each(kind: Kind, *paths: ShellText) -> Seen:
    """The writes of ``paths``, each as ``kind``, an empty one standing for a path the call does not make known."""
    return tuple((FilePath(path) if path else None, kind) for path in paths)


def call_writes(text: ShellText, cwd: Directory | None) -> tuple[Write, ...]:
    """Every write of every command of ``text`` run from ``cwd``, command by command."""
    call = build(text, cwd, SETTINGS)
    assert not call.gaps, call.gaps
    return tuple(write for command in call.commands for write in writes(command))


def seen(text: ShellText, cwd: Directory | None = R) -> Seen:
    """The path and kind of every write of ``text`` run from ``cwd``."""
    return tuple((write.path, write.kind) for write in call_writes(text, cwd))


class Cases(unittest.TestCase):
    """A base reading each case's text from /r."""

    def check(self, cases: Sequence[Case]) -> None:
        """Each case's text writes what it says."""
        for text, expected in cases:
            with self.subTest(text):
                self.assertEqual(seen(text), expected)


STREAMS: tuple[Case, ...] = (
    (T("echo x > o"), each("created", T("/r/o"))),
    (T("echo x >| o"), each("created", T("/r/o"))),
    (T("echo x >> o"), each("appended", T("/r/o"))),
    (T("echo x &> o"), each("created", T("/r/o"))),
    (T("echo x &>> o"), each("appended", T("/r/o"))),
    (T("cat <> o"), each("edited", T("/r/o"))),
    (T("cmd 2> e"), each("created", T("/r/e"))),
    (T("cat < o"), ()),
    (T("echo x > /dev/null"), ()),
    (T("echo x > $u"), each("created", T(""))),
    (T("{ a; b; } > g"), each("created", T("/r/g"), T("/r/g"))),
    (T("exec > l; echo x"), each("created", T("/r/l"), T("/r/l"))),
    (T("bash -c 'echo x' > h"), each("created", T("/r/h"), T("/r/h"))),
    (T("echo x > /dev/stderr 2> e"), each("created", T("/r/e"))),
    (T("cat > f <<'E'\nx\nE"), each("created", T("/r/f"))),
)
REDIRECTIONS: tuple[Case, ...] = (
    (T("cmd > a > b"), each("created", T("/r/b"), T("/r/a"))),
    (T("cmd 3> t"), each("created", T("/r/t"))),
    (T("cmd 4>> c"), each("appended", T("/r/c"))),
    (T("cmd 5<> d"), each("edited", T("/r/d"))),
    (T("exec {fd}> f"), each("created", T("/r/f"))),
    (T("cmd >& f"), each("created", T("/r/f"))),
    (T("cmd >& f >& g"), each("created", T("/r/g"), T("/r/f"))),
    (T("cmd >&2"), ()),
    (T("cmd >&-"), ()),
    (T("cmd >&3-"), ()),
    (T("cmd >&$u"), ()),
    (T("cmd 2>&f"), ()),
    (T("cmd 3> /dev/fd/1"), ()),
    (T("cmd 3> /dev/null"), ()),
    (T("cmd 3> >(cat)"), ()),
    (T("echo x > >(cat)"), ()),
)
PERL_OPTIONAL: tuple[Case, ...] = tuple((T(f"perl -i -e p -{letter}I x f"), each("edited", T("/r/x"), T("/r/f")))
                                        for letter in "0CdDFlmMVx")
SED_PROGRAMS: tuple[Case, ...] = tuple((T(f"sed -i {option} s {option} s f"), each("edited", T("/r/f")))
                                       for option in ("-e", "--expression", "-f", "--file"))
PERL_PROGRAMS: tuple[Case, ...] = tuple((T(f"perl -i {option} s {option} s f"), each("edited", T("/r/f")))
                                        for option in ("-e", "-E"))
GAWK_PROGRAMS: tuple[Case, ...] = tuple((T(f"gawk -i inplace {option} s {option} s f"), each("edited", T("/r/f")))
                                        for option in ("-f", "--file", "-e", "--source", "-E", "--exec"))
GAWK_VALUED: tuple[Case, ...] = tuple((T(f"gawk -i inplace {option} v p f"), each("edited", T("/r/f")))
                                      for option in ("-F", "--field-separator", "-v", "--assign", "-l", "--load"))
GAWK_OPTIONAL: tuple[Case, ...] = tuple((T(f"gawk -i inplace -{letter}v x p f"), each("edited", T("/r/p"), T("/r/f")))
                                        for letter in "dDLop")


def unread(head: ShellText, options: ShellText, kind: Kind | None, *paths: ShellText) -> tuple[Case, ...]:
    """A case per option of the space-separated ``options``, ``head`` given it and the value v, then the operand a:
    the value is never a path, and the writes are ``paths`` as ``kind``, none where it is None."""
    expected = () if kind is None else each(kind, *paths)
    return tuple((T(f"{head} {option} v a"), expected) for option in options.split())


ROWS: dict[ShellText, tuple[Case, ...]] = {
    T("sed"): (
        (T("sed -i s/a/b/ f"), each("edited", T("/r/f"))),
        (T("sed s/a/b/ f"), ()),
        (T("sed -i.bak s/a/b/ f"), each("edited", T("/r/f"))),
        (T("sed -il 5 p f"), each("edited", T("/r/p"), T("/r/f"))),
        (T("sed --in-place=.bak s/a/b/ f g"), each("edited", T("/r/f"), T("/r/g"))),
        (T("sed -i -e p f g"), each("edited", T("/r/f"), T("/r/g"))),
        (T("sed -i --expression p f"), each("edited", T("/r/f"))),
        (T("sed -i --expression=p f"), each("edited", T("/r/f"))),
        (T("sed -i -f s f"), each("edited", T("/r/f"))),
        (T("sed -i --file s f"), each("edited", T("/r/f"))),
        (T("sed -i -l 5 p f"), each("edited", T("/r/f"))),
        (T("sed -i --line-length 5 p f"), each("edited", T("/r/f"))),
        (T("sed p f -i"), each("edited", T("/r/f"))),
        (T("sed -i -- p -f"), each("edited", T("/r/-f"))),
        *SED_PROGRAMS,
    ),
    T("perl"): (
        (T("perl -pi -e s/a/b/ f"), each("edited", T("/r/f"))),
        (T("perl -pe s/a/b/ f"), ()),
        (T("perl -i prog.pl f g"), each("edited", T("/r/f"), T("/r/g"))),
        (T("perl -i -E p f"), each("edited", T("/r/f"))),
        (T("perl -i -ep f"), each("edited", T("/r/f"))),
        (T("perl -I lib -i -e p f"), each("edited", T("/r/f"))),
        (T("perl -iI x f"), each("edited", T("/r/f"))),
        (T("perl -e p f -i"), ()),
        *PERL_OPTIONAL, *PERL_PROGRAMS,
    ),
    T("gawk"): (
        (T("gawk -i inplace '{print}' f"), each("edited", T("/r/f"))),
        (T("gawk -iinplace '{print}' f"), each("edited", T("/r/f"))),
        (T("gawk --include=inplace '{print}' f"), each("edited", T("/r/f"))),
        (T("gawk --include inplace '{print}' f"), each("edited", T("/r/f"))),
        (T("gawk -i other '{print}' f"), ()),
        (T("gawk -i inplace '{print}' a=1 f"), each("edited", T("/r/f"))),
        (T("gawk -i inplace '{print}' a=$v f"), each("edited", T("/r/f"))),
        (T("gawk '{print}' f -i inplace"), ()),
        *GAWK_PROGRAMS, *GAWK_VALUED, *GAWK_OPTIONAL,
    ),
    T("awk"): ((T("awk -i inplace '{print}' f"), each("edited", T("/r/f"))),),
    T("tee"): (
        (T("tee o"), each("created", T("/r/o"))),
        (T("tee -a o"), each("appended", T("/r/o"))),
        (T("tee --append o"), each("appended", T("/r/o"))),
        (T("tee -ip o p"), each("created", T("/r/o"), T("/r/p"))),
    ),
    T("cp"): (
        (T("cp a b"), each("destination", T("/r/b"))),
        (T("cp a b d"), each("destination", T("/r/d"))),
        (T("cp a"), ()),
        (T("cp -t d a b"), each("destination", T("/r/d"))),
        (T("cp -td a"), each("destination", T("/r/d"))),
        (T("cp --target-directory=d a"), each("destination", T("/r/d"))),
        (T("cp --target-directory d a b"), each("destination", T("/r/d"))),
        (T("cp a -t d"), each("destination", T("/r/d"))),
        *unread(T("cp"), T("-S --suffix --no-preserve --sparse"), None),
    ),
    T("mv"): (
        (T("mv a b"), each("destination", T("/r/b")) + each("moved", T("/r/a"))),
        (T("mv a b d"), each("destination", T("/r/d")) + each("moved", T("/r/a"), T("/r/b"))),
        (T("mv -t d a"), each("destination", T("/r/d")) + each("moved", T("/r/a"))),
        (T("mv --target-directory d a"), each("destination", T("/r/d")) + each("moved", T("/r/a"))),
        *unread(T("mv"), T("-S --suffix"), None),
    ),
    T("install"): (
        (T("install -m 755 x y"), each("destination", T("/r/y"))),
        (T("install -t d a"), each("destination", T("/r/d"))),
        (T("install -d a b"), each("created", T("/r/a"), T("/r/b"))),
        (T("install --directory a"), each("created", T("/r/a"))),
        *unread(T("install"), T("-g --group -m --mode -o --owner --strip-program -S --suffix"), None),
    ),
    T("rm"): (
        (T("rm -f a b"), each("removed", T("/r/a"), T("/r/b"))),
        (T("rm -rf -- -x"), each("removed", T("/r/-x"))),
        (T("rm"), ()),
    ),
    T("unlink"): ((T("unlink a"), each("removed", T("/r/a"))),),
    T("shred"): (
        (T("shred a"), each("edited", T("/r/a"))),
        (T("shred -u a"), each("removed", T("/r/a"))),
        (T("shred --remove=wipe a"), each("removed", T("/r/a"))),
        *unread(T("shred"), T("-n --iterations --random-source -s --size"), "edited", T("/r/a")),
    ),
    T("touch"): ((T("touch a"), each("created", T("/r/a"))),
                 *unread(T("touch"), T("-d --date -r --reference -t --time"), "created", T("/r/a"))),
    T("mkdir"): ((T("mkdir -p a/b"), each("created", T("/r/a/b"))),
                 *unread(T("mkdir"), T("-m --mode"), "created", T("/r/a"))),
    T("truncate"): ((T("truncate -s 0 a"), each("created", T("/r/a"))),
                    *unread(T("truncate"), T("-r --reference -s --size"), "created", T("/r/a"))),
    T("dd"): (
        (T("dd if=/dev/zero of=y bs=1"), each("created", T("/r/y"))),
        (T("dd if=a"), ()),
        (T("dd of=$f"), each("created", T(""))),
    ),
}
READING: tuple[Case, ...] = (
    (T("rm $u"), each("removed", T(""))),
    (T("rm -$u a"), each("removed", T("/r/a"))),
    (T("rm --$u a"), each("removed", T("/r/a"))),
    (T("rm - a"), each("removed", T("/r/-"), T("/r/a"))),
    (T("cp -t$d a"), each("destination", T(""))),
    (T("cp --target-directory=$d a"), each("destination", T(""))),
    (T("sudo rm a"), each("removed", T("/r/a"))),
    (T("/usr/bin/rm a"), each("removed", T("/r/a"))),
    (T("$x a"), ()),
    (T("ls a"), ()),
)


class Streams(Cases):
    """A command's streams, and its own redirections on every descriptor."""

    def test_streams(self) -> None:
        """A stream writes the file it ends in where it opens it for writing, appending or update, inherited ends
        included, never a descriptor's copy, /dev/null or a file it reads."""
        self.check(STREAMS)

    def test_redirections(self) -> None:
        """Every own redirection opening a file writes it, on any descriptor, a later one taking its descriptor
        over or not; a copy, a close, a move, a descriptor's path, /dev/null and a substitution write none."""
        self.check(REDIRECTIONS)

    def test_placement(self) -> None:
        """A path is placed against the command's directory, normalised; a relative one with no directory, or one
        the call does not make known, has none, its word kept."""
        self.assertEqual(seen(T("echo x > /a/../b"), None), each("created", T("/b")))
        self.assertEqual(seen(T("echo x > rel"), None), each("created", T("")))
        self.assertEqual(seen(T("echo x > ./a/../b")), each("created", T("/r/b")))
        (glob,) = call_writes(T("rm /t/tools/**/*.log"), Directory("/q"))
        self.assertEqual(glob.word.folder(Directory("/q")), Directory("/t/tools"))
        (keyed,) = call_writes(T("dd of=$f"), R)
        self.assertEqual((keyed.word.text, keyed.word.prefix), (T("of=$f"), T("")))
        (target,) = call_writes(T("cp --target-directory=/t/d a"), R)
        self.assertEqual((target.word.text, target.word.value), (T("--target-directory=/t/d"), T("/t/d")))


class Rows(Cases):
    """Every row of the writer table, each field shown by a case that reads otherwise without it."""

    def test_sed(self) -> None:
        """sed -i and --in-place edit each file, the program an operand unless -e, -f or their long forms give it."""
        self.check(ROWS[T("sed")])

    def test_perl(self) -> None:
        """perl -i edits each file, its options ending at its first operand."""
        self.check(ROWS[T("perl")])

    def test_gawk(self) -> None:
        """gawk -i inplace edits each file, an assignment no file, its options ending at the program."""
        self.check(ROWS[T("gawk")])

    def test_awk(self) -> None:
        """awk reads gawk's row, the program it runs here."""
        self.assertIs(WRITERS[T("awk")], WRITERS[T("gawk")])
        self.check(ROWS[T("awk")])

    def test_tee(self) -> None:
        """tee creates each file, appends with -a."""
        self.check(ROWS[T("tee")])

    def test_cp(self) -> None:
        """cp writes its destination, the last operand or -t's."""
        self.check(ROWS[T("cp")])

    def test_mv(self) -> None:
        """mv writes its destination and moves each source."""
        self.check(ROWS[T("mv")])

    def test_install(self) -> None:
        """install writes its destination, or creates each operand under -d."""
        self.check(ROWS[T("install")])

    def test_rm(self) -> None:
        """rm removes each operand."""
        self.check(ROWS[T("rm")])

    def test_unlink(self) -> None:
        """unlink removes its operand."""
        self.check(ROWS[T("unlink")])

    def test_shred(self) -> None:
        """shred overwrites each file in place, removes it under -u or --remove."""
        self.check(ROWS[T("shred")])

    def test_touch(self) -> None:
        """touch creates each file."""
        self.check(ROWS[T("touch")])

    def test_mkdir(self) -> None:
        """mkdir creates each folder."""
        self.check(ROWS[T("mkdir")])

    def test_truncate(self) -> None:
        """truncate creates or truncates each file."""
        self.check(ROWS[T("truncate")])

    def test_dd(self) -> None:
        """dd writes the file of= names."""
        self.check(ROWS[T("dd")])

    def test_reading(self) -> None:
        """A word the call does not make known is read by its prefix, a wrapped or pathed name finds its row, and a
        command no row names writes nothing by its words."""
        self.check(READING)


CARRIED: tuple[Carried, ...] = (
    (T("B04"), T("cp tools/x.py /s/c.py"), Directory("/t"), each("destination", T("/s/c.py"))),
    (T("B05"), T("cp /tmp/a tools/x.py"), Directory("/t"), each("destination", T("/t/tools/x.py"))),
    (T("B06"), T("cp /s/a /t/tools/x.py"), Directory("/s"), each("destination", T("/t/tools/x.py"))),
    (T("B07"), T("cd /t && echo x > note.txt"), Directory("/s"), each("created", T("/t/note.txt"))),
    (T("B10"), T("sed -ni 's/a/b/p' tools/x.py"), Directory("/t"), each("edited", T("/t/tools/x.py"))),
    (T("B11"), T("sed -e 's/a/b/' -i tools/x.py"), Directory("/t"), each("edited", T("/t/tools/x.py"))),
    (T("B12"), T("sed --in-place 's/a/b/' tools/x.py"), Directory("/t"), each("edited", T("/t/tools/x.py"))),
    (T("B12b"), T("sed -n 's/a/b/p' tools/x.py"), Directory("/t"), ()),
    (T("B18"), T('R=/t; echo x > "$R/note.txt"'), Directory("/s"), each("created", T("/t/note.txt"))),
    (T("B19"), T("echo x > /tmp/y"), Directory("/t"), each("created", T("/tmp/y"))),
    (T("B22b"), T("dd if=/dev/zero of=tools/y bs=1 count=1"), Directory("/t"), each("created", T("/t/tools/y"))),
    (T("B22c"), T("install -m 755 /tmp/x tools/y.sh"), Directory("/t"), each("destination", T("/t/tools/y.sh"))),
    (T("B22d"), T("truncate -s 0 CHANGELOG.md"), Directory("/t"), each("created", T("/t/CHANGELOG.md"))),
    (T("B22e"), T("touch CHANGELOG.md"), Directory("/t"), each("created", T("/t/CHANGELOG.md"))),
    (T("B25"), T("cp tools/x.py tools/x.bak"), Directory("/q"), each("destination", T("/q/tools/x.bak"))),
    (T("B26"), T("cat > /t/.commands-to-run.sh <<'EOF'\ngit status\nEOF"), Directory("/s"),
     each("created", T("/t/.commands-to-run.sh"))),
    (T("B28"), T("echo a#b > tools/f.txt"), Directory("/t"), each("created", T("/t/tools/f.txt"))),
    (T("B29"), T("touch /tmp/zz; echo y > /t/note.txt"), Directory("/q"),
     each("created", T("/tmp/zz"), T("/t/note.txt"))),
    (T("B30"), T("echo a > /q/x.txt; echo b > /t/y.txt"), Directory("/s"),
     each("created", T("/q/x.txt"), T("/t/y.txt"))),
    (T("B31"), T("echo x > /tmp/../t/note.txt"), Directory("/s"), each("created", T("/t/note.txt"))),
    (T("B32"), T("echo x > /t/tools/scratchpad-notes.txt"), Directory("/s"),
     each("created", T("/t/tools/scratchpad-notes.txt"))),
    (T("B36"), T("mkdir tools/new"), Directory("/t"), each("created", T("/t/tools/new"))),
    (T("B43"), T("awk -i inplace '{print}' tools/x.py"), Directory("/t"), each("edited", T("/t/tools/x.py"))),
    (T("B45"), T("cat > /t/n.txt <<'END-DATA'\nwe don't\nEND-DATA"), Directory("/s"), each("created", T("/t/n.txt"))),
    (T("B46"), T("echo '*.tmp' >> .git/info/exclude"), Directory("/t"), each("appended", T("/t/.git/info/exclude"))),
    (T("B49"), T("cp /s/a /b/outside/link.py"), Directory("/s"), each("destination", T("/b/outside/link.py"))),
    (T("B51"), T("tee tools/out.txt < /tmp/in"), Directory("/t"), each("created", T("/t/tools/out.txt"))),
    (T("B53"), T("perl -pi -e 's/a/b/' tools/x.py"), Directory("/t"), each("edited", T("/t/tools/x.py"))),
    (T("B55"), T("rm -f tools/x.py"), Directory("/t"), each("removed", T("/t/tools/x.py"))),
    (T("B56"), T("mv /s/a tools/"), Directory("/t"), each("destination", T("/t/tools")) + each("moved", T("/s/a"))),
    (T("ZS1"), T("rm /t/tools/**/*.log"), Directory("/q"), each("removed", T(""))),
    (T("ZQ1"), T("echo x > '/s/l(x)/f.txt'"), Directory("/s"), each("created", T("/s/l(x)/f.txt"))),
    (T("ZQ2"), T("sed -i s/a/b/ '/s/l(x)/f.txt'"), Directory("/s"), each("edited", T("/s/l(x)/f.txt"))),
    (T("ZQ9"), T("echo x > /s/l#x/f.txt"), Directory("/s"), each("created", T("/s/l#x/f.txt"))),
)


class Old(unittest.TestCase):
    """The old kit's write cases whose targets these writers and redirections decide, by their old ids."""

    def test_carried(self) -> None:
        """Each old case writes the paths its old verdict was judged by."""
        for name, text, cwd, expected in CARRIED:
            with self.subTest(name):
                self.assertEqual(seen(text, cwd), expected)


if __name__ == "__main__":
    unittest.main()
