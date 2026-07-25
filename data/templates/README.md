# Authoring templates

**Owner:** Member 4 — Data and Science.

These are **guidance skeletons** for humans authoring real artifacts from the ledger. They
carry inline `_help_*` annotation keys and `FILL_...` placeholders, so they are **not**
schema-valid as written — strip the `_help_*` keys and replace every `FILL_...` before
validating. The authoritative shapes are the versioned schemas in `data/schemas/`
(see `data/schemas/VERSIONS.md`); the protocol is `data/ledger.md`.

| Template | Fills schema | Written to |
|---|---|---|
| `footage_session_manifest.template.json` | `footage_session_manifest.schema.json` | `data/manifests/<session>.json` (then sealed) |
| `ledger_entry.template.json` | `ledger_entry.schema.json` | ledger (per session) |
| `query_family.template.json` | `query_family.schema.json` | `data/queries/families/<family>.json` |

Validate a finished artifact before committing:

```bash
python -c "import sys; sys.path.insert(0,'.'); import json; from eval import schema as S; \
print(S.validate_query_family(json.load(open('data/queries/families/fam_XXXX.json'))))"
```

Seal a manifest (compute its immutable `content_hash`) with
`eval.schema.seal_manifest(...)`; never hand-write the hash.
