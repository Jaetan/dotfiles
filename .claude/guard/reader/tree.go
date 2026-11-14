package main

// The tree mode's JSON. Every node is an object with "type", "text" (its verbatim source), "pos" and "end"
// ({offset, line, col}: bytes of the UTF-8 text, 1-based line and byte column, line and col 0 past mvdan's limits of
// line 262143 and column 16383, where the offset stays exact).
// A node's text never reaches into a heredoc body: a redirection ends at its word and its body is under "hdoc", whose
// word is the body as mvdan reads it, ending where the delimiter's line starts, and whose body is that text as the
// command reads it, leading tabs stripped under `<<-`, expansions left as written.
//
//	file      {ok: true, stmts: [stmt]} | {ok: false, error: {message, line, col}}
//	stmt      negated, background, coprocess, redirs: [redirect], cmd: command | null
//	command   andor {items: [{op: null|"&&"|"||", stmt}]} | pipeline {items: [{op: null|"|"|"|&", stmt}]}
//	          call {assigns: [assign], args: [word]} | subshell {stmts} | block {stmts}
//	          if {clauses: [{keyword, pos, cond, then}], else: [stmt] | null} | while {until, cond, do}
//	          for {select, loop: words {name, in, items} | cstyle {init, cond, post}, do} | case {word, items}
//	          function {name, keyword, parens, body: stmt, judge_body_as_run: true} | arith_cmd {expr}
//	          test {expr} | decl {variant, args} | let {exprs} | time {posix, stmt} | coproc {name, stmt}
//	case_item op: ";;"|";&"|";;&"|null, patterns: [word], stmts
//	assign    name, append, naked, index: arith, value: word, array: {elems: [array_elem {index, value}]}
//	redirect  op, fd: "2"|"{v}"|null, word, hdoc: {delim, delim_quoted, strip_tabs, body, word} | null
//	word      parts: [lit {value} | single {value} | ansi_c {value} | double {dollar, parts}
//	          | param {short, excl, length, name, index, slice: {offset, length}, repl: {all, orig, with},
//	          names: "*"|"@"|null, exp: {op, word}} | cmdsubst {form, stmts} | arith {bracket, expr}
//	          | procsubst {op, stmts} | extglob {op, pattern}]
//	arith     arith_binary {op, x, y} | arith_unary {op, post, x} | arith_paren {x} | word
//	test expr test_binary {op, x, y} | test_unary {op, x} | test_paren {x} | word
//
// A cmdsubst's form is "dollar" $(..), "backquote" `..`, "brace" ${ ..;} or "brace_reply" ${| ..;}, the last two
// bash 5.3's forms that run in the current shell. A flag of another shell's the tree cannot show (zsh's ${=a}, mksh's
// ((# a)), zsh's &|) is a defect, as is a node the tree does not hold.
//
// mvdan nests `f() { a; } && b` into the function's body, so every function body is to be judged as run where it is
// defined: judge_body_as_run says so on every function node.

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"

	"mvdan.cc/sh/v3/syntax"
)

type node = map[string]any

type builder struct {
	src     string
	seen    map[syntax.Node]bool
	defects []string
}

// readTree parses src and builds its tree; defects names each node of mvdan's the tree does not hold.
func readTree(p *syntax.Parser, src string) (node, []string) {
	f, err := p.Parse(strings.NewReader(src), "")
	if err != nil {
		return node{"ok": false, "error": parseError(err)}, nil
	}
	return buildTree(f, src)
}

// buildTree is the tree of f, parsed from src, and each node of f's the tree does not hold.
func buildTree(f *syntax.File, src string) (node, []string) {
	b := &builder{src: src, seen: map[syntax.Node]bool{f: true}}
	tree := node{"ok": true, "stmts": b.stmts(f.Stmts)}
	return tree, b.missing(f)
}

