# py311 cron / management commands / critical webhooks report

- Date: 2026-04-27
- Target: `contactapi2.static.fyi`
- Scope: read-only cron inventory, safe `manage.py <command> --help`, source review for risky commands, critical webhook follow-up from existing sweep.
- Production safety: cron wrappers were inspected, not executed. Risky management commands and webhook payloads were not blind-run.

## 1. Cron inventory

Sources checked:

| source | result |
|---|---|
| root crontab | project cron jobs found |
| daryna user crontab | no crontab |
| `/etc/crontab` | no project jobs |
| `/etc/cron.d` | system-only entries: `0hourly`, `raid-check`, `sysstat`, `update-motd` |
| `/etc/cron.hourly`, `/etc/cron.daily`, `/etc/cron.weekly`, `/etc/cron.monthly` | system-only periodic scripts found |
| systemd timers grep for contact/django/manage/celery/uwsgi | no project timer jobs found |

Active project cron jobs from root crontab:

| schedule | wrapper | extracted management command | classification | notes |
|---|---|---|---|---|
| `3 6 * * *` | `daily_email_report.sh` | `daily_email_report` | external_integration | sends email report |
| `4 6 * * *` | `daily_email_report_v2.sh` | `daily_email_report_v2` | external_integration | sends email report |
| `17 * * * *` | `reschedule_verify_phone_number_cronjob.sh` | `reschedule_verify_phone_number_cronjob` | risky_mutation | reschedules verification tasks |
| `10 * * * *` | `reschedule_maintenance_tasks_cronjob.sh` | `reschedule_maintenance_tasks_cronjob` | risky_mutation | reschedules maintenance tasks |
| `5 * * * *` | `sipgate_check_devices_cronjob.sh` | `sipgate_check_devices` | external_integration | checks/restarts Sipgate/baresip related process, can send alert |
| `10 * * * *` | `incoming_custom_conversion_monitor_cronjob.sh` | `incoming_custom_conversion_monitor_cronjob` | risky_mutation | tracking task path |
| `0 * * * *` | `incoming_custom_conversions_tfbank_from_thrive_cronjob.sh` | `incoming_custom_conversions_tfbank_from_thrive_cronjob` | risky_mutation | tracking task path |
| `2 0 * * *` | `saved_http_request_id_range_cronjob.sh` | `saved_http_request_id_range_cronjob` | safe_readonly_or_housekeeping | source appears bounded housekeeping/report style; not executed |
| `22 22 * * *` | `daily_contactapi_cronjob.sh` | `daily_contactapi_cronjob` | risky_mutation/external_integration | Dialfire transfer/import settings |
| `15 8 * * *` | `daily_contactapi_cronjob_at_8.sh` | `daily_contactapi_cronjob_at_8` | risky_mutation/external_integration | Airtable sync + Dialfire follow-up tasks |
| `55 7 * * *` | `daily_transfer_for_defined_at_8.sh` | `daily_transfer_for_defined_at_8` | risky_mutation | transfer contacts |
| `2 8 * * *` | `daily_transfer_for_defined_at_8_weekdays_no_vacation.sh` | `daily_transfer_for_defined_at_8_weekdays_no_vacation` | risky_mutation | transfer contacts |
| `8 8 * * *` | `daily_transfer_for_defined_at_8_weekdays.sh` | `daily_transfer_for_defined_at_8_weekdays` | risky_mutation | transfer contacts |
| `8 8 * * *` | `daily_transfer_for_defined_at_8_monday_to_thursday.sh` | `daily_transfer_for_defined_at_8_monday_to_thursday` | risky_mutation | transfer contacts |
| `1 9,14 * * *` | `daily_transfer_for_defined_at_9_and_14_weekdays.sh` | `daily_transfer_for_defined_at_9_and_14_weekdays` | risky_mutation | transfer contacts |
| `59 8 * * *` | `daily_transfer_for_defined_at_9.sh` | `daily_transfer_for_defined_at_9` | risky_mutation | transfer contacts |
| `29 10 * * *` | `daily_transfer_for_defined_at_10_30.sh` | `daily_transfer_for_defined_at_10_30` | risky_mutation | transfer contacts |
| `59 11 * * *` | `daily_transfer_for_defined_at_12.sh` | `daily_transfer_for_defined_at_12` | risky_mutation | transfer contacts |
| `55 0 * * *` | `daily_transfer_for_defined_at_1.sh` | `daily_transfer_for_defined_at_1` | risky_mutation | transfer contacts |
| `57 23 * * *` | `daily_transfer_for_defined_at_0.sh` | `daily_transfer_for_defined_at_0` | risky_mutation/external_integration | transfer contacts and product-closing task |
| `59 19 * * *` | `daily_transfer_for_defined_at_20.sh` | `daily_transfer_for_defined_at_20` | risky_mutation | transfer contacts |
| `3 * * * *` | `clickfunnels_watched_domains_cronjob.sh` | `clickfunnels_watched_domains_cronjob` | risky_mutation | clickfunnels task path |
| `*/5 * * * *` | `clean_defunct_contacts.sh` | `clean_defunct_contacts` | risky_mutation | saves/updates contacts |
| `*/15 * * * *` | `user_identification_data_set_cleaner_cronjob.sh` | `user_identification_data_set_cleaner_cronjob` | risky_mutation | tracking cleanup task |
| `*/15 * * * *` | `transferred_contacts_extra_transfer_rules_cronjob.sh` | `transferred_contacts_extra_transfer_rules_cronjob` | risky_mutation | transfer rule cronjob |
| `0 * * * *` | `monitor_cronjob.sh` | `monitor_cronjob` | external_integration | can send SMS alerts |
| `19 * * * *` | `hourly_doublet_task.sh` | `hourly_doublet_task` | risky_mutation | tracking + clickfunnels doublet tasks |
| `57 1 * * *` | `cleanup_ipc_locks.sh` | `cleanup_ipc_locks` | safe_readonly_or_housekeeping | disk cleanup of IPC locks; not executed |
| `0 1 * * *` | `quota_reset_cronjob.sh` | `quota_reset_cronjob` | risky_mutation | resets transfer quotas |
| `0 2 * * 6` | `airtable_sync_blaudirektcontracts.sh` | `airtable_sync_blaudirektcontracts` | external_integration | Airtable sync |
| `0 2 * * 0` | `airtable_sync_blaudirektdocuments.sh` | `airtable_sync_blaudirektdocuments` | external_integration | Airtable sync |
| `45 23 * * *` | `airtable_sync_campaigndata.sh` | `airtable_sync_campaigndata` | external_integration | Airtable/ad API sync |
| `*/30 * * * *`, `30 4 * * *`, `30 21 * * *` | `airtable_sync_timedout_transferred_contacts*.sh` | `airtable_sync_timedout_transferred_contacts` | external_integration/risky_mutation | retries Airtable sync for transferred contacts |
| `1 2 * * *` | `dialfire_send_crosssales.sh` | `dialfire_send_crosssales` | external_integration/risky_mutation | sends cross-sell contacts to Dialfire |

