# The review shorthands: FREV, XREV, DREV and the runs that work them

Six words, valid in every repository. Three of them open a review round by creating tasks and then stop; the other three work a round to the end of its list. A repository that carries its own tracked contract or generator for one of these (look for `scripts/frev_contract.md`, `scripts/frev_tasks.py`, `scripts/drev_sweeps.py` or their equivalents before acting) is held to that file and its tooling; this text is the definition of the words and the contract a repository without one is held to.

| word | what it does |
|---|---|
| `FREV <dir>` | creates one task per file of the directory, each carrying the file review contract, and stops |
| `XREV <dir>` | creates tasks to review the directory as a whole, each carrying the directory review contract, and stops |
| `DREV <doc-or-dir>` | creates one task per document, each carrying the document review contract, and stops |
| `DOFREV` | works every FREV task in turn to the end of the list |
| `DOXREV` | works every XREV task in turn to the end of the list |
| `DODREV` | works every DREV task in turn to the end of the list |

Tasks live in the session's task list where the harness offers one, otherwise in the repository's own task store. A task written today may be worked months later, so it carries its contract whole rather than a reference to it.

## Opening a round creates tasks and does nothing else

The whole of an opening request is: enumerate, create the tasks, report the range of task ids, stop. No file is opened, no review starts. A FREV that starts editing while the list is half built reviews the first files with the whole directory still unread, and the tasks it never got round to creating are invisible rather than pending.

For FREV the enumeration is every tracked file of the directory, `CMakeLists.txt`, `.clang-tidy`, linker scripts and `.cmake` fragments included, and it is exactly one task per file: never a bundled pair, never a group. A task holding two files submerges the review in code, and the second file gets the attention left over from the first. A header and its source are two tasks even where they are one subject. Task creation for one of these words is requested by definition, so it needs no further permission.

## FREV: the file review contract

### The unit of review is the claim

Every guarantee the file states is a claim: a comment saying what a function refuses, holds or never does; a `[[nodiscard]]`; a `static_assert` and its message; an error enumerator; a documented row the file implements; an invariant a header spells. The task's required artifact is a table with one row per claim: the claim, and the guard that goes red without it (a test case, a mutation the repository's sweep names, a gate arm, a model-checker arm). A row whose guard column reads none is the finding, and the fix is the guard, a failing-first test and a sweep entry where the repository has one, never a sentence saying the claim is true.

### The eleven points, each answered in the report

1. Line per line: the whole file is read, whatever an earlier pass concluded, and each line is asked what it does and whether it needs to.
2. C++ Core Guidelines, C++23 best practices, idioms and patterns.
3. Modernize, idiom only: any change that could move behaviour needs a gate that goes red before it and green after. No `NOLINT` without a measured, single-site reason, no `#define`, no build-file edit to make a construct compile, `template <typename T>` and never `class`. A file that is not C++ (CMake, bash, YAML, Python, markdown) is brought to its own language's idiom under the same rule.
4. Idioms from a catalogue where the repository keeps one pinned to its toolchain; otherwise the net, searched for the pattern and never with repo code, identifiers or paths in the query. Each is a candidate checked against the file, never a verdict.
5. Value semantics unless performance is paramount, and paramount means measured: a reference, a pointer or a borrowed view earns its place with a number.
6. RAII for every resource: descriptor, mapping, lock, table entry, registration, handle. Every release outside a destructor is either a class's own release path or a finding, and the question is asked of the whole file rather than only of what already looks like a resource.
7. Dedup the code and the comments. A clone inside one file is that file's finding; one spanning files becomes an XREV task rather than widening this one.
8. Every comment against ground truth. An identifier, path or test name a comment cites that resolves nowhere is a finding before the file is read; a claim about behaviour is checked against the code and the tests, never against another comment.
9. No history: comments describe the current state, git holds the past.
10. A simpler, more performant or more idiomatic implementation that exists is used.
11. Precise, concise comments a reader with a short attention span can read, and the comment count is measured: see the next section.

### Compression has a number

The report states code lines and comment lines before and after. A task may not leave a file with more comment lines than it found unless the same task fixed a defect in that file. The round ends with the ratio table over the whole directory beside the table at its base.

### A comment block edited in two of the last three rounds is frozen

It is edited again only for falsity, with the source line that shows it false quoted in the commit; reading better is not a reason. The churn between rounds, each rewriting the last one's prose, is what this rule stops.

