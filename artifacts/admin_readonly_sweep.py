import argparse
import json
import os
import signal
import site
import subprocess
import sys
import time
import traceback
from contextlib import contextmanager
from pathlib import Path


PROJECT_ROOT = Path("/var/www/hosts/contactapi2.static.fyi/contactapi/contactapi")
BASE_DIR = PROJECT_ROOT / "contactapi"
APPS_DIR = PROJECT_ROOT / "apps"
REQUEST_TIMEOUT_SECONDS = 15
MODEL_TIMEOUT_SECONDS = 45
PROGRESS_PATH = Path("/tmp/admin_readonly_sweep.progress.jsonl")

sys.path.insert(0, str(PROJECT_ROOT))
site.addsitedir(str(APPS_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "contactapi.settings_py311")
os.environ.setdefault("DJANGO_EXECUTED_BY_MANAGE_COMMAND", "True")

import django

django.setup()

from django.contrib import admin
from django.contrib.admin.utils import quote
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import NoReverseMatch, reverse


def _safe_error(exc):
    return {
        "type": exc.__class__.__name__,
        "message": str(exc),
        "traceback": traceback.format_exc(limit=6),
    }


@contextmanager
def _time_limit(seconds):
    def _raise_timeout(signum, frame):
        raise TimeoutError(f"Operation exceeded {seconds}s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _append_progress(payload):
    with PROGRESS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str))
        handle.write("\n")


def _safe_get(client, path):
    started = time.time()
    try:
        with _time_limit(REQUEST_TIMEOUT_SECONDS):
            response = client.get(path)
        return {
            "status": response.status_code,
            "duration_ms": int((time.time() - started) * 1000),
            "redirect_chain": getattr(response, "redirect_chain", []),
            "error": None,
        }
    except Exception as exc:
        return {
            "status": None,
            "duration_ms": int((time.time() - started) * 1000),
            "redirect_chain": [],
            "error": _safe_error(exc),
        }


def _model_sample_pk(model):
    pk_name = model._meta.pk.name
    with _time_limit(REQUEST_TIMEOUT_SECONDS):
        return model._default_manager.order_by(pk_name).values_list(pk_name, flat=True).first()


def _build_admin_url(name, opts, args=None):
    return reverse(f"admin:{opts.app_label}_{opts.model_name}_{name}", args=args or [])


def _registry_items():
    return sorted(
        admin.site._registry.items(),
        key=lambda item: (item[0]._meta.app_label, item[0]._meta.model_name),
    )


def _registry_entry(model_label):
    for model, model_admin in _registry_items():
        if f"{model._meta.app_label}.{model._meta.model_name}" == model_label:
            return model, model_admin
    raise KeyError(f"Unknown admin model: {model_label}")


def _sweep_model(client, model, model_admin):
    opts = model._meta
    started = time.time()
    result = {
        "model": f"{opts.app_label}.{opts.model_name}",
        "object_name": opts.object_name,
        "admin_class": model_admin.__class__.__name__,
        "changelist": None,
        "add": None,
        "change": None,
        "sample_pk": None,
        "sample_pk_error": None,
    }

    for action in ("changelist", "add"):
        try:
            path = _build_admin_url(action, opts)
        except Exception as exc:
            result[action] = {
                "status": None,
                "duration_ms": 0,
                "redirect_chain": [],
                "error": _safe_error(exc),
            }
            continue

        payload = _safe_get(client, path)
        payload["path"] = path
        result[action] = payload

    try:
        sample_pk = _model_sample_pk(model)
        result["sample_pk"] = sample_pk
    except Exception as exc:
        result["sample_pk_error"] = _safe_error(exc)
        sample_pk = None

    if sample_pk is not None:
        try:
            path = _build_admin_url("change", opts, [quote(sample_pk)])
            payload = _safe_get(client, path)
            payload["path"] = path
            result["change"] = payload
        except Exception as exc:
            result["change"] = {
                "status": None,
                "duration_ms": 0,
                "redirect_chain": [],
                "error": _safe_error(exc),
            }
    else:
        result["change"] = {
            "status": "skipped",
            "duration_ms": 0,
            "redirect_chain": [],
            "error": None,
            "reason": "no sample object or pk lookup failed",
        }

    result["model_duration_ms"] = int((time.time() - started) * 1000)
    return result


