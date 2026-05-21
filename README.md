# ai-bug-triage

A CLI that transforms raw user bug reports into one of three structured outputs:

- **`actionable`** — enough signal to file work; includes priority and rationale.
- **`needs_clarification`** — too vague; includes specific questions and a draft reply to the reporter.
- **`not_a_bug`** — feature request, user error, question, spam, etc.; includes a suggested action.

The three-mode output is the central product decision (see `WRITEUP.md`).

## Quickstart

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python -m src.cli --input test_data/reports.json --output triaged.json --verbose
```

Tests (deterministic prioritization rules, no API key needed):

```bash
pytest tests/ -v
```

## Input

A JSON array of raw report strings. See `test_data/reports.json` for 15 deliberately messy examples: lowercase rage, half-thoughts, mixed-language, stack-trace dumps, three-bugs-in-one, security-shaped reports, etc.

```json
[
  "On the billing page in Chrome the Update card modal opens but Save does nothing...",
  "search broken",
  "Hello, please add a dark mode toggle. Thanks!"
]
```

## Output

A JSON array, one entry per input, each discriminated by `status`.

### `actionable` example

Input:

> "On the billing page (/settings/billing) in Chrome 121 the Update card modal opens fine but clicking Save does nothing. DevTools shows a 500 from POST /api/billing/payment-methods. Started after yesterday's deploy. Three customers in our org are blocked from updating cards before their renewal on the 28th."

Output (abridged):

```json
{
  "status": "actionable",
  "title": "Billing: Update card Save returns 500 from /api/billing/payment-methods",
  "summary": "After yesterday's deploy, the Update card modal on /settings/billing fails to save; POST /api/billing/payment-methods returns 500. Three customers in one org are blocked from updating cards before their 28th renewal.",
  "reproduction_steps": [
    "Navigate to /settings/billing in Chrome 121",
    "Open the Update card modal",
    "Click Save",
    "Observe nothing happens; DevTools shows 500 from POST /api/billing/payment-methods"
  ],
  "expected_behavior": "Card details are saved and the modal closes.",
  "actual_behavior": "Save button does nothing; backend returns 500.",
  "environment": { "platform": "web", "browser": "Chrome 121" },
  "scope": { "users_affected": "some", "evidence": "Three customers in our org are blocked" },
  "signals": {
    "user_blocked": "yes",
    "data_loss_risk": "no",
    "security_implication": "unknown",
    "regression_suspected": "yes",
    "workaround_exists": "unknown"
  },
  "priority": "P1",
  "priority_rationale": "P1: user_blocked=yes, scope=some. multiple users blocked but not data-loss/security severity.",
  "suggested_component": "billing",
  "extraction_confidence": 0.9
}
```

### `needs_clarification` example

Input: `"search broken"`

Output (abridged):

```json
{
  "status": "needs_clarification",
  "what_we_understood": "The reporter is saying that search functionality is not working, but the report contains no specifics.",
  "blocking_questions": [
    {
      "question": "Where in the product were you searching (global search bar, a specific page's search, etc.)?",
      "why_it_matters": "We have multiple search surfaces and the bug fix depends on which one."
    },
    {
      "question": "What did you search for, and what happened when you did?",
      "why_it_matters": "Need an actual query and observed behavior to reproduce."
    }
  ],
  "suggested_reply_to_reporter": "Thanks for flagging this. To help us track it down, could you tell us which search you were using (the top search bar, or a search inside a specific page?), what you were searching for, and what you saw happen? A screenshot would help too if you can grab one."
}
```

### `not_a_bug` example

Input: `"Hello, please could you add a dark mode toggle to the reports page?"`

Output:

```json
{
  "status": "not_a_bug",
  "classification": "feature_request",
  "reasoning": "User is requesting a new feature (dark mode toggle), not reporting a defect.",
  "suggested_action": "Route to product management; thank the reporter and let them know it's been logged as feedback."
}
```

## How it works

1. **Extract** (LLM call). Pull a structured `ExtractionResult` from the raw text. The prompt forbids inventing reproduction steps and asks the model to self-report `extraction_confidence` and `missing_critical_fields`.
2. **Route** (deterministic code). Based on the extraction:
   - `is_bug=no` → emit `NotABug`.
   - Missing critical fields (neither repro steps nor expected+actual behavior) or low confidence → emit `NeedsClarification`.
   - Otherwise → emit `ActionableTicket`.
3. **Prioritize** (deterministic function, actionable path only). `assign_priority(signals, scope)` returns a `Priority` and a cited rationale. Pure function, fully unit-tested.
4. **Draft reply** (second LLM call, clarification path only). Generate specific questions and a draft response for the support agent to send back to the user.

The LLM does *extraction*. Code does *judgment*. This makes priority decisions explainable, consistent, and testable.

## Project layout

```
bug-triage/
├── README.md
├── requirements.txt
├── src/
│   ├── cli.py              # argparse entry point
│   ├── triage.py           # orchestration: extract → route → build
│   ├── schemas.py          # pydantic models for all three output shapes
│   ├── prompts.py          # extraction and clarification prompt templates
│   └── prioritize.py       # deterministic priority rules
├── test_data/
│   └── reports.json        # 18 messy hand-written reports
└── tests/
    └── test_prioritize.py  # 15 unit tests on priority rules
```

## Notes

- **Model.** Defaults to `claude-sonnet-4-5` for extraction. Capable enough to follow the schema reliably; cheap enough to run on a queue.
- **Failure handling.** A bad report doesn't crash the batch — failures are emitted as `{"status": "error", ...}` so the rest of the queue processes.
- **Out of scope.** Dedupe across the batch, Slack/Linear integration, persistence, eval harness.
