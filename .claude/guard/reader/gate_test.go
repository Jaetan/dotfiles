package main

import "testing"

// The gate print is text bash reads as the source means: a <<- keeps its dash and its body verbatim, a heredoc's
// closing line is the delimiter bash takes, a redirection to a process substitution keeps the space that stops
// bash reading a heredoc, and each C-style for clause keeps the blanks bash's reprint keeps.
func TestGatePrint(t *testing.T) {
	for _, c := range []struct{ name, src, want string }{
		{"dash heredoc", "cat <<-EOF\n\tx\n\tEOF\necho done",
			"\x01cat\x02 <<-\x01EOF\x02\n\x03\tx\n\t\x04EOF\n\n\x01echo\x02 \x01done\x02\n"},
		{"dash heredoc nested", "if a; then\n\tcat <<-E\n\t\tx\n\tE\nfi",
			"if \x01a\x02; then\n    \x01cat\x02 <<-\x01E\x02\n\x03\t\tx\n\t\x04E\n\nfi\n"},
		{"closing line single quoted", "cat <<'EOF'\n$x\nEOF",
			"\x01cat\x02 <<\x01'EOF'\x02\n\x03$x\n\x04EOF\n"},
		{"closing line single quoted backslash", "cat <<'a\\b'\nx\na\\b",
			"\x01cat\x02 <<\x01'a\\b'\x02\n\x03x\n\x04a\\b\n"},
		{"closing line double quoted", "cat <<\"E\\$F\"\nx\nE\\$F\nE$F",
			"\x01cat\x02 <<\x01\"E\\$F\"\x02\n\x03x\n\x04E$F\n\n\x01E$F\x02\n"},
		{"closing line double quoted kept backslash", "cat <<\"E\\OF\"\nx\nE\\OF",
			"\x01cat\x02 <<\x01\"E\\OF\"\x02\n\x03x\n\x04E\\OF\n"},
		{"closing line backslash", "cat <<E\\OF\nx\nEOF",
			"\x01cat\x02 <<\x01E\\OF\x02\n\x03x\n\x04EOF\n"},
		{"closing line ansi-c", "cat <<$'E\\x41'\nx\nE\\x41\nEA",
			"\x01cat\x02 <<\x01$'E\\x41'\x02\n\x03x\n\x04EA\n\n\x01EA\x02\n"},
		{"closing line line continuation", "cat <<E\\\nOF\nx\nEOF",
			"\x01cat\x02 <<\x01E\\\nOF\x02\n\x03x\n\x04EOF\n"},
		{"closing line empty body", "cat <<'EOF'\nEOF\ntrue",
			"\x01cat\x02 <<\x01'EOF'\x02\n\x03\x04EOF\n\x01true\x02\n"},
		{"closing lines two", "cat <<A <<'B'\na\nA\nb\nB",
			"\x01cat\x02 <<\x01A\x02 <<\x01'B'\x02\n\x03a\n\x04A\n\x03b\n\x04B\n"},
		{"redirection to process substitution", "while read l; do :; done < <(a)",
			"while \x01read\x02 \x01l\x02; do \x01:\x02; done < \x01<(a)\x02\n"},
		{"numbered redirection to process substitution", "diff <(a) >(b) 2> >(c)",
			"\x01diff\x02 \x01<(a)\x02 \x01>(b)\x02 2> \x01>(c)\x02\n"},
		{"redirection to a file", "ls >f 2>&1", "\x01ls\x02 >\x01f\x02 2>&\x011\x02\n"},
		{"c-style for blanks", "for (( i=0 ; i<3 ; i++ )); do :; done",
			"for ((\x01 i=0 \x02; \x01 i<3 \x02; \x01 i++ \x02)); do \x01:\x02; done\n"},
		{"c-style for tight", "for ((i=0;i<3;i++)); do :; done",
			"for ((\x01i=0\x02; \x01i<3\x02; \x01i++\x02)); do \x01:\x02; done\n"},
		{"c-style for empty", "for ((;;)); do break; done",
			"for ((\x01\x02; \x01\x02; \x01\x02)); do \x01break\x02; done\n"},
		{"c-style for separator in a substitution", "for ((i=$(echo 0;);i<1;i++)); do :; done",
			"for ((\x01i=$(echo 0;)\x02; \x01i<1\x02; \x01i++\x02)); do \x01:\x02; done\n"},
		{"c-style for newline", "for ((i=0;\ni<3; ));do :; done",
			"for ((\x01i=0\x02; \x01\ni<3\x02; \x01 \x02)); do \x01:\x02; done\n"},
	} {
		o := gateRow(newParser(), in{I: 1, Cmd: c.src})
		if !o.Ok || o.PErr != "" || o.Print != c.want {
			t.Errorf("FAIL-ASSERT row=%s path=print ok=%v perr=%q got=%q want=%q", c.name, o.Ok, o.PErr, o.Print,
				c.want)
		}
	}
}
