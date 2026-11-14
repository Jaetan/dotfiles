package main

// The corpus gate's protocol, the research prototype's for bash: JSONL {i, cmd} on stdin, one row out per input,
// {i, ok, err, print, perr, tags, subs, hdocs}. print is mvdan's printer over the tree with every word one marked
// literal, \x01 its verbatim source text \x02, and every heredoc body marked whole, \x03 body \x04, its closing line
// the delimiter bash takes: text bash reads as the source means. Each sub is a command or process substitution's
// source interior and its statements printed alike; each hdoc a heredoc's operator, delimiter, body and the unit
// holding it (-1 the top level, k the k-th sub). A row whose tree misses a node of mvdan's (tree.go) is refused, err
// "tree: ...", so the gate holds the tree mode over the whole corpus.
// A line of stdin that is not JSON panics, a non-zero exit.

import (
	"bufio"
	"bytes"
	"encoding/json"
	"io"
	"regexp"
	"sort"
	"strings"

	"mvdan.cc/sh/v3/expand"
	"mvdan.cc/sh/v3/syntax"
)

type in struct {
	I   int    `json:"i"`
	Cmd string `json:"cmd"`
}

type sub struct {
	Kind  string `json:"kind"`
	Depth int    `json:"depth"`
	Inner string `json:"inner"`
	Print string `json:"print"`
	Err   string `json:"err,omitempty"`
}

type hdoc struct {
	Op       string   `json:"op"`
	OpOff    int      `json:"op_off"`
	Delim    string   `json:"delim"`
	BodySpan string   `json:"body_span"`
	BodyOff  int      `json:"body_off"`
	BodyEnd  int      `json:"body_end"`
	BodyText string   `json:"body_text"`
	Words    []string `json:"words"`
	Unit     int      `json:"unit"`
}

type out struct {
	I     int      `json:"i"`
	Ok    bool     `json:"ok"`
	Err   string   `json:"err,omitempty"`
	Print string   `json:"print"`
	PErr  string   `json:"perr,omitempty"`
	Tags  []string `json:"tags"`
	Subs  []sub    `json:"subs"`
	Hdocs []hdoc   `json:"hdocs"`
}

type state struct {
	src  string
	done map[*syntax.Word]bool
}

func (s *state) span(a, b syntax.Pos) string {
	x, y := int(a.Offset()), int(b.Offset())
	if x < 0 || y > len(s.src) || x > y {
		return ""
	}
	return s.src[x:y]
}

// Word markers: \x01..\x02 around each word's verbatim source text, \x03..\x04 around each here-document body.
const wo, wc, ho, hc = "\x01", "\x02", "\x03", "\x04"

func (s *state) mark(text string) *syntax.Word {
	w := &syntax.Word{Parts: []syntax.WordPart{&syntax.Lit{Value: wo + text + wc}}}
	s.done[w] = true
	return w
}

func (s *state) assignText(a *syntax.Assign) string {
	if a.Array == nil {
		return s.span(a.Pos(), a.End())
	}
	head := s.span(a.Pos(), a.Array.Lparen)
	elems := make([]string, 0, len(a.Array.Elems))
	for _, e := range a.Array.Elems {
		elems = append(elems, s.span(e.Pos(), e.End()))
	}
	return head + "(" + strings.Join(elems, " ") + ")"
}

// clause is a C-style for clause's source text from a to the first ; after its expression e, blanks included, and
// where the next clause starts.
func (s *state) clause(a int, e syntax.ArithmExpr) (string, int) {
	from := a
	if e != nil {
		from = int(e.End().Offset())
	}
	b := from + strings.IndexByte(s.src[from:], ';')
	return s.src[a:b], b + 1
}

