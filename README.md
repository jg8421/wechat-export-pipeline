# WeChat Export Pipeline · 微信聊天记录 → 可分析语料 → 工作技能手册

把**自己电脑上**的微信聊天记录，整理成结构化、可检索、可喂给大模型的语料，再用多子代理并行提炼成按人区分的「工作技能」手册（Markdown + PDF）。

> ⚠️ **本仓库只包含方法与代码，不含任何聊天记录、人名、账号或个人信息。**
> 所有路径、账号、联系人都是占位符，需要你自己填。

---

## 这套方法解决什么问题

微信聊天记录本身是一份高密度的个人知识库（工作指令、判断口径、行业信息、决策过程），但它：

- 加密存储，普通方式读不出来
- 有文字、语音、图片、文件多种形态
- 噪音极大（表情、系统消息、广告、大群刷屏）
- 一条条看没有价值，**按人/按主题重组**才有价值

本流水线把它变成：**每人一份带逐字引用的工作方法手册**。

---

## 整体流程

```
┌─ 1. 解密导出 ─────────────────────────────────────────┐
│  第三方工具（WeChat EXP）读取微信进程内存里的密钥，     │
│  解密 SQLite 数据库 + 迁移媒体文件                      │
└──────────────────────┬────────────────────────────────┘
                       ▼
┌─ 2. 直读 SQLite 导出 CSV ─────────────────────────────┐
│  wx_export_db.py：读 message_*.db 的 Msg_<md5> 表，    │
│  zstd 解压 message_content，输出统一 CSV                │
│  （比走工具的 HTTP API 快几个数量级）                    │
└──────────────────────┬────────────────────────────────┘
                       ▼
┌─ 3. 语音转文字 ───────────────────────────────────────┐
│  wx_voice_dump.py：从 media_*.db 抽出全部 SILK 语音    │
│  wx_voice_all.py：SILK→WAV + faster-whisper 批量转写    │
└──────────────────────┬────────────────────────────────┘
                       ▼
┌─ 4. 按人归集 + 语料化 ────────────────────────────────┐
│  wx_by_person.py：1:1 全量 + 群聊发言带上下文          │
│  wx_person_corpus.py：过滤噪音 + 上下文保留 + 分片      │
└──────────────────────┬────────────────────────────────┘
                       ▼
┌─ 5. 多子代理并行提炼 ─────────────────────────────────┐
│  每个语料片段交给一个 subagent，输出固定 8 节结构       │
│  wx_skill_merge.py：按节合并成每人一份文档              │
└──────────────────────┬────────────────────────────────┘
                       ▼
┌─ 6. 出书 ─────────────────────────────────────────────┐
│  wx_skill_book.py：全部人物合成一本                     │
│  md2html + html2pdf_safe.py：Edge CDP 打印 PDF          │
└───────────────────────────────────────────────────────┘
```

---

## 目录

```
scripts/
  wx_export_db.py        直读解密 SQLite → CSV（核心）
  wx_export.py           走工具 HTTP API → CSV（备选，慢但简单）
  wx_voice_dump.py       从 media_*.db 抽 SILK 语音
  wx_voice_all.py        SILK→WAV + faster-whisper 批量转写
  wx_voice.py            按会话批量转写并回填 CSV
  wx_fix_voice_paths.py  回填语音文件路径
  wx_by_person.py        按人员归集（1:1 + 群聊上下文）
  wx_person_corpus.py    过滤 + 分片，生成分析语料
  wx_html.py             CSV → 可搜索的 HTML 浏览页
  wx_report.py           全量 QA 总览
  wx_deliver_index.py    按人重新生成文本 + 索引
  wx_skill_merge.py      片段提炼结果 → 每人一份文档
  wx_skill_book.py       全部人物 → 合订本
  wx_probe.py            调试：消息类型分布
  html2pdf_safe.py       HTML → PDF（Edge CDP，多实例安全）
docs/
  01-解密与导出.md
  02-语音转写.md
  03-语料化与分片.md
  04-多代理提炼与出书.md
  05-踩坑记录.md
examples/
  sections.json          合订本分节配置示例
```

