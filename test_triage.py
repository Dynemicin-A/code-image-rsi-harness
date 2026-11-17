"""Synthetic decision fixtures. Their receipt IDs and timings are not research results."""
import unittest

from triage import decide


def experiment(action="toggle_cloth", cause="flat_brdf", kind="probe", cost=2):
    return {"kind": kind, "cause": cause, "action": action, "cost_s": cost,
            "change": "Hold geometry and lighting fixed, vary the cloth response",
            "expect": "Different grazing highlights across identical folds",
            "falsify": "The response buffer is unchanged"}


def issue(name="cloth", kind="quality", **extra):
    return {"id": name, "kind": kind, "observations": ["review: paper-like folds"],
            "experiments": [experiment()], **extra}


def event(action, outcome, cause="flat_brdf"):
    return {"id": action, "cause": cause, "action": action,
            "outcome": outcome, "receipt": f"log-{action}"}


def closure():
    def case(scene, method, target):
        return {"scene": scene, "method": method, "receipt": scene + method,
                "target": target, "checks": {"whole_image": "pass", "silhouette": "pass"}}
    return {"source_change": "method.py@b", "mechanism_check": "BRDF comparison",
            "before": case("fold", "a", "fail"), "after": case("fold", "b", "pass"),
            "transfer": case("draped chair", "b", "pass"),
            "development_suite": {
                "frozen_receipt": "synthetic registry v1", "scenes": {
                    "fold": {"family": "tabletop still life", "prompt": "Blue folded linen on a table"},
                    "draped chair": {"family": "interiors", "prompt": "Sunlight on a draped armchair"}}}}


