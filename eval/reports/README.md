# Evaluation reports

**Owner:** Member 4 — Data and Science.

| Artifact | What it is | How it is produced |
|---|---|---|
| `benchmark_panel.json` | **Machine-readable benchmark panel** (§22.4): config hash, evaluation date, dataset summary, data-integrity report, and every §22.1 metric per configuration. | `python -m eval.run_eval --out eval/reports/benchmark_panel.json` — **regenerated, never hand-edited** |
| `benchmark_panel.txt` | The §22.4 UI text block rendered from the panel. | same command, `--report` |
| `evaluation_report_template.md` | Human evaluation report skeleton mirroring §22.1/§22.2/§22.3. | copy to `evaluation_report_<date>.md` and fill **only** from a real run |

## Not-yet-measured invariant

Until real held-out predictions are supplied via `--predictions`, every metric in the panel
is the string `not_yet_measured`, and `data_integrity.ok` reflects only **structural**
validity (schemas, split discipline, immutability, annotation ordering). A metric becomes a
number **only** when a predictions bundle produced by the system under test (Members 1–3) is
scored against the held-out split. The code never fabricates a number:
`eval.baselines.compute_config_metrics(..., bundle=None)` returns `not_yet_measured` for
every field. Optional lanes (spatial, track/graph, bounded-logic) stay `not_yet_measured`
until real held-out results for that lane exist.

## Producing a measured panel

```bash
# Members 1–3 emit one predictions bundle per configuration (see eval/baselines.py docstring).
python -m eval.run_eval \
  --predictions FULL.json FULL+GRAPH.json B1.json \
  --primary-config FULL \
  --out eval/reports/benchmark_panel.json \
  --report eval/reports/benchmark_panel.txt \
  --strict
```

The panel's top-level `config_hash` is the **evaluation** config hash (thresholds + budget +
dataset manifest hashes + schema version). Each configuration section also records the
**system** operating-point hash carried by its predictions bundle.
