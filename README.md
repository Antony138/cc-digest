# cc-digest

Claude Code 更新的中文 AI 摘要站。云端定时 agent（Claude Code routine）每天检查 [官方 changelog](https://code.claude.com/docs/en/changelog)，每 2-3 天发布一期中文摘要，帮你追踪这个几乎每个工作日都在发版的工具。

**网页**: https://antony138.github.io/cc-digest/

手机用法：Safari/Chrome 打开 → 分享 → **添加到主屏幕**。支持离线查看和 RSS 订阅（`rss.xml`）。

## 工作原理

```
Claude Code routine（每天 7:00 JST，跑在 Anthropic 云端，走订阅额度，零现金成本）
  └─ agent checkout 本仓库
       ├─ python3 scripts/collect.py check     # 有新版本 && 距上期 ≥2 天 → PUBLISH，否则 SKIP
       ├─ python3 scripts/collect.py collect   # 输出新版本的原文变更（JSON）
       ├─ agent 阅读原文，撰写中文摘要 → docs/data/YYYY-MM-DD.json（schema 见下）
       ├─ python3 scripts/collect.py finalize docs/data/YYYY-MM-DD.json
       │      # 严格校验 schema；更新 docs/data/index.json 和 docs/rss.xml
       └─ commit & push → GitHub Pages（main 分支 /docs 直出）自动生效
```

没有 GitHub Actions、没有 API key、没有任何 secrets。数据即静态文件，历史归档天然可回看。

## 数据 schema（关键契约，routine 和前端都依赖它）

### 每期摘要 `docs/data/YYYY-MM-DD.json`

```json
{
 "date": "2026-07-21",
 "version_range": {"from": "2.1.215", "to": "2.1.216", "count": 2},
 "tldr": "一两句话点出本期最重要的变化（简体中文）。",
 "highlights": [
  {
   "title": "简短中文标题",
   "category": "新功能",
   "detail": "中文说明：这是什么、为什么值得注意、对日常使用的影响。",
   "action": "需要用户主动做的事；无需行动则为 null",
   "versions": ["2.1.216"]
  }
 ],
 "versions": [
  {"version": "2.1.216", "date": "2026-07-20", "bullets": ["英文原文 bullet，逐条保留", "…"]},
  {"version": "2.1.215", "date": "2026-07-19", "bullets": ["…"]}
 ]
}
```

约束：
- `category` 只能是：`新功能` / `改进` / `修复` / `破坏性变更` / `安全`
- `highlights` 3-6 条，面向老用户提炼，不逐条翻译；**破坏性变更和需要改配置的条目必须收录**，并写明 `action`
- `versions` 按版本号从新到旧排列，`bullets` 为英文原文逐条照抄；`versions[].date` 为 `YYYY-MM-DD`，日期源不可用时允许 `null`（沿用 `collect` 的输出即可）
- `version_range.from` 是本期最旧版本，`to` 是最新版本，`count` 是版本个数
- 文件编码 UTF-8，`ensure_ascii=False`

### 索引 `docs/data/index.json`（由 `finalize` 维护，勿手改）

```json
{
 "last_version": "2.1.216",
 "last_digest_date": "2026-07-21",
 "digests": [
  {"date": "2026-07-21", "from": "2.1.215", "to": "2.1.216", "count": 2, "tldr": "…"}
 ]
}
```

`digests` 按日期从新到旧。`last_version` 是已摘要过的最高版本号（语义化版本比较；changelog 偶有跳号，如 2.1.213 无条目，属正常）。

## 常用操作

- **调整节奏/时间/提示词**：https://claude.ai/code/routines （routine 的 cron 为 UTC，7:00 JST = `0 22 * * *`）
- **手动补一期**：本地跑 `python3 scripts/collect.py check && python3 scripts/collect.py collect`，照 schema 写好 JSON 后 `python3 scripts/collect.py finalize docs/data/YYYY-MM-DD.json`，commit + push
- **调界面**：改 `docs/index.html`，push 即生效（Pages 从 main:/docs 直出）
- **数据源**：主源 `code.claude.com/docs/en/changelog.md`（含日期的 MDX）；兜底 `raw.githubusercontent.com/.../CHANGELOG.md` + npm registry `time` 字段补日期

## 成本

零现金：GitHub Pages 免费 + routine 走 Claude 订阅额度（每天一次几分钟的小会话）。
