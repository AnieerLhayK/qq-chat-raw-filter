# QQ Chat Raw Material Filter

**QCE Block Filter** — 将 QQ Chat Exporter (QCE) V5 导出的原始聊天记录，
粗处理成结构化分桶材料，用于后续 character skill 提炼。

> 这是一个**粗处理过滤器**，不是最终 skill 生成器。
> 它只负责将原始数据分桶输出，后续再用小模型或人工从候选材料中提炼 character skill。

---

## 目录结构

```
raw_material_filter/
├── README.md                         # 本文件
├── filter_config.toml                # 配置注册表（阈值、词库路径等）
├── requirements.txt                  # 依赖说明
├── config_loader.py                  # TOML 配置加载与校验
├── qce_parser.py                     # QCE JSON 解析器
├── block_builder.py                  # 消息→turn→session→my_block 管道
├── scorer.py                         # 四个评分维度
├── bucket.py                         # 分桶决策
├── pipeline.py                       # Pipeline 编排器
├── dedup.py                          # 去重
├── filter_applier.py                 # 过滤规则应用
├── privacy_filter.py                 # 隐私模式
├── style_tagger.py                   # 风格标签
├── review_sampler.py                 # 分层采样
├── tuning_advice.py                  # 调参建议
├── lexicon_loader.py                 # 外部词库加载器
├── phrase_miner.py                   # 自动短语发现引擎
├── qce_block_filter.py               # 主 CLI 入口
├── lexicons/
│   ├── phrase_bank.jsonl             # 已确认风格短语库
│   ├── phrase_candidates.jsonl       # 自动发现候选短语(未确认)
│   ├── phrase_stoplist.txt           # 无意义高频短语黑名单
│   ├── privacy_lexicon.jsonl         # 隐私风险词库
│   ├── chaos_lexicon.jsonl           # 抽象/脏话/混乱词库
│   ├── drop_sentence_words.jsonl     # 整句丢弃触发词
│   ├── mask_words.jsonl              # 隐私关键词脱敏
│   ├── manual_keep.jsonl             # 手动保留短语
│   └── manual_drop.jsonl             # 手动丢弃短语
├── tests/
│   └── test_small_sample.py          # 轻量测试
└── debug/
    └── .gitkeep                      # 调试输出目录
```

## 架构演进：从 TOML 大词库到注册表模式

### 旧方式

```
人工先在 TOML 里写一大堆词
→ Python 扫 raw 数据时检索这些词
→ 根据命中情况过滤/加分
```

- TOML 文件臃肿，维护困难
- 词库越加越大，但可扩展性差
- 无法自动发现新的风格短语

### 新方式

```
少量基础规则 + raw 数据自动查频
→ 自动发现候选短语
→ 输出 phrase_candidates / review 文件
→ 人工或后续模型确认
→ 沉淀为外部词库
→ raw filter 再读取外部词库辅助处理
```

**核心原则：词库不应该主要是扫描前的人工输入。词库应该更多是扫描 raw 数据后的产物。**

### 现在 TOML 里有什么

`filter_config.toml` 现在是**配置注册表**，不再包含任何词库：

- 所有数值阈值不变（score、ratio、my_block 等）
- 所有词库路径注册在 `[lexicon]` 段
- 短语挖掘参数注册在 `[phrase_mining]` 段
- 全部词库（包括 filter 列表）已移到 `lexicons/` 目录下的外部文件
- `private_names` / `private_places` 是仅剩的 inline 列表（通常极短）

---

## 词库文件说明

| 文件 | 作用 | 更新方式 |
|------|------|---------|
| `phrase_bank.jsonl` | 已确认/高置信度的风格短语库 | **人工维护**，默认不自动覆盖 |
| `phrase_candidates.jsonl` | 自动查频发现的候选短语（未确认） | 自动追加（config 控制） |
| `phrase_stoplist.txt` | 无意义高频短语，用于过滤 | 人工维护 |
| `privacy_lexicon.jsonl` | 隐私风险短语和规则 | 人工+review |
| `chaos_lexicon.jsonl` | 抽象/吐槽/脏话/互怼相关短语 | 人工+review |
| `drop_sentence_words.jsonl` | 整句丢弃触发词 | 人工维护 |
| `mask_words.jsonl` | 隐私关键词脱敏列表 | 人工维护 |
| `manual_keep.jsonl` | 手动指定保留的短语 | 人工 |
| `manual_drop.jsonl` | 手动指定丢弃或强降权的短语 | 人工 |

### phrase_bank.jsonl 格式

```jsonl
{"phrase": "我感觉", "label": "analysis_marker", "source": "toml_migration", "status": "reviewed", "allowed_in_skill": true}
{"phrase": "确实", "label": "tone_marker", "source": "toml_migration", "status": "reviewed", "allowed_in_skill": true}
{"phrase": "啊？", "label": "light_interruption", "source": "toml_migration", "status": "reviewed", "allowed_in_skill": true}
```

label 值：`analysis_marker`、`tone_marker`、`light_interruption`、`argument_marker`、`analysis_tag_marker`

