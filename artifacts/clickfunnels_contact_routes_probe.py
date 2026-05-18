#!/usr/bin/env python3
"""Focused py311 probe for clickfunnels contact-specific routes on contactapi2."""

from __future__ import annotations

import argparse
import contextlib
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


def progress(message: str) -> None:
    print(f"[probe] {message}", file=sys.stderr, flush=True)


def setup_django(project_root: Path) -> None:
    sys.path.insert(0, str(project_root))
    site.addsitedir(str(project_root / "apps"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "contactapi.settings_py311")
    os.environ.setdefault("DJANGO_EXECUTED_BY_MANAGE_COMMAND", "True")

    import django

    django.setup()


def safe_error(exc: BaseException) -> dict[str, str]:
    return {
        "type": exc.__class__.__name__,
        "message": str(exc),
        "traceback": traceback.format_exc(limit=8),
    }


def contact_summary(contact: Any | None) -> dict[str, Any] | None:
    if contact is None:
        return None
    return {
        "id": contact.id,
        "uuid": getattr(contact, "uuid", None),
        "category": contact.category,
    }


def token_categories(token: Any) -> list[str]:
    try:
        return [str(category).strip() for category in (token.categories or []) if str(category).strip()]
    except Exception:
        return []


def has_address(contact: Any) -> bool:
    return bool(contact and contact.street and contact.zipcode and contact.city)


def has_kvz_file_upload(contact: Any) -> bool:
    try:
        for file_upload in contact.file_uploads.all():
            data = file_upload.data if isinstance(file_upload.data, dict) else {}
            if data.get("dialfire_product_name") != "kvz":
                continue
            file_path = file_upload.get_file_path()
            if file_path is None or file_path.exists():
                return True
    except Exception:
        return False
    return False


def has_dialfire_transfer(contact: Any) -> bool:
    from django.db.models import Q

    try:
        return contact.transferred_contacts.filter(
            Q(transfer_method__id=478) | Q(transfer_method__name__icontains="transfer_to_dialfire_api")
        ).exists()
    except Exception:
        return False


def find_token_and_contact() -> dict[str, Any]:
    from clickfunnels.models import ClickfunnelsContact
    from core.models import ContactPDFInfoToken
    from django.core.exceptions import FieldDoesNotExist

    try:
        ClickfunnelsContact._meta.get_field("uuid")
        uuid_field_present = True
    except FieldDoesNotExist:
        uuid_field_present = False

    exact_token_match = None
    wildcard_candidate = None
    tokens_checked = 0
    for token in ContactPDFInfoToken.objects.exclude(revoked=True).exclude(value__isnull=True).order_by("-id")[:100]:
        tokens_checked += 1
        categories = token_categories(token)
        if not categories:
            continue
        if "*" in categories:
            contact = ClickfunnelsContact.objects.exclude(category__in=["", None]).order_by("-id").first()
            if contact is not None and wildcard_candidate is None:
                wildcard_candidate = (token, contact, categories)
            continue
        contact = ClickfunnelsContact.objects.filter(category__in=categories).order_by("-id").first()
        if contact is not None:
            exact_token_match = (token, contact, categories)
            break

    token, contact, categories = exact_token_match or wildcard_candidate or (None, None, [])
    uuid_value = getattr(contact, "uuid", None) if contact is not None else None
    lookup_id = None
    uuid_int_matches = None
    lookup_error = None

    if contact is not None and uuid_value is not None:
        try:
            from uuid import UUID

            uuid_int_matches = UUID(uuid_value).int == contact.id
            lookup_id = (
                ClickfunnelsContact.objects.filter(id__is_uuid=uuid_value)
                .values_list("id", flat=True)
                .first()
            )
        except Exception as exc:
            lookup_error = safe_error(exc)

    return {
        "tokens_checked": tokens_checked,
        "token_found": token is not None,
        "token_id": getattr(token, "id", None),
        "token_categories": categories,
        "token_uses_wildcard": "*" in categories,
        "contact": contact_summary(contact),
        "uuid_field_present": uuid_field_present,
        "uuid_property_present": bool(uuid_value),
        "uuid_int_matches_id": uuid_int_matches,
        "id_is_uuid_lookup_id": lookup_id,
        "id_is_uuid_lookup_matches": bool(contact is not None and lookup_id == contact.id),
        "lookup_error": lookup_error,
        "_token_value": getattr(token, "value", None),
        "_contact": contact,
    }


def find_pdf_contacts(limit: int, include_letterxpress: bool) -> dict[str, Any]:
    from clickfunnels.models import (
        CATEGORY_CFXKVZ2,
        CATEGORY_CFXKVZS,
        CATEGORY_CFXKVZS_HEYFLOW,
        CATEGORY_CFXKVZS_WEBFLOW,
        ClickfunnelsContact,
    )
    from core.models import FileUpload

    relevant_categories = [
        CATEGORY_CFXKVZS,
        CATEGORY_CFXKVZ2,
        CATEGORY_CFXKVZS_HEYFLOW,
        CATEGORY_CFXKVZS_WEBFLOW,
        "cfxkvzs_alternative_webflow",
        "cfxkvzs_phonesale",
    ]

    cancellation_contact = None
    letter_contact = None

    qs = (
        ClickfunnelsContact.objects.filter(category__in=relevant_categories)
        .exclude(street__in=["", None])
        .exclude(zipcode__in=["", None])
        .exclude(city__in=["", None])
        .order_by("-id")
    )
    for contact in qs[:limit]:
        if cancellation_contact is None:
            cancellation_contact = contact
        if (
            letter_contact is None
            and not contact.is_from_dialfire
            and has_dialfire_transfer(contact)
            and has_kvz_file_upload(contact)
        ):
            letter_contact = contact
            break

    if include_letterxpress and letter_contact is None:
        for file_upload in FileUpload.objects.order_by("-id")[:limit]:
            data = file_upload.data if isinstance(file_upload.data, dict) else {}
            if data.get("dialfire_product_name") != "kvz":
                continue
            for contact in file_upload.clickfunnels_contacts.all():
                if (
                    contact.category in relevant_categories
                    and has_address(contact)
                    and not contact.is_from_dialfire
                    and has_dialfire_transfer(contact)
                ):
                    letter_contact = contact
                    break
            if letter_contact is not None:
                break

    return {
        "cancellation_contact": contact_summary(cancellation_contact),
        "letter_contact": contact_summary(letter_contact),
        "letterxpress_search_enabled": include_letterxpress,
        "_cancellation_contact": cancellation_contact,
        "_letter_contact": letter_contact,
    }


def http_get(base_url: str, path: str, query: dict[str, str] | None = None, timeout: int = 30) -> dict[str, Any]:
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    if query:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    context = ssl._create_unverified_context()
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "contactapi-py311-clickfunnels-contact-probe"},
        method="GET",
    )
    started = time.time()
    try:
        response = urllib.request.urlopen(request, timeout=timeout, context=context)
        body = response.read(256)
        return {
            "path": path,
            "status_code": response.status,
            "duration_ms": int((time.time() - started) * 1000),
            "content_type": response.headers.get("Content-Type"),
            "content_length": response.headers.get("Content-Length"),
            "pdf_magic": body.startswith(b"%PDF"),
            "error": None,
        }
    except urllib.error.HTTPError as exc:
        body = exc.read(256)
        return {
            "path": path,
            "status_code": exc.code,
            "duration_ms": int((time.time() - started) * 1000),
            "content_type": exc.headers.get("Content-Type"),
            "content_length": exc.headers.get("Content-Length"),
            "pdf_magic": body.startswith(b"%PDF"),
            "error": None,
        }
    except Exception as exc:
        return {
            "path": path,
            "status_code": None,
            "duration_ms": int((time.time() - started) * 1000),
            "content_type": None,
            "content_length": None,
            "pdf_magic": False,
            "error": safe_error(exc),
        }


