#!/usr/bin/env python3
"""Find a cfxkvzs letterxpress candidate whose KVZ upload exists on disk."""

from __future__ import annotations

import json
import os
import site
import sys
from pathlib import Path


def main() -> int:
    root = Path("/var/www/hosts/contactapi2.static.fyi/contactapi/contactapi")
    sys.path.insert(0, str(root))
    site.addsitedir(str(root / "apps"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "contactapi.settings_py311")
    os.environ.setdefault("DJANGO_EXECUTED_BY_MANAGE_COMMAND", "True")

    import django

    django.setup()

    from django.db.models import Q
    from clickfunnels.models import (
        CATEGORY_CFXKVZ2,
        CATEGORY_CFXKVZS,
        CATEGORY_CFXKVZS_HEYFLOW,
        CATEGORY_CFXKVZS_WEBFLOW,
    )
    from core.models import FileUpload

    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    categories = {
        CATEGORY_CFXKVZS,
        CATEGORY_CFXKVZ2,
        CATEGORY_CFXKVZS_HEYFLOW,
        CATEGORY_CFXKVZS_WEBFLOW,
        "cfxkvzs_alternative_webflow",
        "cfxkvzs_phonesale",
    }
    checked = 0
    kvz_seen = 0
    existing_seen = 0

    for file_upload in FileUpload.objects.order_by("-id")[:limit]:
        checked += 1
        data = file_upload.data if isinstance(file_upload.data, dict) else {}
        if data.get("dialfire_product_name") != "kvz":
            continue
        kvz_seen += 1
        file_path = file_upload.get_file_path()
        if file_path is not None and not file_path.exists():
            continue
        existing_seen += 1
        for contact in file_upload.clickfunnels_contacts.all():
            if contact.category not in categories or contact.is_from_dialfire:
                continue
            has_transfer = contact.transferred_contacts.filter(
                Q(transfer_method__id=478) | Q(transfer_method__name__icontains="transfer_to_dialfire_api")
            ).exists()
            if not has_transfer:
                continue
            print(
                json.dumps(
                    {
                        "found": True,
                        "checked_file_uploads": checked,
                        "kvz_uploads_seen": kvz_seen,
                        "existing_kvz_uploads_seen": existing_seen,
                        "file_upload_id": file_upload.id,
                        "contact": {
                            "id": contact.id,
                            "uuid": contact.uuid,
                            "category": contact.category,
                        },
                    },
                    ensure_ascii=False,
                )
            )
            return 0

    print(
        json.dumps(
            {
                "found": False,
                "checked_file_uploads": checked,
                "kvz_uploads_seen": kvz_seen,
                "existing_kvz_uploads_seen": existing_seen,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