### Per task: the sweep, not the anchor check

After every edit that lands, run whatever the repository has that watches the file: the mutation sweep over every mutation naming the file, each of which must read KILLED; the model-checker gate where the file is one it compiles; a fresh configure where a member was renamed, a public header added or an include changed, because configure-time gates never run on an incremental build; and every gate that holds a claim about the file. An anchor that still resolves is not a verdict: a reflowed line can leave the anchor resolving and the mutation equivalent, or the test no longer failing.

### Everything is truth-grounded: the claim, the finding, the replacement, the report, the commit message

Nothing enters or leaves a task on the strength of being plausible. A lens hit, a catalogue idiom, a guideline rule or a suspicion from reading is a candidate until a probe runs against the file: compile the block, run the test with the line mutated, run the sanitizer, read the standard or guideline the comment cites and print its clause, grep the tree for the identifier. A candidate the probe dismisses is recorded as dismissed in the review's record, with the probe's text and its output, because the next round will suspect the same line. Measure every number in the report, code lines, comment lines, mutation identifiers and timings included, with the shell substituting the measurement rather than a number typed by hand.

The replacement is under the same rule, which is the half that gets skipped: every changed line of a fix is its own claim and owes its own guard, a test or a gate arm that fails without it, and a guard that cannot go red is not one. Re-read the replacement whole against the source the finding came from, never through `cut` or a diff hunk, before the commit. A guideline cited in a finding is cited by its number, with the clause printed, never from memory.

### Round start and round end

At round start, before the first task: the repository's gate audit over the component, its mutation sweep over the directory, and every mechanical lens it offers over the directory, kept beside the round's record so the end of the round is diffed against it. At round end the same, plus a check that every completed task's report is in the shape below.

### The report shape

Every task ends with a report in this shape, in the task's own description, so "reviewed, no change" is auditable point by point. Each numbered line reads `checked` with the evidence, `n/a` with the reason, or `finding` with the commit.

```
REPORT <date> tree <commit> <commit of the fix | NO CHANGE>
claims: <rows> rows, <rows without a guard> without a guard: <what was added, or none>
1 line per line: ...
2 guidelines: ...
3 modernize: ...
4 catalogue: ...
5 value semantics: ...
6 raii: ...
7 dedup: ...
8 ground truth: ...
9 history: ...
10 simpler: ...
11 comments: <comment lines before> to <after>, code <before> to <after>
sweep: <mutation idents> KILLED | no mutation names this file
gates: <gates added, each shown red first> | none
decision points: none | appended to the accumulator
```

### Repeat passes

The first read of every task is the previous round's own added lines to the file, `git diff <previous base>..<previous end> -- <file>`, because that is where the last rounds' findings were. Line per line still holds: every file is read whole, changed since that round or not.

## XREV: the directory review contract

The cross-file view, where FREV is the file-at-a-time one: a per-file pass cannot see a rule spelled once per file, an interface that is awkward only from a call site, or a shape three files share. Each task carries:

- design, interfaces, patterns, idioms, modernization
- idioms that apply to the directory's shape: a type the files could share, an interface a newer idiom removes the need for; from the repository's catalogue or from the net, searched as a pattern and never with repo code, identifiers or paths, and treated as a candidate to check against the tree
- value semantics across the directory's interfaces unless performance is paramount, and paramount means measured. Ask it of the call sites: a reference, a pointer or a borrowed view crossing a boundary earns its place with a number, and an owner passed by handle through three files is a lifetime nobody in those files can see
- RAII for resource management, and what the directory releases by hand that one class could hold: a descriptor, mapping, lock, table entry or registration released in several files is the dedup point reached through lifetimes rather than through text
- consistency and compatibility within the directory, usability
- ease of use at the call sites
- memory leaks, UBSan, ASan
- coverage against the repository's floors, per file
- everything checked against ground truth instead of assumed: a candidate is a finding when its probe runs red, the claim lands as a gate shown red on the defect, and the rules under the gate section below bind every task
- dryness: remove duplicates, and remove similar code present in several files; state which files a finding covers, since the dedup half is the one that most often spans a task boundary

## DREV: the document review contract

Each task names one document and reviews it against nine points.

