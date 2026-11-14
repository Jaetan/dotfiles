// Command shell-reader is the guard kit's shell reader: mvdan.cc/sh, pinned in go.mod, reading bash.
//
// shell-reader tree reads one call text on stdin and prints its syntax tree as one JSON object (tree.go). Exit 0: the
// JSON is printed, a parse error being {"ok": false, "error": {message, line, col}}. Exit 2: a usage error, or stdin
// is not UTF-8. Exit 3: the tree misses a node of mvdan's, a defect of this reader, named on stderr.
//
// shell-reader with no arguments speaks the corpus gate's protocol (gate.go): JSONL {i, cmd} on stdin, one row out
// per input. Any other argument exits 2.
package main

import (
	"fmt"
	"io"
	"os"
	"unicode/utf8"

	"mvdan.cc/sh/v3/syntax"
)

// newParser is the one parser both modes read with: bash, comments dropped.
func newParser() *syntax.Parser { return syntax.NewParser(syntax.Variant(syntax.LangBash)) }

func run(args []string, stdin io.Reader, stdout, stderr io.Writer) int {
	switch {
	case len(args) == 0:
		gateMode(stdin, stdout)
		return 0
	case len(args) == 1 && args[0] == "tree":
		return treeMode(newParser(), stdin, stdout, stderr)
	default:
		fmt.Fprintln(stderr, "usage: shell-reader [tree]")
		return 2
	}
}

func treeMode(p *syntax.Parser, stdin io.Reader, stdout, stderr io.Writer) int {
	src, err := io.ReadAll(stdin)
	if err != nil {
		fmt.Fprintln(stderr, "shell-reader: reading stdin:", err)
		return 2
	}
	if !utf8.Valid(src) {
		fmt.Fprintln(stderr, "shell-reader: the call text is not UTF-8")
		return 2
	}
	tree, defects := readTree(p, string(src))
	if len(defects) > 0 {
		fmt.Fprintln(stderr, "shell-reader: the tree misses", defects[0])
		return 3
	}
	if err := encode(stdout, tree); err != nil {
		fmt.Fprintln(stderr, "shell-reader: writing stdout:", err)
		return 2
	}
	return 0
}

func main() { os.Exit(run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr)) }
