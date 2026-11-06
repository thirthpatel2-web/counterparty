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

# Belief study (beliefs-v1)

Committed before any live belief study.

**Question:** does Hindsight learn the truth about each vendor from deal outcomes alone?

- **Evidence:** the rule-based learner (no LLM) plays 24 deals per study, and every outcome is retained in a fresh Hindsight bank. The learner pushes past "final" claims and finds walk-away rounds, so the evidence is informative and identical for every memory being tested.
- **Questions, per vendor:** does it bluff about "final"? At which round does it walk away? Is quarter-end cheaper? Each is checked against the simulator's hidden traits. Walk-away rounds vary by one, so either value counts as right. "Unknown" counts as not correct.
- **Checkpoints:** after each vendor's 1st, 2nd, 3rd, 4th, 6th and 8th deal.
- **Comparison:** exact arithmetic over the same records (`LocalMemory.brief`). This is the ceiling for what the records support.
- **Seeds:** 201, 202 and 203, in that order. `python scripts/belief_study.py --seed 201`
- **Reported:** every live study, pooled per checkpoint, with the range across studies.

**Known limit:** Bharat PCB only says "final" when its ask lands within 1% of its floor, and that doesn't happen in every deal. Some of its deals carry no "final" claim at all, so even perfect arithmetic can't always answer the bluff question for Bharat.

## Negotiation runs under protocol v2

Seed 101 was stopped by Groq's daily token limit after 19 of 24 deals, so it is not a recorded result. Its console output is kept in `runs/archive-v2-partial/seed-101-console.txt`. Across those 19 deals, the LLM buyer with Hindsight memory paid more than the rule on every deal, including the first deal, where it had no memory. The LLM negotiator itself is weaker than a one-line rule, and that is what we report.

## Disposition experiment

Committed before running. In study 201, the skeptical bank (skepticism 5) called honest Bharat PCB a bluffer for its first 6 deals. Hypothesis: the skeptical disposition causes that bias.

- **Design:** the same evidence goes to two Hindsight banks that differ only in skepticism, 5 and 3. Run with `python scripts/belief_study.py --seed 301 --neutral`, then `--seed 302 --neutral`.
- **Measure:** how many of Bharat's checkpoints each bank gets right on the bluff question, plus overall accuracy.
- These runs are reported separately and are not pooled into the main belief results.
