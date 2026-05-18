# webhooks/tracking py311 URL sweep

- run_id: `webhooks-tracking-20260424T111118Z`
- base_url: `https://contactapi2.static.fyi`
- started_at: `2026-04-24T11:11:18Z`
- finished_at: `2026-04-24T11:11:22Z`
- total routes: `99`
- hard failures: `0`
- risky/manual skipped: `33`
- legacy/obsolete: `1`

## Route Results

| app | pattern | view | classification | methods | result | status | traceback | notes |
|---|---|---|---|---|---|---:|---|---|
| webhooks | `^zeo-testlead/$` | `zeo_send_testlead` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| webhooks | `^auto-delete-email/$` | `auto_delete_email_webhook` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| webhooks | `^airtable-webhook/blaudirektcontract/sync$` | `airtable_webhook_blaudirektcontract_sync` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^airtable-webhook/dialfireabrechnungquali/trigger$` | `airtable_webhook_dialfire_accounting_lead_qualification_trigger` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^airtable-webhook/callanalyzer/created$` | `airtable_webhook_callanalyzer_created` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^airtable-webhook/cancelconditions/created$` | `airtable_webhook_cancelconditions_created` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^airtable-webhook/cancelconditions/changed$` | `airtable_webhook_cancelconditions_changed` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^airtable-webhook/deal-delivery/new$` | `airtable_webhook_deal_delivery_new` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^airtable-webhook/nottransferreddata/triggertransfer$` | `airtable_webhook_nottransferreddata_triggertransfer` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^airtable-webhook/dialfiretag/query$` | `airtable_webhook_dialfire_tag_query` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^lead/trigger/fake/(?P<user_id>.{1,255})/$` | `lead_trigger_fake` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| webhooks | `^lead/receive/fake/(?P<user_id>.{1,255})/$` | `lead_receive_fake` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| webhooks | `^airtable-webhook/humancallanalyzer/status-updated$` | `airtable_webhook_human_call_analyzer_status_updated` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^airtable-webhook/dialfireimportsettings/triggertransfer$` | `airtable_webhook_dialfire_import_settings_trigger_transfer` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^airtable-webhook/invoice/created$` | `airtable_webhook_invoice_created` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^airtable-webhook/invoice/accounted` | `airtable_webhook_invoice_accounted` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^airtable-webhook/invoice/changed$` | `airtable_webhook_invoice_changed` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^airtable-webhook/contacts-zipcodes-average/query$` | `airtable_webhook_contacts_zipcodes_average_query` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^airtable-webhook/transfermethod/changed$` | `airtable_webhook_transfermethod_changed` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^airtable-webhook/transfermethod/sync$` | `airtable_webhook_transfermethod_sync` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^airtable-webhook/transfermethod/triggertestlead$` | `airtable_webhook_transfermethod_triggertestlead` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^airtable-webhook/sendmail$` | `airtable_webhook_sendmail` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^blaudirekt-webhook/(?P<status>.{1,255})$` | `blaudirekt_webhook_with_status` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^dialfire/pflege/(?P<id>.{1,255})/uploadapplication/$` | `dialfire_pflege_upload_application` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^dialfire/cfxzzvs_phonesale/(?P<id>.{1,255})/uploadpolice/$` | `dialfire_cfxzzvs_phonesale_uploadpolice` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^dialfire/cfxzzvs_phonesale/(?P<id>.{1,255})/send-cancellation-mail/$` | `dialfire_cfxzzvs_phonesale_send_cancellation_mail` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^dialfire/cfxzzvs_phonesale_new/(?P<id>.{1,255})/uploadpolice/$` | `dialfire_cfxzzvs_phonesale_new_uploadpolice` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^dialfire/cfxzzvs_phonesale_new/(?P<id>.{1,255})/send-cancellation-mail/$` | `dialfire_cfxzzvs_phonesale_new_send_cancellation_mail` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^dialfire/cfxkvzs_phonesale/export_storno_success/$` | `dialfire_cfxkvzs_phonesale_export_storno_success` | `safe_get` | `HEAD` | OK | 200 | no | HTTP 200 |
| webhooks | `^dialfire/cfxkvzs_phonesale/export_storno_failed/$` | `dialfire_cfxkvzs_phonesale_export_storno_failed` | `safe_get` | `HEAD` | OK | 200 | no | HTTP 200 |
| webhooks | `^dialfire/cfxkvzs_phonesale/(?P<id>.{1,255})/send-cancellation-mail/$` | `dialfire_cfxkvzs_phonesale_send_cancellation_mail` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^dialfire/cfxkvzs_phonesale/(?P<id>.{1,255})/uploadpolice/$` | `dialfire_cfxkvzs_phonesale_uploadpolice` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^dialfire/cfxkvzs_phonesale/(?P<id>.{1,255})/uploadpolice/dev/$` | `dialfire_phonesale_uploadpolice_dev` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| webhooks | `^dialfire/cfxkvz/(?P<id>.{1,255})/export-to-superchat/$` | `dialfire_cfxkvz_export_to_superchat` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^dialfire/cfxkvz/(?P<id>.{1,255})/send-offer-email/$` | `dialfire_cfxkvz_send_offer_email` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^dialfire/cfxxxx_storno_phonesale/(?P<id>.{1,255})/send-cancellation-mail/$` | `dialfire_cfxxxx_storno_phonesale_send_cancellation_mail` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^dialfire/cfxxxx_storno_phonesale/(?P<id>.{1,255})/uploadpolice/$` | `dialfire_cfxxxx_storno_phonesale_uploadpolice` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^dialfire/cfxxxx_customercare_phonesale/(?P<id>.{1,255})/send-cancellation-mail/$` | `dialfire_cfxxxx_customercare_phonesale_send_cancellation_mail` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^dialfire/cfxxxx_customercare_phonesale/(?P<id>.{1,255})/uploadpolice/$` | `dialfire_cfxxxx_customercare_phonesale_uploadpolice` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^dialfire/cfxxxx_customercare_phonesale/(?P<id>.{1,255})/send-offer-email/$` | `dialfire_cfxxxx_customercare_phonesale_send_offer_email` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^dialfire/cfxkvz/(?P<id>.{1,255})/superchat-trigger-automation/$` | `dialfire_cfxkvz_superchat_trigger_automation` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^dialfire/cfxkvz/superchat-trigger-automation-from-export/$` | `dialfire_cfxkvz_superchat_trigger_automation_from_export` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^lead/(?P<contact_uuid_or_id>.{1,255})/transfer-with-transfermethod/(?P<transfer_method_id>.{1,255})/$` | `lead_transfer_with_transfermethod` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^lead/(?P<contact_uuid_or_id>.{1,255})/transfer-with-transfermethod/(?P<transfer_method_id>.{1,255})$` | `lead_transfer_with_transfermethod` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^lead/(?P<contact_uuid_or_id>.{1,255})/reset-category-to-origin-and-schedule/$` | `lead_reset_category_to_origin` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^lead/(?P<contact_uuid_or_id>.{1,255})/reset-category-to-origin-and-schedule/$` | `lead_reset_category_to_origin` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^leads/daily-transfer-with-transfermethod/(?P<transfer_method_id>.{1,255})/$` | `leads_daily_transfer_with_transfermethod` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^transfermethod/(?P<transfer_method_id>.{1,255})/details$` | `transfer_method_details` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^transfermethod/(?P<transfer_method_id>.{1,255})/details/$` | `transfer_method_details` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^lead/import/dialfire/(?P<product>.{1,255})/$` | `dialfire_lead_import` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^lead/import-dialfire/(?P<product>.{1,255})/$` | `dialfire_lead_import` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| webhooks | `^lead/process/dialfire-product-closing/(?P<product>.{1,255})/$` | `dialfire_process_product_closing_export` | `safe_get` | `HEAD` | OK | 200 | no | HTTP 200 |
| webhooks | `^facebook/thumbnail-for-ad/(?P<ad_id>.{1,255})/$` | `facebook_thumbnail_for_ad` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^admin/tracking_api_reset_cache/` | `admin_clear_tracking_cache` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^admin/tracking/googleadsapiauthorization/oauth2flow/refreshtoken/` | `admin_googleads_api_oauth2_authorization_flow_refresh_token` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| tracking | `^admin/tracking/googleadsapiauthorization/oauth2flow/set-code/` | `admin_googleads_api_oauth2_authorization_flow_set_code` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^admin/tracking/googleadsapiauthorization/oauth2flow/authurl/` | `admin_googleads_api_oauth2_authorization_flow_auth_url` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| tracking | `^admin/tracking/googleadsapiauthorization/oauth2flow/` | `admin_googleads_api_oauth2_authorization_flow` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^admin/tracking/incomingcustomconversion/report-difference/$` | `admin_incoming_custom_conversion_difference_report` | `safe_get` | `HEAD` | OK | 302 | no | HTTP 302 |
| tracking | `^admin/tracking/incomingcustomconversion/report-difference/ajax/$` | `admin_incoming_custom_conversion_report_difference_ajax` | `safe_get` | `HEAD` | OK | 200 | no | HTTP 200 |
| tracking | `^admin/tracking/incomingcustomconversion/report/$` | `admin_incoming_custom_conversion_report` | `safe_get` | `HEAD` | OK | 302 | no | HTTP 302 |
| tracking | `^admin/tracking/incomingcustomconversion/report/ajax/$` | `admin_incoming_custom_conversion_report_ajax` | `safe_get` | `HEAD` | OK | 200 | no | HTTP 200 |
| tracking | `^tracking/script/conversion-pixel.js$` | `script_conversion_pixel` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^tracking/script/dev/conversion-pixel.js$` | `script_dev_conversion_pixel` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^tracking/conversion.png$` | `conversion_png` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^tracking/custom-conversion.js$` | `script_incoming_custom_conversion` | `safe_get` | `HEAD` | OK | 200 | no | HTTP 200 |
| tracking | `^tracking/custom-conversion/(?P<slug>.{1,255})/$` | `incoming_custom_conversion` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^tracking/forward-s2s/$` | `forward_s2s` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^tracking/facebook-conversion-api/$` | `facebook_conversion_api_s2s` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| tracking | `^tracking/active-campaign-tag-contact/$` | `active_campaign_tag_contact` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^tracking/active-campaign-tag-contact-extended/$` | `active_campaign_tag_contact_extended` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^tracking/active-campaign-tag-contact-kvzs/$` | `active_campaign_tag_contact_kvzs_cancellation` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^tracking/active-campaign-tag-contact-kvzs-cancellation/$` | `active_campaign_tag_contact_kvzs_cancellation` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^tracking/active-campaign-tag-contact-zzvs-cancellation/$` | `active_campaign_tag_contact_zzvs_cancellation` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^tracking/wattfox-perform-s2s/$` | `wattfox_perform_s2s` | `risky_skip` | `` | LEGACY |  | no | not automatically probed |
| tracking | `^tracking/shopify-webhook-gads-user-list-upload/dev/(?P<instance_id>.{1,255})/$` | `shopify_webhook_googleads_user_list_upload_dev` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^tracking/shopify-webhook-gads-user-list-upload/(?P<instance_id>.{1,255})/$` | `shopify_webhook_googleads_user_list_upload` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^tracking/shopify-webhook-hydrip-de-order-create/$` | `shopify_webhook_hydrip_de_order_create` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^tracking/shopify-webhook-order-create/(?P<slug>.{1,255})/$` | `shopify_webhook_order_create_to_incoming_custom_conversion` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^tracking/shopify-webhook-hydrip-en-order-create/$` | `shopify_webhook_hydrip_en_order_create` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^sms-link/(?P<slug>.{1,255})/$` | `sms_link` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^sms-link/(?P<slug>.{1,255})/(?P<extra_information>.{1,255})/$` | `sms_link` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^send-sms-link/$` | `send_sms_link` | `safe_options` | `OPTIONS` | OK | 200 | no | HTTP 200 |
| tracking | `^send-sms-link/dev/$` | `send_sms_link_dev` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^tracking/api/auth/$` | `api_authentication` | `conditional_post` | `POST` | OK | 403 | no | permission/auth boundary reached; not counted as backend failure |
| tracking | `^tracking/api/status/$` | `api_status` | `safe_get` | `HEAD` | OK | 401 | no | permission/auth boundary reached; not counted as backend failure |
| tracking | `^tracking/api/resetcache/$` | `api_reset_cache` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^tracking/api/incomingcustomconversionrequests/$` | `api_incoming_custom_conversion_requests` | `safe_get` | `HEAD` | OK | 401 | no | permission/auth boundary reached; not counted as backend failure |
| tracking | `^tracking/api/incomingcustomconversion/$` | `api_incoming_custom_conversion` | `safe_get` | `HEAD` | OK | 401 | no | permission/auth boundary reached; not counted as backend failure |
| tracking | `^tracking/api/clickfunnelscontacts/$` | `api_clickfunnels_contacts` | `safe_get` | `HEAD` | OK | 401 | no | permission/auth boundary reached; not counted as backend failure |
| tracking | `^tracking/api/transferredcontacts/$` | `api_transferred_contacts` | `safe_get` | `HEAD` | OK | 401 | no | permission/auth boundary reached; not counted as backend failure |
| tracking | `^tracking/api/maincategories/$` | `api_maincategories` | `safe_get` | `HEAD` | OK | 401 | no | permission/auth boundary reached; not counted as backend failure |
| tracking | `^tracking/api/maincategory/$` | `api_maincategory` | `safe_get` | `HEAD` | OK | 401 | no | permission/auth boundary reached; not counted as backend failure |
| tracking | `^tracking/api/categories/$` | `api_categories` | `safe_get` | `HEAD` | OK | 401 | no | permission/auth boundary reached; not counted as backend failure |
| tracking | `^tracking/api/taboola-account-report/$` | `api_taboola_account_report` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^tracking/api/taboola-item-report-last-30d/$` | `api_taboola_item_report_last_30d` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^tracking/api/outbrain-promotedcontent-report-last-30d/$` | `api_outbrain_ads_report_last_30d` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^tracking/api/outbrain-promotedcontent-report-for-date/$` | `api_outbrain_ads_report_for_date` | `risky_skip` | `` | RISKY |  | no | not automatically probed |
| tracking | `^tracking/api/analyze/clickfunnelscontacts/$` | `api_analyze_clickfunnels_contacts` | `safe_get` | `HEAD` | OK | 401 | no | permission/auth boundary reached; not counted as backend failure |

## Problems

- No hard backend failures in automatically probed routes.

## Risky / Manual Review

- `webhooks` `^zeo-testlead/$` -> `zeo_send_testlead`: webhook/action/external integration route; manual review required
- `webhooks` `^auto-delete-email/$` -> `auto_delete_email_webhook`: unclassified webhooks route; defaulting to risky
- `webhooks` `^lead/trigger/fake/(?P<user_id>.{1,255})/$` -> `lead_trigger_fake`: webhook/action/external integration route; manual review required
- `webhooks` `^lead/receive/fake/(?P<user_id>.{1,255})/$` -> `lead_receive_fake`: webhook/action/external integration route; manual review required
- `webhooks` `^dialfire/cfxkvzs_phonesale/(?P<id>.{1,255})/uploadpolice/dev/$` -> `dialfire_phonesale_uploadpolice_dev`: webhook/action/external integration route; manual review required
- `webhooks` `^facebook/thumbnail-for-ad/(?P<ad_id>.{1,255})/$` -> `facebook_thumbnail_for_ad`: webhook/action/external integration route; manual review required
- `tracking` `^admin/tracking_api_reset_cache/` -> `admin_clear_tracking_cache`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^admin/tracking/googleadsapiauthorization/oauth2flow/set-code/` -> `admin_googleads_api_oauth2_authorization_flow_set_code`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^admin/tracking/googleadsapiauthorization/oauth2flow/` -> `admin_googleads_api_oauth2_authorization_flow`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/script/conversion-pixel.js$` -> `script_conversion_pixel`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/script/dev/conversion-pixel.js$` -> `script_dev_conversion_pixel`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/conversion.png$` -> `conversion_png`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/custom-conversion/(?P<slug>.{1,255})/$` -> `incoming_custom_conversion`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/forward-s2s/$` -> `forward_s2s`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/active-campaign-tag-contact/$` -> `active_campaign_tag_contact`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/active-campaign-tag-contact-extended/$` -> `active_campaign_tag_contact_extended`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/active-campaign-tag-contact-kvzs/$` -> `active_campaign_tag_contact_kvzs_cancellation`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/active-campaign-tag-contact-kvzs-cancellation/$` -> `active_campaign_tag_contact_kvzs_cancellation`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/active-campaign-tag-contact-zzvs-cancellation/$` -> `active_campaign_tag_contact_zzvs_cancellation`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/wattfox-perform-s2s/$` -> `wattfox_perform_s2s`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/shopify-webhook-gads-user-list-upload/dev/(?P<instance_id>.{1,255})/$` -> `shopify_webhook_googleads_user_list_upload_dev`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/shopify-webhook-gads-user-list-upload/(?P<instance_id>.{1,255})/$` -> `shopify_webhook_googleads_user_list_upload`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/shopify-webhook-hydrip-de-order-create/$` -> `shopify_webhook_hydrip_de_order_create`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/shopify-webhook-order-create/(?P<slug>.{1,255})/$` -> `shopify_webhook_order_create_to_incoming_custom_conversion`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/shopify-webhook-hydrip-en-order-create/$` -> `shopify_webhook_hydrip_en_order_create`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^sms-link/(?P<slug>.{1,255})/$` -> `sms_link`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^sms-link/(?P<slug>.{1,255})/(?P<extra_information>.{1,255})/$` -> `sms_link`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^send-sms-link/dev/$` -> `send_sms_link_dev`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/api/resetcache/$` -> `api_reset_cache`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/api/taboola-account-report/$` -> `api_taboola_account_report`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/api/taboola-item-report-last-30d/$` -> `api_taboola_item_report_last_30d`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/api/outbrain-promotedcontent-report-last-30d/$` -> `api_outbrain_ads_report_last_30d`: capture/webhook/mutation/external API route; manual review required
- `tracking` `^tracking/api/outbrain-promotedcontent-report-for-date/$` -> `api_outbrain_ads_report_for_date`: capture/webhook/mutation/external API route; manual review required

## Legacy / Obsolete

- `tracking` `^tracking/wattfox-perform-s2s/$` -> `wattfox_perform_s2s`