def _run_child_sweep(model_label):
    command = [sys.executable, str(Path(__file__).resolve()), "--model", model_label]
    started = time.time()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=MODEL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.time() - started) * 1000)
        return {
            "model": model_label,
            "object_name": model_label.split(".")[-1],
            "admin_class": None,
            "changelist": {
                "status": None,
                "duration_ms": duration_ms,
                "redirect_chain": [],
                "error": {
                    "type": "TimeoutExpired",
                    "message": f"Model sweep subprocess exceeded {MODEL_TIMEOUT_SECONDS}s",
                    "traceback": None,
                },
                "path": None,
            },
            "add": {
                "status": "skipped",
                "duration_ms": 0,
                "redirect_chain": [],
                "error": None,
                "reason": "model sweep subprocess timed out",
            },
            "change": {
                "status": "skipped",
                "duration_ms": 0,
                "redirect_chain": [],
                "error": None,
                "reason": "model sweep subprocess timed out",
            },
            "sample_pk": None,
            "sample_pk_error": None,
            "model_duration_ms": duration_ms,
            "subprocess_stderr": (exc.stderr or "")[-2000:],
        }

    duration_ms = int((time.time() - started) * 1000)
    stdout = completed.stdout or ""
    json_line = None
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            json_line = stripped
            break

    try:
        payload = json.loads(json_line or "")
        payload["model_duration_ms"] = duration_ms
        if completed.stderr:
            payload["subprocess_stderr"] = completed.stderr[-2000:]
        return payload
    except json.JSONDecodeError:
        return {
            "model": model_label,
            "object_name": model_label.split(".")[-1],
            "admin_class": None,
            "changelist": {
                "status": None,
                "duration_ms": duration_ms,
                "redirect_chain": [],
                "error": {
                    "type": "InvalidChildOutput",
                    "message": f"Child exited with code {completed.returncode}",
                    "traceback": (completed.stderr or "")[-2000:],
                },
                "path": None,
            },
            "add": {
                "status": "skipped",
                "duration_ms": 0,
                "redirect_chain": [],
                "error": None,
                "reason": "child output was not valid JSON",
            },
            "change": {
                "status": "skipped",
                "duration_ms": 0,
                "redirect_chain": [],
                "error": None,
                "reason": "child output was not valid JSON",
            },
            "sample_pk": None,
            "sample_pk_error": None,
            "model_duration_ms": duration_ms,
            "child_stdout": stdout[-2000:],
            "subprocess_stderr": (completed.stderr or "")[-2000:],
        }


def _single_model_payload(model_label):
    user = get_user_model().objects.filter(is_superuser=True).order_by("id").first()
    if user is None:
        raise RuntimeError("No superuser available for admin sweep")

    client = Client()
    client.force_login(user)
    model, model_admin = _registry_entry(model_label)
    return _sweep_model(client, model, model_admin)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    args = parser.parse_args()

    if args.model:
        print(json.dumps(_single_model_payload(args.model), ensure_ascii=False, default=str))
        return

    if PROGRESS_PATH.exists():
        PROGRESS_PATH.unlink()

    results = []
    for model, _model_admin in _registry_items():
        model_label = f"{model._meta.app_label}.{model._meta.model_name}"
        _append_progress({"event": "start_model", "model": model_label})
        result = _run_child_sweep(model_label)
        results.append(result)
        _append_progress(
            {
                "event": "finish_model",
                "model": model_label,
                "model_duration_ms": result["model_duration_ms"],
                "statuses": {
                    "changelist": result["changelist"]["status"] if result["changelist"] else None,
                    "add": result["add"]["status"] if result["add"] else None,
                    "change": result["change"]["status"] if result["change"] else None,
                },
            }
        )

    summary = {
        "checked_models": len(results),
        "failures": [],
        "slow_pages": [],
    }

    for item in results:
        for action in ("changelist", "add", "change"):
            payload = item.get(action) or {}
            status = payload.get("status")
            if payload.get("error") or (isinstance(status, int) and status >= 500):
                summary["failures"].append(
                    {
                        "model": item["model"],
                        "action": action,
                        "status": status,
                        "path": payload.get("path"),
                        "error": payload.get("error"),
                        "reason": payload.get("reason"),
                    }
                )
            if isinstance(payload.get("duration_ms"), int) and payload["duration_ms"] >= 3000:
                summary["slow_pages"].append(
                    {
                        "model": item["model"],
                        "action": action,
                        "status": status,
                        "duration_ms": payload["duration_ms"],
                        "path": payload.get("path"),
                    }
                )

        if item.get("sample_pk_error"):
            summary["failures"].append(
                {
                    "model": item["model"],
                    "action": "sample_pk",
                    "status": None,
                    "path": None,
                    "error": item["sample_pk_error"],
                    "reason": None,
                }
            )

    print(
        json.dumps(
            {
                "summary": summary,
                "results": results,
            },
            ensure_ascii=False,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