class TriageTests(unittest.TestCase):
    def test_blocking_quality_inherits_priority(self):
        state = {"issues": [issue("rejected", "user", depends_on=["layout"]),
                            issue("layout", "composition"), issue("tile", "detail"),
                            issue("render", "performance", stage="trace")],
                 "profile": {"trace": 9, "load": 1}}
        answer = decide(state)
        self.assertEqual((answer["focus"], answer["issue"]), ("rejected", "layout"))
        self.assertEqual(answer["hotspots"][0], {"stage": "trace", "seconds": 9,
                                                 "fraction_of_profiled": .9})

    def test_cost_hotspots_govern_performance_work(self):
        state = {"issues": [issue("a", "performance", stage="mesh"),
                            issue("z", "performance", stage="shade")],
                 "profile": {"mesh": 1.1, "shade": 4.3}}
        self.assertEqual(decide(state)["issue"], "z")

    def test_cheapest_discriminating_probe_before_unsupported_fix(self):
        expensive = experiment("rerender_global", cost=40)
        fix = dict(experiment("alter_shader", kind="source_change", cost=1),
                   source_target="cloth.py:brdf")
        task = issue(experiments=[expensive, fix, experiment()])
        self.assertEqual(decide({"issues": [task]})["experiment"]["action"], "toggle_cloth")
        task["attempts"] = [event("toggle_cloth", "supported")]
        self.assertEqual(decide({"issues": [task]})["experiment"]["action"], "alter_shader")
        task["observations"] = []
        self.assertEqual(decide({"issues": [task]})["experiment"]["action"], "rerender_global")

    def test_budget_limits_even_a_supported_change(self):
        fix = dict(experiment("alter_shader", kind="source_change", cost=18),
                   source_target="materials.py", confirmed_by=["buffer difference"])
        state = {"issues": [issue(experiments=[fix, experiment()])], "remaining_s": 4}
        self.assertEqual(decide(state)["experiment"]["action"], "toggle_cloth")
        state["remaining_s"] = 1
        self.assertEqual(decide(state)["lowest_eligible_cost_s"], 2)

    def test_unprofiled_slow_stage_needs_probe(self):
        fix = dict(experiment(kind="source_change"), source_target="render.py",
                   confirmed_by=["source read"])
        state = {"issues": [issue(kind="performance", stage="trace", experiments=[fix])],
                 "profile": {"total process": 53}}
        self.assertEqual(decide(state)["decision"], "design_probe")

    def test_failed_actions_cannot_loop(self):
        other = experiment("contrast_lamps", "fill_light")
        state = {"issues": [issue(attempts=[event("toggle_cloth", "unchanged")],
                                  experiments=[experiment(), other])]}
        self.assertEqual(decide(state)["experiment"], other)
        task = state["issues"][0]
        task["attempts"] = [event("alternate_normal", "no_gain"), event("alternate_brdf", "regressed")]
        answer = decide(state)
        self.assertEqual(answer["experiment"], other)
        task["experiments"] = [experiment()]
        self.assertEqual(decide(state)["retired_causes"], ["flat_brdf"])

    def test_independent_source_fix_regresses(self):
        task = issue(closure=closure())
        state = {"issues": [task], "protected": ["silhouette"]}
        self.assertEqual(decide(state)["closed_issues"], ["cloth"])
        task["closure"]["transfer"]["checks"]["silhouette"] = "fail"
        self.assertEqual(decide(state)["decision"], "attribute_regression")
        task["closure"].update({"cause": "flat_brdf", "action": "toggle_cloth"})
        task["attempts"] = [event("toggle_cloth", "improved")]
        self.assertEqual(decide(state)["decision"], "design_probe")

    def test_provisional_gain_needs_same_method_new_scene_and_whole_image(self):
        task = issue(attempts=[event("alter_shader", "improved")])
        state = {"issues": [task], "protected": ["silhouette"]}
        self.assertEqual(decide(state)["decision"], "verify_fix")
        for key, value in [("scene", "fold"), ("method", "other"),
                           ("checks", {"silhouette": "pass"}), ("receipt", "foldb")]:
            with self.subTest(key=key):
                task["closure"] = closure()
                task["closure"]["transfer"][key] = value
                self.assertEqual(decide(state)["decision"], "verify_fix")

    def test_prerequisite_and_user_review_remain_open(self):
        task = issue("user-feedback", "user", closure=closure(), depends_on=["cloth"])
        state = {"issues": [task, issue()]}
        self.assertEqual(decide(state)["issue"], "cloth")
        state["issues"][1]["closure"] = closure()
        self.assertEqual(decide(state)["issue"], "user-feedback")
        task["closure"]["after"]["checks"]["user_acceptance"] = "pass"
        self.assertEqual(decide(state)["decision"], "evaluate_generator")

    def test_generalization_blocks_detail_and_runtime(self):
        state = {"issues": [issue("material"), issue("latency", "performance", stage="trace"),
                            issue("unseen-scene", "generalization")],
                 "profile": {"trace": 90}}
        self.assertEqual(decide(state)["focus"], "unseen-scene")

    def test_new_id_same_prompt_or_same_family_cannot_close_fix(self):
        task = issue(closure=closure())
        state = {"issues": [task]}
        for field, impostor in [("prompt", "  BLUE FOLDED linen on a table "),
                                ("family", "Tabletop still life")]:
            with self.subTest(field=field):
                task["closure"] = closure()
                task["closure"]["development_suite"]["scenes"]["draped chair"][field] = impostor
                self.assertEqual(decide(state)["decision"], "verify_fix")
        task["closure"] = closure()
        del task["closure"]["development_suite"]["frozen_receipt"]
        self.assertEqual(decide(state)["decision"], "verify_fix")

    def test_empty_or_closed_backlog_cannot_approve_generator(self):
        for entries in ([], [issue(closure=closure())]):
            answer = decide({"issues": entries})
            self.assertEqual(answer["decision"], "evaluate_generator")
            self.assertEqual(answer["generator_acceptance"], "pending_independent_evaluation")

    def test_invalid_dependency_and_times(self):
        for entries in [[issue(depends_on=["ghost"])], [issue(depends_on=["cloth"])],
                        [issue(), issue()]]:
            with self.assertRaises(ValueError):
                decide({"issues": entries})
        for bad in (-2, float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                decide({"issues": [], "profile": {"trace": bad}})


if __name__ == "__main__":
    unittest.main()
