"""Check configured API credentials and model catalogs without generating tokens.

Run from the project root with .venv/Scripts/python.exe scripts/check_api_access.py.
Only statuses and public model identifiers are printed, never keys or error bodies.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from dotenv import dotenv_values


def main() -> None:
    values = dotenv_values(Path(__file__).resolve().parents[1] / ".env", interpolate=False)
    checks = [
        ("Groq", "GROQ_API_KEY", "https://api.groq.com/openai/v1/models"),
        (
            "Google AI",
            "GOOGLEAI_API_KEY",
            "https://generativelanguage.googleapis.com/v1beta/models",
        ),
        ("OpenRouter", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1/key"),
    ]
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        for provider, variable, url in checks:
            key = os.environ.get(variable, values.get(variable))
            if not key:
                print(f"{provider}: {variable} is missing")
                continue
            headers = (
                {"x-goog-api-key": key}
                if provider == "Google AI"
                else {"Authorization": f"Bearer {key}"}
            )
            try:
                response = client.get(url, headers=headers)
                print(f"{provider}: HTTP {response.status_code}")
                if response.status_code != 200:
                    continue
                data = response.json()
                if provider == "Google AI":
                    names = [
                        item["name"]
                        for item in data.get("models", [])
                        if "generateContent" in item.get("supportedGenerationMethods", [])
                        and "flash" in item["name"]
                        and not any(x in item["name"] for x in ("image", "tts", "audio", "live"))
                    ]
                    print("  Text models: " + ", ".join(names))
                    print("  Billing tier cannot be determined from this response.")
                elif provider == "Groq":
                    names = [
                        item["id"]
                        for item in data.get("data", [])
                        if "qwen" in item["id"] or "gpt-oss" in item["id"]
                    ]
                    print("  Models: " + ", ".join(names))
                else:
                    catalog = client.get("https://openrouter.ai/api/v1/models")
                    print(f"  Public catalog: HTTP {catalog.status_code}")
                    if catalog.status_code == 200:
                        names = [
                            item["id"]
                            for item in catalog.json().get("data", [])
                            if item["id"].endswith(":free")
                            and "tools" in item.get("supported_parameters", [])
                            and all(
                                float(item.get("pricing", {}).get(x, "-1")) == 0
                                for x in ("prompt", "completion")
                            )
                        ]
                        print("  Free tool models: " + ", ".join(names))
            except Exception as error:
                print(f"{provider}: check failed ({type(error).__name__}); details hidden")


if __name__ == "__main__":
    main()