---

## 快速开始

**0. 前置**

- Windows（本流程在 Windows 上验证；Linux/macOS 思路相同）
- Python 3.10+
- 微信 PC 版已登录并保持运行（首次提取密钥需要）
- NVIDIA GPU 可选（语音转写会快很多）

**1. 解密导出**（用第三方工具，本仓库不包含）

```bash
# 例：WeChat EXP（开源，见 docs/01）
wechat_exp.exe backup --db-dir "<微信数据目录>\db_storage" -o "<备份目录>" --days 0
```

**2. 导出 CSV**

```bash
python scripts/wx_export_db.py --backup "<备份目录>" --out "<导出目录>" --all        --self-id "<你自己的wxid>"
# 先看有哪些会话：
python scripts/wx_export_db.py --backup "<备份目录>" --out "<导出目录>" --list
```

**3. 语音转文字**

```bash
python scripts/wx_voice_dump.py --backup "<备份目录>"
python scripts/wx_voice_all.py  --backup "<备份目录>" --shard 0 --shards 2        --model small --device cuda --base http://127.0.0.1:5001
# 重新导出一次，语音文字就会写进 CSV 的「语音转写」列
```

**4. 按人归集 + 语料化**

```bash
python scripts/wx_by_person.py     --root "<导出目录>" --out "<按人目录>"
python scripts/wx_person_corpus.py --people "<按人目录>" --out "<语料目录>"
```

**5. 提炼 + 出书**（提炼由你的 agent 框架完成，见 docs/04）

```bash
python scripts/wx_skill_merge.py --parts "<片段提炼目录>" --out "<每人文档目录>" --plan plan.json
python scripts/wx_skill_book.py  --dir "<每人文档目录>" --out book.md        --plan examples/sections.json --preface preface.md --appendix appendix.md
```

---

## CSV 列约定

| 列 | 说明 |
|---|---|
| 时间 | `YYYY-MM-DD HH:MM:SS` |
| 发送者 | `我` 表示自己，否则为对方显示名 |
| 类型 | 文本 / 图片 / 语音 / 视频 / 表情 / 链接/应用 / 名片 / 系统消息 / … |
| 内容 | 文本正文；链接/引用/文件会带 `[链接]` `[引用]` `[文件]` 前缀 |
| 图片文件 | 图片的 md5 / 相对路径 |
| 语音转写 | 语音消息的转写文字（由第 3 步回填） |
| 媒体文件 | 文件/视频/语音的相对路径 |

---

## 提炼出的文档结构（每人 8 节）

1. 人物定位与关系
2. 工作方法与判断标准
3. 沟通与指令模式
4. 数字口径与红线
5. 高频场景与应对
6. 可复用句式（**逐字原文引用 + 日期**）
7. 他会挑的毛病 / 常见错误
8. 与本人的协作要点

关键约束：**引文必须逐字准确并标注日期**，语料没涉及的写「（语料未涉及）」，禁止编造。

---

## 合规与风险提示

- **只处理自己的数据**。不要用它读取同事、家人或公司他人设备上的记录。
- 导出物可能包含商业机密、个人隐私、他人姓名与联系方式 → **仅本地保存**，不要上传网盘或公开仓库。
- 第三方解密工具可能随微信更新失效，且存在被下架的风险；升级微信前先做一次完整备份。
- 语音转写是 ASR 结果，**必然有错字**（专业术语、方言、中英混说），引用前需人工核对。
- 本仓库**不包含**任何解密工具、密钥、聊天数据；请自行从官方发布页获取工具并校验哈希。

---

## License

MIT（见 LICENSE）。仅用于个人数据备份与自用分析。
