"""A small stand-in for Confluent Schema Registry, used ONLY to test the gate's
control flow without Docker: HTTP codes, history replay, mode read-back,
fail-closed paths, subject cleanup.

Compatibility verdicts come from Apache Avro's reference checker
(avro.compatibility), the same reader/writer algorithm the Java registry uses.
This is NOT a substitute for the real registry: the CI job always judges PRs
with the real cp-schema-registry image. Its job is to prove the *gate logic*
does the right thing for every response the registry can give.

Fault injection (tests only): POST /__faults with a JSON object, e.g.
  {"register_status": 500}         -> POST /subjects/*/versions returns 500
  {"config_echo": "NONE"}           -> GET /config/* lies about the mode
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from avro.compatibility import ReaderWriterCompatibilityChecker, SchemaCompatibilityType
from avro.schema import parse as avro_parse

MODES = {"NONE", "BACKWARD", "BACKWARD_TRANSITIVE", "FORWARD", "FORWARD_TRANSITIVE", "FULL", "FULL_TRANSITIVE"}
_CHECKER = ReaderWriterCompatibilityChecker()


def _compatible(reader: str, writer: str) -> tuple[bool, list[str]]:
    res = _CHECKER.get_compatibility(avro_parse(reader), avro_parse(writer))
    ok = res.compatibility == SchemaCompatibilityType.compatible
    msgs = sorted(str(m) for m in (getattr(res, "messages", None) or []))
    return ok, msgs


def check(mode: str, new: str, prev: list[str]) -> tuple[bool, list[str]]:
    if mode == "NONE" or not prev:
        return True, []
    targets = prev if mode.endswith("_TRANSITIVE") else prev[-1:]
    base = mode.replace("_TRANSITIVE", "")
    problems: list[str] = []
    for old in targets:
        if base in ("BACKWARD", "FULL"):
            ok, m = _compatible(new, old)
            if not ok:
                problems.append("new schema cannot read an older version: " + "; ".join(m))
        if base in ("FORWARD", "FULL"):
            ok, m = _compatible(old, new)
            if not ok:
                problems.append("an older schema cannot read the new one: " + "; ".join(m))
    return not problems, problems


def _canon(s: str) -> str:
    return json.dumps(json.loads(s), sort_keys=True)


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.subjects: dict[str, list[dict]] = {}   # subject -> versions
        self.soft_deleted: set[str] = set()
        self.config: dict[str, str] = {}
        self.global_mode = "BACKWARD"
        self.next_id = 1
        self.faults: dict = {}

    def active(self, s: str) -> list[dict]:
        if s in self.soft_deleted:
            return []
        return self.subjects.get(s, [])


class _Handler(BaseHTTPRequestHandler):
    state: _State  # injected

    def log_message(self, *a):  # silence
        pass

    def _send(self, code: int, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/vnd.schemaregistry.v1+json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return {}

    # ---------------- GET ----------------
    def do_GET(self):
        st = self.state
        path = urlparse(self.path).path
        with st.lock:
            if path == "/subjects":
                return self._send(200, sorted(s for s in st.subjects if st.active(s)))
            m = re.fullmatch(r"/config/([^/]+)", path)
            if m:
                s = m.group(1)
                if "config_echo" in st.faults:
                    return self._send(200, {"compatibilityLevel": st.faults["config_echo"]})
                if s not in st.config:
                    return self._send(404, {"error_code": 40408, "message": f"Subject '{s}' does not have subject-level compatibility configured"})
                return self._send(200, {"compatibilityLevel": st.config[s]})
            m = re.fullmatch(r"/subjects/([^/]+)/versions", path)
            if m:
                vs = st.active(m.group(1))
                if not vs:
                    return self._send(404, {"error_code": 40401, "message": "Subject not found."})
                return self._send(200, [v["version"] for v in vs])
        return self._send(404, {"error_code": 404, "message": "not found"})

    # ---------------- PUT ----------------
    def do_PUT(self):
        st = self.state
        path = urlparse(self.path).path
        body = self._body()
        mode = body.get("compatibility")
        if mode not in MODES:
            return self._send(422, {"error_code": 42203, "message": f"Invalid compatibility level: {mode}"})
        with st.lock:
            if path == "/config":
                st.global_mode = mode
                return self._send(200, {"compatibility": mode})
            m = re.fullmatch(r"/config/([^/]+)", path)
            if m:
                st.config[m.group(1)] = mode
                return self._send(200, {"compatibility": mode})
        return self._send(404, {"error_code": 404, "message": "not found"})

    # ---------------- POST ----------------
    def do_POST(self):
        st = self.state
        u = urlparse(self.path)
        path, qs = u.path, parse_qs(u.query)
        body = self._body()
        if path == "/__faults":
            with st.lock:
                st.faults = body
            return self._send(200, {"faults": body})
        m = re.fullmatch(r"/subjects/([^/]+)/versions", path)
        if m:
            s = m.group(1)
            with st.lock:
                if "register_status" in st.faults:
                    code = int(st.faults["register_status"])
                    return self._send(code, {"error_code": code * 100 + 1, "message": "injected fault"})
                schema = body.get("schema", "")
                try:
                    avro_parse(schema)
                    canon = _canon(schema)
                except Exception as e:  # noqa: BLE001
                    return self._send(422, {"error_code": 42201, "message": f"Invalid schema: {e}"})
                if s in st.soft_deleted:
                    st.subjects.pop(s, None)
                    st.soft_deleted.discard(s)
                vs = st.subjects.setdefault(s, [])
                for v in vs:
                    if v["canon"] == canon:
                        return self._send(200, {"id": v["id"]})
                mode = st.config.get(s, st.global_mode)
                ok, problems = check(mode, schema, [v["schema"] for v in vs])
                if not ok:
                    return self._send(409, {"error_code": 409, "message":
                        f"Schema being registered is incompatible with an earlier schema for subject \"{s}\", details: {problems}"})
                sid = st.next_id
                st.next_id += 1
                vs.append({"version": len(vs) + 1, "id": sid, "schema": schema, "canon": canon})
                return self._send(200, {"id": sid})
        m = re.fullmatch(r"/compatibility/subjects/([^/]+)/versions/([^/]+)", path)
        if m:
            s, ver = m.group(1), m.group(2)
            with st.lock:
                vs = st.active(s)
                if not vs:
                    return self._send(404, {"error_code": 40401, "message": "Subject not found."})
                target = vs[-1] if ver == "latest" else next((v for v in vs if str(v["version"]) == ver), None)
                if target is None:
                    return self._send(404, {"error_code": 40402, "message": "Version not found."})
                mode = st.config.get(s, st.global_mode).replace("_TRANSITIVE", "")
                ok, problems = check(mode, body.get("schema", ""), [target["schema"]])
                out = {"is_compatible": ok}
                if qs.get("verbose", ["false"])[0] == "true":
                    out["messages"] = problems
                return self._send(200, out)
        return self._send(404, {"error_code": 404, "message": "not found"})

    # ---------------- DELETE ----------------
    def do_DELETE(self):
        st = self.state
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        m = re.fullmatch(r"/subjects/([^/]+)", u.path)
        if not m:
            return self._send(404, {"error_code": 404, "message": "not found"})
        s = m.group(1)
        permanent = qs.get("permanent", ["false"])[0] == "true"
        with st.lock:
            if s not in st.subjects:
                return self._send(404, {"error_code": 40401, "message": "Subject not found."})
            versions = [v["version"] for v in st.subjects[s]]
            if permanent:
                if s not in st.soft_deleted:
                    return self._send(404, {"error_code": 40405, "message": "Subject must be soft-deleted first."})
                del st.subjects[s]
                st.soft_deleted.discard(s)
                st.config.pop(s, None)
            else:
                st.soft_deleted.add(s)
            return self._send(200, versions)


class MockRegistry:
    """Context manager: `with MockRegistry() as reg: reg.url`"""

    def __init__(self) -> None:
        self.state = _State()
        handler = type("H", (_Handler,), {"state": self.state})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._t = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "MockRegistry":
        self._t.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()

    def live_subjects(self) -> list[str]:
        with self.state.lock:
            return sorted(self.state.subjects)


if __name__ == "__main__":  # manual use: python tests/gate/mock_registry.py
    import time
    with MockRegistry() as r:
        print(r.url, flush=True)
        while True:
            time.sleep(3600)
