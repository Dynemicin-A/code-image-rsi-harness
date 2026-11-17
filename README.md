# 纯代码生图与 RSI · 本地 v0.16

当前目标是让文字模型利用冻结的公共代码在未见题上首次生成图像，达到同一 prompt 的既有 diffusion 参照积分的 90%。泛化是总验收的硬条件。主规则写在 [spec.md](spec.md) 开头。此目录包含规范、运行器、评估器和一个可选的历史后端回归样例。正式成绩仍待独立留出实验。

## 可选历史回归样例

雨街花店的原生图中出现了面包，因为冻结渲染器写死了商品和褐色窗面。运行下面的命令可以重放当时那组证据下的调度决策。

```sh
python3 -B triage.py storefront/triage.json
```

输入同时登记了泛化缺口和它依赖的源码问题，返回的下一步指向橱窗接口。这里的 10 秒为探针预估；没有分阶段实测时，开销热点为空。新版接口接收可组合的窗内几何及玻璃色，开发图中能辨认花瓶和书架。保留旧输入时，用同一环境运行旧版和新版得到一致的原生 RGB。两种店面都属于同一街景模板，跨题材验收仍然开放。详见 [源文件、消融及复现记录](storefront/README.md)。

`triage.py` 根据已登记的观察、依赖、实验及非重叠阶段耗时作选择，不能自行看图找出没有登记的缺陷。空队列只触发独立验收。复盘开销可参考同一 work 下的既有记录 `clean_runs/image_02/workspace_pre_zxh_storage_20261116_1306/method_track/execution_tests/roi24576/equivalence_result.json`。在那次 24,576 区域的同图拟合中，耗时从 465.25 秒降至 11.00 秒，状态与 PNG 字节一致。该记录只回答那个算子的耗时问题。当前研究没有端到端 RSI 加速或策略收益成绩。

## 正式验收入口

1. 评估方运行 `python3 quality_eval.py example` 取得合同示例。填写原始题目、同 prompt 的既有 diffusion 原件及来源、未见家族与布局、可辨识的语义条件、带锚点的整图量表、评审名单、开发清单、作者模型与策略摘要、方法和镜像摘要及每题预算。正式合同使用 `purpose=formal`。若来源或独立性无法建立，只能记开发结果。
2. 评估方用 `python3 quality_eval.py freeze contract.json lock.json` 冻结。由宿主运行 `python3 quality_eval.py prompts lock.json`，把数组拆成单题，再给每个隔离的作者上下文。各题作者只得到该题文字、同一冻结公共方法接口和必要的执行约定。参考路径、整份题库、其他作者程序、评分及结果留在评估侧。
3. 作者提交实现 `build(prompt)` 的 Python 源码。评估宿主在同一登记目录运行 `python3 first_pass.py task.json method.py program.py runs/registry --timeout 120`。超时时间取冻结预算。当前运行器默认使用本地 Python 进程，读取独立临时目录，网络环境由受托宿主关闭，失败也占该题的尝试。作者程序输出 JSON 场景，冻结模块负责 `method.render(scene)` 并返回 RGB 图。当前适配器支持一个公共 Python 模块、1 MiB 场景描述、最长边 4096；其他后端需另行实现同等边界。`RSI_PYTHON` 可以指定受托宿主使用的 Python 解释器。普通本地子进程不自动证明安全隔离，正式实验必须保留宿主的进程、ACL、网络和读取日志。
4. 评估方提供候选索引，每项含 `id`、`prompt_sha256`、`image` 和 `receipt`，后两项指向登记产生的文件。运行 `python3 quality_eval.py prepare lock.json candidates.json packet`，把匿名的 `review/reviewer-*` 分别交给评审。每项可填单一锚点或上下限；双方各自检查该题硬条件。评估方从受托宿主另取证言，表结构可用 `python3 quality_eval.py host-example` 导出。受托方须核对作者侧实际权限、完整首次提交历史、素材来源、方法冻结和模板血缘。回执哈希只绑定记录；共用一台机器时，登记册和证言来源须由评估宿主管理。
5. `python3 quality_eval.py score lock.json packet --host-attestation trusted.json` 输出逐题原分、家族和布局的保守比值与总状态。**退出码 0 只用于正式质量与泛化同时通过，1 表示总验收未通过或证据不足，2 表示输入或合同错误。** 唯一通过状态为 `meets_quality_and_generalization_on_declared_holdouts`。已见题、选片、未评分、弱参照、单一族、缺少首发／证言等条件均无法通过。不得用开发分或描述性总均分代替这个状态。

## 本地复核

核心测试只依赖 Python 标准库，可在仓库根目录运行 `python3 -B -m unittest discover -v`。`storefront/` 是旧雨街接口修复的可选回归样例，使用它时再安装 `python3 -m pip install -r storefront/requirements.txt` 并在该目录运行测试。新隔离实验不要求使用这个目录、这个题材或这个渲染后端。测试数据中的示例评分仅用于验证门槛分支。

本地仓库的首次提交保存 v0.15 主规范（原 SHA-256 `9e29183f78db1db1aa1e1c77f66a0b34e640c5537c49ad9b07a2257773d37902`）和三份可读细则。原件来自相邻的 `harness_v0.15_quality_rsi/`。旧版文本提及的 v0.13 完整参考及外置盘运行目录不在此工作副本中。此次未迁移或恢复 zxh 上的旧实验。

## 文件可用性

克隆仓库后，规范、源码、测试、示例 triage 状态和隔离实验 runbook 都在仓库中。下面这些文件由新 run 或受托评估宿主创建，不应期待它们出现在代码仓库里：

- `contract.json`、`lock.json`、`candidates.json`、`task.json`、`trusted.json`
- `method.py`、`program.py`、每题 `receipt.json`、`image.png`
- `runs/<run_id>/` 下的 `events.jsonl`、心跳、账本、评审批次、`round_<id>_close.json` 和报告
- 评估侧保存的 diffusion 参照、隐藏题库、A/B 映射和正式评分

当前公开仓库没有以下历史或外部材料：`clean_runs/image_02/workspace_pre_zxh_storage_20261116_1306/method_track/execution_tests/roi24576/equivalence_result.json`、`clean_runs/probe_image_01/workspace/OUTPUT_INSPECTION.md`、zxh 上的旧 run、原始 teacher 图和旧版 v0.13 完整参考。这些路径只作为历史证据引用，缺失时不能运行对应复盘，也不能把历史数字当作当前 run 的实测结果。

`storefront/` 的可选历史回归依赖 NumPy 和 Pillow，可用 `pip install -r storefront/requirements.txt` 安装。新 run 可以完全不读取这个目录。formal 评估还需要受托宿主实际提供访问日志、计时账本和盲评脱敏流程。仓库当前的 `quality_eval.py` 是评分逻辑起点，不能单独证明这些外部隔离事实。