// transform gives every word to the printer as one marked literal, its verbatim source text, so each word is one
// token of mvdan's reading; heredoc bodies are marked whole, each with its closing line after its end mark. A
// redirection word that opens with a process substitution is marked one space after its operator, as mvdan prints
// it, since bash reads `<<(` as a heredoc.
func (s *state) transform(root syntax.Node) {
	syntax.Walk(root, func(n syntax.Node) bool {
		switch x := n.(type) {
		case *syntax.Word:
			if !s.done[x] {
				x.Parts = []syntax.WordPart{&syntax.Lit{Value: wo + s.span(x.Pos(), x.End()) + wc}}
				s.done[x] = true
			}
		case *syntax.Stmt:
			if a, ok := x.Cmd.(*syntax.ArithmCmd); ok {
				x.Cmd = &syntax.CallExpr{Args: []*syntax.Word{s.mark(s.span(a.Pos(), a.End()))}}
			}
			for _, rd := range x.Redirs {
				switch {
				case rd.Op != syntax.Hdoc && rd.Op != syntax.DashHdoc:
					if _, ok := rd.Word.Parts[0].(*syntax.ProcSubst); ok && !s.done[rd.Word] {
						rd.Word = s.mark(s.span(rd.Word.Pos(), rd.Word.End()))
						lit := rd.Word.Parts[0].(*syntax.Lit)
						lit.Value = " " + lit.Value
					}
				case rd.Hdoc == nil || !s.done[rd.Hdoc]:
					body := ""
					if rd.Hdoc != nil {
						body = hdocText(s, rd.Hdoc)
					}
					rd.Hdoc = &syntax.Word{Parts: []syntax.WordPart{
						&syntax.Lit{Value: ho + body + hc + s.delimiter(rd.Word)}}}
					s.done[rd.Hdoc] = true
				}
			}
		case *syntax.CallExpr:
			if len(x.Assigns) > 0 {
				ws := make([]*syntax.Word, 0, len(x.Assigns)+len(x.Args))
				for _, a := range x.Assigns {
					ws = append(ws, s.mark(s.assignText(a)))
				}
				x.Args, x.Assigns = append(ws, x.Args...), nil
			}
		case *syntax.DeclClause:
			for k, a := range x.Args {
				x.Args[k] = &syntax.Assign{Naked: true, Value: s.mark(s.assignText(a))}
			}
		case *syntax.FuncDecl:
			x.RsrvWord, x.Parens = false, true
			x.Name = &syntax.Lit{Value: wo + x.Name.Value + wc}
			if _, ok := x.Body.Cmd.(*syntax.Block); !ok {
				x.Body = &syntax.Stmt{Cmd: &syntax.Block{Stmts: []*syntax.Stmt{x.Body}}}
			}
		case *syntax.WordIter:
			x.Name = &syntax.Lit{Value: wo + x.Name.Value + wc}
		case *syntax.CStyleLoop:
			init, next := s.clause(int(x.Lparen.Offset())+2, x.Init)
			cond, next := s.clause(next, x.Cond)
			post := s.src[next:x.Rparen.Offset()]
			x.Init, x.Cond, x.Post = s.mark(init), s.mark(cond), s.mark(post)
		case *syntax.LetClause:
			for k, e := range x.Exprs {
				x.Exprs[k] = s.mark(s.span(e.Pos(), e.End()))
			}
		}
		return true
	})
}

func hdocText(s *state, w *syntax.Word) string {
	var b strings.Builder
	for _, part := range w.Parts {
		if lit, ok := part.(*syntax.Lit); ok {
			b.WriteString(lit.Value)
		} else {
			b.WriteString(s.span(part.Pos(), part.End()))
		}
	}
	return b.String()
}

// delimiter is the closing line bash takes for a heredoc word: its quotes removed as bash removes them, $'...'
// decoded. mvdan refuses an expansion in a heredoc word, so every other part is a literal.
func (s *state) delimiter(w *syntax.Word) string {
	var b strings.Builder
	for _, part := range w.Parts {
		switch x := part.(type) {
		case *syntax.SglQuoted:
			if x.Dollar {
				lit, _ := expand.Literal(nil, &syntax.Word{Parts: []syntax.WordPart{x}})
				b.WriteString(lit)
			} else {
				b.WriteString(x.Value)
			}
		case *syntax.DblQuoted:
			for _, in := range x.Parts {
				b.WriteString(unescape(s.span(in.Pos(), in.End()), func(c byte) bool {
					return strings.IndexByte("$`\"\\\n", c) >= 0
				}))
			}
		default:
			b.WriteString(unescape(s.span(part.Pos(), part.End()), func(byte) bool { return true }))
		}
	}
	return b.String()
}

// unescape drops each backslash before a byte it escapes, and a backslash-newline whole.
func unescape(v string, escapes func(byte) bool) string {
	var b strings.Builder
	for i := 0; i < len(v); i++ {
		if v[i] == '\\' && i+1 < len(v) && escapes(v[i+1]) {
			if i++; v[i] == '\n' {
				continue
			}
		}
		b.WriteByte(v[i])
	}
	return b.String()
}

// closing is the printer's own closing line, the marked delimiter word written right after a body's closing line.
var closing = regexp.MustCompile("\x04([^\x01-\x04\n]*)\x01[^\x02]*\x02")

// printNode prints with spaces for indentation, so mvdan prints a <<- body verbatim rather than re-indented with tabs.
func printNode(n syntax.Node) (string, string) {
	var b bytes.Buffer
	if err := syntax.NewPrinter(syntax.Indent(4)).Print(&b, n); err != nil {
		return b.String(), err.Error()
	}
	return closing.ReplaceAllString(b.String(), "\x04$1"), ""
}

type pend struct {
	kind  string
	depth int
	inner string
	stmts []*syntax.Stmt
}

func isCStyle(f *syntax.ForClause) bool {
	_, c := f.Loop.(*syntax.CStyleLoop)
	return c
}

func (s *state) hdocOf(x *syntax.Stmt, rd *syntax.Redirect) hdoc {
	h := hdoc{Op: rd.Op.String(), OpOff: int(rd.OpPos.Offset()), Words: []string{}, BodyOff: -1, BodyEnd: -1}
	if rd.Word != nil {
		h.Delim = s.span(rd.Word.Pos(), rd.Word.End())
	}
	if rd.Hdoc != nil {
		h.BodyOff, h.BodyEnd = int(rd.Hdoc.Pos().Offset()), int(rd.Hdoc.End().Offset())
		h.BodySpan = s.span(rd.Hdoc.Pos(), rd.Hdoc.End())
		h.BodyText = hdocText(s, rd.Hdoc)
	}
	if c, ok := x.Cmd.(*syntax.CallExpr); ok {
		for _, a := range c.Args {
			h.Words = append(h.Words, s.span(a.Pos(), a.End()))
		}
	}
	return h
}