func parseError(err error) node {
	var pe syntax.ParseError
	if errors.As(err, &pe) {
		return node{"message": pe.Text, "line": pe.Pos.Line(), "col": pe.Pos.Col()}
	}
	var le syntax.LangError
	if errors.As(err, &le) {
		return node{"message": strings.TrimPrefix(le.Error(), le.Pos.String()+": "), "line": le.Pos.Line(),
			"col": le.Pos.Col()}
	}
	return node{"message": err.Error(), "line": 0, "col": 0}
}

func (b *builder) missing(f *syntax.File) []string {
	for n := range syntax.Preorder(f) {
		if !b.seen[n] {
			b.defects = append(b.defects, fmt.Sprintf("%T at %s", n, n.Pos()))
		}
	}
	return b.defects
}

func encode(w io.Writer, v node) error {
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	return enc.Encode(v)
}

func (b *builder) pos(p syntax.Pos) node {
	return node{"offset": p.Offset(), "line": p.Line(), "col": p.Col()}
}

// at is the node n of type typ spanning from to to.
func (b *builder) at(n syntax.Node, typ string, from, to syntax.Pos) node {
	b.seen[n] = true
	return node{"type": typ, "text": b.src[from.Offset():to.Offset()], "pos": b.pos(from), "end": b.pos(to)}
}

func (b *builder) node(n syntax.Node, typ string) node { return b.at(n, typ, b.start(n), b.end(n)) }

// start is mvdan's Pos, except that a statement not negated starts at its command or first redirection (mvdan gives
// the first statement of `! a | b` the position of the `!`) and an array element with an index at its `[`, which
// mvdan does not record.
func (b *builder) start(n syntax.Node) syntax.Pos {
	switch x := n.(type) {
	case *syntax.Stmt:
		if x.Negated || x.Cmd == nil && len(x.Redirs) == 0 {
			return x.Position
		}
		var s syntax.Pos
		if x.Cmd != nil {
			s = b.start(x.Cmd)
		}
		for _, r := range x.Redirs {
			if p := r.Pos(); !s.IsValid() || p.Offset() < s.Offset() {
				s = p
			}
		}
		return s
	case *syntax.BinaryCmd:
		return b.start(x.X)
	case *syntax.ArrayElem:
		if x.Index == nil {
			break
		}
		p := x.Index.Pos()
		k := p.Offset()
		for k > 0 && (b.src[k-1] == ' ' || b.src[k-1] == '\t') {
			k--
		}
		if k > 0 && b.src[k-1] == '[' {
			d, col := p.Offset()-(k-1), uint(0)
			if p.Col() > d {
				col = p.Col() - d
			}
			return syntax.NewPos(k-1, p.Line(), col)
		}
	}
	return n.Pos()
}

// end is mvdan's End, except that a redirection ends at its word, so no node runs on into a heredoc body.
func (b *builder) end(n syntax.Node) syntax.Pos {
	switch x := n.(type) {
	case *syntax.Redirect:
		return x.Word.End()
	case *syntax.Stmt:
		if x.Semicolon.IsValid() {
			return x.End()
		}
		e := x.Position
		if x.Cmd != nil {
			e = later(e, b.end(x.Cmd))
		}
		for _, r := range x.Redirs {
			e = later(e, b.end(r))
		}
		return e
	case *syntax.BinaryCmd:
		return b.end(x.Y)
	case *syntax.FuncDecl:
		return b.end(x.Body)
	case *syntax.TimeClause:
		if x.Stmt != nil {
			return b.end(x.Stmt)
		}
	case *syntax.CoprocClause:
		return b.end(x.Stmt)
	}
	return n.End()
}

func later(a, c syntax.Pos) syntax.Pos {
	if c.Offset() > a.Offset() {
		return c
	}
	return a
}

func (b *builder) lit(l *syntax.Lit) any {
	if l == nil {
		return nil
	}
	b.seen[l] = true
	return l.Value
}

