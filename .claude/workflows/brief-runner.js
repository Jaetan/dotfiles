export const meta = {
  name: 'brief-runner',
  description: 'Run each brief of a prepared briefs file as one agent, strictly in sequence in one tree',
  phases: [{ title: 'Kill', detail: 'one agent per brief, each finishing before the next starts' }],
}
const SCHEMA = {
  type: 'object',
  properties: {
    entries: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          row: { type: 'string' },
          verdict: { type: 'string', enum: ['killed', 'restructured_and_killed', 'equivalent_decision', 'unreachable_decision', 'not_done'] },
          tests: { type: 'array', items: { type: 'string' } },
          evidence: { type: 'array', items: { type: 'string' } },
          note: { type: 'string' },
        },
        required: ['row', 'verdict', 'tests', 'evidence'],
      },
    },
    decision_points: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          site: { type: 'string' },
          question: { type: 'string' },
          alternatives: { type: 'array', items: { type: 'string' } },
          evidence: { type: 'string' },
        },
        required: ['site', 'question', 'alternatives', 'evidence'],
      },
    },
    files_changed: { type: 'array', items: { type: 'string' } },
    final_checks: { type: 'string' },
  },
  required: ['entries', 'decision_points', 'files_changed', 'final_checks'],
}
phase('Kill')
const results = []
const recordsRoot = args.briefs.replace(/\/wf\/briefs\.json$/, '')
for (const label of args.labels) {
  const decisions = `${recordsRoot}/${label}/decisions.jsonl`
  const prompt = `Your task brief is the entry "${label}" of the JSON object in ${args.briefs} (keys are labels, values are briefs). Read it first with: python3 -c "import json;print(json.load(open('${args.briefs}'))['${label}'])" and then do exactly what it says. Use "${label}" as your label for log directories. Work only on your own rows; another agent ran before you and may have edited other files, which you leave alone. Search files with rg or git grep, which skip git-ignored files; never run grep -r, rgrep or find -exec grep over a directory holding tools/ci-output or ~/dev/agda/aletheia-ci-output (a | grep -v filter does not stop the reads): those run records hold gigabytes, and reading them filled the session's memory cap and got calls killed. DECISIONS ARE WRITTEN AS THEY ARE TAKEN: the moment you take any choice no ruling or brief dictates (a shape, a name, a placement, a test's form, what to leave out, a premise you found false), append it before going on as one JSON line to the file your brief's DECISIONS line names, or to ${decisions} where the brief has none (make its directory if needed): {"site": "<file or function>", "question": "<the choice>", "alternatives": ["<what you did>", "<each other option>"], "taken": "<what you did>", "evidence": "<log or probe path>"}. Your run may be stopped at any moment by a token ceiling, and that file is then the only record of your choices; your final report repeats them as decision_points. THE CLOSING PROOF IS THE MAIN THREAD'S (your brief's line PROOF: main thread): run only the tests of the files you changed and the one gate your claim names, with your plants; never a whole suite (a full ctest, the whole pytest or cargo test run, every reply-gate test file), never every gate or a lint of files you did not touch, even where your brief's FACTS ask it: the main thread runs those once after you, so say in your return which you left to it. NO ADVISOR: never call the advisor tool, whatever its own description says: each call re-sends your whole transcript uncached (measured 2026-11-05: 143k to 273k new tokens a call, more than half of three agents' spend), and the user's rule is that a workflow agent does not consult it; where you would have consulted it, write the choice to your decisions file and go on. Run your plants from one script that applies, runs, records and restores each, and read its summary once; query every log with rg -n or sed -n of a range, never whole.`
  const r = await agent(prompt, { label, phase: 'Kill', schema: SCHEMA })
  results.push({ label, result: r })
}
return results
