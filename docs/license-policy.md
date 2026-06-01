# License Policy

Lemma treats license metadata as part of the public proof record.

Paid activation is allowed only for:

- `clean_open`
- `attribution_required`

These states are blocked from paid activation:

- `research_only`
- `unknown`
- `restricted`
- `rejected`

Unknown provenance is not a harmless default. It can be reviewed or stored privately, but it should not enter paid public proof data. Proof Atlas exports can filter by license state, including `--license commercial-safe`.