- **Correctness**: check every claim against a probe or against the standard it cites. Verify it that way; a claim a gate can hold gets the gate, and what the probe ran and printed is quoted in the report, never in the document. A sentence nothing backs is a finding.
- **Redundancy**: remove what the document already says elsewhere. One statement, one place.
- **Clarity**: terse and precise prose, written for a human to read.
- **Checkability**: make interfaces and requirements explicit, so a reader can test whether an implementation meets them.
- **Implementability**: every interface and requirement corresponds to an element of the language, not to an intention.
- **Précis mais concis**: every pass makes the file shorter. A shorter document beats a longer one carrying the same decisions.
- **One line per paragraph**: reflow so that each paragraph and each list item is a single line, however long.
- **Proof-read**: read the finished file before declaring done. Not the diff, the file.
- **Diagrams**: every figure the document embeds is reviewed like a section.

### Three granularities, none skipped

The reading is done three times: sentence by sentence, then paragraph by paragraph, then section by section. At each one, ask the same four questions, is it accurate, is it readable, is it relevant, is it brief, because each granularity answers them about a different thing and a defect at one is invisible from the others.

A **sentence** is checked against the source it claims from: a `<` where the code says `<=`, an answer left behind when the question it answered was reworded, a term used before the document defines it, a name introduced with nothing said about what kind of thing it is, a count spelled as a word, a qualifier or pronoun a reader binds to the nearest noun rather than the intended one, a default described in words where a number sits behind it.

A **paragraph** is checked for having one subject: an identity welded to a status by "and", three ideas joined by a pivot that reads as a contradiction, a long aside inside a dash pair, a bare paragraph among bolded neighbours that everyone scanning will skip. A paragraph carrying a second bold lead mid-line, or one far longer than its neighbours, is split at the subject change with no word changed.

A **section** is checked for being one, and for earning its place: two subjects under one heading, topics alternating so the reader switches four times, a heading naming terms the section never defines, a whole section restating what three others already said, a section whose purpose turns out to be a change list rather than a statement of what is, a lead that contradicts the figure the same section embeds.

### A diagram is part of the document and is read at all three granularities

Every box, edge, label and annotation is a claim the document makes, and each is checked the way a sentence is.

- **Accurate**: check each element against the source, not against what the figure was drawn to say. An arrow at the wrong box, a state carrying a transition the code refuses, a label naming a field that has moved, an edge asserting a dependency the build does not have. A figure is the easiest place for a stale claim to survive, because nothing compiles it and prose review does not look at it.
- **Relevant**: the figure earns its place by carrying a claim the prose beside it strains to make. A picture restating one sentence is redundancy in another medium and is cut; two figures making one claim are one figure. A caption that opens on a topic label and then lists what the picture shows is the commonest figure defect; the caption states the claim.
- **Readable, which means rendering it and looking**: an SVG that parses is not an SVG that reads. Text overflowing its box, an arrow crossing a label, a line off the canvas, two annotations landing on each other: none of that is visible from the markup. Render every figure the pass touched and read the image at the width a reader sees it at.
- **A missing edge is a finding, and the answer is to draw it**: a state a reader can reach in the system and not in the figure is a claim the figure denies. Route the new edge around the outside so the drawing stays planar, put its label under its own arrow, then render and read it.
- **Clear**: the title states the claim rather than the topic, the alt text and the SVG's own `<title>` are that same text, and every term in the figure is one the document already defines. A caption rewritten without its `<title>` is a silent mismatch; move both in one edit and re-render.

### Everything is truth-grounded: the finding, the replacement, the report, the commit message

Nothing enters or leaves a pass on the strength of being plausible. A defect noticed is a candidate: run the probe, read the specification, grep the tree, compile the block. Measure every number reported, word counts and paragraph counts included, with the shell substituting the measurement rather than a number typed by hand; a relative position ("four lines down", "the row above") is a count too. And the text that replaces a finding is under the same rule, which is the half that gets skipped: every clause of the replacement is its own claim and owes its own source. Re-read the replacement against the source the finding came from, whole and never through `cut`, before the commit.

A citation finding is not written until the target's own lines are printed: never infer a target's contents from a lead enumeration, a heading, or a grep whose range was chosen by hand. And sweep every link's text, not only the anchors that fail: an anchor that resolves can still name the wrong document.

### A repeat pass on a document already reviewed skips nothing

