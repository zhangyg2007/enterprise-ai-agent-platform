"""Offline scoring and evidence tests; no model quality is measured here."""
import copy
import contextlib
import io
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from enterprise_ai.baseline import load_cases, score_response, main
from enterprise_ai.evaluation import run_evaluation
from enterprise_ai.ollama import ModelError
from enterprise_ai.ollama import OllamaClient
from tests.test_ollama import Boundary, Response

CASES = Path(__file__).resolve().parents[1] / "data/samples/m04-intents.jsonl"
ANSWER = {"intent": "customer_summary", "customer_id": 1,
          "product_id": None, "action": "query"}


def response(answer=ANSWER, **extra):
    return {"model": "test:local", "done": True, "done_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(answer)},
            **extra}


class FakeClient:
    base_url = "http://127.0.0.1:11434"
    timeout_seconds = 120

    def __init__(self, responses, drift=False):
        self.responses = iter(responses)
        self.drift = drift
        self.inventories = 0
        self.messages = []

    def inventory(self, model):
        self.inventories += 1
        return {"model": model, "digest": "changed" if self.drift and self.inventories > 1 else "fixed",
                "server_version": "test-only", "template_sha256": "template"}

    def chat(self, model, messages, **kwargs):
        self.messages.append(copy.deepcopy(messages))
        item = next(self.responses)
        if isinstance(item, BaseException):
            raise item
        return item