def probe_pdf_routes(base_url: str, token_data: dict[str, Any], pdf_data: dict[str, Any], timeout: int) -> dict[str, Any]:
    probes: dict[str, Any] = {}
    contact = token_data.get("_contact")
    token = token_data.get("_token_value")
    if contact is not None:
        probes["auskunft_id_with_token"] = http_get(
            base_url,
            f"/clickfunnels/contact/id/{contact.id}/auskunft.pdf",
            query={"token": token} if token else None,
            timeout=timeout,
        )
        probes["auskunft_uuid"] = http_get(
            base_url,
            f"/clickfunnels/contact/{contact.uuid}/auskunft.pdf",
            timeout=timeout,
        )

    cancellation_contact = pdf_data.get("_cancellation_contact")
    if cancellation_contact is not None:
        probes["widerruf_id"] = http_get(
            base_url,
            f"/clickfunnels/contact/id/{cancellation_contact.id}/cfxkvzs_widerruf.pdf",
            timeout=timeout,
        )
        probes["widerruf_uuid"] = http_get(
            base_url,
            f"/clickfunnels/contact/{cancellation_contact.uuid}/cfxkvzs_widerruf.pdf",
            timeout=timeout,
        )

    letter_contact = pdf_data.get("_letter_contact")
    if letter_contact is not None:
        probes["letterxpress_id"] = http_get(
            base_url,
            f"/clickfunnels/contact/id/{letter_contact.id}/cfxkvzs_letterxpress.pdf",
            timeout=timeout,
        )
        probes["letterxpress_uuid"] = http_get(
            base_url,
            f"/clickfunnels/contact/{letter_contact.uuid}/cfxkvzs_letterxpress.pdf",
            timeout=timeout,
        )
    return probes


