#!/usr/bin/env python3
"""Validate runs in this repository.

    python3 tools/validate.py                 # every run under runs/
    python3 tools/validate.py runs/<run_id>   # one or more runs

Checks, for each run:
  - run.json against schemas/run.schema.json
  - the prompt file exists and its sha256 matches run.json
  - every record against the run's record schema
  - record.run_id == run_id == folder name; kind == run_id prefix
  - shard path == records/<court_id>/<year of date_filed>.jsonl (unknown.jsonl when no date)
  - records sorted by courtlistener_cluster_id within a shard, each courtlistener_cluster_id once per run
  - one JSON object per line, UTF-8, LF line endings, keys in canonical order
  - first_paragraph <= last_paragraph (and <= input.paragraph_count when given)
  - status rules: ok/no_holding need >= 1 headnote, failed needs none and an error;
    no_holding headnotes carry no slugs
  - slugs against taxonomies/<name>.txt when run.json says the taxonomy is verified
  - counts in run.json match the records
  - shard sizes: warn over 25 MB, fail over 50 MB (GitHub warns at 50, refuses at 100)

Standard library only (Python 3.8+). If the `jsonschema` package is installed it is used
for the schema step; otherwise a built-in validator covers the subset of JSON Schema the
schemas here use. Exit status is 1 when any error is found.
"""
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WARN_BYTES, MAX_BYTES = 25 * 1024 * 1024, 50 * 1024 * 1024

try:  # optional
    import jsonschema  # type: ignore
except ImportError:  # pragma: no cover
    jsonschema = None

# Key order every record line is written in (tools/write_shards.py uses the same).
RECORD_KEY_ORDER = ["courtlistener_cluster_id", "run_id", "court_id", "date_filed", "case_name", "citation",
                    "courtlistener_opinion_id", "status", "error", "input", "headnotes", "extra"]
HEADNOTE_KEY_ORDER = ["text", "concept_slugs", "first_paragraph", "last_paragraph", "anchors"]


# ---------------------------------------------------------------- schema subset

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _resolve(ref, root):
    if not ref.startswith("#/"):
        raise ValueError("only local $ref is supported: %s" % ref)
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def subset_validate(value, schema, root, path="$"):
    """Yield error strings. Supports: $ref (local), type, enum, const, required,
    properties, additionalProperties (bool), items, minItems, maxItems, uniqueItems,
    minLength, pattern, minimum."""
    if "$ref" in schema:
        yield from subset_validate(value, _resolve(schema["$ref"], root), root, path)
        return
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES[x](value) for x in types):
            yield "%s: expected %s, got %s" % (path, "/".join(types), type(value).__name__)
            return
    if "enum" in schema and value not in schema["enum"]:
        yield "%s: %r not in %r" % (path, value, schema["enum"])
    if "const" in schema and value != schema["const"]:
        yield "%s: must be %r" % (path, schema["const"])
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            yield "%s: shorter than %d" % (path, schema["minLength"])
        if "pattern" in schema and not re.search(schema["pattern"], value):
            yield "%s: %r does not match %s" % (path, value, schema["pattern"])
    if _TYPES["number"](value) and "minimum" in schema and value < schema["minimum"]:
        yield "%s: below minimum %s" % (path, schema["minimum"])
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            yield "%s: fewer than %d items" % (path, schema["minItems"])
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            yield "%s: more than %d items" % (path, schema["maxItems"])
        if schema.get("uniqueItems"):
            seen = [json.dumps(v, sort_keys=True) for v in value]
            if len(seen) != len(set(seen)):
                yield "%s: items not unique" % path
        if "items" in schema:
            for i, v in enumerate(value):
                yield from subset_validate(v, schema["items"], root, "%s[%d]" % (path, i))
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                yield "%s: missing %s" % (path, k)
        props = schema.get("properties", {})
        for k, v in value.items():
            if k in props:
                yield from subset_validate(v, props[k], root, "%s.%s" % (path, k))
            elif schema.get("additionalProperties") is False:
                yield "%s: unexpected field %s" % (path, k)


