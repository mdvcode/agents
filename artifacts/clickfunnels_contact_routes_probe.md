# clickfunnels contact route py311 probe

- run_id: `clickfunnels-contact-routes-20260427T103049Z`
- base_url: `https://contactapi2.static.fyi`
- started_at: `2026-04-27T10:30:49Z`
- finished_at: `2026-04-27T10:47:48Z`

## UUID Contract

- ORM uuid field present: `False`
- uuid property present: `True`
- `UUID(contact.uuid).int == contact.id`: `True`
- `id__is_uuid` lookup matches contact id: `True`

## HTTP PDF Probes

| probe | path | status | content_type | pdf_magic |
|---|---|---:|---|---|
| `auskunft_id_with_token` | `/clickfunnels/contact/id/1168743/auskunft.pdf` | `200` | `application/pdf` | `True` |
| `auskunft_uuid` | `/clickfunnels/contact/00000000-0000-0000-0000-00000011d567/auskunft.pdf` | `200` | `application/pdf` | `True` |
| `widerruf_id` | `/clickfunnels/contact/id/1642532/cfxkvzs_widerruf.pdf` | `200` | `application/pdf` | `True` |
| `widerruf_uuid` | `/clickfunnels/contact/00000000-0000-0000-0000-000000191024/cfxkvzs_widerruf.pdf` | `200` | `application/pdf` | `True` |
| `letterxpress_id` | `/clickfunnels/contact/id/760974/cfxkvzs_letterxpress.pdf` | `404` | `text/html` | `False` |
| `letterxpress_uuid` | `/clickfunnels/contact/00000000-0000-0000-0000-0000000b9c8e/cfxkvzs_letterxpress.pdf` | `404` | `text/html` | `False` |

## SavedHttpRequest Route Probes

- `heyflow`: found=`True`, request_id=`4674425`, category=`cfxheatpump_heyflow`, result=`FAIL`, status=`None`
- `webflow`: found=`True`, request_id=`4674358`, category=`cfxkvz_webflow`, result=`OK`, status=`200`

## Legacy / Out Of Scope

- `import_lastnames_csv`: obsolete; approved by Mario for deletion, not a py311 blocker.
- `/clickfunnels/script/dev/*`: dev-only DynDNS/MacBook path; not a py311 blocker.
