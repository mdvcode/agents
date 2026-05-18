# clickfunnels contact route py311 probe

- run_id: `clickfunnels-contact-routes-20260427T105724Z`
- base_url: `https://contactapi2.static.fyi`
- started_at: `2026-04-27T10:57:24Z`
- finished_at: `2026-04-27T10:57:37Z`

## UUID Contract

- ORM uuid field present: `False`
- uuid property present: `True`
- `UUID(contact.uuid).int == contact.id`: `True`
- `id__is_uuid` lookup matches contact id: `True`

## HTTP PDF Probes

| probe | path | status | content_type | pdf_magic |
|---|---|---:|---|---|
| `skipped` | `None` | `None` | `None` | `None` |

## SavedHttpRequest Route Probes

- `heyflow`: found=`True`, request_id=`4674488`, category=`cfxzzv_heyflow`, result=`OK`, status=`200`
- `webflow`: found=`True`, request_id=`4674358`, category=`cfxkvz_webflow`, result=`OK`, status=`200`

## Legacy / Out Of Scope

- `import_lastnames_csv`: obsolete; approved by Mario for deletion, not a py311 blocker.
- `/clickfunnels/script/dev/*`: dev-only DynDNS/MacBook path; not a py311 blocker.
