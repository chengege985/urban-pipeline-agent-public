# GAS public-data read-only adapters

This feature keeps PHMSA historical gas incident archives and the Dryad
controlled methane-release experiment outside the RAG corpus until the
adapters pass independent review.

## Runtime configuration

Set the following before starting the FastAPI process.

```text
GAS_PUBLIC_DATA_ROOT=/workdata/WaterPipNet/01_工程项目/data/gas_public_20261124
```

The service reads only these receipt-pinned paths.

- `raw/US-PHMSA/**`
- `raw/US-DRYAD-CH4/doi_10_5061_dryad_ncjsxkt15__v20231105.zip`
- `receipts/phmsa_manual_download_20261124.json`
- `receipts/dryad_manual_download_20261124.json`

The caller must be an authenticated internal principal with the
`gas-public-20261124` corpus permission. Raw archives are never returned.

## Read-only HTTP routes

- `GET /api/v1/gas/engineering/public-data/catalog`
- `GET /api/v1/gas/engineering/public-data/phmsa/quality`
- `GET /api/v1/gas/engineering/public-data/phmsa/incidents`
- `GET /api/v1/gas/engineering/public-data/dryad/quality`
- `GET /api/v1/gas/engineering/public-data/dryad/experiments/{experiment_id}`
- `GET /api/v1/gas/engineering/public-data/reports/US-PHMSA-INCIDENT`
- `GET /api/v1/gas/engineering/public-data/reports/US-DRYAD-CH4`

The existing read-only Agent routes these questions to deterministic tools.

- `phmsa.incident.query`
- `dryad.experiment.query`
- `public.report.query`

PHMSA results retain source archive, source row and archive hash. Dryad
results retain member SHA-256 and MD5, experiment filename and row-level
quality counts.

## Boundaries

PHMSA is United States historical incident data. Dryad is a Colorado METEC
controlled-release experiment. Neither dataset supplies current Chongqing
asset state, operating pressure, operating flow, leakage probability or
control instructions.

The service does not write raw data, modify K-001, perform CUV calculations,
or update the RAG manifest. RAG source cards can be added after this adapter
candidate is independently accepted.
