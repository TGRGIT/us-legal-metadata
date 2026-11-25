#!/usr/bin/env python3
"""Add records to a run's shards, in canonical form.

    python3 tools/write_shards.py runs/<run_id> new-records.jsonl [more.jsonl ...]
    python3 tools/write_shards.py --replace runs/<run_id> fixes.jsonl

Each input line must already be a record in the run's record schema (see
schemas/). Records are routed to records/<court_id>/<year>.jsonl, merged with what
is there, sorted by cluster_id and written with the key order tools/validate.py
expects. A cluster_id already in the run is an error unless --replace is given.
Only shards that receive records are rewritten, so a large run can be loaded batch by
batch with small diffs. Afterwards, update counts in run.json and run tools/validate.py.

Standard library only.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import canonical_line, shard_rel  # noqa: E402


def read_jsonl(path):
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except ValueError as e:
                    sys.exit("%s:%d: bad JSON (%s)" % (path, n, e))


def main(argv):
    replace = "--replace" in argv
    argv = [a for a in argv if a != "--replace"]
    if len(argv) < 2:
        sys.exit(__doc__)
    run_dir, inputs = argv[0], argv[1:]
    run_id = os.path.basename(os.path.normpath(run_dir))

    existing = {}  # cluster_id -> shard rel path, across the whole run
    rec_root = os.path.join(run_dir, "records")
    for dirpath, _, files in os.walk(rec_root):
        for name in files:
            rel = os.path.relpath(os.path.join(dirpath, name), run_dir)
            for rec in read_jsonl(os.path.join(run_dir, rel)):
                existing[rec["cluster_id"]] = rel

    incoming = {}
    for path in inputs:
        for rec in read_jsonl(path):
            rec.setdefault("run_id", run_id)
            if rec["run_id"] != run_id:
                sys.exit("cluster %s has run_id %r, not %r" % (rec["cluster_id"], rec["run_id"], run_id))
            cid = rec["cluster_id"]
            if cid in incoming:
                sys.exit("cluster %s appears twice in the input" % cid)
            if cid in existing and not replace:
                sys.exit("cluster %s is already in %s (use --replace)" % (cid, existing[cid]))
            incoming[cid] = rec

    touched = {}
    for rec in incoming.values():
        touched.setdefault(shard_rel(rec), {})[rec["cluster_id"]] = rec
    # a replaced record may move shard (e.g. a corrected date): drop it from the old one
    for cid in incoming:
        if cid in existing and existing[cid] != shard_rel(incoming[cid]):
            touched.setdefault(existing[cid], {})
    for rel, new in sorted(touched.items()):
        path = os.path.join(run_dir, rel)
        merged = {}
        if os.path.exists(path):
            for rec in read_jsonl(path):
                if rec["cluster_id"] not in incoming:
                    merged[rec["cluster_id"]] = rec
        merged.update(new)
        if not merged:
            os.remove(path)
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            for cid in sorted(merged):
                f.write(canonical_line(merged[cid]) + "\n")
        os.replace(tmp, path)
        print("%s: %d records (%d new or replaced)" % (rel, len(merged), len(new)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
