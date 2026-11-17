"""离线评估同提示词的 code-to-image 和已有 diffusion 参考图（仅用标准库）。

example > contract.json 可取得待填写的合同。先 freeze，再以 prompts LOCK 导出
生成侧题面；冻结合同、参考路径和盲评包由评估侧持有。candidates.json 是对象列表，
每项为 {"id": "题号", "prompt_sha256": "题面中的值", "image": "生成结果路径"}。
prepare 后只将 review 下相应的评审目录交给本人。以同尺寸、不显示文件元数据
的方式看图。在 ratings.json 中按 0..4 锚点评分，可用 [下限,上限] 表达疑义，
并分别为两幅图的硬条件填写 true/false。score 输出 JSON 到标准输出。

判据是冻结有限题集的加权量表积分比。等权平均评审，随后等权平均题目。
区间将同一项各评审给出的可能等级合并取包络，传播到总体积分比。它描述
评审分歧与自报不确定性，没有未见任务的统计覆盖保证。formal 合同另冻结方法、
单次预算和开发清单；每题及每个必需家族、布局的下界均须达到 0.90。
每个候选的 receipt 为独立首次运行器的 JSON。score --host-attestation FILE
从评估方取得独立宿主声明；生成方不能自签。声明只对该受托宿主可观察的历史
负责，本工具检查绑定和内容，不鉴定签名。字段见 host-example。
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import unicodedata


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def fingerprint(value):
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                           allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def format_suffix(path):
    with Path(path).open("rb") as stream:
        header = stream.read(12)
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if header.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return ".webp"
    raise ValueError(f"图片格式需为 PNG、JPEG 或 WebP: {path}")


def texts(values):
    return (isinstance(values, list) and bool(values) and
            all(isinstance(v, str) and v.strip() for v in values) and
            len(values) == len(set(values)))


def number(value):
    return type(value) in (float, int) and math.isfinite(value)


def normalized(value):
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def runtime_id(value):
    return isinstance(value, str) and re.fullmatch(r"(?:sha256|local):[0-9a-f]{64}", value) is not None


FORMAL_PASS = "meets_quality_and_generalization_on_declared_holdouts"


def validate_holdout(spec):
    gen = spec["generalization"]
    require(any(dim.get("scope") == "whole_image_visual" for dim in spec["dimensions"].values()),
            "正式量表需覆盖整图视觉质量")
    for case in spec["cases"]:
        hard = case.get("semantic_hard")
        require(texts(hard) and set(hard).issubset(case["requirements"]), "每题必须指定已列入逐项验收的语义硬条件")
    require(isinstance(gen["author_model"], str) and gen["author_model"].strip()
            and sha(gen["author_policy_sha256"]), "需冻结文字作者模型和生成策略")
    require(sha(gen["method_sha256"]), "必须冻结公共方法的 SHA256")
    require(runtime_id(gen["runtime_image"]), "必须冻结运行环境摘要")
    budget = gen["budget"]
    require(type(budget["attempts"]) is int and budget["attempts"] == 1 and
            type(budget["render_budget"]) is int and budget["render_budget"] == 1 and
            number(budget["wall_seconds"]) and budget["wall_seconds"] > 0, "需冻结单次生成预算")
    require(isinstance(gen["trusted_host"], str) and gen["trusted_host"].strip(), "须声明受托宿主")
    require(len(spec["judges"]) >= 2, "正式评估至少需要两个独立评审")
    require(type(gen.get("min_holdout_cases")) is int and gen["min_holdout_cases"] >= 2,
            "正式合同须声明至少两道留出题")
    require(type(gen.get("min_cases_per_family")) is int and gen["min_cases_per_family"] >= 1,
            "正式合同须声明每家族最小题数")
    require(type(gen.get("min_cases_per_layout")) is int and gen["min_cases_per_layout"] >= 1,
            "正式合同须声明每布局最小题数")
    require(type(gen.get("minimum_effective_wall_seconds")) in (int, float)
            and math.isfinite(gen["minimum_effective_wall_seconds"])
            and gen["minimum_effective_wall_seconds"] >= 0,
            "正式合同须声明有效研究时间下限")
    dev = gen["development"]
    cases = spec["cases"]
    require(isinstance(dev, list), "需冻结开发题目清单")
    require(len(cases) >= gen["min_holdout_cases"], "留出题数量低于冻结的最小规模")
    for row in dev + cases:
        for field in ("id", "prompt", "family", "layout", "template"):
            require(isinstance(row[field], str) and normalized(row[field]), f"题目缺少 {field}")
    for field in ("id", "prompt", "family", "layout", "template"):
        seen = {normalized(row[field]) for row in dev}
        held = [normalized(row[field]) for row in cases]
        require(not seen.intersection(held), f"开发与留出重叠: {field}")
        if field in ("id", "prompt"):
            require(len(set(held)) == len(held), f"留出题重复: {field}")
    for field, plural in (("family", "families"), ("layout", "layouts")):
        required = gen["required_" + plural]
        require(texts(required) and len({normalized(v) for v in required}) == len(required) >= 2
                and {c[field] for c in cases} == set(required), f"至少两个独立留出 {field} 且完全覆盖")
        minimum = gen["min_cases_per_" + field]
        counts = {group: sum(normalized(c[field]) == normalized(group) for c in cases) for group in required}
        require(all(count >= minimum for count in counts.values()), f"每个留出 {field} 至少需要 {minimum} 道题")
    origins = {}
    for c in cases:
        template = normalized(c["template"])
        identity = (normalized(c["family"]), normalized(c["layout"]))
        require(origins.setdefault(template, identity) == identity, "同模板不能计为不同家族或布局")


HOST_FACTS = ("development_inventory_complete", "method_frozen_before_holdout_access",
              "method_contains_no_holdout_examples", "families_and_layouts_independently_reviewed",
              "author_model_and_policy_unchanged", "single_operator_owned_registry",
              "references_are_existing_diffusion_outputs_for_exact_prompts",
              "rubric_covers_whole_image_and_required_prompt_semantics",
              "network_isolation_verified", "filesystem_acl_verified",
              "resource_limits_verified", "no_host_credentials_exposed",
              "reviewer_contexts_are_independent")
CASE_FACTS = ("held_out_from_development", "independent_not_paraphrase_or_template_reuse",
              "first_authored_program", "no_embedded_reference_or_candidate_image_refinement",
              "author_context_isolated", "no_reference_or_feedback_or_selection",
              "frozen_method_used_without_test_time_patch", "no_image_generation_models", "method_used_for_output")


def host_example():
    return {"schema": "rsi.holdout_attestation.v1", "host": "受托宿主标识", "contract": "锁的 id",
            "effective_wall_seconds": 21600, "time_ledger_sha256": "替换为计时账本 SHA256",
            **dict.fromkeys(HOST_FACTS), "cases": [{"id": "留出题号", "receipt_sha256": "首次运行回执文件原始 SHA256",
                                                  **dict.fromkeys(CASE_FACTS)}]}


def example():
    return {"purpose": "pilot", "reference_method": "请填写已有 diffusion 参考的来源与选图规则",
            "judges": ["reviewer1", "reviewer2"], "min_reference_score": 0.5,
            "dimensions": {
                "整体构图与层次": {"scope": "whole_image_visual", "weight": 1, "anchors": ["主体不可辨", "主体可辨但空间冲突",
                      "主体明确，前后关系部分成立", "前中后景清楚，局部抢焦", "层次和视觉主次连贯"]},
                "材质与用光": {"weight": 1, "anchors": ["材质与光向均不可辨", "仅局部可辨",
                      "主材质可辨，光向局部矛盾", "主要表面与光向协调", "表面、遮挡及反光关系一致"]}},
            "generalization": {"author_model": "", "author_policy_sha256": "",
                "method_sha256": "替换为冻结公共方法的 SHA256", "trusted_host": "受托宿主标识",
            "runtime_image": "local:替换为冻结运行环境摘要", "backend": "填写本次选择的 adapter",
            "budget": {"attempts": 1, "render_budget": 1, "wall_seconds": 300},
                "min_holdout_cases": 48, "min_cases_per_family": 2,
                "min_cases_per_layout": 2, "minimum_effective_wall_seconds": 21600,
                "development": [{"id": "dev1", "prompt": "填写已见提示词", "family": "已见家族",
                                 "layout": "已见布局", "template": "已见模板"}],
                "required_families": ["留出家族一", "留出家族二"], "required_layouts": ["留出布局一", "留出布局二"]},
            "cases": [{"id": "case1", "prompt": "填写完整原始提示词", "reference": "reference.png",
                       "family": "留出家族一", "layout": "留出布局一", "template": "留出模板一",
                       "requirements": ["填写该题可判真的关键内容或空间关系"],
                       "semantic_hard": ["填写该题可判真的关键内容或空间关系"]}]}


def freeze(contract_path, lock_path):
    spec = read(contract_path)
    require(spec["purpose"] in ("pilot", "formal"), "purpose 需为 pilot 或 formal")
    require(isinstance(spec["reference_method"], str) and spec["reference_method"].strip(),
            "请声明参考方法、来源和选图口径")
    require(texts(spec["judges"]), "评审名单为空或重复")
    floor = spec["min_reference_score"]
    require(number(floor) and 0 < floor <= 1, "参考图最低积分比例应在 (0,1] 内")
    dims = spec["dimensions"]
    require(isinstance(dims, dict) and dims, "缺少质量维度")
    for name, dim in dims.items():
        require(name.strip() and number(dim["weight"]) and dim["weight"] > 0, "维度权重须为正数")
        require(texts(dim["anchors"]) and len(dim["anchors"]) == 5, "每维需五个可区分的 0..4 锚点")
    if spec["purpose"] == "formal":
        validate_holdout(spec)
        require(spec["reference_method"] != example()["reference_method"],
                "正式评估需填写真实参考来源")
    cases = spec["cases"]
    require(texts([case["id"] for case in cases]), "题集为空或题号重复")
    require(texts([case["prompt"] for case in cases]), "提示词为空或重复")
    for case in cases:
        require(texts(case["requirements"]), "每题需要可核对的硬条件")
        ref = (Path(contract_path).parent / case["reference"]).resolve()
        format_suffix(ref)
        case.update(reference=str(ref), reference_sha256=file_hash(ref),
                    prompt_sha256=hashlib.sha256(case["prompt"].encode()).hexdigest())
    require(len({c["reference_sha256"] for c in cases}) == len(cases),
            "不同题目的参考图不能复用同一文件")
    write_new(lock_path, {"schema": 1, "id": fingerprint(spec), "contract": spec})


def locked(path):
    lock = read(path)
    require(lock["schema"] == 1 and lock["id"] == fingerprint(lock["contract"]), "冻结合同校验失败")
    return lock


def prompts(lock_path):
    lock = locked(lock_path)
    spec = lock["contract"]
    gen = spec.get("generalization", {})
    method = gen.get("method_sha256")
    return [{**{k: case[k] for k in ("id", "prompt", "prompt_sha256")}, "contract": lock["id"],
             "method_sha256": method if sha(method) else None,
             "runtime_image": gen.get("runtime_image"), "backend": gen.get("backend", "local")}
            for case in spec["cases"]]


def sheet_side(dimensions, requirements):
    return {"scores": dict.fromkeys(dimensions), "hard": dict.fromkeys(requirements)}


def validate_candidate_receipt(receipt, case, lock_id, expected_method, expected_runtime, image_digest):
    require(isinstance(receipt, dict) and receipt.get("schema") == "rsi.first_pass.v1",
            "候选回执 schema 不符")
    require(receipt.get("status") == "rendered" and receipt.get("id") == case["id"]
            and receipt.get("contract") == lock_id
            and receipt.get("prompt_sha256") == case["prompt_sha256"], "候选回执题目绑定不符")
    if expected_method is not None:
        require(receipt.get("method_sha256") == expected_method, "候选回执方法绑定不符")
    if expected_runtime is not None:
        require(receipt.get("runtime_image") == expected_runtime, "候选回执运行环境绑定不符")
    require(receipt.get("image_sha256") == image_digest, "候选回执图像摘要不符")
    require(receipt.get("attempt") == 1 and receipt.get("render_budget") == 1
            and receipt.get("reference_inputs") == 0
            and receipt.get("method_entry") == "method.render", "候选回执首发条件不符")
    require(number(receipt.get("wall_seconds")) and receipt["wall_seconds"] >= 0
            and sha(receipt.get("program_sha256")), "候选回执缺少程序摘要或耗时")


def prepare(lock_path, candidate_path, destination):
    lock = locked(lock_path)
    spec = lock["contract"]
    entries = read(candidate_path)
    require(isinstance(entries, list) and texts([e["id"] for e in entries]), "候选题号为空或重复")
    selected = {entry["id"]: entry for entry in entries}
    require(set(selected) == {case["id"] for case in spec["cases"]}, "候选必须覆盖所有冻结题目")
    refs = {c["reference_sha256"] for c in spec["cases"]}
    assets = []
    receipts = {}
    gen = spec.get("generalization", {})
    for case in spec["cases"]:
        candidate = selected[case["id"]]
        require(candidate["prompt_sha256"] == case["prompt_sha256"], "候选提示词摘要不符")
        ref = Path(case["reference"])
        require(file_hash(ref) == case["reference_sha256"], "参考图已变更")
        image = (Path(candidate_path).parent / candidate["image"]).resolve()
        format_suffix(image)
        digest = file_hash(image)
        require(digest not in refs, "候选与冻结参考文件完全相同，请核查来源")
        assets.append(((image, digest), (ref, case["reference_sha256"])))
        if candidate.get("receipt"):
            data = (Path(candidate_path).parent / candidate["receipt"]).read_bytes()
            body = json.loads(data)
            validate_candidate_receipt(body, case, lock["id"], gen.get("method_sha256"),
                                       gen.get("runtime_image"), digest)
            receipts[case["id"]] = {"sha256": hashlib.sha256(data).hexdigest(), "body": body}
        elif spec["purpose"] == "formal":
            require(False, f"正式题目 {case['id']} 缺少首次执行回执")

    destination = Path(destination)
    require(not destination.exists(), "盲评目录已存在")
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        stage = Path(temporary) / "packet"
        stage.mkdir()
        key = {"contract": lock["id"], "run": secrets.token_hex(16), "panels": [], "receipts": receipts}
        for i, judge in enumerate(spec["judges"], 1):
            label = f"reviewer-{i:03}"
            panel = stage / "review" / label
            panel.mkdir(parents=True)
            assignments = []
            rows = []
            for n, (case, pair) in enumerate(zip(spec["cases"], assets), 1):
                code_side = secrets.choice(("A", "B"))
                shapes = {}
                sides = {}
                for side in ("A", "B"):
                    path, sha = pair[0 if side == code_side else 1]
                    name = f"{n:03}-{side}" + format_suffix(path)
                    shutil.copyfile(path, panel / name)
                    require(file_hash(panel / name) == sha, "准备期间输入文件发生变更")
                    shapes[side] = {"file": name, "sha256": sha}
                    sides[side] = {"file": name, **sheet_side(spec["dimensions"], case["requirements"])}
                assignments.append({"code_side": code_side, "images": shapes})
                rows.append({"id": case["id"], "prompt": case["prompt"], **sides})
            sheet = {"run": key["run"], "contract": lock["id"], "reviewer": label,
                     "dimensions": spec["dimensions"], "rows": rows}
            write_new(panel / "ratings.json", sheet)
            key["panels"].append({"label": label, "judge": judge, "assignments": assignments})
        write_new(stage / "private.json", {"key": key, "sha256": fingerprint(key)})
        stage.rename(destination)


def grade(value):
    bounds = value if isinstance(value, list) else [value, value]
    require(len(bounds) == 2 and all(type(v) is int and 0 <= v <= 4 for v in bounds)
            and bounds[0] <= bounds[1], "评分需为整数 0..4 或递增的两个整数端点")
    return bounds


def generalization_issues(lock, key, raw, host):
    # The evaluator obtains the attestation independently of the candidate manifest.
    gen = lock["contract"]["generalization"]
    if host is None:
        return ["缺少独立受托宿主声明"]
    if (not isinstance(host, dict) or host.get("schema") != "rsi.holdout_attestation.v1"
            or host.get("host") != gen["trusted_host"] or host.get("contract") != lock["id"]
            or any(host.get(k) is not True for k in HOST_FACTS)):
        return ["独立宿主身份、合同或全局证言不符"]
    if (not number(host.get("effective_wall_seconds"))
            or host["effective_wall_seconds"] < gen["minimum_effective_wall_seconds"]
            or not sha(host.get("time_ledger_sha256"))):
        return ["有效研究时间或计时账本证据不足"]
    records = host.get("cases")
    if not isinstance(records, list) or any(not isinstance(h, dict) for h in records):
        return ["独立逐题证言缺失"]
    if not texts([h.get("id") for h in records]) or {h["id"] for h in records} != set(raw):
        return ["独立逐题证言覆盖不符"]
    hosts = {h["id"]: h for h in records}
    issues = []
    for case in lock["contract"]["cases"]:
        case_id = case["id"]
        witness = hosts[case_id]
        receipt = key["receipts"].get(case_id)
        if not receipt or not isinstance(receipt["body"], dict):
            issues.append(f"{case_id}: 缺少首次执行回执")
            continue
        body = receipt["body"]
        expected = {"schema": "rsi.first_pass.v1", "status": "rendered", "contract": lock["id"],
                    "id": case_id, "prompt_sha256": case["prompt_sha256"], "method_sha256": gen["method_sha256"],
                    "attempt": 1, "render_budget": 1, "network": body.get("network"), "reference_inputs": 0,
                    "method_entry": "method.render",
                    "runtime_image": gen["runtime_image"]}
        if (body.get("network") not in ("none", "unverified")
                or any(type(body.get(k)) is not type(v) or body[k] != v for k, v in expected.items())
                or not sha(body.get("program_sha256"))
                or not number(body.get("wall_seconds")) or not 0 <= body["wall_seconds"] <= gen["budget"]["wall_seconds"]
                or any(r["code"]["image_sha256"] != body.get("image_sha256") for r in raw[case_id])):
            issues.append(f"{case_id}: 首次生成回执与冻结条件或评审实物不符")
        if (witness.get("receipt_sha256") != receipt["sha256"] or
                any(witness.get(k) is not True for k in CASE_FACTS)):
            issues.append(f"{case_id}: 独立宿主未核实该回执或未见、首次作者输出与隔离条件")
    return issues


def ratio_band(points):
    total = {role: [sum(p[role][k] for p in points) for k in range(3)] for role in ("code", "diffusion")}
    code = total["code"]
    ref = total["diffusion"]
    return [c / d if d else None for c, d in zip(code, reversed(ref))]


def score(lock_path, packet, attestation_path=None):
    lock = locked(lock_path)
    spec = lock["contract"]
    packet = Path(packet)
    envelope = read(packet / "private.json")
    key = envelope["key"]
    require(fingerprint(key) == envelope["sha256"], "盲评映射校验失败")
    require(key["contract"] == lock["id"], "盲评批次绑定了其他合同")
    require([p["judge"] for p in key["panels"]] == spec["judges"], "评审名单不符")
    raw = {c["id"]: [] for c in spec["cases"]}
    pending = []
    for i, panel in enumerate(key["panels"], 1):
        label = f"reviewer-{i:03}"
        require(panel["label"] == label, "评审编号不符")
        root = packet / "review" / label
        sheet = read(root / "ratings.json")
        require((sheet["run"], sheet["contract"], sheet["reviewer"], sheet["dimensions"]) ==
                (key["run"], lock["id"], label, spec["dimensions"]), "评分表绑定或量表变更")
        require(len(sheet["rows"]) == len(panel["assignments"]) == len(spec["cases"]), "题集覆盖不足")
        for case, row, assignment in zip(spec["cases"], sheet["rows"], panel["assignments"]):
            require((row["id"], row["prompt"]) == (case["id"], case["prompt"]), "题号或提示词变更")
            review = {"judge": panel["judge"]}
            require(assignment["code_side"] in ("A", "B"), "无效盲评映射")
            for side in ("A", "B"):
                role = "code" if side == assignment["code_side"] else "diffusion"
                mark = row[side]
                image = assignment["images"][side]
                require(mark["file"] == image["file"] and Path(mark["file"]).name == mark["file"]
                        and file_hash(root / mark["file"]) == image["sha256"], "盲评图片绑定失败")
                if role == "diffusion":
                    require(image["sha256"] == case["reference_sha256"], "参考图与盲评映射不符")
                require(mark["scores"].keys() == spec["dimensions"].keys() and
                        set(mark["hard"]) == set(case["requirements"]), "评分维度或硬条件变更")
                for v in mark["scores"].values():
                    if v is not None:
                        grade(v)
                require(all(v is None or type(v) is bool for v in mark["hard"].values()), "硬条件应为布尔值")
                if None in list(mark["scores"].values()) + list(mark["hard"].values()):
                    pending.append([case["id"], label, side])
                review[role] = {"image_sha256": image["sha256"], "scores": mark["scores"], "hard": mark["hard"]}
            raw[case["id"]].append(review)
    result = {"scope": "frozen_cases_and_raters_only", "contract": lock["id"], "run": key["run"],
              "purpose": spec["purpose"], "raw": raw}
    if pending:
        return {**result, "status": "incomplete", "pending": pending}

    points = {}
    failed_hard = {"code": [], "diffusion": []}
    weak_refs = []
    weights = {d: v["weight"] for d, v in spec["dimensions"].items()}
    denominator = 4 * sum(weights.values())
    for case_id, reviews in raw.items():
        points[case_id] = {}
        for role in failed_hard:
            for review in reviews:
                for criterion, passed in review[role]["hard"].items():
                    if not passed:
                        failed_hard[role].append([case_id, review["judge"], criterion])
            total = [0., 0., 0.]
            for dim, weight in weights.items():
                ranges = [grade(r[role]["scores"][dim]) for r in reviews]
                lo = min(v[0] for v in ranges)
                mid = sum((a + b) / 2 for a, b in ranges) / len(ranges)
                hi = max(v[1] for v in ranges)
                for k, value in enumerate((lo, mid, hi)):
                    total[k] += value * weight / denominator
            points[case_id][role] = total
        if points[case_id]["diffusion"][0] < spec["min_reference_score"]:
            weak_refs.append(case_id)
    means = {role: [sum(p[role][k] for p in points.values()) / len(points) for k in range(3)]
             for role in failed_hard}
    clo, cmid, chi = means["code"]
    dlo, dmid, dhi = means["diffusion"]
    lower = clo / dhi if dhi else None
    upper = chi / dlo if dlo else None
    if failed_hard["diffusion"] or weak_refs:
        status = "inadequate_reference"
    elif failed_hard["code"]:
        status = "hard_condition_failed"
    elif lower >= .9 - 1e-12:
        status = "meets_on_frozen_cases"
    elif upper < .9 - 1e-12:
        status = "below_target"
    else:
        status = "inconclusive"
    decision = "pilot_only"
    issues = []
    groups = {}
    evidence = None
    if spec["purpose"] == "formal":
        validate_holdout(spec)
        gen = spec["generalization"]
        document = Path(attestation_path).read_bytes() if attestation_path is not None else None
        issues = generalization_issues(lock, key, raw, json.loads(document) if document is not None else None)
        evidence = {"trusted_host": gen["trusted_host"], "method_sha256": gen["method_sha256"],
                    "budget": gen["budget"], "runtime_image": gen["runtime_image"],
                    "minimum_effective_wall_seconds": gen["minimum_effective_wall_seconds"],
                    "attestation_sha256": hashlib.sha256(document).hexdigest() if document is not None else None,
                    "receipt_sha256_by_case": {cid: r["sha256"] for cid, r in key["receipts"].items()}}
        bands = [ratio_band([p]) for p in points.values()]
        for field, plural in (("family", "families"), ("layout", "layouts")):
            groups[field] = {group: ratio_band([points[c["id"]] for c in spec["cases"] if c[field] == group])
                             for group in spec["generalization"]["required_" + plural]}
            bands.extend(groups[field].values())
        if issues:
            decision = "insufficient_generalization_evidence"
        elif status != "meets_on_frozen_cases":
            decision = status
        elif any(b[2] is not None and b[2] < .9 - 1e-12 for b in bands):
            decision = "holdout_below_target"
        elif any(b[0] is None or b[0] < .9 - 1e-12 for b in bands):
            decision = "holdout_inconclusive"
        else:
            decision = FORMAL_PASS
    group_counts = {field: {group: sum(c.get(field) == group for c in spec["cases"])
                            for group in spec.get("generalization", {}).get("required_" + plural, [])}
                   for field, plural in (("family", "families"), ("layout", "layouts"))}
    return {**result, "status": decision, "case_count": len(spec["cases"]),
            "group_counts": group_counts, "descriptive_suite_quality": status,
            "holdout_evidence_issues": issues, "evidence": evidence, "group_ratio_low_mid_high": groups,
            "points_low_mid_high": points, "mean_low_mid_high": means, "target": .9,
            "point_ratio": cmid / dmid if dmid else None, "rating_sensitivity_range": [lower, upper],
            "failed_hard": failed_hard, "weak_reference_cases": weak_refs}


ROUND_DECISIONS = {"growth", "potential", "no_progress", "blocked"}


def validate_round(path):
    """Validate one machine-readable RSI round close without trusting its claims."""
    close = read(path)
    require(isinstance(close, dict) and close.get("schema") == "rsi.round_close.v1",
            "round close schema 不符")
    for field in ("round_id", "parent_method_sha256", "candidate_method_sha256", "next_action"):
        require(isinstance(close.get(field), str) and close[field].strip(), f"round close 缺少 {field}")
    for field in ("parent_method_sha256", "candidate_method_sha256"):
        require(sha(close[field]), f"round close 的 {field} 必须是 SHA256")
    decision = close.get("decision")
    require(decision in ROUND_DECISIONS, "round close decision 无效")
    for field in ("growth_evidence", "potential_evidence", "failed_cases", "new_holdout_coverage",
                  "regressions", "subagent_reviews"):
        require(isinstance(close.get(field), list), f"round close 的 {field} 必须为数组")
    for field in ("wall_seconds", "compute_seconds"):
        require(number(close.get(field)) and close[field] >= 0, f"round close 的 {field} 无效")
    for field in ("images_created", "images_reviewed"):
        require(type(close.get(field)) is int and close[field] >= 0, f"round close 的 {field} 无效")
    require(close["images_reviewed"] <= close["images_created"], "评审数不能超过产图数")
    if decision == "growth":
        require(close["growth_evidence"], "growth 必须附带证据")
        require(not close["potential_evidence"], "growth round 不应同时声明 potential")
        for evidence in close["growth_evidence"]:
            require(isinstance(evidence, dict), "growth evidence 必须为对象")
            for field in ("evidence_id", "before", "after", "delta", "receipt_sha256", "comparison"):
                require(isinstance(evidence.get(field), str) and evidence[field].strip(),
                        f"growth evidence 缺少 {field}")
            require(sha(evidence["receipt_sha256"]), "growth evidence receipt 摘要无效")
    elif decision == "potential":
        require(close["potential_evidence"], "potential 必须附带已执行探针证据")
        for evidence in close["potential_evidence"]:
            require(isinstance(evidence, dict), "potential evidence 必须为对象")
            for field in ("evidence_id", "probe_receipt_sha256", "hypothesis", "falsifier", "next_test"):
                require(isinstance(evidence.get(field), str) and evidence[field].strip(),
                        f"potential evidence 缺少 {field}")
            require(sha(evidence["probe_receipt_sha256"]), "potential probe 摘要无效")
    elif decision == "no_progress":
        require(not close["growth_evidence"] and not close["potential_evidence"],
                "no_progress 不能同时声明 growth 或 potential 证据")
        require(isinstance(close.get("changed_assumption"), str) and close["changed_assumption"].strip(),
                "no_progress 必须说明改变的假设")
    else:
        require(isinstance(close.get("blocked_reason"), str) and close["blocked_reason"].strip(),
                "blocked 必须说明阻塞来源")
    return {"status": "valid", "round_id": close["round_id"], "decision": decision}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("example")
    commands.add_parser("host-example")
    round_parser = commands.add_parser("validate-round")
    round_parser.add_argument("round", type=Path)
    for command, params in (("freeze", ["contract", "lock"]), ("prompts", ["lock"]),
                            ("prepare", ["lock", "candidates", "packet"]),
                            ("score", ["lock", "packet"])):
        command_parser = commands.add_parser(command)
        for param in params:
            command_parser.add_argument(param, type=Path)
        if command == "score":
            command_parser.add_argument("--host-attestation", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "example":
            value = example()
        elif args.action == "host-example":
            value = host_example()
        elif args.action == "freeze":
            freeze(args.contract, args.lock)
            value = {"lock": str(args.lock), "id": locked(args.lock)["id"]}
        elif args.action == "prompts":
            value = prompts(args.lock)
        elif args.action == "prepare":
            prepare(args.lock, args.candidates, args.packet)
            value = {"review": str(args.packet / "review")}
        elif args.action == "validate-round":
            value = validate_round(args.round)
        else:
            value = score(args.lock, args.packet, args.host_attestation)
        print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
        if args.action == "score" and value["status"] != FORMAL_PASS:
            parser.exit(1)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"评估输入错误: {error}\n")


if __name__ == "__main__":
    main()
