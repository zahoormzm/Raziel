# Challengers

**Owner:** Member 4 — Data and Science.

Typed challengers (§21.4) are **primarily embedded inside each query family** under
`challengers[]` (see `data/queries/families/*.json`), because a challenger is meaningful only
relative to the family it must be rejected against. That is where the dataset card computes the
challenger distribution and where `eval/tests/test_dataset.py` checks type coverage.

This directory is reserved for **standalone** challenger files (validated against
`data/schemas/challenger.schema.json`) when a near-miss is shared across families or authored
before its family exists. It is empty in the synthetic seed.