Commented-out cron lines were recorded as non-active legacy/non-priority candidates. They include `lifeforestry_cronjob`, `harald_cronjob`, `auto_custom_conversions`, `powerleads_cronjob`, `clickfunnels_assets_cronjob`, `cfxstg_75_cronjob`, `affiliflare_cronjob`, and older `airtable_sync_nottransferreddatas*` lines.

## 2. Management commands status

Safe startup check performed on the py311 test checkout:

```text
cd /var/www/hosts/contactapi2.static.fyi/contactapi/contactapi
/var/www/hosts/contactapi.static.fyi/contactapi/venv311/bin/python manage.py <command> --help --settings=contactapi.settings_py311
```

| command | cron-driven | `--help` status | classification | risky? | needs fix? | notes |
|---|---:|---|---|---:|---:|---|
| `daily_email_report` | yes | OK | external_integration | yes | no | imports and help OK; real run sends email |
| `daily_email_report_v2` | yes | OK | external_integration | yes | no | imports and help OK; real run sends email |
| `reschedule_verify_phone_number_cronjob` | yes | OK | risky_mutation | yes | no | do not blind-run; reschedules tasks |
| `reschedule_maintenance_tasks_cronjob` | yes | OK | risky_mutation | yes | no | do not blind-run; reschedules tasks |
| `sipgate_check_devices` | yes | OK | external_integration | yes | no | can kill/restart baresip and send alert |
| `incoming_custom_conversion_monitor_cronjob` | yes | OK | risky_mutation | yes | no | tracking task path |
| `incoming_custom_conversions_tfbank_from_thrive_cronjob` | yes | OK | risky_mutation | yes | no | tracking task path |
| `saved_http_request_id_range_cronjob` | yes | OK | safe_readonly_or_housekeeping | medium | no | help OK; real run not needed for py311 blocker check |
| `daily_contactapi_cronjob` | yes | OK | risky_mutation/external_integration | yes | no | Dialfire transfer/import flow; no blind run |
| `daily_contactapi_cronjob_at_8` | yes | OK | risky_mutation/external_integration | yes | no | Airtable sync + Dialfire tasks; no blind run |
| `daily_transfer_for_defined_at_8` | yes | OK | risky_mutation | yes | no | transfer contacts |
| `daily_transfer_for_defined_at_8_weekdays_no_vacation` | yes | OK | risky_mutation | yes | no | transfer contacts |
| `daily_transfer_for_defined_at_8_weekdays` | yes | OK | risky_mutation | yes | no | transfer contacts |
| `daily_transfer_for_defined_at_8_monday_to_thursday` | yes | OK | risky_mutation | yes | no | transfer contacts |
| `daily_transfer_for_defined_at_9_and_14_weekdays` | yes | OK | risky_mutation | yes | no | transfer contacts |
| `daily_transfer_for_defined_at_9` | yes | OK | risky_mutation | yes | no | transfer contacts |
| `daily_transfer_for_defined_at_10_30` | yes | OK | risky_mutation | yes | no | transfer contacts |
| `daily_transfer_for_defined_at_12` | yes | OK | risky_mutation | yes | no | transfer contacts |
| `daily_transfer_for_defined_at_1` | yes | OK | risky_mutation | yes | no | transfer contacts |
| `daily_transfer_for_defined_at_0` | yes | OK | risky_mutation/external_integration | yes | no | transfer contacts + product closing task |
| `daily_transfer_for_defined_at_20` | yes | OK | risky_mutation | yes | no | transfer contacts |
| `clickfunnels_watched_domains_cronjob` | yes | OK | risky_mutation | yes | no | clickfunnels task path |
| `clean_defunct_contacts` | yes | OK | risky_mutation | yes | no | source saves/updates contacts |
| `user_identification_data_set_cleaner_cronjob` | yes | OK | risky_mutation | yes | no | tracking cleanup task |
| `transferred_contacts_extra_transfer_rules_cronjob` | yes | FAIL then OK after fix | risky_mutation | yes | fixed | see blocker below |
| `monitor_cronjob` | yes | OK | external_integration | yes | no | can send SMS alerts |
| `hourly_doublet_task` | yes | OK | risky_mutation | yes | no | task calls can mutate/process contacts |
| `cleanup_ipc_locks` | yes | OK | safe_readonly_or_housekeeping | medium | no | deletes old disk lock files; no blind run |
| `quota_reset_cronjob` | yes | OK | risky_mutation | yes | no | resets transfer quotas |
| `airtable_sync_blaudirektcontracts` | yes | OK | external_integration | yes | no | Airtable sync; no real run |
| `airtable_sync_blaudirektdocuments` | yes | OK | external_integration | yes | no | Airtable sync; no real run |
| `airtable_sync_campaigndata` | yes | OK | external_integration | yes | no | Airtable/ad API sync; no real run |
| `airtable_sync_timedout_transferred_contacts` | yes | OK | external_integration/risky_mutation | yes | no | has `--dry-run`, but cron path is real sync; no blind run |
| `dialfire_send_crosssales` | yes | OK | external_integration/risky_mutation | yes | no | sends contacts to Dialfire |