### phrase_candidates.jsonl 格式

```jsonl
{"phrase": "我感觉", "length": 3, "freq": 183, "bucket_freq": {...}, "min_pmi": 3.42, "style_keyness": 5.8, ...}
```

### chaos_lexicon.jsonl 格式

```jsonl
{"phrase": "草", "severity": "mild", "source": "toml_migration", "status": "reviewed", "allowed_in_skill": true, "max_usage": "medium"}
{"phrase": "傻逼", "severity": "strong", "source": "toml_migration", "status": "reviewed", "allowed_in_skill": false, "max_usage": "none"}
```

### privacy_lexicon.jsonl 格式

```jsonl
{"phrase": "密码", "risk": "high", "source": "toml_migration", "status": "reviewed", "score": 3.0}
{"phrase": "学校", "risk": "medium", "source": "toml_migration", "status": "reviewed", "score": 1.5}
```

### drop_sentence_words.jsonl 格式

```jsonl
{"phrase": "进群", "source": "toml_migration", "status": "reviewed"}
```

block 的 `my_text` 含有列表中任一 phrase → 整块丢弃至 rejected。

### mask_words.jsonl 格式

```jsonl
{"phrase": "微信号", "source": "toml_migration", "status": "reviewed"}
```

block 的 `my_text` 中匹配到的 phrase → 替换为 `[MASKED]`。

---

## 短语挖掘（Phrase Mining）

自动从你的发言中发现 2~4 字中文高频短语。

### 算法

- **频率统计**：统计 n-gram 在你消息中的总出现次数和分桶出现次数
- **PMI（凝固度）**：判断短语内部是否稳定，避免随机拼接
- **左右熵**：判断短语左右搭配是否丰富，避免只截到长短语中间一段
- **Style Keyness**：在 candidates/micro_style 中出现越多，在 rejected 中出现越少 → keyness 越高
- **Chaos Rate**：在 chaos_style 中的占比
- **Privacy Rate**：在 need_anonymize 中的占比

### 输出

每次运行的 `normalized/run_xxx/phrase_mining/` 目录：

```
phrase_mining/
├── phrase_freq_2gram.jsonl        # 2字短语频率表
├── phrase_freq_3gram.jsonl        # 3字短语频率表
├── phrase_freq_4gram.jsonl        # 4字短语频率表
├── phrase_candidates.jsonl        # 候选短语（ranked）
├── review_phrases_top.jsonl       # 人工审核候选
├── review_privacy_terms.jsonl     # 隐私风险候选
├── review_chaos_terms.jsonl       # 混乱内容候选
├── review_stopword_candidates.jsonl  # 停用词候选
├── phrase_mining_stats.json       # 聚合统计
└── phrase_mining_report.md        # 可读报告
```

---

## 输入 / 输出路径

| 路径 | 说明 |
|------|------|
| `raw_material/qq/exports/raw/qq-chat-exporter-live/` | 原始 QCE 导出（只读） |
| `raw_material/qq/exports/normalized/run_YYYYMMDD_HHMMSS/` | 每次运行的输出目录 |

所有路径以 `AI_ROOT` 为最高级，代码中无硬编码绝对路径。
默认 `AI_ROOT = D:/AI`，可通过环境变量 `AI_ROOT` 覆盖，或通过 `--input-dir` / `--output-dir` 临时指定。

---

## 快速开始

### 1. 配置身份

在 `filter_config.toml` 中填写你的 QQ 号或昵称：

```toml
[identity]
me_ids = ["你的QQ号", "你的UID"]
me_names = ["你的昵称"]
```

或通过命令行指定：

```bash
python qce_block_filter.py --me-id 123456789
python qce_block_filter.py --me-id u_xxx_uid 1733930883
python qce_block_filter.py --me-name "我的昵称"
```

### 2. 运行

```bash
# 首次建议 dry-run
python qce_block_filter.py --dry-run --limit-files 2 --me-id 123456789

# 完整处理
python qce_block_filter.py --me-id 123456789

# 调试模式
python qce_block_filter.py --me-id 123456789 --debug

# 限制文件数
python qce_block_filter.py --limit-files 5 --me-id 123456789

# 跳过短语挖掘
python qce_block_filter.py --skip-phrase-mining --me-id 123456789

# 强制更新词库候选
python qce_block_filter.py --update-lexicon --me-id 123456789

# 危险：覆盖人工 phrase_bank
python qce_block_filter.py --force-update-phrase-bank --me-id 123456789
```

### 3. 查看输出

```
candidates.jsonl          # 高价值候选材料
micro_style.jsonl         # 短风格碎片
need_anonymize.jsonl      # 需要匿名化
chaos_style.jsonl         # 抽象/吐槽/脏话
rejected.jsonl            # 丢弃材料（含原因）
debatable.jsonl           # 有风格值但质量门未通过
active_config.toml        # 本次运行的实际配置
stats.json                # 统计信息
pipeline_trace.json       # 各阶段执行跟踪
debug_sessions.jsonl      # 调试信息
review_samples/           # 人工审核样本（6个分层文件）
tuning_advice.md          # 调参建议
phrase_mining/            # 短语挖掘结果（详见上方）
```