def find_saved_request(kind: str, limit: int) -> dict[str, Any]:
    from core.models import SavedHttpRequest

    if kind == "heyflow":
        needle = "import-lead-heyflow"
        pattern = re.compile(r"/import-lead-heyflow/(?P<category>[^/]+)/")
    else:
        needle = "webflow-ajax"
        pattern = re.compile(r"/webflow-ajax/(?P<category>[^/]+)/")

    # Avoid a full-table icontains scan on this large legacy table. Walk recent
    # primary keys in indexed order and filter the route string in Python.
    checked = 0
    batch_size = min(500, max(50, limit))
    seen = 0
    cursor = None
    while seen < limit:
        queryset = SavedHttpRequest.objects.all()
        if cursor is not None:
            queryset = queryset.filter(id__lt=cursor)
        rows = list(queryset.order_by("-id").values("id", "path", "full_path")[:batch_size])
        if not rows:
            break
        cursor = rows[-1]["id"]
        seen += len(rows)

        candidate_ids = []
        for row in rows:
            checked += 1
            full_path = row.get("full_path") or row.get("path") or ""
            path = row.get("path") or ""
            if needle not in full_path and needle not in path:
                continue
            match = pattern.search(full_path or path)
            if match is None:
                continue
            candidate_ids.append((row["id"], match.group("category")))

        for saved_request_id, category in candidate_ids:
            saved_request = SavedHttpRequest.objects.get(id=saved_request_id)
            full_path = saved_request.get_full_path() or saved_request.path
            method = saved_request.method
            if method != "POST":
                continue
            if kind == "heyflow" and not saved_request.body:
                continue
            if kind == "webflow" and not saved_request.POST:
                continue
            return {
                "found": True,
                "checked": checked,
                "id": saved_request.id,
                "path": saved_request.path,
                "full_path": full_path,
                "method": method,
                "category": category,
                "body_length": len(saved_request.body or ""),
                "post_keys": sorted(list(saved_request.POST.keys()))[:25],
                "_saved_request": saved_request,
            }

        if len(rows) < batch_size:
            break

    return {"found": False, "checked": checked}


