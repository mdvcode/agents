#!/usr/bin/env python3
"""Safe URL sweep for webhooks.urls and tracking.urls on the py311 test stand."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import site
import ssl
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_PROJECT_ROOT = "/var/www/hosts/contactapi2.static.fyi/contactapi/contactapi"
DEFAULT_BASE_URL = "https://contactapi2.static.fyi"

WEBHOOK_SAFE_GET = {
    "dialfire_cfxkvzs_phonesale_export_storno_success",
    "dialfire_cfxkvzs_phonesale_export_storno_failed",
    "dialfire_process_product_closing_export",
}
WEBHOOK_LEGACY = {
    "dialfire_cfxkvzs_phonesale_export_storno_success",
    "dialfire_cfxkvzs_phonesale_export_storno_failed",
    "dialfire_process_product_closing_export",
}
WEBHOOK_SAFE_OPTIONS_PREFIXES = (
    "airtable_webhook_",
    "dialfire_",
    "lead_transfer_with_transfermethod",
    "lead_reset_category_to_origin",
    "leads_daily_transfer_with_transfermethod",
    "transfer_method_details",
    "blaudirekt_webhook_with_status",
)
WEBHOOK_RISKY = {
    "zeo_send_testlead",
    "lead_trigger_fake",
    "lead_receive_fake",
    "facebook_thumbnail_for_ad",
    "dialfire_phonesale_uploadpolice_dev",
}

TRACKING_SAFE_GET = {
    "debug_conversion_png",
    "admin_incoming_custom_conversion_difference_report",
    "admin_incoming_custom_conversion_report_difference_ajax",
    "admin_incoming_custom_conversion_report",
    "admin_incoming_custom_conversion_report_ajax",
    "script_incoming_custom_conversion",
    "api_status",
    "api_incoming_custom_conversion_requests",
    "api_incoming_custom_conversion",
    "api_clickfunnels_contacts",
    "api_transferred_contacts",
    "api_maincategories",
    "api_maincategory",
    "api_categories",
    "api_analyze_clickfunnels_contacts",
}
TRACKING_SAFE_OPTIONS = {
    "admin_googleads_api_oauth2_authorization_flow_refresh_token",
    "admin_googleads_api_oauth2_authorization_flow_auth_url",
    "facebook_conversion_api_s2s",
    "send_sms_link",
}
TRACKING_CONDITIONAL_POST = {"api_authentication"}
TRACKING_RISKY = {
    "admin_clear_tracking_cache",
    "admin_googleads_api_oauth2_authorization_flow_set_code",
    "admin_googleads_api_oauth2_authorization_flow",
    "script_conversion_pixel",
    "script_dev_conversion_pixel",
    "conversion_png",
    "incoming_custom_conversion",
    "forward_s2s",
    "active_campaign_tag_contact",
    "active_campaign_tag_contact_extended",
    "active_campaign_tag_contact_kvzs_cancellation",
    "active_campaign_tag_contact_zzvs_cancellation",
    "wattfox_perform_s2s",
    "shopify_webhook_googleads_user_list_upload_dev",
    "shopify_webhook_googleads_user_list_upload",
    "shopify_webhook_hydrip_de_order_create",
    "shopify_webhook_order_create_to_incoming_custom_conversion",
    "shopify_webhook_hydrip_en_order_create",
    "sms_link",
    "send_sms_link_dev",
    "api_reset_cache",
    "api_taboola_account_report",
    "api_taboola_item_report_last_30d",
    "api_outbrain_ads_report_last_30d",
    "api_outbrain_ads_report_for_date",
}
TRACKING_LEGACY = {"wattfox_perform_s2s"}

SAMPLE_VALUES = {
    "ad_id": "1",
    "anything": "sample",
    "category": "sample",
    "contact_uuid_or_id": "1",
    "distance": "1",
    "extra_information": "sample",
    "filename": "sample.csv",
    "id": "1",
    "instance_id": "1",
    "product": "sample",
    "slug": "sample",
    "status": "sample",
    "transfer_method_id": "1",
    "user_id": "1",
    "uuid": "00000000-0000-0000-0000-000000000000",
    "uuid_or_id": "1",
    "zipcode": "10115",
}


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def setup_django(project_root: Path) -> None:
    sys.path.insert(0, str(project_root))
    site.addsitedir(str(project_root / "apps"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "contactapi.settings_py311")
    os.environ.setdefault("DJANGO_EXECUTED_BY_MANAGE_COMMAND", "True")

    import django

    django.setup()


def callback_name(callback: Any) -> str:
    view_class = getattr(callback, "view_class", None)
    if view_class is not None:
        return f"{view_class.__module__}.{view_class.__name__}"
    return getattr(callback, "__name__", repr(callback))


def route_regex(pattern: Any) -> str:
    regex = getattr(getattr(pattern, "pattern", None), "regex", None)
    if regex is not None:
        return regex.pattern
    return str(pattern.pattern)


def sample_path(regex: str) -> str:
    path = regex.strip()
    path = path.removeprefix("^").removesuffix("$").removesuffix(r"\Z")

    def replace_group(match: re.Match[str]) -> str:
        name = match.group("name")
        return SAMPLE_VALUES.get(name, "sample")

    path = re.sub(r"\(\?P<(?P<name>[^>]+)>[^)]*\)", replace_group, path)
    path = path.replace("\\/", "/")
    path = path.replace("\\.", ".")
    path = path.replace("?", "")
    if not path.startswith("/"):
        path = "/" + path
    return path


def classify(app: str, view: str) -> dict[str, Any]:
    if app == "webhooks":
        if view in WEBHOOK_SAFE_GET:
            result = "LEGACY" if view in WEBHOOK_LEGACY else "OK"
            return {
                "classification": "safe_get",
                "result_if_skipped": result,
                "notes": "safe no-op/obsolete GET endpoint",
            }
        if view in WEBHOOK_RISKY:
            return {
                "classification": "risky_skip",
                "result_if_skipped": "RISKY",
                "notes": "webhook/action/external integration route; manual review required",
            }
        if view.startswith(WEBHOOK_SAFE_OPTIONS_PREFIXES):
            return {
                "classification": "safe_options",
                "result_if_skipped": "MANUAL_REVIEW",
                "notes": "OPTIONS is explicitly handled; non-OPTIONS can mutate/enqueue/call integrations",
            }
        return {
            "classification": "risky_skip",
            "result_if_skipped": "RISKY",
            "notes": "unclassified webhooks route; defaulting to risky",
        }

    if app == "tracking":
        if view in TRACKING_SAFE_GET:
            return {
                "classification": "safe_get",
                "result_if_skipped": "OK",
                "notes": "read/report/script/API route; authenticated APIs are probed only to auth boundary",
            }
        if view in TRACKING_SAFE_OPTIONS:
            return {
                "classification": "safe_options",
                "result_if_skipped": "MANUAL_REVIEW",
                "notes": "OPTIONS/CORS probe only; non-OPTIONS may expose sensitive state or side effects",
            }
        if view in TRACKING_CONDITIONAL_POST:
            return {
                "classification": "conditional_post",
                "result_if_skipped": "OK",
                "notes": "empty POST validates auth branch and should not mutate",
            }
        if view in TRACKING_RISKY:
            result = "LEGACY" if view in TRACKING_LEGACY else "RISKY"
            return {
                "classification": "risky_skip",
                "result_if_skipped": result,
                "notes": "capture/webhook/mutation/external API route; manual review required",
            }
        return {
            "classification": "risky_skip",
            "result_if_skipped": "RISKY",
            "notes": "unclassified tracking route; defaulting to risky",
        }

    return {"classification": "risky_skip", "result_if_skipped": "RISKY", "notes": "unknown app"}


def extract_routes() -> list[dict[str, Any]]:
    import importlib

    routes: list[dict[str, Any]] = []
    for app, module_name in (("webhooks", "webhooks.urls"), ("tracking", "tracking.urls")):
        module = importlib.import_module(module_name)
        for index, pattern in enumerate(getattr(module, "urlpatterns_legacy")):
            view = callback_name(pattern.callback)
            regex = route_regex(pattern)
            item = {
                "app": app,
                "index": index,
                "url_pattern": regex,
                "sample_path": sample_path(regex),
                "view": view,
                "route_name": getattr(pattern, "name", None),
            }
            item.update(classify(app, view))
            routes.append(item)
    return routes


def make_request(opener: urllib.request.OpenerDirector, base_url: str, method: str, path: str, timeout: int, run_id: str) -> dict[str, Any]:
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    data = b"{}" if method == "POST" else None
    headers = {
        "Accept": "*/*",
        "User-Agent": f"contactapi-py311-url-sweep/{run_id}",
        "X-Py311-Sweep-Run-Id": run_id,
    }
    if method == "POST":
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.time()
    try:
        response = opener.open(request, timeout=timeout)
        body = response.read(4000)
        return {
            "method": method,
            "url": url,
            "status_code": response.status,
            "duration_ms": int((time.time() - started) * 1000),
            "headers": dict(response.headers),
            "body_excerpt": body.decode("utf-8", errors="replace")[:1000],
            "error": None,
        }
    except urllib.error.HTTPError as exc:
        body = exc.read(4000)
        return {
            "method": method,
            "url": url,
            "status_code": exc.code,
            "duration_ms": int((time.time() - started) * 1000),
            "headers": dict(exc.headers),
            "body_excerpt": body.decode("utf-8", errors="replace")[:1000],
            "error": None,
        }
    except Exception as exc:
        return {
            "method": method,
            "url": url,
            "status_code": None,
            "duration_ms": int((time.time() - started) * 1000),
            "headers": {},
            "body_excerpt": "",
            "error": {
                "type": exc.__class__.__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(limit=6),
            },
        }


def classify_http_result(route: dict[str, Any], probes: list[dict[str, Any]]) -> dict[str, Any]:
    if not probes:
        return {
            "result": route["result_if_skipped"],
            "traceback_present": False,
            "needs_fix": False,
            "root_cause": "not automatically probed",
            "regression_class": "unclear",
        }
    last = probes[-1]
    status_code = last.get("status_code")
    if status_code is None:
        return {
            "result": "FAIL",
            "traceback_present": False,
            "needs_fix": True,
            "root_cause": last.get("error", {}).get("message", "request exception"),
            "regression_class": "unclear",
        }
    if status_code >= 500:
        return {
            "result": "FAIL",
            "traceback_present": "Traceback" in last.get("body_excerpt", ""),
            "needs_fix": True,
            "root_cause": f"HTTP {status_code}",
            "regression_class": "unclear",
        }
    if status_code in (401, 403):
        note = "permission/auth boundary reached; not counted as backend failure"
    elif status_code == 404:
        note = "missing sample object/token; not counted as backend failure"
    elif status_code == 405:
        note = "method not allowed; not counted as backend failure"
    elif status_code == 400:
        note = "payload/context required; not counted as backend failure"
    else:
        note = f"HTTP {status_code}"
    return {
        "result": "OK",
        "traceback_present": False,
        "needs_fix": False,
        "root_cause": note,
        "regression_class": "unclear",
    }


def probe_routes(
    routes: list[dict[str, Any]],
    base_url: str,
    timeout: int,
    run_id: str,
    verify_ssl: bool,
) -> list[dict[str, Any]]:
    ssl_context = ssl.create_default_context() if verify_ssl else ssl._create_unverified_context()
    opener = urllib.request.build_opener(
        NoRedirectHandler,
        urllib.request.HTTPSHandler(context=ssl_context),
    )
    tested: list[dict[str, Any]] = []
    for route in routes:
        probes: list[dict[str, Any]] = []
        classification = route["classification"]
        if classification == "safe_get":
            first = make_request(opener, base_url, "HEAD", route["sample_path"], timeout, run_id)
            probes.append(first)
            if first.get("status_code") == 405:
                probes.append(make_request(opener, base_url, "GET", route["sample_path"], timeout, run_id))
        elif classification == "safe_options":
            probes.append(make_request(opener, base_url, "OPTIONS", route["sample_path"], timeout, run_id))
        elif classification == "conditional_post" and route["view"] == "api_authentication":
            probes.append(make_request(opener, base_url, "POST", route["sample_path"], timeout, run_id))

        result = dict(route)
        result["tested_methods"] = [probe["method"] for probe in probes]
        result["probes"] = probes
        result.update(classify_http_result(route, probes))
        tested.append(result)
    return tested


def write_markdown(payload: dict[str, Any], path: Path) -> None:
    routes = payload["routes"]
    problems = [route for route in routes if route["result"] == "FAIL"]
    risky = [route for route in routes if route["classification"] == "risky_skip"]
    legacy = [route for route in routes if route["result"] == "LEGACY"]

    lines = [
        "# webhooks/tracking py311 URL sweep",
        "",
        f"- run_id: `{payload['run_id']}`",
        f"- base_url: `{payload['base_url']}`",
        f"- started_at: `{payload['started_at']}`",
        f"- finished_at: `{payload['finished_at']}`",
        f"- total routes: `{len(routes)}`",
        f"- hard failures: `{len(problems)}`",
        f"- risky/manual skipped: `{len(risky)}`",
        f"- legacy/obsolete: `{len(legacy)}`",
        "",
        "## Route Results",
        "",
        "| app | pattern | view | classification | methods | result | status | traceback | notes |",
        "|---|---|---|---|---|---|---:|---|---|",
    ]
    for route in routes:
        status = ""
        if route["probes"]:
            status = str(route["probes"][-1].get("status_code"))
        lines.append(
            "| {app} | `{pattern}` | `{view}` | `{classification}` | `{methods}` | {result} | {status} | {traceback} | {notes} |".format(
                app=route["app"],
                pattern=route["url_pattern"].replace("|", "\\|"),
                view=route["view"],
                classification=route["classification"],
                methods=",".join(route["tested_methods"]),
                result=route["result"],
                status=status,
                traceback="yes" if route["traceback_present"] else "no",
                notes=(route["root_cause"] or route["notes"]).replace("|", "\\|"),
            )
        )

    lines.extend(["", "## Problems", ""])
    if problems:
        for route in problems:
            probe = route["probes"][-1] if route["probes"] else {}
            lines.append(
                f"- `{probe.get('method')}` `{probe.get('url')}` -> `{probe.get('status_code')}`: {route['root_cause']}"
            )
    else:
        lines.append("- No hard backend failures in automatically probed routes.")

    lines.extend(["", "## Risky / Manual Review", ""])
    for route in risky:
        lines.append(f"- `{route['app']}` `{route['url_pattern']}` -> `{route['view']}`: {route['notes']}")

    lines.extend(["", "## Legacy / Obsolete", ""])
    if legacy:
        for route in legacy:
            lines.append(f"- `{route['app']}` `{route['url_pattern']}` -> `{route['view']}`")
    else:
        lines.append("- None detected by the conservative classifier.")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--output", default="/tmp/webhooks_tracking_url_sweep.json")
    parser.add_argument("--markdown-output", default="/tmp/webhooks_tracking_url_sweep.md")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--verify-ssl", action="store_true", help="Verify HTTPS certificates during probes.")
    args = parser.parse_args()

    run_id = "webhooks-tracking-" + dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    started_at = dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    setup_django(Path(args.project_root))
    routes = extract_routes()
    routes = probe_routes(routes, args.base_url, args.timeout, run_id, verify_ssl=args.verify_ssl)
    finished_at = dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    payload = {
        "run_id": run_id,
        "base_url": args.base_url,
        "project_root": args.project_root,
        "started_at": started_at,
        "finished_at": finished_at,
        "routes": routes,
    }
    output_path = Path(args.output)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_markdown(payload, Path(args.markdown_output))
    print(json.dumps({"run_id": run_id, "json": str(output_path), "markdown": args.markdown_output, "routes": len(routes)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
