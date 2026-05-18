# pyairtable_custom migration inventory

## Target Package
- Installed package: `pyairtable`
- Target version: `2.3.3`
- Reason: the vendored package declares `__version__ = "2.3.3"`, so this is the lowest-risk upstream target for py311 validation.
- Strategy: compatibility layer.
- Compatibility goal: preserve the existing project-side imports while routing runtime API/ORM/types/models/utils to installed `pyairtable`.

## API Comparison Summary
- Compatible:
  - `Api`, `Base`, `Table`, `Workspace`, `Enterprise`, `retry_strategy`
  - `pyairtable.orm.Model`
  - `pyairtable.orm.fields`
  - `pyairtable.utils` date/datetime helpers and attachment helpers
  - `pyairtable.api.types`
- Partially compatible:
  - `formulas.match`; upstream 2.3.3 uses keyword-only `match_any`, while the vendored copy allowed positional use.
- Incompatible / project extension:
  - `formulas.RECORD_ID`; used by project code and absent from upstream 2.3.3.

## Runtime Uses Found
- `contactapi/apps/core/models_airtable.py`
  - Imports ORM model, fields, formulas, date helpers.
  - Criticality: high for Airtable sync commands and model helpers.
  - Replacement complexity: medium; keep old import path with shim.
- `contactapi/apps/core/utils/models_airtable_mixin.py`
  - Imports ORM model, fields, `match`, `OR`, `EQUAL`, `RECORD_ID`.
  - Criticality: high; shared by Airtable model classes.
  - Replacement complexity: medium because of `RECORD_ID`.
- `contactapi/apps/clickfunnels/models_airtable.py`
  - Imports ORM model, fields, formulas.
  - Criticality: medium/high for Airtable sync flows.
  - Replacement complexity: low with shim.
- `contactapi/apps/blaudirekt/models_airtable.py`
  - Imports ORM model, fields, formulas.
  - Criticality: medium/high for Blaudirekt Airtable sync commands.
  - Replacement complexity: low with shim.
- `contactapi/apps/tracking/models_airtable.py`
  - Imports ORM model, fields, formulas.
  - Criticality: medium for tracking Airtable sync helpers.
  - Replacement complexity: low with shim.
- `contactapi/apps/core/utils/airtable.py` and `contactapi/apps/core/utils/__init__.py`
  - Re-export pyairtable symbols.
  - Criticality: medium; import compatibility surface.
  - Replacement complexity: low with shim.

## Management Commands / Flows Requiring Follow-up
- `airtable_sync_nottransferreddatas.py`
- `airtable_sync_nottransferreddatas_deletion.py`
- `airtable_sync_transferreddatas.py`
- `airtable_sync_timedout_transferred_contacts.py`
- `airtable_sync_campaigndata.py`
- `airtable_sync_blaudirektcontracts.py`
- `airtable_sync_blaudirektdocuments.py`

These commands should be checked with `--help` / import checks under py311. Real sync execution should remain manual because it can read/write Airtable and mutate local records.
