#!/usr/bin/env python3
"""Verify SDK availability and ChatGPT-subscription authentication without a model turn."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def result(status: str, blockers: list[str] | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "execution_status": status,
        "blockers": blockers or [],
        "warnings": [],
        **extra,
    }


def check_sdk(repo: Path, *, list_models: bool = False) -> dict[str, Any]:
    repo = repo.resolve()
    if not repo.is_dir():
        return result("blocked", [f"target repo is not accessible: {repo}"])
    try:
        from openai_codex import Codex, CodexConfig, __version__
    except ImportError as exc:
        return result("blocked", [f"official Python Codex SDK is unavailable: {exc}"], error_type="SdkUnavailable")

    try:
        discovery_options = {
            "client_name": "ai_harness_worker",
            "client_title": "Tweebit AI Harness by Daryna",
            "config_overrides": ("features.fast_mode=true",),
        } if list_models else {}
        with Codex(CodexConfig(cwd=str(repo), **discovery_options)) as codex:
            account_response = codex.account()
            account = account_response.account
            account_root = account.root if account is not None else None
            account_type = str(getattr(account_root, "type", ""))
            plan_type = getattr(getattr(account_root, "plan_type", None), "value", "")
            if list_models and account_type == "chatgpt":
                from adapters.codex_sdk_models import models_for_client

                return models_for_client(codex)
    except Exception as exc:
        return result(
            "blocked",
            [f"Codex SDK could not initialize the local authenticated runtime: {type(exc).__name__}: {exc}"],
            error_type="SdkInitializationError",
        )
    if account_type != "chatgpt":
        return result(
            "blocked",
            [
                "Codex SDK production runtime requires Sign in with ChatGPT; "
                f"current account type is {account_type or 'not signed in'}"
            ],
            error_type="SubscriptionAuthRequired",
        )
    return result(
        "completed",
        sdk_version=__version__,
        account_type=account_type,
        plan_type=str(plan_type),
        repository=str(repo),
        sandbox="read-only",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--models", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = check_sdk(args.repo, list_models=args.models)
    if args.models:
        if "status" not in payload:
            payload = {"status": "unavailable", "provider": "codex-sdk", "models": [], "message": "Model discovery requires an available SDK and Sign in with ChatGPT"}
        print(json.dumps(payload, ensure_ascii=False))
        return 0 if payload["status"] == "available" else 1
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["execution_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
