# 验证记录

## 2026-10-08 公开副本复测

环境：Windows、Python 3.12.14、NumPy 2.3.5。检查直接针对本发布副本运行。

| 检查 | 实际结果 |
| --- | --- |
| `python -m unittest discover -s scripts -p test_lab.py -v` | 32 项：31 通过、1 跳过，约 40 秒 |
| `python examples/run_attention_demo.py --output <新目录>` | 成功；5 轮、25 次种子运行，约 28 秒 |
| 错误对照判定 | `score_improved_conditions_failed` |
| 冻结候选确认 | `score_improved_conditions_met` |
| 证据校验与报告 | `verify` 成功；HTML / JSON / CSV 已生成 |

跳过项为符号链接路径检查，原因是当前 Windows 用户无法创建该链接。未把跳过项算作通过。示例结果摘要见 [example-results.json](example-results.json)。该文件是本次实测摘编；完整逐种子记录可用 README 中的示例入口重新生成。

本次使用仓库内置的基础示例，有 3 项可测条件；历史扩展案例另有直接 Jacobian 等测量。两者范围分别记录，不把历史指标混入本次结果。

## 既有扩展案例

2026-10-05 前的扩展注意力机制案例完成 25 次主运行、10 次冻结快照复跑，并另写数学实现核对原始数组。4 个预设局部条件各有 5/5 种子通过。复跑复用冻结配置，不算新增独立统计样本。公开仓库不携带原有约 398 MiB 的机器本地实验目录。

## 尚未验证

Linux/macOS、GPU、跨机器、完整 Transformer 训练、官方 PaperBench 和真实数据上的研究收益。哈希验证不是防篡改证明。
