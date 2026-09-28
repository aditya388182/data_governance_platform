"""Minimal Schema Registry client used by fraud-service jobs."""
import json
import os
import urllib.request

REGISTRY_URL = os.environ.get("SCHEMA_REGISTRY_URL", "http://localhost:8081")


def latest_schema(subject: str) -> str:
    """Return the latest registered Avro schema (JSON string) for a subject."""
    with urllib.request.urlopen(f"{REGISTRY_URL}/subjects/{subject}/versions/latest", timeout=10) as r:
        return json.loads(r.read())["schema"]
