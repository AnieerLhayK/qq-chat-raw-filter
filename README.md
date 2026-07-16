# QQ Chat Raw Material Filter

<div align="center">

**结构化分桶引擎** — 将 QQ Chat Exporter 导出的原始聊天记录粗处理为分桶材料，用于后续 character skill 提炼。

[![Frame for AI Workspace](https://img.shields.io/badge/Frame_for_AI_Workspace-181717?style=flat-square&logo=github)](https://github.com/AnieerLhayK/Frame-for-AI-workspace)
[![Chatty Ch System](https://img.shields.io/badge/Chatty_Ch_System-4B5563?style=flat-square&logo=github)](https://github.com/AnieerLhayK/Chatty-Ch-System)
[![QQ Chat Exporter](https://img.shields.io/badge/QQ_Chat_Exporter-00BFFF?style=flat-square&logo=tencentqq)](https://github.com/shuakami/qq-chat-exporter)

</div>

---

## 📌 定位

**`qq-chat-raw-filter` 是 [Frame for AI Workspace](https://github.com/AnieerLhayK/Frame-for-AI-workspace) 生态中的材料预处理层。**

```
QQ Chat Exporter           QQ Chat Raw Material Filter       Character Skill
（原始数据导出）               （粗处理分桶）                    （后续提炼）
     │                              │                               │
     ▼                              ▼                               ▼
 raw JSON exports ──▶ 结构化分桶候选材料 ──▶ 人工/小模型提炼 skill
     │                              │
 qq-chat-exporter             本仓库
 shuakami/qq-chat-exporter    AnieerLhayK/qq-chat-raw-filter
```

- **上游依赖**：[shuakami/qq-chat-exporter](https://github.com/shuakami/qq-chat-exporter) V5 — 提供原始 JSON 导出
- **下游产出**：分桶候选材料，可作为 [Chatty Ch System](https://github.com/AnieerLhayK/Chatty-Ch-System) 的授权语料输入，并在 [Frame for AI Workspace](https://github.com/AnieerLhayK/Frame-for-AI-workspace) 中进入 character skill 生成/诊断/维护流程
- **本仓库不处理**：模型训练、最终 skill 生成、数据可视化

---

## 🚧 维护规则（重要）

> ⚠️ **本仓库不做本地独立维护。所有源文件来自工作区本地目录。**

### 维护方式

| 项目 | 规则 |
|------|------|
| **源代码来源** | 来自 workspace `packages/character-system/engineering/corpus-preparation/qq-raw-material-filter/` 的公开投影 |
| **本地仓库** | ❌ 不在本地单独 `git clone`，不建立本地独立仓库 |
| **远端维护** | ✅ 仅在此远端仓库 (`qq-chat-raw-filter`) 发布，workspace 是唯一源代码来源 |
| **同步命令** | `python scripts/sync_qq_raw_filter_repo.py --record-id TASK-YYYYMMDD-### --push` |
| **反向同步** | 不反向（远端修改不在工作区内使用） |

### 为什么这样设计

1. **单一事实来源** — 代码以 workspace 本地为准，远端只是发布镜像
2. **避免分裂** — 本地两份仓库会导致版本混乱、冲突不断
3. **简化工作流** — 开发/调试/测试全在 workspace 完成，确认稳定后由公开投影脚本同步到远端

### 如何贡献/修改

1. 在 workspace 本地修改 `packages/character-system/engineering/corpus-preparation/qq-raw-material-filter/` 下的代码
2. 提交到 workspace 仓库（feature 分支）
3. 创建声明了 `external_write` 的活动任务记录；合并到 `main` 后执行 `python scripts/sync_qq_raw_filter_repo.py --record-id TASK-YYYYMMDD-### --push` 同步到此远端

---

## 📦 包结构

```
qq-raw-material-filter/
├── qce_block_filter.py              # ▶ CLI 入口（薄封装）
├── qq_raw_filter/                   # 📦 Python 包
│   ├── __init__.py                  #   包初始化
│   ├── _cli.py                      #   CLI 主逻辑
│   ├── qce_parser.py                #   QCE JSON 解析 + 身份识别
│   ├── block_builder.py             #   消息 → turn → session → MyBlock
│   ├── bucket.py                    #   分桶决策
│   ├── config_loader.py             #   TOML 配置加载与校验
│   ├── scorer.py                    #   四维评分（style/privacy/junk/chaos）
│   ├── style_tagger.py              #   风格标签
│   ├── filter_applier.py            #   过滤规则应用（drop/mask）
│   ├── privacy_filter.py            #   隐私模式
│   ├── dedup.py                     #   去重
│   ├── pipeline.py                  #   Pipeline 编排器
│   ├── phrase_miner.py              #   自动短语发现引擎（2~4字 n-gram）
│   ├── lexicon_loader.py            #   外部词库加载器（active + archive）
│   ├── lexicon_auditor.py           #   词库频率审计引擎
│   ├── archive_lexicons.py          #   词库归档脚本
│   ├── generate_active_lexicon.py   #   Active lexicon 重新生成工具
│   ├── review_sampler.py            #   分层采样
│   └── tuning_advice.py             #   调参建议
├── pyproject.toml                    # 📋 包元数据
├── filter_config.toml               # ⚙️ 配置注册表（阈值/词库路径）
├── requirements.txt                  # 📎 依赖
├── tests/                            # 🧪 测试
│   ├── test_small_sample.py
│   └── __init__.py
└── debug/                            # 🔧 调试输出
    └── .gitkeep
```

> 通过 `pip install -e .` 可以安装为可导入包，CLI 命令 `qce-block-filter` 全局可用。
> 直接运行 `python qce_block_filter.py` 同样支持，无需安装。

---

## 🧭 Pipeline 流程

```
raw JSON (QCE v5)
    │
    ▼
qce_parser        ── 解析 JSON，按身份（--me-id）过滤
    │
    ▼
block_builder     ── 消息合并 → turn → session → MyBlock
    │
    ▼
scorer            ── style / privacy / junk / chaos 四维评分
    │
    ▼
bucket            ── 分桶：candidates / micro_style / need_anonymize / chaos_style / rejected
    │
    ├──▶ lexicon_auditor  ── 归档词库频率审计（v0.3+）
    │
    ├──▶ phrase_miner     ── 2~4字 n-gram 自动发现
    │
    ▼
输出：分桶 JSONL + 审计报告 + 短语候选 + 调参建议
```

---

## ⚡ 快速开始

```bash
# 1. 安装（可选，直接运行也可）
pip install -e .

# 2. 运行 zf 角色语料（输入 writer_name，不输入 character.zf 整体）
python qce_block_filter.py --writer-name zf --me-id 123456789

# 3. 显式覆盖原语料和输出地址（两者都必须非空）
python qce_block_filter.py --writer-name zf \
  --input-dir ${WORKSPACE_ROOT}/raw_material/qq/exports/character.zf/raw/qq-chat-exporter-live \
  --output-dir ${WORKSPACE_ROOT}/raw_material/qq/exports/character.zf/normalized \
  --me-id 123456789

# 4. 调试模式
python qce_block_filter.py --writer-name zf --me-id 123456789 --debug --limit-files 2
```

> 默认路径为 `raw_material/qq/exports/character.<writer_name>/raw/qq-chat-exporter-live`
> 与 `raw_material/qq/exports/character.<writer_name>/normalized`；
> `writer_name`、输入地址或输出地址为空时程序会报错并停止。

---

## 📋 命令行参数

| 参数 | 说明 |
|------|------|
| `--config PATH` | 指定 TOML 配置文件 |
| `--input-dir PATH` | 覆盖输入目录 |
| `--output-dir PATH` | 覆盖输出目录 |
| `--me-id [ID ...]` | 我的 QQ 号或 UID（可多个） |
| `--me-name [NAME ...]` | 我的昵称（可多个） |
| `--include-all-speakers` | 调试：不过滤身份 |
| `--dry-run` | 仅解析，不输出 |
| `--limit-files N` | 限制输入文件数 |
| `--max-messages N` | 限制解析消息数 |
| `--debug` | 调试日志 |
| `--mine-phrases` | 启用短语挖掘 |
| `--skip-phrase-mining` | 禁用短语挖掘 |
| `--update-lexicon` | 更新候选短语文件 |
| `--force-update-phrase-bank` | 强制更新 phrase_bank（危险） |

---

## 📚 分桶含义

| 桶 | 说明 |
|----|------|
| `candidates` | 高价值候选。体现表达结构、分析习惯、语气特点 |
| `micro_style` | 短风格碎片。如"确实"、"绷不住了" |
| `need_anonymize` | 有风格价值但含隐私/具体事实 |
| `chaos_style` | 抽象/吐槽/脏话风格 |
| `debatable` | 有风格值但质量门未通过的待定块 |
| `rejected` | 丢弃材料，含丢弃原因 |

---

## 🗂️ 词库机制（v0.3+）

### Archive → Audit → Active

```
旧词库归档 (archive/)
    ↓
全量数据扫描 + 分桶
    ↓
频率审计 ── 每个归档词在各桶中的出现频率
    ├── promote_to_active: 高频 + 好桶率高
    ├── keep_as_candidate: 中等频率
    └── demote_or_remove: 低频或高废料率
    ↓
短语自动发现 ── n-gram + PMI + style_keyness
    ↓
generate_active_lexicon.py
    ├── 高频验证旧词 → 进入 active
    ├── 高 keyness 新短语 → 进入 active
    └── 低频/零频旧词 → 降级或移除
```

**核心原则：词库不是扫描前的主观假设。Active lexicon 只保留被真实数据验证过的词。**

> 详细词库说明、格式、生命周期见 [词库文档](#词库文件说明)。

---

## 🔐 隐私与安全

- 只读原始数据，从不修改 raw 目录
- 不上传数据，所有处理在本地完成
- 隐私相关短语不会自动进入允许输出的风格库
- 强攻击词默认 `allowed_in_skill: false`

---

## 🧪 测试

```bash
pytest tests/ -v
```

需要 `pip install pytest`。

## Local Review Feedback

Review decisions remain in the private lexicon directory and are never part of
this repository.  The configured file is
`raw_material/qq/exports/character.<writer_name>/lexicons/review_decisions.jsonl`
relative to `AI_ROOT`.
After each run, `review_samples/review_decisions.template.jsonl` contains the
stable block IDs selected for review. Copy selected records into the private
decision file and replace `pending` with `keep` or `drop`:

```json
{"kind":"block","key":"review-id-from-template","decision":"keep","reason":"useful reflective turn"}
{"kind":"phrase","key":"candidate phrase","decision":"drop","reason":"generic chat filler"}
```

Later records for the same phrase or block supersede earlier records. A block
`keep` cannot override a prior privacy, explicit filter, or dedup rejection.
Phrase clustering is review-only and does not merge or promote terms by itself.

---

## 📎 依赖

- Python 3.11+ 标准库
- Python 3.8-3.10 需 `pip install tomli`

---

## 📬 相关项目

| 项目 | 链接 | 说明 |
|------|------|------|
| Frame for AI Workspace | [https://github.com/AnieerLhayK/Frame-for-AI-workspace](https://github.com/AnieerLhayK/Frame-for-AI-workspace) | AI 工作流框架 — 本仓库的上层生态 |
| Chatty Ch System | [https://github.com/AnieerLhayK/Chatty-Ch-System](https://github.com/AnieerLhayK/Chatty-Ch-System) | Character skill 工程系统 — 本仓库 filter 后的分桶语料可作为其生成输入 |
| QQ Chat Exporter | [https://github.com/shuakami/qq-chat-exporter](https://github.com/shuakami/qq-chat-exporter) | QQ 聊天记录导出工具 V5 — 本仓库的数据来源 |
