# Headnote prompt used for this pilot (reconstruction, not the repo's text)

The repo's prompt (data/prompts/headnotes.md, sent to Haiku as INSTRUCTIONS.md under
headnotes-circuits-v1) could not be read from here. This prompt was rebuilt from what the
Haiku run's own transcript shows about the rules and the answer shape. Where the Haiku
agents reported the rules differently, the stricter version is used.

## Task
You are given one published decision of a federal court of appeals. Write headnotes for it:
short, self-contained statements of the rules of law the court decided.

## Rules
1. Write 1 to 5 headnotes. Fewer is better than padding: one per distinct holding.
2. Headnote the opinion of the court only. Pass over concurrences and dissents, and give no
   headnote for a point the court says it does not reach or is not properly before it.
3. Each headnote states a rule of law the court applied or announced, in general terms
   (no party names), in 60 words or fewer, as one or two plain sentences.
4. Each headnote cites the span of the opinion it comes from as first_paragraph and
   last_paragraph: integers, first <= last, both numbers that appear as [N] in the text.
5. concept_slugs: up to 3 topic slugs from the taxonomy; never a topic together with its
   own subtopic; an empty list when nothing fits. (In this pilot the 681-slug taxonomy was
   not available, so slugs are guesses in the taxonomy's style and must be remapped.)
6. A text with no holding (certification order, bare affirmance, order on rehearing,
   amending order) gets one headnote saying what was ordered, with concept_slugs [], and is
   flagged so it can be dropped, as the Haiku run drops them.

## Answer shape (exactly this object, nothing else)
{"headnotes": [{"text": "...", "concept_slugs": ["..."], "first_paragraph": 3, "last_paragraph": 5}]}

## Input layout (as the Haiku requests are built)
Decision: <case name>, <citation> (<year>).
Lead opinion: <type words>[, by <author>]. <N> paragraphs.

[1] first paragraph

[2] second paragraph ...
Long opinions are cut to about 48,000 bytes: roughly the first 36,000 and the last 12,000.
