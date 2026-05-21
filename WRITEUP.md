# Write-up

## How I interpreted the problem

The brief is framed as "turn bad reports into good tickets," but the support agent's actual pain is bidirectional: they get vague reports *and* they have to chase reporters for details. A tool that emits a "good ticket" no matter what the input looks like would just push the chasing problem onto the engineer instead of onto the reporter — same problem, different victim. So I treated the script's job as deciding **what the next action should be**, with three possible outputs per report: file an actionable ticket, hand back a structured clarification request (with a draft reply), or classify it as not-actually-a-bug. The clarification path directly addresses the line in the brief about wishing the agent could "hand it off and get a proper ticket back" — sometimes the right hand-off is back to the reporter, with the right questions pre-written.

## Key decisions

- **Three output modes, discriminated by `status`.** Forces the script to commit to a routing decision per report rather than producing a uniformly-shaped ticket that's secretly empty.
- **Extraction (LLM) is separated from judgment (code).** The LLM pulls a structured `ExtractionResult` — including a self-reported `extraction_confidence` and an explicit list of `missing_critical_fields`. My code then decides routing and priority based on those fields. This makes the routing logic readable, testable, and tunable without prompt engineering.
- **Priority is a deterministic function of signals, not an LLM judgment.** `assign_priority(signals, scope)` is a pure function with explicit rules (data loss → P0, blocked + many → P0, blocked + some → P1, etc.) and it returns a rationale that cites which signals fired. An engineer can disagree with my rules without disagreeing with the signals, and the priority logic has 15 unit tests that don't touch the API.
- **Refuse to hallucinate reproduction steps.** The extraction prompt explicitly forbids synthesizing plausible steps from a symptom description. A ticket with `reproduction_steps: null` and a clarification request is more useful than one with confidently-wrong steps.
- **The clarification output includes a draft reply.** Specific questions plus a ready-to-send message back to the reporter — because the agent's complaint was as much about chasing as it was about ticket quality.
- **Multi-issue handling: flag, don't split.** When a single report describes three bugs, the extraction picks the most actionable as the primary and sets `multiple_issues_detected=true` with a note. Splitting reports would be a real feature but creates duplicate-management problems I don't want to half-solve.

## What I'd do differently with more time

- **Calibrate the vagueness threshold against real data.** Right now I require either reproduction steps OR (expected + actual behavior) AND confidence ≥ 0.5. That's my best guess. Real labeled data — "would an engineer act on this as-is?" — would let me tune both criteria.
- **Eval harness with rubric scoring.** Did the priority match the human label? Was the title scannable? Were the clarification questions specific enough to actually unblock work? Without this it's hard to iterate on prompts confidently.
- **Dedupe / similarity clustering across the batch.** The brief says don't worry about plumbing, but in practice a queue has duplicates and clustering them up-front matters more than any single-ticket quality improvement.
- **Actually split multi-issue reports** rather than flagging-and-picking-primary.
- **A confidence-aware retry path** — borderline extractions retry with a stronger model or a different prompt before falling through to clarification.

## Most / least confident

**Most confident:** the three-output-mode structure and the extraction-vs-judgment split. These shape the rest of the design and I'd defend them in a follow-up conversation. The deterministic priority function is also clearly correct in principle — even if the specific rules are imperfect, the *form* (rules over signals, with cited rationale) is right.

**Least confident:** the specific vagueness threshold (it's a product call that wants real data behind it), the exact priority rule cutoffs (I'd calibrate these with the on-call team in a real role), and a few of the LLM extraction edge cases — for example, "I deleted everything and now it's gone" is genuinely ambiguous between user error and a missing-confirmation bug, and I lean toward treating it as a bug-with-clarification, but I could be talked out of it.