def schema_errors(value, schema):
    if jsonschema is not None:
        v = jsonschema.Draft202012Validator(schema)
        return ["%s: %s" % ("$" + "".join("[%r]" % p for p in e.absolute_path), e.message)
                for e in v.iter_errors(value)]
    return list(subset_validate(value, schema, schema))


def load_schema(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- run checks

def canonical_line(rec):
    """The exact line a record is stored as."""
    out = {k: rec[k] for k in RECORD_KEY_ORDER if k in rec}
    out.update({k: v for k, v in rec.items() if k not in out})
    if isinstance(out.get("headnotes"), list):
        hn = []
        for h in out["headnotes"]:
            if isinstance(h, dict):
                o = {k: h[k] for k in HEADNOTE_KEY_ORDER if k in h}
                o.update({k: v for k, v in h.items() if k not in o})
                h = o
            hn.append(h)
        out["headnotes"] = hn
    return json.dumps(out, ensure_ascii=False, separators=(", ", ": "))


def shard_rel(rec):
    year = rec["date_filed"][:4] if rec.get("date_filed") else "unknown"
    return os.path.join("records", rec["court_id"], year + ".jsonl")


def validate_run(run_dir, run_schema):
    errors, warnings = [], []
    run_id = os.path.basename(os.path.normpath(run_dir))
    E = lambda msg: errors.append("%s: %s" % (run_id, msg))
    W = lambda msg: warnings.append("%s: %s" % (run_id, msg))

    manifest_path = os.path.join(run_dir, "run.json")
    try:
        with open(manifest_path, encoding="utf-8") as f:
            run = json.load(f)
    except (OSError, ValueError) as e:
        E("run.json unreadable: %s" % e)
        return errors, warnings, None
    for msg in schema_errors(run, run_schema):
        E("run.json " + msg)
    if errors:
        return errors, warnings, None
    if run["run_id"] != run_id:
        E("run.json run_id %r != folder name" % run["run_id"])
    if run_id.split("--")[0] != run["kind"]:
        E("kind %r is not the run_id prefix" % run["kind"])
    if os.path.basename(run["record_schema"]).split(".")[0] != run["kind"]:
        E("record_schema %r is not for kind %r" % (run["record_schema"], run["kind"]))

    prompt_path = os.path.join(run_dir, run["prompt"]["file"])
    if not os.path.isfile(prompt_path):
        E("prompt file %s missing" % run["prompt"]["file"])
    else:
        with open(prompt_path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        if digest != run["prompt"]["sha256"]:
            E("prompt sha256 is %s, run.json says %s" % (digest, run["prompt"]["sha256"]))

    try:
        rec_schema = load_schema(run["record_schema"])
    except (OSError, ValueError) as e:
        E("record schema %s unreadable: %s" % (run["record_schema"], e))
        return errors, warnings, run

    taxonomy = None
    tax = run.get("taxonomy")
    if tax and tax.get("verified"):
        tax_path = os.path.join(ROOT, "taxonomies", tax["name"] + ".txt")
        if not os.path.isfile(tax_path):
            E("taxonomy marked verified but taxonomies/%s.txt is missing" % tax["name"])
        else:
            with open(tax_path, encoding="utf-8") as f:
                taxonomy = {l.strip() for l in f if l.strip() and not l.startswith("#")}

    seen = {}
    n_records = n_headnotes = 0
    by_status = {}
    rec_root = os.path.join(run_dir, "records")
    shards = []
    for dirpath, _, files in os.walk(rec_root):
        for name in files:
            shards.append(os.path.join(dirpath, name))
    for shard in sorted(shards):
        rel = os.path.relpath(shard, run_dir)
        if not shard.endswith(".jsonl"):
            E("%s: not a .jsonl file" % rel)
            continue
        size = os.path.getsize(shard)
        if size > MAX_BYTES:
            E("%s: %.1f MB, over the 50 MB limit; split the shard" % (rel, size / 1e6))
        elif size > WARN_BYTES:
            W("%s: %.1f MB, nearing the 50 MB limit" % (rel, size / 1e6))
        with open(shard, "rb") as f:
            raw = f.read()
        if b"\r" in raw:
            E("%s: CR characters; use LF line endings" % rel)
        if raw and not raw.endswith(b"\n"):
            E("%s: last line has no newline" % rel)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            E("%s: not UTF-8 (%s)" % (rel, e))
            continue
        prev = None
        for lineno, line in enumerate(text.split("\n")[:-1] if text else [], 1):
            where = "%s:%d" % (rel, lineno)
            if not line.strip():
                E("%s: blank line" % where)
                continue
            try:
                rec = json.loads(line)
            except ValueError as e:
                E("%s: bad JSON (%s)" % (where, e))
                continue
            errs = schema_errors(rec, rec_schema)
            for msg in errs:
                E("%s %s" % (where, msg))
            if errs:
                continue
            n_records += 1
            cid = rec["courtlistener_cluster_id"]
            by_status[rec["status"]] = by_status.get(rec["status"], 0) + 1
            n_headnotes += len(rec["headnotes"])
            if line != canonical_line(rec):
                E("%s: not in canonical form (run tools/write_shards.py)" % where)
            if rec["run_id"] != run_id:
                E("%s: run_id %r != %r" % (where, rec["run_id"], run_id))
            if shard_rel(rec) != rel:
                E("%s: cluster %d belongs in %s" % (where, cid, shard_rel(rec)))
            if cid in seen:
                E("%s: cluster %d already in %s" % (where, cid, seen[cid]))
            seen[cid] = where
            if prev is not None and cid <= prev:
                E("%s: not sorted by courtlistener_cluster_id" % where)
            prev = cid
            status, hns = rec["status"], rec["headnotes"]
            if status == "failed":
                if hns:
                    E("%s: failed record has headnotes" % where)
                if not rec.get("error"):
                    E("%s: failed record has no error" % where)
            elif not hns:
                E("%s: %s record has no headnotes" % (where, status))
            pc = (rec.get("input") or {}).get("paragraph_count")
            for i, h in enumerate(hns):
                if h["first_paragraph"] > h["last_paragraph"]:
                    E("%s: headnote %d first_paragraph > last_paragraph" % (where, i))
                if pc is not None and h["last_paragraph"] > pc:
                    E("%s: headnote %d cites paragraph %d of %d" % (where, i, h["last_paragraph"], pc))
                if status == "no_holding" and h["concept_slugs"]:
                    E("%s: no_holding headnote has slugs" % where)
                if taxonomy is not None:
                    for s in h["concept_slugs"]:
                        if s not in taxonomy:
                            E("%s: slug %r not in taxonomy %s" % (where, s, tax["name"]))

    counts = run["counts"]
    if counts["records"] != n_records:
        E("counts.records %d != %d records found" % (counts["records"], n_records))
    if "headnotes" in counts and counts["headnotes"] != n_headnotes:
        E("counts.headnotes %d != %d found" % (counts["headnotes"], n_headnotes))
    if "by_status" in counts and counts["by_status"] != by_status:
        E("counts.by_status %r != %r found" % (counts["by_status"], by_status))
    return errors, warnings, {"records": n_records, "headnotes": n_headnotes, "shards": len(shards)}


def main(argv):
    run_schema = load_schema("schemas/run.schema.json")
    if argv:
        run_dirs = argv
    else:
        base = os.path.join(ROOT, "runs")
        run_dirs = sorted(os.path.join(base, d) for d in os.listdir(base)
                          if os.path.isdir(os.path.join(base, d)))
    all_errors = 0
    for run_dir in run_dirs:
        errors, warnings, summary = validate_run(run_dir, run_schema)
        for w in warnings:
            print("warning: " + w)
        for e in errors:
            print("error: " + e)
        all_errors += len(errors)
        name = os.path.basename(os.path.normpath(run_dir))
        if summary:
            print("%s: %s, %d records, %d headnotes, %d shards"
                  % (name, "FAIL" if errors else "ok", summary["records"], summary["headnotes"], summary["shards"]))
        else:
            print("%s: FAIL" % name)
    if not run_dirs:
        print("no runs found")
    print("validator: %s" % ("jsonschema" if jsonschema else "built-in subset"))
    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