func (b *builder) unsupported(n syntax.Node) node {
	b.defects = append(b.defects, fmt.Sprintf("%T at %s, a kind the reader does not know", n, n.Pos()))
	return b.node(n, "unsupported")
}

func (b *builder) stmts(list []*syntax.Stmt) []node {
	out := make([]node, 0, len(list))
	for _, s := range list {
		out = append(out, b.stmt(s))
	}
	return out
}

func (b *builder) stmt(s *syntax.Stmt) node {
	if s == nil {
		return nil
	}
	b.flag(s, s.Disown, "zsh disown")
	n := b.node(s, "stmt")
	n["negated"], n["background"], n["coprocess"] = s.Negated, s.Background, s.Coprocess
	redirs := make([]node, 0, len(s.Redirs))
	for _, r := range s.Redirs {
		redirs = append(redirs, b.redirect(r))
	}
	n["redirs"] = redirs
	n["cmd"] = b.command(s.Cmd)
	return n
}

func family(op syntax.BinCmdOperator) string {
	if op == syntax.Pipe || op == syntax.PipeAll {
		return "pipeline"
	}
	return "andor"
}

func plain(s *syntax.Stmt) bool {
	return !s.Negated && !s.Background && !s.Coprocess && !s.Disown && len(s.Redirs) == 0 && len(s.Comments) == 0
}

// chain appends the items of x, its first one carrying op, splicing in each operand that is itself a chain of the
// same family behind a plain statement.
func (b *builder) chain(x *syntax.BinaryCmd, fam string, items []node, op any) []node {
	b.seen[x] = true
	items = b.link(x.X, fam, items, op)
	return b.link(x.Y, fam, items, x.Op.String())
}

func (b *builder) link(s *syntax.Stmt, fam string, items []node, op any) []node {
	if y, ok := s.Cmd.(*syntax.BinaryCmd); ok && plain(s) && family(y.Op) == fam {
		b.seen[s] = true
		return b.chain(y, fam, items, op)
	}
	return append(items, node{"op": op, "stmt": b.stmt(s)})
}

func (b *builder) command(c syntax.Command) node {
	switch x := c.(type) {
	case nil:
		return nil
	case *syntax.CallExpr:
		n := b.node(x, "call")
		assigns := make([]node, 0, len(x.Assigns))
		for _, a := range x.Assigns {
			assigns = append(assigns, b.assign(a))
		}
		n["assigns"], n["args"] = assigns, b.words(x.Args)
		return n
	case *syntax.BinaryCmd:
		fam := family(x.Op)
		n := b.node(x, fam)
		n["items"] = b.chain(x, fam, nil, nil)
		return n
	case *syntax.Subshell:
		n := b.node(x, "subshell")
		n["stmts"] = b.stmts(x.Stmts)
		return n
	case *syntax.Block:
		n := b.node(x, "block")
		n["stmts"] = b.stmts(x.Stmts)
		return n
	case *syntax.IfClause:
		return b.ifClause(x)
	case *syntax.WhileClause:
		n := b.node(x, "while")
		n["until"], n["cond"], n["do"] = x.Until, b.stmts(x.Cond), b.stmts(x.Do)
		return n
	case *syntax.ForClause:
		n := b.node(x, "for")
		n["select"], n["loop"], n["do"] = x.Select, b.loop(x.Loop), b.stmts(x.Do)
		return n
	case *syntax.CaseClause:
		n := b.node(x, "case")
		items := make([]node, 0, len(x.Items))
		for _, it := range x.Items {
			in := b.node(it, "case_item")
			in["op"] = nil
			if it.OpPos.IsValid() {
				in["op"] = it.Op.String()
			}
			in["patterns"], in["stmts"] = b.words(it.Patterns), b.stmts(it.Stmts)
			items = append(items, in)
		}
		n["word"], n["items"] = b.word(x.Word), items
		return n
	case *syntax.FuncDecl:
		n := b.node(x, "function")
		n["name"], n["keyword"], n["parens"] = b.lit(x.Name), x.RsrvWord, x.Parens
		n["body"], n["judge_body_as_run"] = b.stmt(x.Body), true
		return n
	case *syntax.ArithmCmd:
		b.flag(x, x.Unsigned, "mksh unsigned arithmetic")
		n := b.node(x, "arith_cmd")
		n["expr"] = b.arith(x.X)
		return n
	case *syntax.TestClause:
		n := b.node(x, "test")
		n["expr"] = b.test(x.X)
		return n
	case *syntax.DeclClause:
		n := b.node(x, "decl")
		args := make([]node, 0, len(x.Args))
		for _, a := range x.Args {
			args = append(args, b.assign(a))
		}
		n["variant"], n["args"] = b.lit(x.Variant), args
		return n
	case *syntax.LetClause:
		n := b.node(x, "let")
		exprs := make([]node, 0, len(x.Exprs))
		for _, e := range x.Exprs {
			exprs = append(exprs, b.arith(e))
		}
		n["exprs"] = exprs
		return n
	case *syntax.TimeClause:
		n := b.node(x, "time")
		n["posix"], n["stmt"] = x.PosixFormat, b.stmt(x.Stmt)
		return n
	case *syntax.CoprocClause:
		n := b.node(x, "coproc")
		n["name"], n["stmt"] = b.word(x.Name), b.stmt(x.Stmt)
		return n
	}
	return b.unsupported(c)
}