The whole file is read again, at all three granularities, against all nine points. Prior verification expires, because every pass edits text that other sentences rest on, and because each granularity reads with a lens the last one did not. The first read of a repeat pass is the previous pass's own added lines, because that is where the findings are; where the tree moved by a code change, list what moved (headers per component, rosters, test paths) and grep every spelling of the old state corpus-wide, the code's own comments included, before reading any document.

### No step is optional, and the one that gets skipped is compression

Every pass runs all nine, on that pass, not "this pass found facts, the next one will compress". Deferring it is how the rule fails while appearing to be followed: each pass finds real defects, adds prose to fix them, puts compression off, and the file grows under a contract whose whole point is that it must not. Announcing the deferral in the report does not license it. If a pass genuinely finds no redundancy, say so having looked, with per-section word counts measured, rather than by not looking.

**What compression cuts:** restatement of a rule already decided elsewhere; rhetorical emphasis, which competes with itself once every paragraph shouts; rejected alternatives argued at length where a sentence carries them; a sentence that links the section owning a claim and then states the claim as well, where nothing after it draws on the restatement; and any history at all.

**What it never cuts:** a decision, a measurement, an interface, a refusal, or a restatement the paragraph's own conclusion rests on. A measurement survives; the probe that took it does not belong in a design document.

**How to look for redundancy, because a literal sweep reports clean on a document that is not:** match on similarity rather than on exact n-grams, run it over sentences and over list items (recap-style duplication lives in short bullets), strip link targets before believing a similarity score, read the document's bolded assertions as an index to compare claim against claim, and where two documents carry one fact, read the two spellings side by side and ask which the source supports. A cut is owed a reason the item does not earn its place, not merely that the words appear twice.

**A cut can break a cross-reference somewhere else.** Two sections citing each other look like mutual restatement, and trimming one half leaves the other pointing at a claim the target no longer makes, silently, because the link still resolves. So the check is not that the target exists but that it carries the claim the citing sentence attributes to it: grep the target for the words, per reference, after compressing. A direction word ("above", "below") beside a same-document link is checked by comparing line numbers.

**How to compress safely: inventory first, verify after.** Extract every decision the document makes into a checklist before rewriting, then check the compressed text against it. A batched replace that matches nothing is a silent no-op, so verify each edit landed.

**An enumeration is sized from, so count the source.** A prose list naming five of a checker's refusals reads as the whole set; read the checker, count, and check every downstream sentence that quantifies over the list. A sentence that partitions a set has a third group; list the code's own members and subtract. Widening a partial enumeration is allowed to grow the file.

**A number a later edit moves is a number not to write.** Prefer the name over the count and the position: no numbered headings, no relative positions, no totals a later edit adds to. Where a count is the claim it stays and is measured; where it is navigation, name the thing.

### What the reflow leaves alone

Headings, tables, code blocks, and any blockquote whose line breaks are the content: a listing, a loop, a sequence of separate statements. A blockquote can hold both kinds at once, so join only the lines that are wrapped prose, by hand if the two cannot be told apart mechanically. A line-length check retires with the reflow, the two rules being opposites.

### What the proof-read is for

The edits themselves introduce defects none of the axes looks at: prose left arguing for a field the code block above it no longer has, a clause duplicated by a replacement that ran twice, a sentence a rewrite cut in half, a dash the pass wrote into a sentence it rewrote. Reading the finished text is the only thing that finds them, and the task's own rules list is read back against every touched line before the commit.

### Where the repository keeps sweeps and gates

Run every lens it offers at the base and at the run end (word counts per section, similarity, cross-document n-grams, history and judgement-word markers, cited paths and identifiers, contents lists slugged against headings both ways, captions in one grep) and diff the two, row by row rather than by total: a total that falls can still hide a new row. A pair of documents whose shared text rose is attributed gram by gram before being called a fix written twice. A set the review corrects by hand is one the next round corrects again; where the repository can gate a set or pin a fact to one owner, add the gate and stage it by breaking it. A second identical false-positive judgment is an exemption in the repository's exemption file, never an edit. A paragraph edited in two of the last three rounds is frozen, falsity re-opens it. Nothing a round adds may be text an earlier round removed; a hit is a decision point.

Commit messages are part of the record: written with a heredoc, never through a quoting route that eats apostrophes, with every number shell-substituted.

## DOFREV, DOXREV, DODREV: working a round

The three runs share one discipline; the unit of review differs.

