package main

import (
	"bytes"
	"encoding/json"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"

	"mvdan.cc/sh/v3/syntax"
)

// A check is a path into the tree's JSON, its segments object keys, list indexes, or # for a list's length.
type check struct {
	path string
	want any
}

const missing = "<missing>"

func get(v any, path string) any {
	for _, seg := range strings.Split(path, ".") {
		switch x := v.(type) {
		case map[string]any:
			val, ok := x[seg]
			if !ok {
				return missing
			}
			v = val
		case []any:
			if seg == "#" {
				return float64(len(x))
			}
			i, err := strconv.Atoi(seg)
			if err != nil || i < 0 || i >= len(x) {
				return missing
			}
			v = x[i]
		default:
			return missing
		}
	}
	return v
}

func decoded(t *testing.T, v any) any {
	t.Helper()
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	var out any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

// assert reports each failed check as FAIL-ASSERT row=... path=..., the line the plant run reads.
func assert(t *testing.T, row string, tree any, checks []check) {
	t.Helper()
	tree = decoded(t, tree)
	for _, c := range checks {
		if os.Getenv("SHELL_READER_LIST_ASSERTS") != "" {
			t.Logf("ASSERT row=%s path=%s", row, c.path)
		}
		got, want := get(tree, c.path), decoded(t, c.want)
		if !reflect.DeepEqual(got, want) {
			t.Errorf("FAIL-ASSERT row=%s path=%s got=%#v want=%#v", row, c.path, got, want)
		}
	}
}

var rows = []struct {
	name, src string
	checks    []check
}{
	{"list", "a; b & c", []check{
		{"stmts.#", 3}, {"stmts.0.background", false}, {"stmts.1.background", true},
		{"stmts.2.cmd.args.0.text", "c"}, {"stmts.0.text", "a;"},
	}},
	{"andor", "a && b || c", []check{
		{"stmts.0.cmd.type", "andor"}, {"stmts.0.cmd.items.#", 3}, {"stmts.0.cmd.items.0.op", nil},
		{"stmts.0.cmd.items.1.op", "&&"}, {"stmts.0.cmd.items.2.op", "||"},
		{"stmts.0.cmd.items.2.stmt.text", "c"}, {"stmts.0.cmd.text", "a && b || c"},
	}},
	{"pipeline", "! a | b |& c", []check{
		{"stmts.0.negated", true}, {"stmts.0.cmd.type", "pipeline"}, {"stmts.0.cmd.items.0.op", nil},
		{"stmts.0.cmd.items.1.op", "|"}, {"stmts.0.cmd.items.2.op", "|&"},
		{"stmts.0.cmd.items.0.stmt.text", "a"}, {"stmts.0.cmd.text", "a | b |& c"}, {"stmts.0.text", "! a | b |& c"},
	}},
	{"andor of pipelines", "a | b && ! c | d", []check{
		{"stmts.0.cmd.type", "andor"}, {"stmts.0.cmd.items.0.stmt.cmd.type", "pipeline"},
		{"stmts.0.cmd.items.0.stmt.cmd.items.#", 2}, {"stmts.0.cmd.items.1.stmt.negated", true},
		{"stmts.0.cmd.items.1.stmt.cmd.type", "pipeline"},
	}},
	{"negation inside a chain", "a && ! b && c", []check{
		{"stmts.0.cmd.items.#", 3}, {"stmts.0.cmd.items.1.stmt.negated", true},
		{"stmts.0.cmd.items.1.stmt.cmd.type", "call"}, {"stmts.0.cmd.items.2.stmt.negated", false},
	}},
	{"call", "A=1 B+=x env -i ls", []check{
		{"stmts.0.cmd.type", "call"}, {"stmts.0.cmd.assigns.0.type", "assign"},
		{"stmts.0.cmd.assigns.0.name", "A"}, {"stmts.0.cmd.assigns.0.value.text", "1"},
		{"stmts.0.cmd.assigns.0.append", false}, {"stmts.0.cmd.assigns.1.append", true},
		{"stmts.0.cmd.assigns.1.text", "B+=x"}, {"stmts.0.cmd.assigns.0.array", nil}, {"stmts.0.cmd.args.#", 3}, {"stmts.0.cmd.args.0.text", "env"},
	}},
	{"array assign", "a=(x [3]=y [ 4 ]=z); b=([\t5]=w)", []check{
		{"stmts.0.cmd.assigns.0.array.elems.1.text", "[3]=y"}, {"stmts.0.cmd.assigns.0.array.elems.1.pos.col", 6},
		{"stmts.0.cmd.assigns.0.array.elems.1.pos.offset", 5}, {"stmts.0.cmd.assigns.0.array.elems.2.text", "[ 4 ]=z"},
		{"stmts.1.cmd.assigns.0.array.elems.0.text", "[\t5]=w"}, {"stmts.0.cmd.assigns.0.array.elems.0.text", "x"},
		{"stmts.0.cmd.assigns.0.array.type", "array"}, {"stmts.0.cmd.assigns.0.array.elems.#", 3},
		{"stmts.0.cmd.assigns.0.array.elems.0.index", nil}, {"stmts.0.cmd.assigns.0.array.elems.1.index.text", "3"},
		{"stmts.0.cmd.assigns.0.array.elems.1.value.text", "y"}, {"stmts.0.cmd.assigns.0.value", nil},
		{"stmts.0.cmd.assigns.0.array.elems.1.type", "array_elem"}, {"stmts.0.cmd.args.#", 0},
	}},
	{"lit", `a\ b`, []check{
		{"stmts.0.cmd.args.0.type", "word"}, {"stmts.0.cmd.args.0.parts.#", 1},
		{"stmts.0.cmd.args.0.parts.0.type", "lit"}, {"stmts.0.cmd.args.0.parts.0.value", `a\ b`},
		{"stmts.0.cmd.assigns.#", 0}, {"stmts.0.redirs.#", 0},
	}},
	{"single", `'x y'`, []check{
		{"stmts.0.cmd.args.0.parts.0.type", "single"}, {"stmts.0.cmd.args.0.parts.0.value", "x y"},
		{"stmts.0.cmd.args.0.parts.0.text", "'x y'"},
	}},
	{"ansi_c", `$'\n'`, []check{
		{"stmts.0.cmd.args.0.parts.0.type", "ansi_c"}, {"stmts.0.cmd.args.0.parts.0.value", `\n`},
	}},
	{"double", `"a $b" $"c"`, []check{
		{"stmts.0.cmd.args.0.parts.0.type", "double"}, {"stmts.0.cmd.args.0.parts.0.dollar", false},
		{"stmts.0.cmd.args.0.parts.0.parts.0.value", "a "}, {"stmts.0.cmd.args.0.parts.0.parts.1.type", "param"},
		{"stmts.0.cmd.args.0.parts.0.parts.1.name", "b"}, {"stmts.0.cmd.args.0.parts.0.parts.1.short", true},
		{"stmts.0.cmd.args.1.parts.0.dollar", true}, {"stmts.0.cmd.args.0.parts.0.parts.0.text", "a "},
	}},
	{"param", "echo ${a:-b} ${#a} ${a[i]} ${a//x/y} ${a:1:2} ${!p*} ${!a}", []check{
		{"stmts.0.cmd.args.1.parts.0.exp.op", ":-"}, {"stmts.0.cmd.args.1.parts.0.exp.word.text", "b"},
		{"stmts.0.cmd.args.1.parts.0.short", false}, {"stmts.0.cmd.args.1.parts.0.length", false},
		{"stmts.0.cmd.args.2.parts.0.length", true}, {"stmts.0.cmd.args.3.parts.0.index.text", "i"},
		{"stmts.0.cmd.args.4.parts.0.repl.all", true}, {"stmts.0.cmd.args.4.parts.0.repl.orig.text", "x"},
		{"stmts.0.cmd.args.4.parts.0.repl.with.text", "y"}, {"stmts.0.cmd.args.5.parts.0.slice.offset.text", "1"},
		{"stmts.0.cmd.args.5.parts.0.slice.length.text", "2"}, {"stmts.0.cmd.args.6.parts.0.names", "*"},
		{"stmts.0.cmd.args.7.parts.0.excl", true}, {"stmts.0.cmd.args.1.parts.0.excl", false},
		{"stmts.0.cmd.args.1.parts.0.names", nil}, {"stmts.0.cmd.args.2.parts.0.exp", nil},
		{"stmts.0.cmd.args.1.parts.0.repl", nil}, {"stmts.0.cmd.args.1.parts.0.slice", nil},
		{"stmts.0.cmd.args.1.parts.0.index", nil},
	}},
	{"cmdsubst", "echo $(ls -l) `pwd` ${ cd d;} ${|r;}", []check{
		{"stmts.0.cmd.args.1.parts.0.type", "cmdsubst"}, {"stmts.0.cmd.args.1.parts.0.form", "dollar"},
		{"stmts.0.cmd.args.1.parts.0.stmts.0.cmd.args.1.text", "-l"},
		{"stmts.0.cmd.args.2.parts.0.form", "backquote"},
		{"stmts.0.cmd.args.2.parts.0.stmts.0.cmd.args.0.text", "pwd"},
		{"stmts.0.cmd.args.3.parts.0.form", "brace"}, {"stmts.0.cmd.args.3.parts.0.stmts.0.cmd.args.0.text", "cd"},
		{"stmts.0.cmd.args.4.parts.0.form", "brace_reply"},
	}},
	{"arith", "echo $((1 + x * 2)) $(( -(y++) )) $[z]", []check{
		{"stmts.0.cmd.args.1.parts.0.type", "arith"}, {"stmts.0.cmd.args.1.parts.0.bracket", false},
		{"stmts.0.cmd.args.1.parts.0.expr.type", "arith_binary"}, {"stmts.0.cmd.args.1.parts.0.expr.op", "+"},
		{"stmts.0.cmd.args.1.parts.0.expr.x.text", "1"}, {"stmts.0.cmd.args.1.parts.0.expr.y.op", "*"},
		{"stmts.0.cmd.args.1.parts.0.expr.y.y.text", "2"},
		{"stmts.0.cmd.args.2.parts.0.expr.type", "arith_unary"}, {"stmts.0.cmd.args.2.parts.0.expr.op", "-"},
		{"stmts.0.cmd.args.2.parts.0.expr.post", false}, {"stmts.0.cmd.args.2.parts.0.expr.x.type", "arith_paren"},
		{"stmts.0.cmd.args.2.parts.0.expr.x.x.op", "++"}, {"stmts.0.cmd.args.2.parts.0.expr.x.x.post", true},
		{"stmts.0.cmd.args.2.parts.0.expr.x.x.x.text", "y"}, {"stmts.0.cmd.args.3.parts.0.bracket", true},
	}},
	{"procsubst", "diff <(ls a) >(cat)", []check{
		{"stmts.0.cmd.args.1.parts.0.type", "procsubst"}, {"stmts.0.cmd.args.1.parts.0.op", "<("},
		{"stmts.0.cmd.args.1.parts.0.stmts.0.cmd.args.1.text", "a"}, {"stmts.0.cmd.args.2.parts.0.op", ">("},
	}},
	{"extglob", "ls +(a|b)", []check{
		{"stmts.0.cmd.args.1.parts.0.type", "extglob"}, {"stmts.0.cmd.args.1.parts.0.op", "+("},
		{"stmts.0.cmd.args.1.parts.0.pattern", "a|b"},
	}},
	{"redirect", "cmd 2>&1 >out <in {fd}>x", []check{
		{"stmts.0.redirs.#", 4}, {"stmts.0.redirs.0.type", "redirect"}, {"stmts.0.redirs.0.op", ">&"},
		{"stmts.0.redirs.0.fd", "2"}, {"stmts.0.redirs.0.word.text", "1"}, {"stmts.0.redirs.1.op", ">"},
		{"stmts.0.redirs.1.fd", nil}, {"stmts.0.redirs.2.op", "<"}, {"stmts.0.redirs.3.fd", "{fd}"},
		{"stmts.0.redirs.0.hdoc", nil}, {"stmts.0.redirs.0.text", "2>&1"},
		{"stmts.0.text", "cmd 2>&1 >out <in {fd}>x"},
	}},
	{"redirect first", ">f a; >g", []check{
		{"stmts.0.text", ">f a;"}, {"stmts.0.cmd.text", "a"}, {"stmts.1.cmd", nil}, {"stmts.1.text", ">g"},
	}},
	{"heredoc", "cat <<EOF | grep x\nbody $(rm y)\nEOF\n", []check{
		{"stmts.0.cmd.items.0.stmt.text", "cat <<EOF"}, {"stmts.0.cmd.items.0.stmt.redirs.0.text", "<<EOF"},
		{"stmts.0.cmd.items.0.stmt.redirs.0.hdoc.delim", "EOF"},
		{"stmts.0.cmd.items.0.stmt.redirs.0.hdoc.delim_quoted", false},
		{"stmts.0.cmd.items.0.stmt.redirs.0.hdoc.strip_tabs", false},
		{"stmts.0.cmd.items.0.stmt.redirs.0.hdoc.body", "body $(rm y)\n"},
		{"stmts.0.cmd.items.0.stmt.redirs.0.hdoc.word.text", "body $(rm y)\n"},
		{"stmts.0.cmd.items.0.stmt.redirs.0.hdoc.word.parts.1.type", "cmdsubst"},
		{"stmts.0.cmd.items.0.stmt.redirs.0.hdoc.word.parts.1.stmts.0.cmd.args.0.text", "rm"},
		{"stmts.0.cmd.items.0.stmt.redirs.0.hdoc.word.parts.2.text", "\n"},
		{"stmts.0.cmd.items.0.stmt.redirs.0.hdoc.word.end.line", 3},
		{"stmts.0.cmd.items.0.stmt.redirs.0.hdoc.word.end.col", 1},
	}},
	{"heredoc quoted, tabs stripped", "cat <<-'E'\n\tx $y\n\tE\n", []check{
		{"stmts.0.redirs.0.hdoc.delim", "E"}, {"stmts.0.redirs.0.hdoc.delim_quoted", true},
		{"stmts.0.redirs.0.hdoc.strip_tabs", true}, {"stmts.0.redirs.0.hdoc.body", "x $y\n"},
		{"stmts.0.redirs.0.hdoc.word.text", "\tx $y\n"}, {"stmts.0.redirs.0.hdoc.word.parts.#", 1},
		{"stmts.0.redirs.0.hdoc.word.parts.0.value", "\tx $y\n"}, {"stmts.0.redirs.0.op", "<<-"},
	}},
	{"heredoc delimiters", "cat <<\\A <<\"B\"c <<D\na\nA\nb\nBc\nd\nD\n", []check{
		{"stmts.0.redirs.0.hdoc.delim", "A"}, {"stmts.0.redirs.0.hdoc.delim_quoted", true},
		{"stmts.0.redirs.1.hdoc.delim", "Bc"}, {"stmts.0.redirs.1.hdoc.delim_quoted", true},
		{"stmts.0.redirs.2.hdoc.delim_quoted", false}, {"stmts.0.redirs.2.hdoc.body", "d\n"},
	}},
	{"heredoc ends", "a && cat <<E\nx\nE\nf() { cat; } <<E\nx\nE\ntime cat <<E\nx\nE\ncoproc cat <<E\nx\nE\n",
		[]check{
			{"stmts.0.cmd.text", "a && cat <<E"}, {"stmts.1.cmd.text", "f() { cat; } <<E"},
			{"stmts.2.cmd.text", "time cat <<E"}, {"stmts.3.cmd.text", "coproc cat <<E"},
		}},
	{"heredoc empty, two tabs, backslash in quotes", "cat <<E\nE\ncat <<-\"x\\y\"\n\t\tq\n\tx\\y\n", []check{
		{"stmts.0.redirs.0.hdoc.word", nil}, {"stmts.0.redirs.0.hdoc.body", ""},
		{"stmts.1.redirs.0.hdoc.delim", "x\\y"}, {"stmts.1.redirs.0.hdoc.body", "q\n"},
	}},
	{"herestring", `cat <<< "$x"`, []check{
		{"stmts.0.redirs.0.op", "<<<"}, {"stmts.0.redirs.0.hdoc", nil},
		{"stmts.0.redirs.0.word.parts.0.type", "double"},
	}},
	{"subshell", "(cd d; ls)", []check{
		{"stmts.0.cmd.type", "subshell"}, {"stmts.0.cmd.stmts.#", 2}, {"stmts.0.cmd.stmts.0.cmd.args.0.text", "cd"},
	}},
	{"block", "{ a; b; } > f", []check{
		{"stmts.0.cmd.type", "block"}, {"stmts.0.cmd.stmts.#", 2}, {"stmts.0.redirs.0.op", ">"},
		{"stmts.0.cmd.text", "{ a; b; }"},
	}},
	{"if", "if a; then b; elif c; then d; else e; fi; if f; then g; fi", []check{
		{"stmts.0.cmd.type", "if"}, {"stmts.0.cmd.clauses.#", 2}, {"stmts.0.cmd.clauses.0.keyword", "if"},
		{"stmts.0.cmd.clauses.0.cond.0.cmd.text", "a"}, {"stmts.0.cmd.clauses.0.then.0.cmd.text", "b"},
		{"stmts.0.cmd.clauses.1.keyword", "elif"}, {"stmts.0.cmd.clauses.1.cond.0.cmd.text", "c"},
		{"stmts.0.cmd.clauses.1.then.0.cmd.text", "d"}, {"stmts.0.cmd.else.0.cmd.text", "e"},
		{"stmts.1.cmd.else", nil}, {"stmts.0.cmd.clauses.1.pos.col", 15},
	}},
	{"while and until", "while a; do b; done; until c; do d; done", []check{
		{"stmts.0.cmd.type", "while"}, {"stmts.0.cmd.until", false}, {"stmts.0.cmd.cond.0.cmd.text", "a"},
		{"stmts.0.cmd.do.0.cmd.text", "b"}, {"stmts.1.cmd.until", true},
	}},
	{"for", "for x in a b; do c; done; for y; do :; done; for ((i=0; i<3; i++)); do :; done; select s in p; do :; done",
		[]check{
			{"stmts.0.cmd.type", "for"}, {"stmts.0.cmd.select", false}, {"stmts.0.cmd.loop.type", "words"},
			{"stmts.0.cmd.loop.name", "x"}, {"stmts.0.cmd.loop.in", true}, {"stmts.0.cmd.loop.items.#", 2},
			{"stmts.0.cmd.do.0.cmd.text", "c"}, {"stmts.1.cmd.loop.in", false},
			{"stmts.2.cmd.loop.type", "cstyle"}, {"stmts.2.cmd.loop.init.op", "="},
			{"stmts.2.cmd.loop.cond.op", "<"}, {"stmts.2.cmd.loop.post.post", true}, {"stmts.3.cmd.select", true},
		}},
	{"case", "case $x in a|b) c;; d) e;& *) f;;& esac; case y in z) w; esac", []check{
		{"stmts.0.cmd.type", "case"}, {"stmts.0.cmd.word.text", "$x"}, {"stmts.0.cmd.items.#", 3},
		{"stmts.0.cmd.items.0.type", "case_item"}, {"stmts.0.cmd.items.0.patterns.#", 2},
		{"stmts.0.cmd.items.0.patterns.1.text", "b"}, {"stmts.0.cmd.items.0.op", ";;"},
		{"stmts.0.cmd.items.1.op", ";&"}, {"stmts.0.cmd.items.2.op", ";;&"},
		{"stmts.0.cmd.items.0.stmts.0.cmd.text", "c"}, {"stmts.1.cmd.items.0.op", nil},
	}},
	{"function", "f() { a; } && b; function g { h; }", []check{
		{"stmts.0.cmd.type", "function"}, {"stmts.0.cmd.name", "f"}, {"stmts.0.cmd.keyword", false},
		{"stmts.0.cmd.parens", true}, {"stmts.0.cmd.judge_body_as_run", true},
		{"stmts.0.cmd.body.cmd.type", "andor"}, {"stmts.0.cmd.body.cmd.items.1.stmt.text", "b"},
		{"stmts.1.cmd.keyword", true}, {"stmts.1.cmd.parens", false}, {"stmts.1.cmd.body.cmd.type", "block"},
	}},
	{"arith_cmd", "(( x > 1 ))", []check{
		{"stmts.0.cmd.type", "arith_cmd"}, {"stmts.0.cmd.expr.op", ">"}, {"stmts.0.cmd.expr.y.text", "1"},
	}},
	{"test", "[[ -f a && $b == c ]]; [[ ( a ) ]]", []check{
		{"stmts.0.cmd.type", "test"}, {"stmts.0.cmd.expr.type", "test_binary"}, {"stmts.0.cmd.expr.op", "&&"},
		{"stmts.0.cmd.expr.x.type", "test_unary"}, {"stmts.0.cmd.expr.x.op", "-f"},
		{"stmts.0.cmd.expr.x.x.text", "a"}, {"stmts.0.cmd.expr.y.op", "=="}, {"stmts.0.cmd.expr.y.y.text", "c"},
		{"stmts.1.cmd.expr.type", "test_paren"}, {"stmts.1.cmd.expr.x.text", "a"},
	}},
	{"decl", "declare -x A=1", []check{
		{"stmts.0.cmd.type", "decl"}, {"stmts.0.cmd.variant", "declare"}, {"stmts.0.cmd.args.0.naked", true},
		{"stmts.0.cmd.args.0.value.text", "-x"}, {"stmts.0.cmd.args.1.name", "A"},
		{"stmts.0.cmd.args.1.naked", false},
	}},
	{"let", "let x=1 y++", []check{
		{"stmts.0.cmd.type", "let"}, {"stmts.0.cmd.exprs.#", 2}, {"stmts.0.cmd.exprs.1.op", "++"},
	}},
	{"time", "time -p a; time", []check{
		{"stmts.0.cmd.type", "time"}, {"stmts.0.cmd.posix", true}, {"stmts.0.cmd.stmt.cmd.text", "a"},
		{"stmts.1.cmd.stmt", nil}, {"stmts.1.cmd.posix", false},
	}},
	{"coproc", "coproc w { a; }", []check{
		{"stmts.0.cmd.type", "coproc"}, {"stmts.0.cmd.name.text", "w"}, {"stmts.0.cmd.stmt.cmd.type", "block"},
	}},
	{"positions", "a  \"b c\"\nb é x", []check{
		{"stmts.0.cmd.args.1.pos.offset", 3}, {"stmts.0.cmd.args.1.pos.line", 1},
		{"stmts.0.cmd.args.1.pos.col", 4}, {"stmts.0.cmd.args.1.end.offset", 8},
		{"stmts.1.pos.line", 2}, {"stmts.1.pos.col", 1}, {"stmts.1.cmd.args.2.pos.offset", 14},
		{"stmts.1.cmd.args.2.pos.col", 6}, {"stmts.0.cmd.args.1.text", "\"b c\""},
	}},
	{"empty", "", []check{{"ok", true}, {"stmts.#", 0}}},
	{"positions past mvdan's limits", "x" + strings.Repeat(" ", 16384) + "y\n" + strings.Repeat("\n", 262143) + "a\n" +
		strings.Repeat(" ", 16384) + "b", []check{
		{"stmts.0.cmd.args.1.pos.col", 0}, {"stmts.0.cmd.args.1.pos.offset", 16385},
		{"stmts.1.pos.line", 0}, {"stmts.1.pos.col", 1}, {"stmts.1.pos.offset", 278530},
		{"stmts.2.pos.offset", 294916}, {"stmts.2.pos.line", 0}, {"stmts.2.pos.col", 0},
	}},
	{"parse error", "echo 'x", []check{
		{"ok", false}, {"error.message", "reached EOF without closing quote `'`"}, {"error.line", 1},
		{"error.col", 6}, {"stmts", missing},
	}},
	{"language error", "echo a\necho =(ls)", []check{
		{"ok", false}, {"error.message", "`=(` process substitutions are a zsh feature; tried parsing as bash"},
		{"error.line", 2}, {"error.col", 6},
	}},
}

func TestTree(t *testing.T) {
	for _, r := range rows {
		t.Run(r.name, func(t *testing.T) {
			tree, defects := readTree(newParser(), r.src)
			if len(defects) > 0 {
				t.Errorf("FAIL-ASSERT row=%s path=defects got=%q", r.name, defects)
			}
			assert(t, r.name, decoded(t, tree), r.checks)
		})
	}
}

func TestDefects(t *testing.T) {
	zsh := syntax.NewParser(syntax.Variant(syntax.LangZsh))
	_, defects := readTree(zsh, "function a b { c; }")
	assert(t, "defects", map[string]any{"n": len(defects) > 0, "first": strings.HasPrefix(strings.Join(defects, ""),
		"*syntax.Lit at 1:10")}, []check{{"n", true}, {"first", true}})
	o := gateRow(zsh, in{I: 7, Cmd: "echo ${=a}"})
	assert(t, "defects gate row", decoded(t, o), []check{
		{"ok", false}, {"err", "tree: the tree misses *syntax.ParamExp at 1:6, its zsh or mksh parameter flag"},
		{"print", ""}, {"i", 7},
	})
	mksh := syntax.NewParser(syntax.Variant(syntax.LangMirBSDKorn))
	for _, c := range []struct {
		p         *syntax.Parser
		src, want string
	}{
		{zsh, "echo ${=a}", "*syntax.ParamExp at 1:6, its zsh or mksh parameter flag"},
		{zsh, "echo ${+a}", "*syntax.ParamExp at 1:6, its zsh or mksh parameter flag"},
		{zsh, "echo ${~a}", "*syntax.ParamExp at 1:6, its zsh or mksh parameter flag"},
		{zsh, "echo ${^a}", "*syntax.ParamExp at 1:6, its zsh or mksh parameter flag"},
		{mksh, "echo ${%a}", "*syntax.ParamExp at 1:6, its zsh or mksh parameter flag"},
		{zsh, "a &|", "*syntax.Stmt at 1:1, its zsh disown"},
		{mksh, "echo $((# 1))", "*syntax.ArithmExp at 1:6, its mksh unsigned arithmetic"},
		{mksh, "((# 1))", "*syntax.ArithmCmd at 1:1, its mksh unsigned arithmetic"},
	} {
		_, defects := readTree(c.p, c.src)
		assert(t, "defects flag "+c.src, map[string]any{"d": strings.Join(defects, "|")}, []check{{"d", c.want}})
	}
	_, defects = readTree(syntax.NewParser(syntax.Variant(syntax.LangBats)), "@test \"x\" { a; }")
	assert(t, "defects unknown kind", map[string]any{"first": strings.Join(defects, "|")}, []check{
		{"first", "*syntax.TestDecl at 1:1, a kind the reader does not know|*syntax.Word at 1:7|*syntax.DblQuoted at 1:7|*syntax.Lit at 1:8|*syntax.Stmt at 1:11|" +
			"*syntax.Block at 1:11|*syntax.Stmt at 1:13|*syntax.CallExpr at 1:13|*syntax.Word at 1:13|" +
			"*syntax.Lit at 1:13"},
	})
}

// A chain operand whose statement carries anything of its own stays one item, never spliced into the chain: mvdan's
// parser makes no such operand, so the tree is mutated to hold one.
func TestChainSplice(t *testing.T) {
	for _, c := range []struct {
		name string
		set  func(*syntax.Stmt)
	}{
		{"negated", func(s *syntax.Stmt) { s.Negated = true }},
		{"background", func(s *syntax.Stmt) { s.Background = true }},
		{"coprocess", func(s *syntax.Stmt) { s.Coprocess = true }},
		{"disown", func(s *syntax.Stmt) { s.Disown = true }},
		{"redirected", func(s *syntax.Stmt) {
			s.Redirs = []*syntax.Redirect{{Op: syntax.RdrOut, Word: s.Cmd.(*syntax.BinaryCmd).X.Cmd.(*syntax.CallExpr).Args[0]}}
		}},
		{"commented", func(s *syntax.Stmt) { s.Comments = []syntax.Comment{{Text: "c"}} }},
	} {
		f, err := newParser().Parse(strings.NewReader("a && b && c"), "")
		if err != nil {
			t.Fatal(err)
		}
		top := f.Stmts[0].Cmd.(*syntax.BinaryCmd)
		inner := top.Y
		if _, ok := top.X.Cmd.(*syntax.BinaryCmd); ok {
			inner = top.X
		}
		c.set(inner)
		tree, _ := buildTree(f, "a && b && c")
		assert(t, "chain splice "+c.name, decoded(t, tree), []check{{"stmts.0.cmd.items.#", 2}})
	}
}

func TestModes(t *testing.T) {
	type result struct {
		Code   int    `json:"code"`
		Stdout string `json:"stdout"`
		Stderr string `json:"stderr"`
	}
	runs := func(args []string, stdin string) any {
		var out, errb bytes.Buffer
		code := run(args, strings.NewReader(stdin), &out, &errb)
		return decoded(t, result{code, out.String(), errb.String()})
	}
	assert(t, "mode gate", runs(nil, `{"i": 3, "cmd": "ls >f"}`+"\n"+`{"i": 4, "cmd": "'"}`+"\n"+
		`{"i": 5, "cmd": "echo ${=a}"}`+"\n"), []check{
		{"code", 0}, {"stdout", `{"i":3,"ok":true,"print":"\u0001ls\u0002 \u003e\u0001f\u0002\n","tags":[],` +
			`"subs":[],"hdocs":[]}` + "\n" + `{"i":4,"ok":false,"err":"1:1: reached EOF without closing quote ` +
			"`'`" + `","print":"","tags":[],"subs":[],"hdocs":[]}` + "\n" +
			`{"i":5,"ok":false,"err":"1:8: invalid parameter name","print":"","tags":[],"subs":[],"hdocs":[]}` + "\n"},
	})
	assert(t, "mode tree", runs([]string{"tree"}, "a>b"), []check{
		{"code", 0}, {"stderr", ""},
	})
	var tree any
	var out bytes.Buffer
	run([]string{"tree"}, strings.NewReader("a>b"), &out, &out)
	if err := json.Unmarshal(out.Bytes(), &tree); err != nil {
		t.Errorf("FAIL-ASSERT row=mode tree path=json got=%q", out.String())
	}
	assert(t, "mode tree", tree, []check{{"stmts.0.redirs.0.word.text", "b"}, {"stmts.0.text", "a>b"}})
	assert(t, "mode tree escapes no HTML", map[string]any{"raw": strings.Contains(out.String(), `"a>b"`)},
		[]check{{"raw", true}})
	assert(t, "mode usage", runs([]string{"gate"}, ""), []check{
		{"code", 2}, {"stdout", ""}, {"stderr", "usage: shell-reader [tree]\n"},
	})
	var out3, err3 bytes.Buffer
	code := treeMode(syntax.NewParser(syntax.Variant(syntax.LangZsh)), strings.NewReader("function a b { c; }"),
		&out3, &err3)
	assert(t, "mode tree defect", decoded(t, result{code, out3.String(), err3.String()}), []check{
		{"code", 3}, {"stdout", ""}, {"stderr", "shell-reader: the tree misses *syntax.Lit at 1:10\n"},
	})
	long := gateRow(newParser(), in{I: 1, Cmd: "cat <<" + strings.Repeat("A", 300)})
	assert(t, "mode gate clip", map[string]any{"n": len(long.Err), "head": long.Err[:40]}, []check{
		{"n", 200}, {"head", "1:5: unclosed here-document `AAAAAAAAAAA"},
	})
	assert(t, "mode not utf-8", runs([]string{"tree"}, "a\xff"), []check{
		{"code", 2}, {"stdout", ""}, {"stderr", "shell-reader: the call text is not UTF-8\n"},
	})
}