func (b *builder) ifClause(x *syntax.IfClause) node {
	n := b.node(x, "if")
	clauses := []node{}
	n["else"] = nil
	for c, kw := x, "if"; c != nil; c, kw = c.Else, "elif" {
		b.seen[c] = true
		if !c.ThenPos.IsValid() {
			n["else"] = b.stmts(c.Then)
			break
		}
		clauses = append(clauses, node{"keyword": kw, "pos": b.pos(c.Position), "cond": b.stmts(c.Cond),
			"then": b.stmts(c.Then)})
	}
	n["clauses"] = clauses
	return n
}

func (b *builder) loop(l syntax.Loop) node {
	switch x := l.(type) {
	case *syntax.WordIter:
		n := b.node(x, "words")
		n["name"], n["in"], n["items"] = b.lit(x.Name), x.InPos.IsValid(), b.words(x.Items)
		return n
	case *syntax.CStyleLoop:
		n := b.node(x, "cstyle")
		n["init"], n["cond"], n["post"] = b.arith(x.Init), b.arith(x.Cond), b.arith(x.Post)
		return n
	}
	return b.unsupported(l)
}

func (b *builder) assign(a *syntax.Assign) node {
	n := b.node(a, "assign")
	n["name"], n["append"], n["naked"] = b.lit(a.Name), a.Append, a.Naked
	n["index"], n["value"], n["array"] = b.arith(a.Index), b.word(a.Value), nil
	if a.Array != nil {
		arr := b.node(a.Array, "array")
		elems := make([]node, 0, len(a.Array.Elems))
		for _, e := range a.Array.Elems {
			en := b.node(e, "array_elem")
			en["index"], en["value"] = b.arith(e.Index), b.word(e.Value)
			elems = append(elems, en)
		}
		arr["elems"] = elems
		n["array"] = arr
	}
	return n
}

func (b *builder) redirect(r *syntax.Redirect) node {
	n := b.node(r, "redirect")
	n["op"], n["fd"], n["word"], n["hdoc"] = r.Op.String(), b.lit(r.N), b.word(r.Word), nil
	if r.Op == syntax.Hdoc || r.Op == syntax.DashHdoc {
		delim, quoted := unquoted(r.Word)
		h := node{"delim": delim, "delim_quoted": quoted, "strip_tabs": r.Op == syntax.DashHdoc, "body": "",
			"word": nil}
		if r.Hdoc != nil {
			w := b.word(r.Hdoc)
			text := b.clipBody(w, r.Hdoc)
			if r.Op == syntax.DashHdoc {
				text = stripTabs(text)
			}
			h["word"], h["body"] = w, text
		}
		n["hdoc"] = h
	}
	return n
}

