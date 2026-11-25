# us-legal-metadata

Machine-made metadata for US court decisions, keyed by CourtListener cluster id. It starts
with headnotes for federal court of appeals decisions (the Starfield citation graph), and is
laid out so the same repository can hold full runs over hundreds of thousands of decisions,
several kinds of metadata, and several producers (models, prompts, versions) side by side.

## Layout

```
schemas/
  run.schema.json                     the run manifest
  headnotes.record.v1.schema.json     one headnotes record
runs/
  <kind>--<YYYY-MM-DD>--<label>/      one folder per run, e.g. headnotes--2026-11-25--cloud-pilot-20
    run.json                          manifest: producer, prompt hash, input, selection, counts, caveats
    prompt.md                         the prompt, byte for byte (its sha256 is in run.json)
    records/<court_id>/<yyyy>.jsonl   one line per decision, sorted by courtlistener_cluster_id
tools/
  validate.py                         checks every run (standard library only)
  write_shards.py                     adds records to a run in canonical form
taxonomies/                           (when added) <name>.txt, one slug per line
```

## Why it is laid out this way

- **A run is the unit.** Everything one producer made with one prompt over one selection
  lives in one folder with a manifest. Runs are never edited into each other, so two runs
  over the same decisions can be compared, a bad run can be withdrawn by changing its
  status, and a new prompt version is a new run rather than a rewrite of history.
- **One record per decision per run, keyed by `courtlistener_cluster_id`.** The cluster id joins a
  record to CourtListener, to the citation graph, and to every other run and kind. A run
  holds each cluster at most once; a re-run of a decision goes in with `--replace` or in a
  new run.
- **Sharded by court and year of `date_filed`.** That keeps every file small (the pilot's
  records average about 2.5 KB, so even a busy circuit-year of 8,000 decisions is about
  20 MB), puts related decisions together for reading, and means a batch of new records
  rewrites only the shards it touches. Sharding by cluster-id range was the alternative;
  court and year was chosen because people look things up that way and shard sizes stay
  even as courts are added.
- **JSON Lines, one record per line, canonical key order, sorted.** Git diffs are then
  one line per changed decision, merges rarely conflict, and any tool can stream the
  files. `tools/write_shards.py` writes this form; `tools/validate.py` rejects anything
  else.
- **Provenance in the manifest, not repeated per record.** Model, method, prompt hash,
  taxonomy, input text and numbering, selection, counts, cost and caveats are stated once
  in `run.json`. Records carry only what differs per decision; producer-specific odds and
  ends go in `extra`, which nothing downstream may rely on.
- **New kinds of metadata** (summaries, citation treatment, topics, judges ...) get their
  own record schema, `schemas/<kind>.record.v<N>.schema.json`, sharing the envelope fields
  (`courtlistener_cluster_id`, `run_id`, `court_id`, `date_filed`, `status`, ...) and their own runs.
  A breaking change to a record shape is a new schema version, never an edit of the old.

## Headnotes records

Each line of `records/<court_id>/<yyyy>.jsonl`, in full in
`schemas/headnotes.record.v1.schema.json`:

```json
{"courtlistener_cluster_id": 282376, "run_id": "headnotes--2026-11-25--cloud-pilot-20", "court_id": "ca8",
 "date_filed": "1968-11-26", "case_name": "...", "citation": "403 F.2d 715", "courtlistener_opinion_id": 282376,
 "status": "ok", "input": {"paragraph_count": 5, "text_chars": 2774, "truncated": false},
 "headnotes": [{"text": "...", "concept_slugs": ["contract-formation"], "first_paragraph": 2,
   "last_paragraph": 5, "anchors": {"first": "...", "last": "..."}}],
 "extra": {"haiku_rank": 16883, "notes": "..."}}
```

(shown wrapped; on disk each record is one line)

- `headnotes` is the answer shape of request set headnotes-circuits-v1: up to 5, each
  with `text`, up to 3 `concept_slugs`, and the paragraph span it rests on. Paragraph
  numbers mean whatever the run's `input.paragraph_numbering` says.
- `status` is `ok`, `no_holding` (the text decides nothing; one headnote saying what was
  ordered, no slugs; kept so it is visible, dropped when loading the graph) or `failed`
  (no usable answer; no headnotes, `error` says why). Recording failures and no-holding
  texts, rather than leaving them out, lets coverage be counted from the repository alone.
- `anchors` is optional: the first words of the cited paragraphs, for runs whose
  numbering differs from the one being compared against.

## Runs

| run | decisions | headnotes | status | notes |
|---|---|---|---|---|
| [headnotes--2026-11-25--cloud-pilot-20](runs/headnotes--2026-11-25--cloud-pilot-20/run.json) | 20 | 70 | complete | Pilot to compare with the Haiku run of headnotes-circuits-v1. Reconstructed prompt, unverified slugs, approximate paragraph numbers: read its caveats. |
| [headnotes--2026-11-25--cloud-batch-1000](runs/headnotes--2026-11-25--cloud-batch-1000/run.json) | see run.json | see run.json | in progress | About 1,000 decisions across all 13 circuits and the years, same prompt as the pilot, long texts cut like the Haiku requests. `selection.jsonl` lists the clusters taken. Read its caveats. |

## Adding a run

1. Make `runs/<kind>--<YYYY-MM-DD>--<label>/` with `run.json` (status `in_progress`) and the
   exact prompt file; put its `sha256sum` in `run.json`.
2. Convert the producer's output to records (one JSON object per line, any order) and run
   `python3 tools/write_shards.py runs/<run_id> converted.jsonl`. Large runs can be loaded
   batch by batch; each call rewrites only the shards it touches.
3. Set `counts` in `run.json`, run `python3 tools/validate.py`, and commit.
4. When the run is finished, set `status` to `complete` and `completed` to the date.

If the slugs were checked against a taxonomy, add it as `taxonomies/<name>.txt` and set
`taxonomy.verified` to true; the validator then checks every slug against it.

## Validation

```
python3 tools/validate.py            # all runs
python3 tools/validate.py runs/<id>  # one run
```

Python 3.8+, standard library only. If the `jsonschema` package is installed it is used for
the schema checks; otherwise a built-in validator covers the subset of JSON Schema these
schemas use. Beyond the schemas it checks prompt hashes, shard placement and order,
duplicate clusters, paragraph spans, status rules, counts and shard sizes.

## Size

A full headnotes run over all 617,847 circuit decisions comes to roughly 1.1 GB of JSONL
without anchors or extras (about 1.6 GB with them), or about 350 to 500 MB once git
compresses it. Every shard stays far under GitHub's 50 MB warning, so Git LFS is not
needed. A repository holding a few such runs is fine; beyond that (GitHub recommends
keeping a repository under a few GB), older complete runs can be moved to release assets
or a dataset host and listed here. Push a large run in several commits (by court, say),
since GitHub refuses a single push over 2 GB.