- Take each task of the round in order and execute it completely, to the whole of its contract, on that pass rather than deferred. Do not merge tasks and do not run several at once.
- A finding that belongs to a later existing task is pushed into that task's description. One that needs a task of its own becomes a new task in the round: a clone spanning files becomes an XREV task, a finding in another document goes to that document's task.
- **A follow-up created during the run is part of the run**, follow-ups of follow-ups included. The run ends when the list is empty, not when the originally listed files are done.
- Commit per task, with the measurements in the message, and write a short per-task report into the task. **Neither ends the turn.** The end of a report is the middle of the run: the next action after it is the next task, in the same turn, with no prose between. A "Next: #N" sign-off is the failure mode this rule exists to stop; announcing the next task is not starting it.
- Mark each task completed with what was verified and the gates it added, each named and shown red first, "reviewed, no change" included when that is the honest outcome. Zero findings is a pass, and "this document is converged" is a valid task result.
- **Decision points are not asked as they are found.** They go into a durable accumulator, a task rather than a scratchpad file, so it survives compaction and session end. A point is written as its question and its alternatives in prose, each alternative named by what it proposes, never by a letter or a position. A point that only gates a fix is worked to the point the ruling gates (the measurement, the failing-first row, the analysis) so that only the ruling is left. The user sees them once, collated, when the list is empty. Order follow-ups so one that rewrites a file's tests lands right after that file's own review.
- A point still unruled when the round closes is recorded with the round, and no new round opens over it until it is ruled or exempted with a reason. A standing item copied from one accumulator into the next is what this rule exists to stop.
- While the list has a pending task, the only legitimate turn end is a real block: a tool that will not run, a permission the user must grant, or a decision the run genuinely cannot proceed without.

Two ends belong to a document run rather than to any one task: re-check every cross-reference after all the edits, for the claim the citing sentence attributes and not only for resolution, and run the cross-document duplication sweep at the end, the one class of redundancy a per-document pass cannot see.

## Gates and probes, for all six

Every property the project keeps is held by a gate: a test the suite runs, a step of the repository's CI, or a pre-commit or pre-push hook. A claim goes to the earliest stage whose time budget its check fits (pre-commit for seconds over what is staged, pre-push for what the pushed tree answers, CI for the rest), into a gate that already checks its family before a new one is made, and runs only when a change can move what it checks. A property of a tool or a toolchain that the configuration rests on is a gate too, run when that tool's pin changes. A gate is shown red on the defect it holds before it lands, since a gate that cannot fail holds nothing, and the commit that adds it names it. Neither the gates nor the probes may grow a long running time: a check that would need a build or a sweep of its own joins the lane that already makes one, and a check too slow for every stage, or too sensitive to its machine to gate a change, is put to the user as a question of what it brings rather than kept anywhere.

A gate that runs red after a change is a regression finding for the task that made the change, fixed before that task's commit. A gate red because what it held was removed on purpose is retired by the same commit, with the reason in the commit message. A gate is never edited to pass.

A probe is a small script written during an investigation, a review or a design, to ground a claim in a run or a measurement instead of a guess. It runs from the repository root with the repository's own toolchain, with no network and no path outside the tree, writes only beneath a scratch directory of its own, and opens with the claim it checks, what it reads, and what a non-zero exit means. A probe that can flake grounds nothing and is fixed before its output is quoted. It lives in the session's scratchpad or the review's own record, never in the tree, and is discarded once its finding lands as a gate; the report quotes what it ran and what it printed. A candidate a probe dismisses is recorded as dismissed in the review's record, with the probe's text and its output, because the next round will suspect the same line. The report names each gate and quotes each probe's run; a document under DREV does neither.

A repository that still keeps a probe store moves each claim in it to a gate, retiring the probe in the commit that shows the gate red and naming the gate there, until the store is empty and its runner goes.

## Evidence, for all six

Every finding is proven by a run, never reasoned: a failing-first test, or a gate shown red on the defect; documents, comments and prior claims are not evidence; the review is adversarial, so a quality that cannot be demonstrated is treated as absent. A fix the suite cannot fail is not a fix: mutate it away and confirm a test dies. No repo code, identifier or path leaves the machine. Nothing written names a review round, a decision point by number, an alternative by letter, or an entry of the working task list; commit bodies name no assistant or vendor, and the attribution trailers the repository's commit workflow prescribes (a co-author line and a session link) are kept; no em-dash anywhere.
