# Research Reproducer

**把研究主张变成可执行实验，并保留能复查的证据。**

A lightweight research skill and experiment runner for reproducible baselines, controlled changes, frozen confirmation runs, and evidence reports.

面向 AI / 机器学习论文的机制验证与研究原型。Agent 负责阅读、提出假设和实现；Python 执行器负责固定协议、运行、记录与核对。核心执行器只依赖标准库，不需要模型 API。当前版本：0.2.0。

## 一个具体例子

“Attention Is All You Need” 的注意力缩放示例比较三种实现：未缩放、除以维度、除以维度平方根。错误的“除以维度”方案也可能改善主指标，但公式和绝对条件不通过。本工具同时报告分数变化与条件结果，避免把指标改善直接当作研究结论。

```text
预先声明主张与条件 → 冻结评估协议 → 基线与受控改动
                                      ↓
逐种子证据报告 ← 留出种子确认 ← 冻结候选快照
```

## 快速运行

需要 Python 3.10+；示例另需 NumPy。推荐在虚拟环境中安装示例依赖。

```bash
git clone https://github.com/namgnal/research-reproducer.git
cd research-reproducer
python -m pip install -r requirements-example.txt
python examples/run_attention_demo.py --output demo-output
```

该命令创建新的输出目录，执行 5 轮、每轮 5 个种子的 CPU 实验：基线、错误对照、正确候选，以及两轮冻结确认；最后校验记录并生成报告。输出目录已存在时拒绝覆盖。打开 `demo-output/.research/report/index.html` 查看结果。

这验证的是合成输入下的注意力局部机制；未训练完整 Transformer，也未验证 WMT / BLEU。示例阈值是本实验预设，不能称作论文的官方评分标准。

## 能做什么

- 预先声明来源、范围、指标、分项条件、种子和预算。
- 对显式文件清单保存快照，阻止评估器在运行间意外变化。
- 保留失败、中断、缺失值、标准输出和每个种子的指标。
- 在确认阶段复用冻结版本，并阻止继续依据确认结果调参。
- 将 `score_improved_conditions_failed` 与条件满足的结果分开。
- 输出 HTML / JSON / CSV；`verify` 检查已有证据的一致性。

## 测试与验证

```bash
python -m unittest discover -s scripts -p test_lab.py -v
```

2026-10-08 公开副本复测为 31 项通过、1 项因 Windows 符号链接权限跳过；同时实际运行快速示例的 5 轮、25 次种子实验，证据校验与报告生成通过。详细结果及历史扩展案例见 [验证记录](docs/validation.md)；冻结复跑不计作新增独立统计样本。

## 作为 Skill 使用

仓库根目录包含 `SKILL.md`、`agents/`、`references/` 和配套脚本。将仓库放入宿主支持的技能目录并按其方式加载；目录已存在时先核对版本，不覆盖自定义修改。也可完全不加载 Skill，直接使用 CLI。

- [执行器接口与协议字段](references/runner.md)
- [研究范围与验收设计](references/protocol.md)
- [来源与取舍](references/upstream.md)

## 边界

哈希校验用于发现证据漂移，不提供防篡改担保或代码安全沙箱。种子分离不会对执行者隐藏测试数据。GPU、跨机器和真实训练任务尚未验证；外部 API 费用需要单独预算。本项目没有实现 MLflow、DVC 或 Optuna 适配器。

## 许可与作者

由 [namgnal](https://github.com/namgnal) 设计并借助 AI 编程工具开发。原始实现采用 [MIT](LICENSE)；研究来源与参考项目见来源文档。
