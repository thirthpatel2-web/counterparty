# Evaluation protocol (v2)

Committed before any v2 live run, so results can't be chosen after seeing them.

## What is frozen

- **Prompt:** `PROMPT_VERSION = "v3-frozen-2026-11-06"` in `engine/brains.py`. Every run records its SHA-256 and the git commit.
- **Model:** `openai/gpt-oss-120b` on Groq, with temperature 0 and low reasoning effort.
- **Simulator:** `engine/simulator.py` as of this commit, with bluff and walk-away rounds that vary per deal.
- **Memory:** outcomes only, with no lessons. Hindsight `reflect` writes the briefing the buyer negotiates from.

## Runs

Seeds 101, 102, 103, 104 and 105, in that order, with 24 deals each:

```bash
python scripts/run_race.py --deals 24 --seed 101
```

A run with any fallback decision is invalid, and its seed is rerun once the provider's quota allows. Runs are never dropped for being unflattering. `python scripts/run_race.py --report` lists every valid run in the README.

## Comparisons

All arms face the same vendor on the same seed:

| Arm | Memory | LLM | Purpose |
| --- | --- | --- | --- |
| Hindsight buyer | Hindsight | Yes | What we built |
| Rule | None | No | The strongest fixed strategy: offer low, accept the first "final" |
| Learner | Exact records | No | Hand-written rules over perfect memory. The bar an LLM with memory should clear |
| Oracle | Knows hidden traits | No | The ceiling |

## Primary result

The primary result is the mean per-deal difference between the Hindsight buyer and the rule. Its interval is taken across runs, because deals within a run aren't independent. Learning is judged on the last third of each run.

## What would change our claim

- **Interval entirely below zero:** memory beats the rule.
- **Interval crossing zero:** we can't tell them apart, and we say so.
- **Interval above zero:** memory loses to the rule, and the README says that.

## Earlier runs

Runs recorded under protocol v1 are kept in `runs/archive-v1`. v1 had a memory-labelling bug that stored some bluffs as true claims, and its prompt was changed between runs, so v1 results aren't used for any claim.

## Amendments

1. **Before any valid run:** the first attempt at seed 101 stopped at its first briefing, because Hindsight's reflect returns HTTP 500 for nullable types in `response_schema`. The briefing schema now uses integers, with 0 meaning unknown. A vendor with no history gets no briefing, and a failed briefing is counted in the run as `brief_failures` instead of stopping it. The prompt, model, simulator and seeds are unchanged.
