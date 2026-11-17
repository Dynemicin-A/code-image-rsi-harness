# 纯代码生图与 RSI Harness

本仓库用于启动一个全新的、可审计的 code-to-image RSI 实验。作者只接收文字 prompt 和冻结的公共方法接口，必须以代码构造图像；实验全程禁用 `image-gen`、图生图、外部图像生成模型和目标图拟合。泛化是硬性验收条件：单题高分、局部修复、速度提升或完整封包都不能替代未见题上的正式结果。

实验合同和执行规则见 [spec.md](spec.md)、[安全与规范细则.md](安全与规范细则.md)、[执行效率细则.md](执行效率细则.md)、[质量与RSI执行细则.md](质量与RSI执行细则.md) 和 [隔离实验_v0.1_6h_泛化与RSI.md](隔离实验_v0.1_6h_泛化与RSI.md)。规范不规定题材、构图、材质、镜头、渲染语言或后端；每轮由 agent 根据测量结果选择并记录方案。

## 新实验流程

1. 评估方运行 `python3 quality_eval.py example` 取得合同示例，填入本轮的 prompt、合法的同 prompt diffusion 参照、语义硬条件、盲评量表、至少两个独立评审、作者模型、策略摘要、方法摘要、运行环境、至少 48 道留出题和有效研究时间下限。正式合同使用 `purpose=formal`。
2. 评估方运行 `python3 quality_eval.py freeze contract.json lock.json` 冻结方法哈希、题集、参照哈希、作者策略和每题预算。运行 `python3 quality_eval.py prompts lock.json` 逐题派发；作者上下文只包含当前文字题、冻结公共接口和必要的执行约定。
3. 作者提交一次 `build(prompt)` 实现。评估宿主可直接运行 `python3 first_pass.py task.json method.py program.py runs/registry --timeout 120`，不需要 Docker。该适配器读取独立临时目录，执行本地 Python 进程，限制场景描述和图像尺寸，并记录退出状态、耗时、RGB 图哈希和回执。宿主必须另外提供进程、ACL、网络和文件读取证据；普通本地子进程本身不能证明安全隔离。
4. 后端由 agent 自主选择。Blender、Python、SVG、GPU、自定义光栅器、二维或三维混合方案均可使用，也可以实现新的 adapter。每个候选必须记录后端版本、依赖、设备、随机约定、输入边界、方法哈希、原生完整输出和失败回执。不得把图像生成模型、扩散采样器或外部图像 API 包装成后端。
5. 评估方运行 `python3 quality_eval.py prepare lock.json candidates.json packet` 生成匿名盲评包。正式包在复制图像前校验每题首发回执、方法／运行环境／图像哈希和一次性预算；将 `review/reviewer-*` 分给独立评审。评审逐题核对语义硬条件和量表锚点；评估宿主保存题库、参照、映射、日志和完整分母。
6. 评估方运行 `python3 quality_eval.py score lock.json packet --host-attestation trusted.json` 结算结果。受托声明必须包含网络、ACL、资源、宿主凭据、评审独立性和有效计时账本证据。退出码 `0` 只表示正式质量和泛化同时通过，`1` 表示未通过或证据不足，`2` 表示输入或合同错误。唯一通过状态为 `meets_quality_and_generalization_on_declared_holdouts`。
7. 每轮关闭前运行 `python3 quality_eval.py validate-round runs/<run_id>/reports/round_<id>_close.json`，校验 `growth`、`potential`、`no_progress` 或 `blocked` 的证据结构；该命令不替代图像、回执或宿主真实性核验。

## 六小时隔离实验

[隔离实验_v0.1_6h_泛化与RSI.md](隔离实验_v0.1_6h_泛化与RSI.md) 定义至少 21,600 秒有效研究墙钟、agent 放权、后端无关性、未见 prompt 泛化、subagent 盲评、增长／潜力／无进展／阻塞结算和停止条件。48 张只是可调整的启动规模；实际批次、分布覆盖和有效时间必须以 append-only 台账和宿主证据为准。没有实测证据时不得宣称质量、泛化或效率达标。

每轮 RSI 必须写明问题、假设、选择理由、预期改变、反证条件、实际测量和下一决策。`growth` 需要可复核的基线差异；`potential` 需要下一步可证伪的实验和支持证据。低信息探针、空队列、等待或自报成功不能伪装成增长。

## 本地运行

核心测试只依赖 Python 标准库：

```sh
python3 -B -m unittest discover -v
```

不安装 Docker、不要求固定渲染器，也不要求额外的仓库外文件。宿主可通过 `RSI_PYTHON` 指定 Python 解释器；运行器会把实际解释器摘要写入回执。后端自身的依赖、编译器和设备由该候选的 manifest 声明并由受托宿主安装或提供。运行器限制输出尺寸和场景大小，但 CPU、内存、网络、ACL 和进程回收仍由受托宿主落实。

## 运行产物

新 run 的登记目录由宿主创建，建议包含：

```text
runs/<run_id>/
  contract.json
  method/
  author_prompts/
  development/
  holdout/
  reviewers/
  events.jsonl
  receipts/
  reports/
  checkpoints/
  STOP_REQUEST
```

合同、题集、参照、作者代码、图像、回执、评审批次、心跳、计时账本和 `round_<id>_close.json` 均由 run 产生并写入登记目录；它们不需要预先存在于代码仓库。正式评估还需要受托宿主提供真实访问日志、权限边界、网络状态、方法冻结和盲评脱敏证据。

## 文件清单

- `first_pass.py`：无 Docker 的本地 Python 适配器。
- `quality_eval.py`：合同冻结、盲评包准备、宿主证言和正式评分。
- `triage.py`：依据登记的观察、依赖、实验和分阶段耗时选择下一步。
- `spec.md`：主合同和验收条件。
- `安全与规范细则.md`、`执行效率细则.md`、`质量与RSI执行细则.md`：执行约束。
- `隔离实验_v0.1_6h_泛化与RSI.md`：本轮六小时新实验 runbook。
- `test_*.py`：标准库单元测试。