@contextlib.contextmanager
def muted_clickfunnels_side_effects():
    from clickfunnels.models import ClickfunnelsContact

    method_names = [
        "process",
        "perform_tracking_for_heyflow",
        "perform_thrive_tracking_for_heyflow",
        "perform_voluum_tracking_for_heyflow",
        "perform_redtrack_tracking_for_heyflow",
        "perform_taboola_tracking_for_heyflow",
        "perform_outbrain_tracking_for_heyflow",
        "perform_baidu_tracking_for_heyflow",
        "perform_tracking_for_cfxstg_heyflow",
        "perform_tracking_for_cfxstg_heyflow_sale",
    ]
    originals: list[tuple[Any, str, Any]] = []

    def noop(self, *args, **kwargs):
        return None

    for method_name in method_names:
        if hasattr(ClickfunnelsContact, method_name):
            originals.append((ClickfunnelsContact, method_name, getattr(ClickfunnelsContact, method_name)))
            setattr(ClickfunnelsContact, method_name, noop)

    original_sleep = time.sleep
    time.sleep = lambda seconds: None
    try:
        yield
    finally:
        time.sleep = original_sleep
        for owner, method_name, original in originals:
            setattr(owner, method_name, original)


def request_meta(saved_request: Any) -> dict[str, Any]:
    meta = {}
    for key, value in saved_request.META.items():
        if key.startswith("HTTP_") and value is not None:
            meta[key] = value
    if "REMOTE_ADDR" in saved_request.META:
        meta["REMOTE_ADDR"] = saved_request.META["REMOTE_ADDR"]
    return meta


def call_route_with_saved_request(kind: str, saved_data: dict[str, Any]) -> dict[str, Any]:
    if not saved_data.get("found"):
        return {"found": False, "result": "SKIP", "reason": "no suitable SavedHttpRequest found"}

    from django.test import RequestFactory
    from django.db import transaction
    from clickfunnels import views

    saved_request = saved_data["_saved_request"]
    category = saved_data["category"]
    factory = RequestFactory()
    meta = request_meta(saved_request)
    path = saved_request.get_full_path() or saved_request.path

    if kind == "heyflow":
        request = factory.generic(
            "POST",
            path,
            data=(saved_request.body or "").encode("utf-8"),
            content_type="application/json",
            **meta,
        )
        view_func = views.api_category_ajax_heyflow
    else:
        post_data = {}
        for key, values in saved_request.POST.lists():
            post_data[key] = values[-1] if values else ""
        request = factory.post(path, data=post_data, **meta)
        view_func = views.webflow_ajax

    try:
        with muted_clickfunnels_side_effects():
            with transaction.atomic():
                response = view_func(request, category=category)
                content = getattr(response, "content", b"")[:300]
                transaction.set_rollback(True)
        return {
            "found": True,
            "result": "OK" if response.status_code < 500 else "FAIL",
            "status_code": response.status_code,
            "body_excerpt": content.decode("utf-8", errors="replace"),
            "rolled_back": True,
            "side_effect_methods_muted": True,
        }
    except Exception as exc:
        return {
            "found": True,
            "result": "FAIL",
            "status_code": None,
            "rolled_back": True,
            "side_effect_methods_muted": True,
            "error": safe_error(exc),
        }


