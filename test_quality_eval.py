"""Synthetic scoring regressions; none is a quality observation."""
import base64
import json
from pathlib import Path
import tempfile
import unittest

import quality_eval as q


# Tiny existing PNG fixture. The copies carry distinct trailing fixture labels.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a4koAAAAASUVORK5CYII=")


class QualityTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.contract = self.root / "contract.json"
        self.lock = self.root / "lock.json"
        self.packet = self.root / "packet"
        self.candidates = self.root / "candidates.json"
        self.spec = q.example()
        self.spec.update(reference_method="synthetic fixture", purpose="pilot", judges=["j1", "j2"])
        self.spec["cases"] = [{"id": "p1", "prompt": "fixture prompt", "reference": "reference.png",
                               "requirements": ["visible detail"]}]
        a, b = self.spec["dimensions"].values()
        a["weight"], b["weight"] = 3, 2
        (self.root / "reference.png").write_bytes(PNG + b"control")
        (self.root / "candidate.png").write_bytes(PNG + b"candidate")

    def lock_and_list(self):
        q.write_new(self.contract, self.spec)
        q.freeze(self.contract, self.lock)
        prompt_sha = q.locked(self.lock)["contract"]["cases"][0]["prompt_sha256"]
        q.write_new(self.candidates, [{"id": "p1", "image": "candidate.png", "prompt_sha256": prompt_sha}])

    def run_prepare(self):
        self.lock_and_list()
        q.prepare(self.lock, self.candidates, self.packet)

    def rate(self, mutate=None):
        for panel in q.read(self.packet / "private.json")["key"]["panels"]:
            form = self.packet / "review" / panel["label"] / "ratings.json"
            sheet = q.read(form)
            row = sheet["rows"][0]
            code = panel["assignments"][0]["code_side"]
            ref = "A" if code == "B" else "B"
            dims = list(self.spec["dimensions"])
            row[code]["scores"] = {dims[0]: 4, dims[1]: 3}
            row[ref]["scores"] = dict.fromkeys(dims, 4)
            row[code]["hard"]["visible detail"] = row[ref]["hard"]["visible detail"] = True
            if mutate:
                mutate(row[code], row[ref], panel["judge"])
            form.write_text(json.dumps(sheet))

    def test_exact_boundary_and_partial(self):
        self.run_prepare()
        self.assertEqual(q.score(self.lock, self.packet)["status"], "incomplete")
        self.rate()
        result = q.score(self.lock, self.packet)
        self.assertEqual(result["status"], "pilot_only")
        self.assertEqual(result["descriptive_suite_quality"], "meets_on_frozen_cases")
        self.assertAlmostEqual(result["point_ratio"], .9)
        self.assertEqual(len(result["raw"]["p1"]), 2)

    def test_prompts_manifest_hides_reference_and_criteria(self):
        self.lock_and_list()
        exported, = q.prompts(self.lock)
        self.assertEqual(set(exported), {"id", "prompt", "prompt_sha256", "contract", "method_sha256",
                                         "runtime_image", "backend"})
        self.assertEqual(exported["prompt"], self.spec["cases"][0]["prompt"])

    def test_cli_refuses_pilot_even_at_target(self):
        import subprocess
        import sys
        self.run_prepare()
        self.rate()
        run = subprocess.run([sys.executable, q.__file__, "score", self.lock, self.packet],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertEqual(json.loads(run.stdout)["status"], "pilot_only")

    def test_hard_failure_and_bad_reference(self):
        self.run_prepare()
        self.rate(lambda c, r, _: c["hard"].update({"visible detail": False}))
        self.assertEqual(q.score(self.lock, self.packet)["descriptive_suite_quality"], "hard_condition_failed")
        self.rate(lambda c, r, _: r["scores"].update(dict.fromkeys(r["scores"], 0)))
        result = q.score(self.lock, self.packet)
        self.assertEqual(result["descriptive_suite_quality"], "inadequate_reference")
        self.assertIsNone(result["point_ratio"])

    def test_disagreement_uncertainty_below_target(self):
        self.run_prepare()
        dim = next(iter(self.spec["dimensions"]))
        self.rate(lambda c, _, judge: c["scores"].update({dim: [2, 4] if judge == "j1" else 4}))
        result = q.score(self.lock, self.packet)
        self.assertEqual(result["descriptive_suite_quality"], "inconclusive")
        self.assertLess(result["rating_sensitivity_range"][0], .9)
        self.rate(lambda c, r, j: c["scores"].update(dict.fromkeys(c["scores"], 2)))
        self.assertEqual(q.score(self.lock, self.packet)["descriptive_suite_quality"], "below_target")

    def test_pilot_does_not_pass(self):
        self.spec["purpose"] = "pilot"
        self.run_prepare()
        self.rate()
        self.assertEqual(q.score(self.lock, self.packet)["status"], "pilot_only")

    def test_lock_prompt_evidence_reuse_and_no_overwrite(self):
        self.lock_and_list()
        records = q.read(self.candidates)
        self.candidates.write_text(json.dumps([{**records[0], "prompt_sha256": "wrong"}]))
        with self.assertRaisesRegex(ValueError, "提示词"):
            q.prepare(self.lock, self.candidates, self.packet)
        self.candidates.write_text(json.dumps([{**records[0], "image": "reference.png"}]))
        with self.assertRaisesRegex(ValueError, "完全相同"):
            q.prepare(self.lock, self.candidates, self.packet)
        self.candidates.write_text(json.dumps(records))
        (self.root / "reference.png").write_bytes(PNG + b"changed")
        with self.assertRaisesRegex(ValueError, "参考图已变更"):
            q.prepare(self.lock, self.candidates, self.packet)
        with self.assertRaises(FileExistsError):
            q.freeze(self.contract, self.lock)

    def test_bad_image_or_edited_contract_rejected(self):
        self.run_prepare()
        form = self.packet / "review" / "reviewer-001" / "ratings.json"
        image = form.parent / q.read(form)["rows"][0]["A"]["file"]
        image.write_bytes(PNG)
        with self.assertRaisesRegex(ValueError, "图片绑定"):
            q.score(self.lock, self.packet)
        lock = q.read(self.lock)
        lock["contract"]["min_reference_score"] = 0.1
        self.lock.write_text(json.dumps(lock))
        with self.assertRaisesRegex(ValueError, "冻结合同"):
            q.score(self.lock, self.packet)

    def test_missing_case_stale_form_invalid_grade(self):
        self.run_prepare()
        form = self.packet / "review" / "reviewer-001" / "ratings.json"
        sheet = q.read(form)
        form.write_text(json.dumps({**sheet, "rows": []}))
        with self.assertRaisesRegex(ValueError, "覆盖不足"):
            q.score(self.lock, self.packet)
        form.write_text(json.dumps({**sheet, "run": "other"}))
        with self.assertRaisesRegex(ValueError, "评分表绑定"):
            q.score(self.lock, self.packet)
        form.write_text(json.dumps(sheet))
        self.rate(lambda c, r, j: c["scores"].update(dict.fromkeys(c["scores"], True)))
        with self.assertRaisesRegex(ValueError, "评分需"):
            q.score(self.lock, self.packet)


class HoldoutTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.contract = self.root / "contract.json"
        self.lock = self.root / "lock.json"
        self.packet = self.root / "packet"
        self.host = self.root / "trusted.json"
        self.spec = q.example()
        self.spec.update(purpose="formal", reference_method="synthetic test", judges=["test judge"])
        self.spec["generalization"].update(author_model="test author", author_policy_sha256="d" * 64,
                                           method_sha256="a" * 64, runtime_image="sha256:" + "b" * 64,
                                           required_families=["coast", "machine"], required_layouts=["side", "cutaway"])
        self.spec["cases"] = []
        for i, (family, layout) in enumerate(zip(["coast", "machine"], ["side", "cutaway"])):
            case_id = f"case-{i}"
            self.spec["cases"].append({"id": case_id, "prompt": "synthetic " + family,
                                      "family": family, "layout": layout, "template": "template-" + family,
                                      "reference": f"{case_id}.png", "requirements": ["test constraint"],
                                      "semantic_hard": ["test constraint"]})
            (self.root / f"{case_id}.png").write_bytes(PNG + f"ref{i}".encode())
            (self.root / f"code-{case_id}.png").write_bytes(PNG + f"code{i}".encode())

    def freeze(self):
        q.write_new(self.contract, self.spec)
        q.freeze(self.contract, self.lock)

    def prepare(self, mutate_receipt=None, all_receipts=True):
        self.freeze()
        lock = q.locked(self.lock)
        host = q.host_example()
        example = {**host["cases"][0], **dict.fromkeys(q.CASE_FACTS, True)}
        host.update(dict.fromkeys(q.HOST_FACTS, True))
        host.update(contract=lock["id"], host=self.spec["generalization"]["trusted_host"], cases=[])
        candidates = []
        for manifest in q.prompts(self.lock):
            cid = manifest["id"]
            code = self.root / f"code-{cid}.png"
            receipt = {"schema": "rsi.first_pass.v1", "status": "rendered", "id": cid, "contract": lock["id"],
                       "prompt_sha256": manifest["prompt_sha256"], "method_sha256": manifest["method_sha256"],
                       "image_sha256": q.file_hash(code), "program_sha256": "c" * 64,
                       "runtime_image": self.spec["generalization"]["runtime_image"], "attempt": 1,
                       "render_budget": 1, "network": "none", "reference_inputs": 0, "wall_seconds": 1.25,
                       "method_entry": "method.render"}
            if mutate_receipt:
                mutate_receipt(cid, receipt)
            receipt_file = self.root / f"{cid}.json"
            q.write_new(receipt_file, receipt)
            entry = {"id": cid, "prompt_sha256": manifest["prompt_sha256"], "image": str(code)}
            if all_receipts or not candidates:
                entry["receipt"] = str(receipt_file)
            candidates.append(entry)
            host["cases"].append({**example, "id": cid, "receipt_sha256": q.file_hash(receipt_file)})
        q.write_new(self.host, host)
        candidates_path = self.root / "candidates.json"
        q.write_new(candidates_path, candidates)
        q.prepare(self.lock, candidates_path, self.packet)

    def rate(self, case_scores):
        panel = q.read(self.packet / "private.json")["key"]["panels"][0]
        form = self.packet / "review" / panel["label"] / "ratings.json"
        sheet = q.read(form)
        for row, assignment, (code_score, ref_score) in zip(sheet["rows"], panel["assignments"], case_scores):
            for side in ("A", "B"):
                value = code_score if side == assignment["code_side"] else ref_score
                row[side]["scores"] = dict.fromkeys(row[side]["scores"], value)
                row[side]["hard"] = dict.fromkeys(row[side]["hard"], True)
        form.write_text(json.dumps(sheet))

    def result(self):
        return q.score(self.lock, self.packet, self.host)

    def test_hard_and_of_holdout_and_quality(self):
        self.prepare()
        self.rate([(4, 4), (4, 4)])
        self.assertEqual(self.result()["status"], "meets_quality_and_generalization_on_declared_holdouts")
        self.assertEqual(q.score(self.lock, self.packet)["status"], "insufficient_generalization_evidence")
        h = q.read(self.host)
        h["cases"][0]["first_authored_program"] = False
        self.host.write_text(json.dumps(h))
        self.assertEqual(self.result()["status"], "insufficient_generalization_evidence")

    def test_aggregate_cannot_hide_weak_family(self):
        self.prepare()
        self.rate([(4, 3), (3, 4)])
        r = self.result()
        self.assertEqual(r["point_ratio"], 1)
        self.assertEqual(r["status"], "holdout_below_target")
        self.assertEqual(r["group_ratio_low_mid_high"]["family"]["machine"][0], .75)

    def test_formal_visual_and_semantic_evidence(self):
        self.spec["cases"][0].pop("semantic_hard")
        with self.assertRaisesRegex(ValueError, "语义硬条件"):
            self.freeze()
        self.contract.unlink()
        self.spec["cases"][0]["semantic_hard"] = ["test constraint"]
        for dim in self.spec["dimensions"].values():
            dim.pop("scope", None)
        with self.assertRaisesRegex(ValueError, "整图视觉"):
            self.freeze()

    def test_unapproved_method_entry_and_author_model_use(self):
        self.prepare(lambda cid, r: r.update(method_entry="program.render"))
        self.rate([(4, 4), (4, 4)])
        self.assertEqual(self.result()["status"], "insufficient_generalization_evidence")
        host = q.read(self.host)
        host["cases"][0]["no_image_generation_models"] = False
        self.host.write_text(json.dumps(host))
        self.assertTrue(any("宿主" in issue for issue in self.result()["holdout_evidence_issues"]))

    def test_missing_receipt(self):
        self.prepare(all_receipts=False)
        self.rate([(4, 4), (4, 4)])
        self.assertEqual(self.result()["status"], "insufficient_generalization_evidence")

    def test_budget_and_sha_receipt_binding(self):
        self.prepare(lambda cid, r: r.update(attempt=2, image_sha256="d" * 64, wall_seconds=9999))
        self.rate([(4, 4), (4, 4)])
        self.assertEqual(self.result()["status"], "insufficient_generalization_evidence")

    def test_renamed_template_and_single_family(self):
        self.spec["cases"][1]["template"] = self.spec["cases"][0]["template"]
        with self.assertRaisesRegex(ValueError, "模板"):
            self.freeze()
        self.spec["cases"][1].update(template="other", family="coast")
        self.contract.unlink()
        with self.assertRaisesRegex(ValueError, "family"):
            self.freeze()

    def test_dev_overlap_normalized(self):
        self.spec["generalization"]["development"][0]["prompt"] = "SYNTHETIC,  COAST"
        with self.assertRaisesRegex(ValueError, "开发与留出"):
            self.freeze()

    def test_case_within_group_cannot_hide_behind_group_mean(self):
        extra = {**self.spec["cases"][0], "id": "case-2", "reference": "case-2.png", "prompt": "new coast theme"}
        self.spec["cases"].append(extra)
        (self.root / "case-2.png").write_bytes(PNG + b"third-ref")
        (self.root / "code-case-2.png").write_bytes(PNG + b"third-code")
        self.prepare()
        self.rate([(4, 3), (4, 4), (3, 4)])
        r = self.result()
        self.assertTrue(all(x[0] >= .9 for x in r["group_ratio_low_mid_high"]["family"].values()))
        self.assertEqual(r["status"], "holdout_below_target")


if __name__ == "__main__":
    unittest.main()