### Fixed blocker

`transferred_contacts_extra_transfer_rules_cronjob --help` initially printed help but did not exit. The py311 process stayed alive because the command armed runtime `Timer` objects and signal handlers in `Command.__init__()`. A `timeout 45` sent SIGTERM; the command caught it as soft-kill instead of exiting, requiring manual cleanup of only that test `--help` process.

Minimal fix:

- keep `__init__()` side-effect-light;
- initialize timer attributes to `None`;
- move timer/signal setup to `_setup_runtime_guards()`;
- call `_setup_runtime_guards()` only from `handle()`, not from `--help`.

Retest:

| check | result |
|---|---|
| `transferred_contacts_extra_transfer_rules_cronjob --help` on contactapi2 py311 | OK, `RC:0`, exits normally |
| `manage.py check --settings=contactapi.settings_py311` on contactapi2 py311 | OK, no issues |
| recent `contactapi2.uwsgi.service` journal grep for traceback/500/import errors | OK, none found |

## 3. Webhooks / critical views follow-up

Baseline used: `artifacts/webhooks_tracking_url_sweep.md`, run id `webhooks-tracking-20260424T111118Z`.

Baseline summary:

| metric | value |
|---|---:|
| total routes from `webhooks.urls` + `tracking.urls` | 99 |
| safe probes executed | 66 |
| hard backend failures | 0 |
| risky/manual skipped | 33 |
| legacy/obsolete | 1 |