def sanitize(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {key: sanitize(value) for key, value in payload.items() if not key.startswith("_")}
    if isinstance(payload, list):
        return [sanitize(value) for value in payload]
    return payload


def write_markdown(payload: dict[str, Any], path: Path) -> None:
    lines = [
        "# clickfunnels contact route py311 probe",
        "",
        f"- run_id: `{payload['run_id']}`",
        f"- base_url: `{payload['base_url']}`",
        f"- started_at: `{payload['started_at']}`",
        f"- finished_at: `{payload['finished_at']}`",
        "",
        "## UUID Contract",
        "",
        f"- ORM uuid field present: `{payload['uuid_contract']['uuid_field_present']}`",
        f"- uuid property present: `{payload['uuid_contract']['uuid_property_present']}`",
        f"- `UUID(contact.uuid).int == contact.id`: `{payload['uuid_contract']['uuid_int_matches_id']}`",
        f"- `id__is_uuid` lookup matches contact id: `{payload['uuid_contract']['id_is_uuid_lookup_matches']}`",
        "",
        "## HTTP PDF Probes",
        "",
        "| probe | path | status | content_type | pdf_magic |",
        "|---|---|---:|---|---|",
    ]
    for name, result in payload["pdf_http_probes"].items():
        lines.append(
            f"| `{name}` | `{result.get('path')}` | `{result.get('status_code')}` | `{result.get('content_type')}` | `{result.get('pdf_magic')}` |"
        )

    lines.extend(["", "## SavedHttpRequest Route Probes", ""])
    for kind in ("heyflow", "webflow"):
        sample = payload["saved_request_samples"][kind]
        result = payload["saved_request_route_probes"][kind]
        lines.append(
            f"- `{kind}`: found=`{sample.get('found')}`, request_id=`{sample.get('id')}`, category=`{sample.get('category')}`, result=`{result.get('result')}`, status=`{result.get('status_code')}`"
        )

    lines.extend(["", "## Legacy / Out Of Scope", ""])
    lines.append("- `import_lastnames_csv`: obsolete; approved by Mario for deletion, not a py311 blocker.")
    lines.append("- `/clickfunnels/script/dev/*`: dev-only DynDNS/MacBook path; not a py311 blocker.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--output", default="/tmp/clickfunnels_contact_routes_probe.json")
    parser.add_argument("--markdown-output", default="/tmp/clickfunnels_contact_routes_probe.md")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--include-letterxpress", action="store_true")
    parser.add_argument("--skip-pdf-http", action="store_true")
    parser.add_argument("--skip-saved-route-calls", action="store_true")
    args = parser.parse_args()

    run_id = "clickfunnels-contact-routes-" + dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    started_at = dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    progress("setup django")
    setup_django(Path(args.project_root))

    progress("discover token/contact and uuid lookup")
    token_data = find_token_and_contact()
    progress("discover pdf contacts")
    pdf_data = find_pdf_contacts(limit=args.limit, include_letterxpress=args.include_letterxpress)
    if args.skip_pdf_http:
        pdf_http_probes = {"skipped": {"result": "SKIP", "reason": "disabled by --skip-pdf-http"}}
    else:
        progress("probe pdf http routes")
        pdf_http_probes = probe_pdf_routes(args.base_url, token_data, pdf_data, timeout=args.timeout)

    progress("find SavedHttpRequest samples")
    saved_samples = {
        "heyflow": find_saved_request("heyflow", args.limit),
        "webflow": find_saved_request("webflow", args.limit),
    }
    if args.skip_saved_route_calls:
        saved_route_probes = {
            "heyflow": {"result": "SKIP", "reason": "disabled by --skip-saved-route-calls"},
            "webflow": {"result": "SKIP", "reason": "disabled by --skip-saved-route-calls"},
        }
    else:
        progress("call SavedHttpRequest-backed views inside rollback")
        saved_route_probes = {
            "heyflow": call_route_with_saved_request("heyflow", saved_samples["heyflow"]),
            "webflow": call_route_with_saved_request("webflow", saved_samples["webflow"]),
        }

    progress("write outputs")
    finished_at = dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    payload = sanitize(
        {
            "run_id": run_id,
            "base_url": args.base_url,
            "started_at": started_at,
            "finished_at": finished_at,
            "uuid_contract": token_data,
            "pdf_contact_candidates": pdf_data,
            "pdf_http_probes": pdf_http_probes,
            "saved_request_samples": saved_samples,
            "saved_request_route_probes": saved_route_probes,
        }
    )

    output = Path(args.output)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_markdown(payload, Path(args.markdown_output))
    print(json.dumps({"run_id": run_id, "json": str(output), "markdown": args.markdown_output}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
