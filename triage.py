"""Choose one source-level experiment from an observed code-to-image run.

python3 triage.py state.json  (use - for stdin). No external commands execute.
Identifiers in observations, attempts and closure records refer to review evidence
supplied by the caller. The controller checks the decision contract, not receipt
authenticity, aesthetic quality or the independence of the supplied scene IDs.
"""
import argparse
import json
import math
import sys
import unicodedata


PRIORITY = {"user": 0, "contract": 1, "generalization": 1, "composition": 2,
            "quality": 3, "code": 4, "detail": 5, "performance": 6}
OUTCOMES = {"unchanged", "no_gain", "regressed", "inconclusive", "falsified",
            "supported", "improved"}


def strings(values):
    if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
        raise ValueError("expected a list of nonempty strings")
    return values


def label(record, field):
    value = record[field]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"expected nonempty {field}")
    return value


def seconds(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("seconds must be finite and nonnegative")
    return value


def unique_by(records, key):
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise ValueError(f"expected object list keyed by {key}")
    result = {label(record, key): record for record in records}
    if len(result) != len(records):
        raise ValueError(f"duplicate {key}")
    return result


def independent_dev_cases(closure, origin, transfer):
    """Compare actual development prompts and families from the frozen suite receipt."""
    suite = closure.get("development_suite", {})
    if not isinstance(suite, dict) or not isinstance(suite.get("frozen_receipt"), str):
        return False
    entries = suite.get("scenes", {})
    if not suite["frozen_receipt"].strip() or not isinstance(entries, dict):
        return False
    def identity(scene, field):
        entry = entries.get(scene, {})
        if not isinstance(entry, dict) or not isinstance(entry.get(field), str):
            return ""
        return " ".join(unicodedata.normalize("NFKC", entry[field]).casefold().split())
    for field in ("prompt", "family"):
        first, second = identity(origin, field), identity(transfer, field)
        if not first or not second or first == second:
            return False
    return True


def verified(closure, protected, require_user=False):
    """A replay on a new scene and the originating regression must use one fix."""
    if not isinstance(closure, dict) or not closure:
        return False
    base, fix, transfer = [closure.get(k) for k in ("before", "after", "transfer")]
    if not all(isinstance(case, dict) for case in (base, fix, transfer)):
        return False
    def present(value):
        return isinstance(value, str) and bool(value.strip())
    if not all(present(case.get(key)) for case in (base, fix, transfer)
               for key in ("scene", "method", "receipt")):
        return False
    if not (present(closure.get("source_change")) and present(closure.get("mechanism_check"))
            and base.get("target") == "fail" and base["scene"] == fix["scene"]
            and fix["scene"] != transfer["scene"] and base["method"] != fix["method"]
            and independent_dev_cases(closure, fix["scene"], transfer["scene"])
            and fix["method"] == transfer["method"]
            and len({case["receipt"] for case in (base, fix, transfer)}) == 3):
        return False
    for case in (fix, transfer):
        checks = case.get("checks", {})
        if case.get("target") != "pass" or not isinstance(checks, dict):
            return False
        if "fail" in checks.values() or any(checks.get(key) != "pass" for key in protected | {"whole_image"}):
            return False
    return not require_user or fix["checks"].get("user_acceptance") == "pass"


def observed_attempts(issue):
    attempts = list(unique_by(issue.get("attempts", []), "id").values())
    for event in attempts:
        for key in ("cause", "action", "receipt"):
            label(event, key)
        if event["outcome"] not in OUTCOMES:
            raise ValueError(f"bad attempt outcome in {issue['id']}")
    return attempts


def plan_experiment(issue, observed, profile, remaining):
    attempts = observed_attempts(issue)
    closure = issue.get("closure", {})
    rejected = any(case.get("target") == "fail" or "fail" in case.get("checks", {}).values()
                   for case in (closure.get("after", {}), closure.get("transfer", {})))
    if rejected:
        if not closure.get("cause") or not closure.get("action"):
            return {"decision": "attribute_regression",
                    "reason": "Keep the incumbent and attach this failed verification to its cause and action."}
        attempts.append({"cause": closure["cause"], "action": closure["action"], "outcome": "regressed"})
    last_result = {(a["cause"], a["action"]): a["outcome"] for a in attempts}
    if "improved" in last_result.values() or (closure and not rejected):
        return {"decision": "verify_fix",
                "reason": "Supply the failing origin, fixed replay and cross-family, different-prompt development case on the same fixed method; check protected requirements and whole-image regressions."}

    tried = {(a["cause"], a["action"]) for a in attempts}
    refuted = {a["cause"] for a in attempts if a["outcome"] == "falsified"}
    for cause in {a["cause"] for a in attempts}:
        failed_ops = {a["action"] for a in attempts if a["cause"] == cause
                      and a["outcome"] in {"regressed", "no_gain"}}
        if len(failed_ops) >= 2:
            refuted.add(cause)
    supported = {a["cause"] for a in attempts if a["outcome"] == "supported"}
    options = []
    over_budget = []
    for action, exp in unique_by(issue.get("experiments", []), "action").items():
        cause = label(exp, "cause")
        if exp["kind"] not in {"probe", "source_change"}:
            raise ValueError("experiment kind must be probe or source_change")
        for key in ("change", "expect", "falsify"):
            label(exp, key)
        cost = seconds(exp["cost_s"])
        if cause in refuted or (cause, action) in tried:
            continue
        source = exp["kind"] == "source_change"
        if source:
            label(exp, "source_target")
            confirmed = strings(exp.get("confirmed_by", [])) or cause in supported
            if not observed or not confirmed:
                continue
            if issue["kind"] == "performance" and not profile.get(issue.get("stage"), 0):
                continue
        if remaining is not None and cost > remaining:
            over_budget.append(cost)
            continue
        options.append(((not source, cost, action), exp))
    if options:
        exp = min(options, key=lambda item: item[0])[1]
        return {"decision": "run_experiment", "experiment": exp,
                "reason": "Prefer a supported source change, then lowest estimated cost among eligible actions of that type."}
    if over_budget:
        return {"decision": "design_probe", "remaining_s": remaining,
                "lowest_eligible_cost_s": min(over_budget),
                "reason": "Design a cheaper discriminating probe within the remaining budget."}
    return {"decision": "design_probe", "retired_causes": sorted(refuted),
            "reason": "Propose a fresh discriminating observation or causal hypothesis before spending another run."}


def decide(state):
    """Return a read-only next decision. profile stores exclusive measured seconds."""
    remaining = seconds(state["remaining_s"]) if "remaining_s" in state else None
    protected = set(strings(state.get("protected", [])))
    issues = unique_by(state["issues"], "id")
    profile = state.get("profile", {})
    for stage, elapsed in profile.items():
        if not isinstance(stage, str) or not stage.strip():
            raise ValueError("empty profile stage")
        seconds(elapsed)
    total = sum(profile.values())
    hotspots = [{"stage": stage, "seconds": elapsed, "fraction_of_profiled": elapsed / total}
                for stage, elapsed in sorted(profile.items(), key=lambda x: (-x[1], x[0])) if elapsed]
    dependencies = {}
    for name, issue in issues.items():
        if issue["kind"] not in PRIORITY:
            raise ValueError(f"unknown kind for {name}")
        strings(issue.get("observations", []))
        strings(issue.get("affected_scenes", []))
        dependencies[name] = strings(issue.get("depends_on", []))
        if set(dependencies[name]) - issues.keys():
            raise ValueError(f"unknown dependency of {name}")

    def urgency(name):
        issue = issues[name]
        affected = len(set(issue.get("affected_scenes", [])))
        timing = profile.get(issue.get("stage"), 0) if issue["kind"] == "performance" else 0
        return PRIORITY[issue["kind"]], -timing, -affected, name

    # No issue can close through an open prerequisite. Cache shared subgraphs.
    roots = {}
    def find_roots(name, path):
        if name in path:
            raise ValueError(f"dependency cycle through {name}")
        if name not in roots:
            blocked = set()
            for dep in dependencies[name]:
                blocked.update(find_roots(dep, path | {name}))
            item = issues[name]
            if not blocked and not verified(item.get("closure"), protected, item["kind"] == "user"):
                blocked.add(name)
            roots[name] = blocked
        return roots[name]

    for name in issues:
        find_roots(name, set())
    closed = {name for name in issues if not roots[name]}
    open_ids = issues.keys() - closed
    audit = {"closed_issues": sorted(closed), "hotspots": hotspots,
             "generator_acceptance": "pending_independent_evaluation",
             "protect": sorted(protected | {"whole_image"})}
    if not open_ids:
        return {**audit, "decision": "evaluate_generator",
                "reason": "No listed issue is open. Evaluate the frozen method on independent locked prompts and families for formal quality and generalization acceptance."}
    focus = min(open_ids, key=urgency)
    root = min(roots[focus], key=urgency)
    issue = issues[root]
    decision = plan_experiment(issue, issue.get("observations", []), profile, remaining)
    return {**audit, "focus": focus, "issue": root,
            "observations": issue.get("observations", []), **decision}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", help="JSON file or - for stdin")
    args = parser.parse_args()
    try:
        if args.state == "-":
            state = json.load(sys.stdin)
        else:
            with open(args.state, encoding="utf8") as fp:
                state = json.load(fp)
        result = decide(state)
    except (OSError, TypeError, ValueError, KeyError, AttributeError) as exc:
        parser.exit(2, f"invalid triage input: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
