# 执行器接口

Python 3.10+。`scripts/lab.py` 和 `scripts/report.py` 只用标准库。示例另需 NumPy。以下 `python` 应替换为当前工程已可用的 Python；`<lab.py>` 是本 Skill 中脚本的绝对路径。

## 协议与输入

先复制示例目录，再编辑自己的 `plan.json`。协议字段见 `assets/scaled-attention/plan.json`：

| 字段 | 含义 |
| --- | --- |
| `title / question / scope / reproduction_level` | 研究问题及机制 / 部分 / 完整复现范围 |
| `files` | 显式列出需快照的相对文件路径，包含候选、配置和评估器；不用 glob |
| `protected_files` | 运行间不可变的评估器、划分或数据清单，必须属于 files |
| `command` | argv 数组，不经过 shell；可用 `{python}`、`{seed}`、`{phase}`、`{result}`、`{workspace}` |
| `seeds / confirmation_seeds` | 两组不重叠的非负整数种子，每轮按该阶段全部种子执行 |
| `metric` | 主指标 name、minimize/maximize、最小改善量 min_delta |
| `budget` | max_trials 为试验轮数（包括失败和确认）；per_run_seconds 为单种子进程时限，total_seconds 为累计运行时限 |
| `required_checks` | 每个重复都必须严格返回 true 的基本有效性检查 |
| `claims` | 主张、来源 URL、原文位置；criterion 为 metric/operator(le 或 ge)/threshold，可选 aggregation(mean 或 all)，或 null 表示尚不验证 |

`criterion.aggregation` 默认为 `mean`，比较完整确认轮的种子均值；`all` 要求每个种子都满足阈值。旧协议省略此字段时继续保持 mean 的原含义。两种规则都输出逐种子值、达标比例和范围；它们不是置信区间。规则在 init 前固定，确认结果出现后不能改规则挑选结论。若要比较单个种子内最差维度等性质，在评估器中先输出相应指标。`required_checks` 是基本有效性，主张条件允许不通过并保留负结果。

`files` 使用正斜杠，不允许绝对路径、符号链接或 `..`。单文件上限 128 MiB。大数据使用固定外部路径及显式版本/内容哈希清单，由评估器核对；没有列入清单的外部数据、依赖和环境不会被自动复制或锁定。运行目录每个种子新建；评估器应从当前目录读取实现与配置，并把结果写到 `--output`。

结果 JSON：

```json
{"metrics": {"loss": 0.21}, "checks": {"finite_output": true}, "artifacts": ["measurements.json"]}
```

所有 metrics 值必须是有限数，布尔值无效。基本检查必须全部通过。额外产物只能声明运行目录内部的相对文件。缺失结果、失败检查或非零退出都留下失败记录；不会把缺失指标当作零。

## 命令

```text
python <lab.py> --project <实验目录> init --plan plan.json
python <lab.py> --project <实验目录> run --baseline --label 基线 --hypothesis "预先声明基线行为"
python <lab.py> --project <实验目录> run --parent <父轮ID> --label 候选 --hypothesis "可被否定的改动假设"
python <lab.py> --project <实验目录> compare --baseline <基线ID> --candidate <候选ID>
python <lab.py> --project <实验目录> run --phase confirmation --source-run <基线ID> --label 基线确认 --hypothesis "在新种子上复跑冻结基线"
python <lab.py> --project <实验目录> run --phase confirmation --source-run <所选候选ID> --label 候选确认 --hypothesis "在新种子上核验冻结候选"
python <lab.py> --project <实验目录> verify
python <lab.py> --project <实验目录> report --run <候选确认ID>
```

命令输出 JSON；正常成功返回 0，失败/无效/超时返回 2。失败返回包含出错种子、错误原因及 stdout/stderr 路径；每轮均返回 record 路径。`status` 列出历史 ID、指标摘要与剩余轮次/时长，运行中标记 running；时长结算以已结束的轮次为准。`--parent` 只标记谱系，不替你复制父版本；要编辑父版本，先从相应 snapshot 复制到工程。`--source-run` 实际复制历史快照，当前候选文件的变化不会混入复跑。

`compare` 保留仅表示分数变化的 `decision`，同时提供 `selection_status`、`candidate_conditions_met` 和逐主张条件。出现 `score_improved_conditions_failed` 时，分数虽改善，候选仍未满足预设条件。开发阶段的主张状态为 inconclusive；条件达到不等于完成确认。

`report` 默认在 `.research/report/` 生成 index.html、report.json、metrics.csv。HTML 可展开逐种子明细，CSV 保留失败与未执行种子，缺失值留空。当前阶段存在唯一完整基线时自动附对照；多个基线时用 `--baseline <ID>` 选择。`--output <目录>` 可将报告输出到单独位置，证据链接相对该目录生成；不要将报告覆盖到 trials 快照目录。CSV 中以公式符号开头的文本会加单引号，避免表格软件将标签当公式。

只有获得有效基线后才能运行候选。确认必须引用已成功完成的开发轮；所选候选只能有一个，进入确认后禁止继续开发。这是预先固定方案的约束，不是测试集保密措施。比较要求相同阶段、协议和配对种子，返回均值、样本标准差与每个种子的改善量；不输出未经检验的“显著提升”。

## 示例

1. 把 `assets/scaled-attention/` 复制到新的实验目录，保留初始 `config.json` 的 `unscaled`，init 并运行基线。
2. 只改 config 的 mode 为 `dimension`，运行过度缩放对照；再改为 `sqrt_dimension`，运行论文公式候选。每次记录不同假设和父轮。
3. 在开发种子上比较，结合公式一致性及绝对方差条件选择候选，不能只看相对改善。
4. 使用冻结基线和所选候选的 ID 分别执行 confirmation，再比较这两轮。
5. verify 后对候选确认轮生成报告。完整 Transformer 的翻译性能主张仍为“未验证”。

示例输入是独立标准正态随机数，开发维度为 16/64/256，确认维度为 32/128/512。方差偏差与熵阈值是本次机制实验的选择，不是论文官方评分；种子变化不是增加了多个真实任务或数据集。

## 保存、恢复与边界

`.research/contract.json` 保存预先注册的协议与保护文件哈希；每轮 `.research/trials/<id>/` 保存 record、snapshot 和 repeats。每个重复保留命令、种子、原始 JSON、日志及声明产物的 SHA-256。`verify` 检查已有证据；报告不含历史证据文件时只能阅读，不能完成完整复核。

同时只能有一个写入者。异常中断后先检查 lock.json 和未完成重复的 PID；确认旧执行器及记录的实验进程都已结束，再执行 `recover --reason "实际中断原因"`。工具拒绝在已记录进程仍存活时恢复，并将未完成轮标为 interrupted；中断轮仍占预算。旧版本没有实验 PID 的记录只能检查其执行器 PID，需要人工核对是否有遗留进程。PID 重用可能导致保守拒绝，需核实后处理。预算不足或更换协议时新建实验目录，保留原记录。

时限作用于前台实验进程；超时清理需要少量额外时间。实验代码不应后台化或留下脱离进程树的服务。API 调用费用、GPU 成本和外部进程不由此执行器控制。文件哈希能发现意外漂移，但同权限执行者可改账本，既不是防篡改证明，也不是代码安全沙箱。未经审阅的代码需要另外的隔离环境。