func (s *state) read(f *syntax.File, o *out) []pend {
	tags := map[string]bool{}
	var subs []pend
	depth := 0
	var stack []syntax.Node
	units := []int{-1}
	syntax.Walk(f, func(n syntax.Node) bool {
		if n == nil {
			top := stack[len(stack)-1]
			stack = stack[:len(stack)-1]
			switch top.(type) {
			case *syntax.CmdSubst, *syntax.ProcSubst:
				depth--
				units = units[:len(units)-1]
			}
			return true
		}
		stack = append(stack, n)
		switch x := n.(type) {
		case *syntax.ForClause:
			switch {
			case x.Select:
				tags["select"] = true
			case isCStyle(x):
				tags["cfor"] = true
			default:
				tags["for"] = true
			}
		case *syntax.WhileClause:
			if x.Until {
				tags["until"] = true
			} else {
				tags["while"] = true
			}
		case *syntax.IfClause:
			tags["if"] = true
		case *syntax.CaseClause:
			tags["case"] = true
		case *syntax.FuncDecl:
			tags["function"] = true
		case *syntax.Subshell:
			tags["subshell"] = true
		case *syntax.Block:
			tags["brace"] = true
		case *syntax.TestClause:
			tags["test"] = true
		case *syntax.ArithmCmd:
			tags["arith"] = true
		case *syntax.CoprocClause:
			tags["coproc"] = true
		case *syntax.TimeClause:
			tags["time"] = true
		case *syntax.LetClause:
			tags["let"] = true
		case *syntax.DeclClause:
			tags["decl"] = true
		case *syntax.CmdSubst:
			k, skip := "cmdsubst", 2
			if x.Backquotes {
				k, skip = "backquote", 1
			}
			tags[k] = true
			inner := s.span(syntax.NewPos(x.Left.Offset()+uint(skip), 0, 0), x.Right)
			units = append(units, len(subs))
			subs = append(subs, pend{k, depth, inner, x.Stmts})
			depth++
		case *syntax.ProcSubst:
			tags["procsubst"] = true
			inner := s.span(syntax.NewPos(x.OpPos.Offset()+2, 0, 0), x.Rparen)
			units = append(units, len(subs))
			subs = append(subs, pend{"procsubst", depth, inner, x.Stmts})
			depth++
		case *syntax.Stmt:
			if x.Negated {
				tags["negated"] = true
			}
			for _, rd := range x.Redirs {
				switch rd.Op {
				case syntax.Hdoc, syntax.DashHdoc:
					tags["heredoc"] = true
					h := s.hdocOf(x, rd)
					h.Unit = units[len(units)-1]
					o.Hdocs = append(o.Hdocs, h)
				case syntax.WordHdoc:
					tags["herestring"] = true
				}
			}
		}
		return true
	})
	for t := range tags {
		o.Tags = append(o.Tags, t)
	}
	sort.Strings(o.Tags)
	return subs
}

func clip(msg string) string {
	if len(msg) > 200 {
		return msg[:200]
	}
	return msg
}

func gateRow(p *syntax.Parser, r in) out {
	o := out{I: r.I, Tags: []string{}, Subs: []sub{}, Hdocs: []hdoc{}}
	f, err := p.Parse(strings.NewReader(r.Cmd), "")
	if err != nil {
		o.Err = clip(err.Error())
		return o
	}
	if _, defects := buildTree(f, r.Cmd); len(defects) > 0 {
		o.Err = clip("tree: the tree misses " + defects[0])
		return o
	}
	o.Ok = true
	s := &state{src: r.Cmd, done: map[*syntax.Word]bool{}}
	subs := s.read(f, &o)
	s.transform(f)
	o.Print, o.PErr = printNode(f)
	for _, ps := range subs {
		for _, st := range ps.stmts {
			s.transform(st)
		}
		pr, pe := printNode(&syntax.File{Stmts: ps.stmts})
		o.Subs = append(o.Subs, sub{Kind: ps.kind, Depth: ps.depth, Inner: ps.inner, Print: pr, Err: pe})
	}
	return o
}

func gateMode(stdin io.Reader, stdout io.Writer) {
	p := newParser()
	sc := bufio.NewScanner(stdin)
	sc.Buffer(make([]byte, 1<<20), 64<<20)
	enc := json.NewEncoder(stdout)
	for sc.Scan() {
		var r in
		if err := json.Unmarshal(sc.Bytes(), &r); err != nil {
			panic(err)
		}
		if err := enc.Encode(gateRow(p, r)); err != nil {
			panic(err)
		}
	}
	if err := sc.Err(); err != nil {
		panic(err)
	}
}
