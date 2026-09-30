import json
import stat
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from scripts import drift_check  # noqa: E402

TAG_DRIFT_PLAN = {
    "resource_drift": [
        {"address": "module.lake_bucket.aws_s3_bucket.this[0]", "change": {"actions": ["update"],
         "before": {"tags": {"DataClassification": "confidential"}, "tags_all": {"DataClassification": "confidential"},
                    "versioning": [{"enabled": False}]},
         "after": {"tags": {"DataClassification": "public"}, "tags_all": {"DataClassification": "public"},
                   "versioning": [{"enabled": True}]}}},
        {"address": "module.lake_bucket.aws_s3_bucket_server_side_encryption_configuration.this", "change": {"actions": ["update"],
         "before": {"rule": [{"bucket_key_enabled": None}]}, "after": {"rule": [{"bucket_key_enabled": False}]}}},
    ],
    "resource_changes": [
        {"address": "module.lake_bucket.aws_s3_bucket.this[0]", "change": {"actions": ["update"],
         "before": {"tags": {"DataClassification": "public"}, "versioning": [{"enabled": True}]},
         "after": {"tags": {"DataClassification": "confidential"}, "versioning": [{"enabled": True}]}}},
        {"address": "module.lake_bucket.aws_s3_bucket_server_side_encryption_configuration.this",
         "change": {"actions": ["no-op"], "before": {}, "after": {}}},
    ],
}
REGISTRY_DRIFT_PLAN = {
    "resource_drift": [{"address": 'restapi_object.subject_compatibility["dev.x-value"]', "change": {"actions": ["update"],
        "before": {"data": '{"compatibility":"BACKWARD","compatibilityLevel":"BACKWARD"}', "api_response": "{}"},
        "after": {"data": '{"compatibility":"BACKWARD","compatibilityLevel":"NONE"}', "api_response": '{"x":1}'}}}],
    "resource_changes": [{"address": 'restapi_object.subject_compatibility["dev.x-value"]', "change": {"actions": ["update"],
        "before": {"data": '{"compatibility":"BACKWARD","compatibilityLevel":"NONE"}'},
        "after": {"data": '{"compatibility":"BACKWARD","compatibilityLevel":"BACKWARD"}'}}}],
}


def fake_tf(tmp_path, rc, plan=None):
    (tmp_path / "plan.json").write_text(json.dumps(plan or {}))
    exe = tmp_path / "terraform"
    exe.write_text(f"#!{sys.executable}\nimport sys\nif sys.argv[1] == 'plan':\n    print('planned'); sys.exit({rc})\n"
                   f"print(open({str(tmp_path / 'plan.json')!r}).read())\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return str(exe)


def test_leaf_diff_opens_json_strings_and_nests():
    d = drift_check.leaf_diff({"a": {"b": 1}, "s": '{"k":"x"}'}, {"a": {"b": 2}, "s": '{"k":"y"}'})
    assert d == [("a.b", 1, 2), ("s.k", "x", "y")]


def test_tag_drift_is_reported_and_refresh_noise_is_not(tmp_path, monkeypatch):
    monkeypatch.setattr(drift_check, "TF", fake_tf(tmp_path, 2, TAG_DRIFT_PLAN))
    r = drift_check.check("dev")
    assert r["exit"] == 2
    assert [(d["attribute"], d["code"], d["live"]) for d in r["drift"]] == [("tags.DataClassification", "confidential", "public")]
    assert r["pending"] == [("module.lake_bucket.aws_s3_bucket.this[0]", "update")]


def test_registry_drift_names_compatibility_level(tmp_path, monkeypatch):
    monkeypatch.setattr(drift_check, "TF", fake_tf(tmp_path, 2, REGISTRY_DRIFT_PLAN))
    r = drift_check.check("dev")
    assert [(d["attribute"], d["code"], d["live"]) for d in r["drift"]] == [("data.compatibilityLevel", "BACKWARD", "NONE")]


def test_changes_without_drift_are_unapplied_not_drift(tmp_path, monkeypatch):
    plan = {"resource_drift": [], "resource_changes": TAG_DRIFT_PLAN["resource_changes"]}
    monkeypatch.setattr(drift_check, "TF", fake_tf(tmp_path, 2, plan))
    r = drift_check.check("dev")
    assert r["exit"] == 2 and r["drift"] == [] and r["pending"]


def test_plan_error_fails_closed_and_clean_is_clean(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(drift_check, "TF", fake_tf(tmp_path, 1))
    monkeypatch.setattr(sys, "argv", ["drift_check.py", "--workspaces", "dev", "--no-push"])
    assert drift_check.main() == 1 and "FAILING CLOSED" in capsys.readouterr().out
    monkeypatch.setattr(drift_check, "TF", fake_tf(tmp_path, 0))
    assert drift_check.main() == 0


def test_push_series_escapes_labels_and_groups_by_workspace():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from runtime import metrics
    got = {}

    class H(BaseHTTPRequestHandler):
        def do_PUT(self):
            got["path"], got["body"] = self.path, self.rfile.read(int(self.headers["Content-Length"])).decode()
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.handle_request, daemon=True).start()
    metrics.push_series(f"http://127.0.0.1:{srv.server_port}", "terraform_drift", {"workspace": "dev"},
                        [("terraform_drift", {}, 1), ("terraform_drift_attribute",
                          {"resource": 'restapi_object.x["dev.s"]', "attribute": "data.compatibilityLevel"}, 1)])
    assert got["path"] == "/metrics/job/terraform_drift/workspace/dev"
    assert 'terraform_drift_attribute{resource="restapi_object.x[\\"dev.s\\"]",attribute="data.compatibilityLevel"} 1' in got["body"]
    assert "# TYPE terraform_drift gauge" in got["body"]
