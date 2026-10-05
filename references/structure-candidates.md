# 可选：PDF 结构候选

`structure-candidates` 是独立的可选入口。它只读已导入 PDF 的 atoms（`page` + `bbox`），按坐标、栏位和行间空隙给出段落候选与阅读顺序建议。默认 `import` → `show` → `submit structure` 流程不变；不运行它也能完整处理文献。

## 不做什么

- 不写 `document.json`，不改 revision、blocks、issues、复核状态或 `submissions`。
- 不调用 `submit`，不生成结构 payload，不自动接受任何候选。
- 不跨页合并，不 OCR，不拆分抽取阶段已经粘在一个 atom 里的内容。

Agent 仍须按页对照原页图确认，再用原来的 `submit` 提交 `structure`（完整 `blocks` 或 `keep_extracted` + `updates` / `merges`）。候选只是起点，不能当成已核对的结构。

## 调用

```sh
python "<SKILL_DIR>/scripts/paper_reader.py" --workspace "<WORKSPACE>" structure-candidates DOC_ID [--out PATH] [--preview PATH] [--preview-pages 3]
```

- 默认把完整候选 JSON 写到 `WORKSPACE/candidates/DOC_ID-structure-candidates.json`，不写进 `data/DOC_ID/`。stdout 只给路径、atom 数、候选数、`coverage_ok`、orphan 数、`hard_spot_counts` 和每页 `mode` / `gutter`。
- `--preview` 写一份前 N 页（默认 3）的 Markdown 预览，便于逐页抽查。
- 写入前会 resolve 默认与显式输出路径（含文件/目录符号链接）；落在 `data/` 内、或 `--out` 与 `--preview` 解析到同一真实路径时直接拒绝，避免覆盖文献数据或用 Markdown 盖掉 JSON。
- 非 PDF 文档（atoms 没有 `page` + `bbox`）直接报错 `ok:false`。
- 每个 atom 必须恰好出现在一个候选里；覆盖检查失败时报错，不输出半成品。

## 输出

顶层：`tool`、`version`（当前 `20261005b`）、`not_a_structure_submission: true`、`source`（document_id、title、读取时的 revision）、`pages`（每页 `mode` single/two、`gutter`、行数、正文字高）、`candidates`、`coverage`、`hard_spot_counts`、`orphan_atom_ids`。

每个候选：`id`、`order`、`page`、`column`（full/single/left/right/header/footer/fragment/none）、`kind_hint`（paragraph/heading/caption/page/header/footer/figure_fragment/orphan）、`atom_ids`、`bbox`、`confidence`、`hard_spots`、`text_preview`。`confidence` 是启发式分数，不是概率。

`hard_spots` 是需要优先看页图的地方：

| 标记 | 含义 |
|---|---|
| `footnote_marker_split` | 几何上可以并，但下一行以 `∗ * † ‡ § ¶ ‖` 开头，所以拆开；确认拆分是否正确 |
| `possible_subscript` | 小字盒挂到所在行末尾（不在原字位置） |
| `inline_gap` | 同一基线中间有洞（多为行内公式）而拼回一行 |
| `hyphen_join` | 连字符断行已并，连字符仍保留 |
| `hanging_indent` | 按悬挂缩进并入（常见于参考文献） |
| `possible_cross_page` / `possible_cross_page_hyphen` | 可能跨页续段，只标记不合并 |
| `isolated_fragment` / `fragment_beside_text` | 图内碎字、表格格子；后者只表示碎字落在某段框内，不是并入正文 |
| `possible_multi_item` | 一个 atom 内含多个邮箱等，抽取阶段已粘连，本工具拆不开 |
| `possible_fused_columns` / `ambiguous_column` | 疑似两栏粘成一行，或行跨中缝 |
| `repeated_margin` / `page_number` | 重复页眉页脚、页码 |
| `missing_bbox` | atom 无坐标，作为 orphan 保留 |

## 方法概要

1. 空文本且几乎铺满页面的 atom 记为整页图候选，不丢。
2. 比正文矮的小字盒只有与宿主行 y 重叠且 x 落在宿主框内，才挂接为 `possible_subscript`。
3. 足够多的行横跨版心（≥ 页宽 55%）判为单栏；否则在页宽 35%–65% 内搜索中缝，两侧各至少 4 条不跨缝的行才判为双栏。不写死任何论文的坐标。
4. 通栏行切开阅读带；带内先左栏自上而下，再右栏。
5. 行距 ≤ 0.45×正文字高且左缘对齐才并段；标题、图注开头、句末后缩进、换页、表格数字不并；行首脚注符号不并（`footnote_marker_split`）。
6. 图内碎字、表格数字各自成候选，故意不按距离聚类。

## 验收范围（收窄后）

只覆盖以下证据，**不是**全文逐块边界通过：

- **源完整**：样例论文（Attention Is All You Need，arXiv 1706.03762，15 页，未随仓库提供）928/928 atom 不丢不重，orphan 0，无重复 id。测试里的合成文档同样逐 atom 检查。
- **点名样本**：样例论文第 1–3 页的短尾行、下标窗口、作者 / 邮箱分开、Abstract 成段、两条文献行分开、† / ‡ 脚注分开。
- **合成双栏**：两栏阅读顺序只在测试里的合成页上验证（标题 → 左栏整段 → 右栏整段；作者式短行不粘合）。样例论文本身全是单栏。
- 真实双栏论文最多只有覆盖层面（atom 不丢不重）的证据，没有完整边界核对。
- 候选数变少不是成功指标；块数多少也不说明好坏。

**计时**：曾做过一次单次 A/B，两臂工作量不等（截图数量、作者区规则不同），且都打满轮次上限。因此**不声称**候选能稳定提速（包括“约 20%”）。要下速度结论，需要两臂工作量一致并重复多次。

## 已知限制

- **上标数字脚注不识别**：如 `4To illustrate…`。NFKC 后上标变成普通数字，无法与列表编号、表格格子区分，仍可能与上一行并错，需看页图。
- 首行突出、续行缩回的脚注（如 `∗` 贡献说明）可能拆成两条：该并没并，方向安全。
- 下标/上标挂在行尾，不回到字的位置；y 不重叠或 x 出框的公式碎片（如 `t−1`、`= 0.1.`）单独成碎片。
- 居中的短尾行不并入上一段；行距处于灰区时宁可不并。
- 旋转的页边文字仍是 paragraph 候选。
- 同一行多个邮箱若在抽取时已是一个 atom，只标 `possible_multi_item`。
- 中缝窄于抽取阈值、两栏已粘成一行时只标记，不凭空切开。
- 跨页只标记，不合并。连字符保留，需在结构提交时决定。
