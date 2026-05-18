#!/usr/bin/env python3
"""Read-only diagnostic for a cfxkvzs letterxpress contact candidate."""

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
    from clickfunnels.models import ClickfunnelsContact
    from core.utils.pdf import pdf_get_pages

    contact_id = int(sys.argv[1])
    contact = ClickfunnelsContact.objects.get(id=contact_id)
    rows = []
    ok = 0
    errors = []

    for file_upload in contact.file_uploads.all():
        data = file_upload.data if isinstance(file_upload.data, dict) else {}
        if data.get("dialfire_product_name") != "kvz":
            continue
        file_path = file_upload.get_file_path()
        exists = file_path is None or file_path.exists()
        rows.append({"id": file_upload.id, "exists": exists, "has_file": bool(file_upload.file)})
        if exists and file_upload.file:
            try:
                pdf_get_pages(file_upload.file)
                ok += 1
            except Exception as exc:
                errors.append(
                    {"id": file_upload.id, "type": exc.__class__.__name__, "message": str(exc)[:120]}
                )

    print(
        json.dumps(
            {
                "id": contact.id,
                "category": contact.category,
                "uuid": contact.uuid,
                "is_from_dialfire": contact.is_from_dialfire,
                "dialfire_transfer": contact.transferred_contacts.filter(
                    Q(transfer_method__id=478) | Q(transfer_method__name__icontains="transfer_to_dialfire_api")
                ).exists(),
                "kvz_uploads": rows,
                "pdf_read_ok_count": ok,
                "pdf_read_errors": errors,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
