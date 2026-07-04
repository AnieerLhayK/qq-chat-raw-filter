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
├── filter_config.toml                # 配置（阈值、关键词等）
├── requirements.txt                  # 依赖说明
├── config_loader.py                  # TOML 配置加载与校验
├── qce_parser.py                     # QCE JSON 解析器
├── block_builder.py                  # 消息→turn→session→my_block 管道
├── scorer.py                         # 四个评分维度
├── bucket.py                         # 分桶决策
├── qce_block_filter.py               # 主 CLI 入口
├── tests/
│   └── test_small_sample.py          # 轻量测试
└── debug/
    └── .gitkeep                      # 调试输出目录
```

## 输入 / 输出路径

| 路径 | 说明 |
|------|------|
| `raw_material/qq/exports/raw/qq-chat-exporter-live/` | 原始 QCE 导出（只读） |
| `raw_material/qq/exports/normalized/run_YYYYMMDD_HHMMSS/` | 每次运行的输出目录 |

所有路径以 `D:\AI` 为最高级（`AI_ROOT`），代码中无硬编码绝对路径。
默认 `AI_ROOT = D:/AI`，可通过 `--input-dir` / `--output-dir` 覆盖。

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
```

### 3. 查看输出

```
candidates.jsonl          # 高价值候选材料
micro_style.jsonl         # 短风格碎片
need_anonymize.jsonl      # 需要匿名化
chaos_style.jsonl         # 抽象/吐槽/脏话
rejected.jsonl            # 丢弃材料（含原因）
stats.json                # 统计信息
active_config.toml        # 本次运行的实际配置
debug_sessions.jsonl      # 调试信息
review_samples/           # 人工审核样本
```

## 分桶含义

| 桶 | 说明 |
|----|------|
| `candidates` | 高价值候选。体现表达结构、分析习惯、语气特点 |
| `micro_style` | 短风格碎片。如"确实"、"绷不住了" |
| `need_anonymize` | 有风格价值但含隐私/具体事实 |
| `chaos_style` | 抽象/吐槽/脏话风格 |
| `rejected` | 丢弃材料，含丢弃原因 |

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

## 核心算法

```
message → turn → session → my_block
```

1. **消息合并**：同一发送者短间隔连续消息 → turn
2. **会话分割**：长时间间隔 → 新 session
3. **发言块**：以我的 turn 为核心，允许短插话不打断
4. **评分**：style / privacy / junk / chaos
5. **分桶**：根据评分阈值分类

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

## 安全说明

- 只读原始数据，从不修改 raw 目录
- 不上传数据，所有处理在本地完成
- 不做模型训练

## 统计参考（3 文件样本）

- candidates: ~21.5% of blocks
- micro_style: ~67.7%
- rejected: ~9.5%
- chaos_style: ~0%（可能需降低阈值）

## 依赖

- Python 3.11+ 标准库
- Python 3.8-3.10 需 `pip install tomli`
- 测试：`pip install pytest`