// clipBody ends the body word w, read as hw, where its closing delimiter's line starts: mvdan ends a body's last
// literal past the delimiter, and a tab-stripped body's last literal holds the delimiter line's tabs. It returns
// the body's verbatim text.
func (b *builder) clipBody(w node, hw *syntax.Word) string {
	from, to := hw.Pos().Offset(), hw.End().Offset()
	if k := strings.LastIndexByte(b.src[from:to], '\n'); k >= 0 {
		to = from + uint(k) + 1
	}
	end := syntax.NewPos(to, hw.End().Line(), 1)
	clip := func(n node) {
		start := n["pos"].(node)["offset"].(uint)
		if n["end"].(node)["offset"].(uint) <= to || start > to {
			return
		}
		text := b.src[start:to]
		if v, ok := n["value"].(string); ok && strings.HasPrefix(v, text) {
			n["value"] = text
		}
		n["text"], n["end"] = text, b.pos(end)
	}
	clip(w)
	if parts := w["parts"].([]node); len(parts) > 0 {
		clip(parts[len(parts)-1])
	}
	return w["text"].(string)
}

// stripTabs is text with each line's leading tabs removed, as `<<-` reads it.
func stripTabs(text string) string {
	lines := strings.SplitAfter(text, "\n")
	for i, l := range lines {
		lines[i] = strings.TrimLeft(l, "\t")
	}
	return strings.Join(lines, "")
}

// unquoted is the delimiter as the heredoc's closing line spells it and whether any of it is quoted, which leaves
// the body unexpanded: mvdan's own rule (Parser.unquotedWordBytes).
func unquoted(w *syntax.Word) (string, bool) {
	var sb strings.Builder
	quoted := false
	var part func(p syntax.WordPart, inDbl bool)
	part = func(p syntax.WordPart, inDbl bool) {
		switch x := p.(type) {
		case *syntax.Lit:
			for i := 0; i < len(x.Value); i++ {
				if c := x.Value[i]; c == '\\' && !inDbl {
					if i++; i < len(x.Value) {
						sb.WriteByte(x.Value[i])
					}
					quoted = true
				} else {
					sb.WriteByte(c)
				}
			}
		case *syntax.SglQuoted:
			sb.WriteString(x.Value)
			quoted = true
		case *syntax.DblQuoted:
			for _, q := range x.Parts {
				part(q, true)
			}
			quoted = true
		}
	}
	for _, p := range w.Parts {
		part(p, false)
	}
	return sb.String(), quoted
}

func (b *builder) words(ws []*syntax.Word) []node {
	out := make([]node, 0, len(ws))
	for _, w := range ws {
		out = append(out, b.word(w))
	}
	return out
}

func (b *builder) word(w *syntax.Word) node {
	if w == nil {
		return nil
	}
	n := b.node(w, "word")
	n["parts"] = b.parts(w.Parts)
	return n
}

func (b *builder) parts(ps []syntax.WordPart) []node {
	out := make([]node, 0, len(ps))
	for _, p := range ps {
		out = append(out, b.part(p))
	}
	return out
}