---

## 命令行参数

| 参数 | 说明 |
|------|------|
| `--config PATH` | 指定 TOML 配置文件 |
| `--input-dir PATH` | 覆盖输入目录 |
| `--output-dir PATH` | 覆盖输出目录 |
| `--me-id [ID ...]` | 我的 QQ 号或 UID（可多个） |
| `--me-name [NAME ...]` | 我的昵称（可多个） |
| `--include-all-speakers` | 调试模式 |
| `--dry-run` | 仅解析，不输出 |
| `--limit-files N` | 限制输入文件数 |
| `--max-messages N` | 限制解析消息数 |
| `--debug` | 调试日志 |
| `--mine-phrases` | 启用短语挖掘（覆盖 config） |
| `--skip-phrase-mining` | 禁用短语挖掘（覆盖 config） |
| `--update-lexicon` | 更新 phrase_candidates.jsonl 自动候选 |
| `--force-update-phrase-bank` | 允许更新 phrase_bank.jsonl（危险） |

---

## 分桶含义

| 桶 | 说明 |
|----|------|
| `candidates` | 高价值候选。体现表达结构、分析习惯、语气特点 |
| `micro_style` | 短风格碎片。如"确实"、"绷不住了" |
| `need_anonymize` | 有风格价值但含隐私/具体事实 |
| `chaos_style` | 抽象/吐槽/脏话风格 |
| `debatable` | 有风格值但质量门未通过的待定块 |
| `rejected` | 丢弃材料，含丢弃原因 |

---

## 人工短语确认流程

1. 运行后查看 `phrase_mining/phrase_mining_report.md`
2. 检查 `review_phrases_top.jsonl` — 高价值风格短语
3. 检查 `review_privacy_terms.jsonl` — 隐私风险短语
4. 检查 `review_chaos_terms.jsonl` — 混沌内容
5. 确认的短语：
   - 风格短语 → 添加到 `phrase_bank.jsonl`（label 根据类型）
   - 隐私词 → 添加到 `privacy_lexicon.jsonl`
   - 混沌词 → 添加到 `chaos_lexicon.jsonl`
6. 无意义高频词 → 添加到 `phrase_stoplist.txt`
7. 使用 `--update-lexicon` 自动合并候选到 `phrase_candidates.jsonl`

---

## 隐私与混沌处理

- **隐私词**（privacy_lexicon.jsonl）不会自动进入 `phrase_bank`
- 隐私词有 `risk: high`（+3.0）和 `risk: medium`（+1.5）两级
- **混沌词**（chaos_lexicon.jsonl）区分 `severity: mild` 和 `severity: strong`
- `allowed_in_skill: false` 的强攻击词不会进入最终 skill
- 强烈建议人工审核所有候选，确保无误

---

## 调试查频结果

| 问题 | 解决 |
|------|------|
| 全是"这个/就是/然后" | 检查 stoplist、PMI、entropy、keyness |
| 几乎没有短语 | 降低 min_freq、min_pmi、min_entropy |
| chaos 词进入主候选太多 | 提高 chaos_rate 惩罚 |
| 隐私词进入 phrase_bank | 加强 privacy 规则 |
| 候选过多 | 提高 top_k_each_length 或 keyness |

---

## 核心算法

```
message → turn → session → my_block → score → bucket → mine_phrases
```

1. **消息合并**：同一发送者短间隔连续消息 → turn
2. **会话分割**：长时间间隔 → 新 session
3. **发言块**：以我的 turn 为核心，允许短插话不打断
4. **评分**：style / privacy / junk / chaos
5. **分桶**：根据评分阈值分类
6. **短语挖掘**：自动发现 2~4 字短语，计算 PMI/entropy/keyness

---

## 调参指南

### candidates 太少
降低 `score.candidate_min_style_score`、`my_block.min_my_block_chars`、
或 `my_block.min_my_msg_count`。

### candidates 太脏
提高 `candidate_min_style_score`、降低 `chaos_separate_score`。

### 短语气词被删太多
在长发言内保留为 fragments。孤立短句进入 `micro_style`。

### 识别不到我的消息
确认 `--me-id`/`--me-name` 正确。使用 `--include-all-speakers` 调试。

### 短语挖掘结果不理想
调整 `[phrase_mining]` 参数：
- 降低 `min_freq` 获得更多候选
- 降低 `min_pmi_2gram` 放松凝固度要求
- 降低 `min_entropy` 接受更多低熵短语
- 调整 `bucket_weight` 改变分桶偏好

---

## 安全说明

- 只读原始数据，从不修改 raw 目录
- 不上传数据，所有处理在本地完成
- 不做模型训练
- 隐私相关短语不会自动进入允许输出的风格库
- 强攻击词默认 `allowed_in_skill: false`

---

## 依赖

- Python 3.11+ 标准库
- Python 3.8-3.10 需 `pip install tomli`
- 测试：`pip install pytest`