Critical webhook/view classifications:

| endpoint group | current status | safe tested? | risky? | needs manual review? | notes |
|---|---|---:|---:|---:|---|
| Airtable webhook routes | OK for `OPTIONS` | yes | yes | yes | non-OPTIONS can mutate local records, enqueue work, or sync Airtable |
| Dialfire upload/send/export/import routes | OK for safe `OPTIONS`/GET where classified | partial | yes | yes | uploads, email, transfer/export, or import behavior requires real payload/contact |
| `lead/*transfer*` and `leads/daily-transfer*` | OK for `OPTIONS` | yes | yes | yes | explicit transfer/category scheduling paths; no blind run |
| `lead/import/dialfire/*` and `lead/process/dialfire-product-closing/*` | OK for safe probe | yes | yes | yes | import/product-closing payload path; needs controlled data |
| fake/testlead endpoints | not executed | no | yes | yes | can create/send test leads |
| `auto-delete-email` | not executed | no | yes | yes | deletion/email semantics unclear; manual review only |
| `facebook/thumbnail-for-ad/*` | not executed | no | yes | yes | external ad API/media path |

No new hard backend failures were found in webhooks during this phase. Risky routes remain intentionally unexecuted until there is an approved payload-aware test case.

## 4. Not executed consciously

The following were not executed beyond `--help` or safe probes:

- `daily_transfer_*`: transfer contacts and can trigger business movement.
- `reschedule_*`: can reschedule verification/maintenance tasks.
- `daily_contactapi_cronjob*`: Dialfire/Airtable/task orchestration.
- `monitor_cronjob`: can send SMS alerts.
- `sipgate_check_devices`: can kill/restart process and send alert.
- Airtable sync commands: can mutate Airtable and local sync state.
- Dialfire/cross-sale commands and webhooks: can send contacts to external systems.
- Import/webhook/capture endpoints: require real payload and side-effect isolation.

## 5. Result

- Cron inventory completed for project-relevant sources.
- Cron-driven management commands were mapped to source files and classifications.
- All active cron-driven management commands now pass safe py311 `--help` startup check.
- One real py311 validation blocker was found and fixed: `transferred_contacts_extra_transfer_rules_cronjob --help` no longer hangs.
- Existing webhooks/tracking safe sweep remains valid: no hard backend failures in safe probes.
- Remaining risky cron commands and webhooks are `MANUAL_REVIEW_REQUIRED`, not py311 blockers by default.