class ScoreTests(unittest.TestCase):
    def test_exact_and_field_scores(self):
        score = score_response(response(), ANSWER)
        self.assertTrue(score["exact_match"])
        wrong = dict(ANSWER, customer_id=2)
        score = score_response(response(wrong), ANSWER)
        self.assertTrue(score["schema_valid"])
        self.assertFalse(score["field_correct"]["customer_id"])
        self.assertTrue(score["field_correct"]["intent"])

    def test_strict_schema_and_json(self):
        for text in ['```json\n{}\n```', '{"intent":1,"intent":2}', '{"x":NaN}']:
            with self.subTest(text=text):
                raw = response()
                raw["message"]["content"] = text
                self.assertFalse(score_response(raw, ANSWER)["json_valid"])
        for answer in [dict(ANSWER, customer_id=True), dict(ANSWER, customer_id="1"),
                       dict(ANSWER, extra=1), dict(ANSWER, customer_id=0), [], None]:
            with self.subTest(answer=answer):
                score = score_response(response(answer), ANSWER)
                self.assertTrue(score["json_valid"])
                self.assertFalse(score["schema_valid"])
                self.assertFalse(any(score["field_correct"].values()))

    def test_truncation_cannot_receive_business_credit(self):
        score = score_response(response(done_reason="length"), ANSWER)
        self.assertTrue(score["json_valid"])
        self.assertTrue(score["schema_valid"])
        self.assertFalse(score["exact_match"])
        self.assertFalse(any(score["field_correct"].values()))
        self.assertEqual(score["failure"], "truncated")

    def test_thinking_never_used_as_answer(self):
        raw = response()
        raw["message"] = {"role": "assistant", "content": "", "thinking": json.dumps(ANSWER)}
        self.assertFalse(score_response(raw, ANSWER)["json_valid"])

    def test_public_cases_strict_unique_and_sixteen(self):
        cases = load_cases(CASES)
        self.assertEqual(len(cases), 16)
        self.assertEqual(len({case["id"] for case in cases}), 16)
        self.assertEqual({c["expected"]["intent"] for c in cases},
                         {"customer_summary", "leave_balance", "inventory", "unsupported"})

    def test_duplicate_case_and_invalid_expected_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cases.jsonl"
            case = {"id": "one", "category": "crm", "input": "客户1", "expected": ANSWER}
            for rows in [[case, case], [dict(case, expected=dict(ANSWER, customer_id=True))], []]:
                path.write_text("\n".join(json.dumps(x) for x in rows), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_cases(path)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.console_out = io.StringIO()
        self.console_err = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.console_out))
        self.enterContext(contextlib.redirect_stderr(self.console_err))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cases = self.root / "cases.jsonl"
        cases = [{"id": str(n), "category": "crm", "input": f"查看客户{n}的订单概况",
                  "expected": dict(ANSWER, customer_id=n)} for n in (1, 2)]
        self.cases.write_text("\n".join(json.dumps(c) for c in cases), encoding="utf-8")
        self.output = self.root / "run"

    def run_lab(self, client, **kw):
        return run_evaluation(client, model="test:local", cases_path=self.cases,
                              output_dir=self.output, **kw)

    def test_failure_stays_in_denominator_and_raw_is_preserved(self):
        raw = response()
        client = FakeClient([raw, ModelError("timeout", "request timed out")])
        summary = self.run_lab(client)
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["exact_match_rate"], 0.5)
        self.assertEqual(summary["field_accuracy"]["intent"], 0.5)
        self.assertEqual(summary["request_errors"], 1)
        records = [json.loads(x) for x in (self.output / "records.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(records[0]["raw"], raw)
        self.assertEqual(records[1]["error"]["code"], "timeout")
        manifest = json.loads((self.output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["selected_count"], 2)
        self.assertEqual(len(manifest["cases_sha256"]), 64)
        self.assertEqual(len(manifest["artifacts"]["records.jsonl"]), 64)
        self.assertNotIn("expected", json.dumps(client.messages))

    def test_all_failures_score_zero_not_missing(self):
        summary = self.run_lab(FakeClient([ModelError("http_error", "HTTP 500")] * 2))
        self.assertEqual(summary["json_valid_rate"], 0)
        self.assertEqual(summary["exact_match_rate"], 0)
        self.assertEqual(summary["request_errors"], 2)

    def test_existing_output_rejected_before_model_call(self):
        self.output.mkdir()
        client = FakeClient([])
        with self.assertRaises(FileExistsError):
            self.run_lab(client)
        self.assertEqual(client.inventories, 0)

    def test_drift_invalidates_comparison(self):
        summary = self.run_lab(FakeClient([response(), response(dict(ANSWER, customer_id=2))], drift=True))
        self.assertFalse(summary["identity_stable"])
        self.assertFalse(summary["comparison_eligible"])

    def test_interrupted_run_preserves_first_record_and_running_manifest(self):
        with self.assertRaises(KeyboardInterrupt):
            self.run_lab(FakeClient([response(), KeyboardInterrupt()]))
        self.assertEqual(len((self.output / "records.jsonl").read_text(encoding="utf-8").splitlines()), 1)
        self.assertEqual(json.loads((self.output / "manifest.json").read_text(encoding="utf-8"))["status"], "running")
        self.assertFalse((self.output / "summary.json").exists())

    def test_limit_and_bad_limit(self):
        client = FakeClient([response()])
        result = self.run_lab(client, limit=1)
        self.assertEqual(result["total"], 1)
        self.assertTrue(result["is_subset"])
        with self.assertRaises(ValueError):
            run_evaluation(FakeClient([]), model="test:local", cases_path=self.cases,
                           output_dir=self.root / "invalid", limit=0)

    def test_bad_options_fail_before_network(self):
        client = FakeClient([])
        with self.assertRaises(ValueError):
            self.run_lab(client, options={"num_ctx": -1})
        self.assertEqual(client.inventories, 0)

    def test_cli_existing_inventory_file_never_calls_service(self):
        self.output.write_text("keep", encoding="utf-8")
        self.assertEqual(main(["inventory", "--model", "test:local", "--output", str(self.output)]), 1)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "keep")
        self.assertEqual(json.loads(self.console_err.getvalue())["error"], "FileExistsError")

    def test_cli_low_accuracy_is_not_transport_failure(self):
        client = FakeClient([response(dict(ANSWER, customer_id=99))] * 2)
        with patch("enterprise_ai.baseline.OllamaClient", return_value=client):
            code = main(["evaluate", "--model", "test:local", "--cases", str(self.cases),
                         "--output", str(self.output)])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(self.console_out.getvalue())["exact_match_rate"], 0)
        self.assertEqual(json.loads((self.output / "summary.json").read_text(encoding="utf-8"))["exact_match_rate"], 0)

    def test_postflight_error_marks_identity_unknown(self):
        client = FakeClient([response(), response()])
        initial = client.inventory("test:local")
        with patch.object(client, "inventory", side_effect=[initial, ModelError("network", "offline")]):
            summary = self.run_lab(client)
        self.assertFalse(summary["identity_stable"])
        self.assertEqual(summary["request_errors"], 0)
        self.assertEqual(json.loads((self.output / "inventory-after.json").read_text(encoding="utf-8"))["error"], "network")

    def test_first_inventory_failure_leaves_no_completed_report(self):
        client = FakeClient([])
        with patch.object(client, "inventory", side_effect=ModelError("network", "offline")):
            with self.assertRaises(ModelError):
                self.run_lab(client)
        self.assertEqual(json.loads((self.output / "manifest.json").read_text(encoding="utf-8"))["status"], "running")
        self.assertFalse((self.output / "summary.json").exists())

    def test_latency_denominator_and_token_duration_units(self):
        client = FakeClient([response(eval_count=10, eval_duration=2_000_000_000),
                             response(eval_count=10, eval_duration=0)])
        with patch("enterprise_ai.evaluation.time.perf_counter", side_effect=[0, 2, 4, 8]):
            summary = self.run_lab(client)
        self.assertEqual(summary["latency_seconds"], {"mean": 3, "p50": 2, "p95": 4})
        records = [json.loads(x) for x in (self.output / "records.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(records[0]["eval_tokens_per_second"], 5)
        self.assertIsNone(records[1]["eval_tokens_per_second"])

    def test_cli_timeout_has_failure_exit_code(self):
        client = FakeClient([ModelError("timeout", "timed out")] * 2)
        with patch("enterprise_ai.baseline.OllamaClient", return_value=client):
            code = main(["evaluate", "--model", "test:local", "--cases", str(self.cases),
                         "--output", str(self.output)])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(self.console_out.getvalue())["request_errors"], 2)

    def test_real_client_and_runner_integrate_at_http_boundary(self):
        inventory = [Response({"version": "test-only"}),
                     Response({"models": [{"name": "test:local", "digest": "fixed"}]}),
                     Response({"template": "test template"})]
        # A fresh set is required because each response stream is consumed once.
        after = [Response({"version": "test-only"}),
                 Response({"models": [{"name": "test:local", "digest": "fixed"}]}),
                 Response({"template": "test template"})]
        boundary = Boundary(*inventory, Response(response()),
                            Response(response(dict(ANSWER, customer_id=2))), *after)
        with patch("enterprise_ai.ollama.http.client.HTTPConnection", side_effect=boundary.connect):
            summary = self.run_lab(OllamaClient())
        self.assertEqual(summary["exact_match_rate"], 1)
        manifest = json.loads((self.output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["base_url"], "http://127.0.0.1:11434")
        self.assertTrue(summary["identity_stable"])

    def test_source_digest_uses_same_snapshot_as_parsed_cases(self):
        original = self.cases.read_bytes()
        real_loader = load_cases
        def changing_loader(*args, **kwargs):
            result = real_loader(*args, **kwargs)
            self.cases.write_text("changed while evaluating", encoding="utf-8")
            return result
        with patch("enterprise_ai.evaluation.load_cases", side_effect=changing_loader):
            self.run_lab(FakeClient([response(), response()]))
        manifest = json.loads((self.output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["source_cases_sha256"], hashlib.sha256(original).hexdigest())

    def test_unpaired_surrogate_metadata_is_retained_without_losing_record(self):
        raw = response()
        raw["message"]["thinking"] = "\ud800"
        result = self.run_lab(FakeClient([raw, response()]))
        self.assertEqual(result["total"], 2)
        record = json.loads((self.output / "records.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(record["raw"]["message"]["thinking"], "\ud800")

    def test_extreme_token_metadata_does_not_abort_evaluation(self):
        result = self.run_lab(FakeClient([response(eval_count=10**400, eval_duration=1), response()]))
        self.assertEqual(result["total"], 2)
        record = json.loads((self.output / "records.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertIsNone(record["eval_tokens_per_second"])


if __name__ == "__main__":
    unittest.main()
