# 调研来源与实现取舍

核对日期：2026-10-05。下列哈希固定本次查看版本。首版整合流程与设计思想，配套执行器为新写代码；没有打包上游框架源码，也没有声称已经接通其 API。

| 来源 / 版本 | 吸收的部分 | 首版处理 |
| --- | --- | --- |
| [karpathy/autoresearch](https://github.com/karpathy/autoresearch/tree/228791fb499afffb54b46200aca536f79142f117) · `228791fb499a` | 固定评估器、先基线、预算内的短实验循环 | 改为有限预算和显式范围，不采用无限循环、自动重置原仓库或单 GPU 必需假设 |
| [WecoAI/AIDE](https://github.com/WecoAI/aideml/tree/60b3978ddf65b71f86eb7c64506965048a1398cf) · `60b3978ddf65` | draft/debug/improve 阶段、候选谱系、保留失败并从父节点接续 | 记录 parent、假设、原始结果与状态；没有移植其树搜索算法或宣称性能等价 |
| [OpenAI PaperBench](https://github.com/openai/frontier-evals/tree/51052cede8cc608f95bb00346635e03759013e5a/project/paperbench) · `51052cede8cc` | 将论文复现拆为可检验条件，区分提交实现、实际复跑和评分 | 来源→主张→条件→证据；确认阶段从快照重新执行。不是官方 PaperBench 测试或得分 |
| [Sakana AI Scientist v2](https://github.com/SakanaAI/AI-Scientist-v2/tree/96bd51617cfdbb494a9fc283af00fe090edfae48) · `96bd51617cfd` | 多阶段实验、失败修复及研究过程留痕 | 设计参考；不引入重型 GPU/模型服务流水线，不复制其代码 |
| [wjgoarxiv/autoresearch-skill](https://github.com/wjgoarxiv/autoresearch-skill/tree/c4c5948994dd9000eb19d69afcb8d62c05bf0112) · `c4c5948994dd` | 宿主 Agent + 可移植 Skill + 小型确定性工具 | 保持论文主张验证的窄入口，不将部署、监控、产品构建等统一塞入本 Skill |
| [MLflow](https://mlflow.org/docs/latest/ml/tracking/) · 仓库 `c6a8c9a5adc7` | 参数、指标、版本、产物与 run 的关联记录 | 借鉴记录模型；已有项目可沿用 MLflow 命令，首版未安装或验证 MLflow 适配器 |
| [DVC](https://github.com/treeverse/dvc/tree/56e59829512ff134aa269099a2099587b810b4dd) · `56e59829512ff` | 数据与实验版本、可复跑的依赖关系 | 小实验用显式文件快照；大数据项目建议沿用 DVC 等既有工具，首版无 DVC 适配器 |
| [Optuna](https://optuna.readthedocs.io/en/stable/tutorial/10_key_features/002_configurations.html) · 仓库 `5c8e50d85b77` | 条件搜索空间、trial 和资源分配 | 大量参数搜索时再用；首版主线是验证研究主张，没有伪装成新的搜索算法 |

## 许可核对

- AIDE、PaperBench 所在仓库、wjgoarxiv/autoresearch-skill、Optuna：所查 LICENSE 为 MIT。
- MLflow、DVC：所查 LICENSE 为 Apache-2.0。
- karpathy/autoresearch：README 声明 MIT，但本次固定版本的独立 LICENSE 路径返回 404，GitHub 许可字段为空；不将其代码视作已完成许可核验的可直接分发素材。
- AI Scientist v2：实际 LICENSE 为 *The AI Scientist Source Code License v1.0*，含使用条件，并非普通 MIT；这里只参考方法。后续复制代码时需重新评估适用条件。

首版没有随包分发这些项目的源码。若后续真正集成依赖或复制片段，应在具体版本上保留必要声明、记录修改，并明确新增依赖及测试情况。

## 论文示例来源

[Attention Is All You Need](https://arxiv.org/abs/1706.03762)，v7，p.4，第 3.2.1 节公式 (1) 与脚注 4。示例据该公式自行实现，并以合成输入验证尺度行为；不包含整篇论文文本、原训练数据或第三方实现。其对照与验收阈值由本次实验制定。