func (b *builder) part(p syntax.WordPart) node {
	switch x := p.(type) {
	case *syntax.Lit:
		n := b.node(x, "lit")
		n["value"] = x.Value
		return n
	case *syntax.SglQuoted:
		typ := "single"
		if x.Dollar {
			typ = "ansi_c"
		}
		n := b.node(x, typ)
		n["value"] = x.Value
		return n
	case *syntax.DblQuoted:
		n := b.node(x, "double")
		n["dollar"], n["parts"] = x.Dollar, b.parts(x.Parts)
		return n
	case *syntax.ParamExp:
		return b.param(x)
	case *syntax.CmdSubst:
		n := b.node(x, "cmdsubst")
		form := "dollar"
		switch {
		case x.Backquotes:
			form = "backquote"
		case x.TempFile:
			form = "brace"
		case x.ReplyVar:
			form = "brace_reply"
		}
		n["form"], n["stmts"] = form, b.stmts(x.Stmts)
		return n
	case *syntax.ArithmExp:
		b.flag(x, x.Unsigned, "mksh unsigned arithmetic")
		n := b.node(x, "arith")
		n["bracket"], n["expr"] = x.Bracket, b.arith(x.X)
		return n
	case *syntax.ProcSubst:
		n := b.node(x, "procsubst")
		n["op"], n["stmts"] = x.Op.String(), b.stmts(x.Stmts)
		return n
	case *syntax.ExtGlob:
		n := b.node(x, "extglob")
		n["op"], n["pattern"] = x.Op.String(), b.lit(x.Pattern)
		return n
	}
	return b.unsupported(p)
}

// flag records a defect where a node carries a flag of another shell's the tree does not show.
func (b *builder) flag(n syntax.Node, set bool, what string) {
	if set {
		b.defects = append(b.defects, fmt.Sprintf("%T at %s, its %s", n, n.Pos(), what))
	}
}

func (b *builder) param(x *syntax.ParamExp) node {
	b.flag(x, x.Width || x.IsSet || x.Split != syntax.OptUnset || x.GlobSubst != syntax.OptUnset ||
		x.RcExpand != syntax.OptUnset, "zsh or mksh parameter flag")
	n := b.node(x, "param")
	n["short"], n["excl"], n["length"] = x.Short, x.Excl, x.Length
	n["name"], n["index"] = b.lit(x.Param), b.arith(x.Index)
	n["slice"], n["repl"], n["names"], n["exp"] = nil, nil, nil, nil
	if x.Slice != nil {
		n["slice"] = node{"offset": b.arith(x.Slice.Offset), "length": b.arith(x.Slice.Length)}
	}
	if x.Repl != nil {
		n["repl"] = node{"all": x.Repl.All, "orig": b.word(x.Repl.Orig), "with": b.word(x.Repl.With)}
	}
	if x.Names != 0 {
		n["names"] = x.Names.String()
	}
	if x.Exp != nil {
		n["exp"] = node{"op": x.Exp.Op.String(), "word": b.word(x.Exp.Word)}
	}
	return n
}

func (b *builder) arith(e syntax.ArithmExpr) node {
	switch x := e.(type) {
	case nil:
		return nil
	case *syntax.Word:
		return b.word(x)
	case *syntax.BinaryArithm:
		n := b.node(x, "arith_binary")
		n["op"], n["x"], n["y"] = x.Op.String(), b.arith(x.X), b.arith(x.Y)
		return n
	case *syntax.UnaryArithm:
		n := b.node(x, "arith_unary")
		n["op"], n["post"], n["x"] = x.Op.String(), x.Post, b.arith(x.X)
		return n
	case *syntax.ParenArithm:
		n := b.node(x, "arith_paren")
		n["x"] = b.arith(x.X)
		return n
	}
	return b.unsupported(e)
}

func (b *builder) test(e syntax.TestExpr) node {
	switch x := e.(type) {
	case nil:
		return nil
	case *syntax.Word:
		return b.word(x)
	case *syntax.BinaryTest:
		n := b.node(x, "test_binary")
		n["op"], n["x"], n["y"] = x.Op.String(), b.test(x.X), b.test(x.Y)
		return n
	case *syntax.UnaryTest:
		n := b.node(x, "test_unary")
		n["op"], n["x"] = x.Op.String(), b.test(x.X)
		return n
	case *syntax.ParenTest:
		n := b.node(x, "test_paren")
		n["x"] = b.test(x.X)
		return n
	}
	return b.unsupported(e)
}
