## 完整流程

1. `python <SKILL_DIR>/scripts/paper_reader.py --workspace <WORKSPACE> import /绝对路径/文献.pdf`（也支持 UTF-8 Markdown、`.txt`、本地 `.html`/`.htm`、`.docx`、单文件 `.tex`）。结果为 JSON，记住 `document_id`。原件复制后按 SHA-256 校验；同一内容重复导入返回同一文档。CLI 导入 Markdown/HTML 可复制同目录内的本地图像（从不抓取远程 URL）；网页上传不含其相邻文件，缺图会阻止完成。HTML 是本地存档解析，不是站点爬取。TeX 仅处理当前单文件，不展开 `\input`/`\include`，也不编译；`\includegraphics` 记为未嵌入问题。DOCX 提取段落/标题/表格，复杂版式（文本框、页眉页脚、嵌入对象）支持有限。Word.Picture.8 的 VML EMF 预览在本机 PATH 有 rsvg-convert 或 inkscape，且 fontconfig 能提供 Times New Roman 替代和 OpenSymbol 时，经 GDI 回放转为 PNG/SVG；保真检查通过才作为图块显示并写下处理结果，否则保留 EMF 且问题保持未解决。`doctor` 用 `emf_preview` 报告这项，不因此判定 PDF 环境失败。
2. `python <SKILL_DIR>/scripts/paper_reader.py --workspace <WORKSPACE> show ID` 阅读**投影**，不要直接打开 `document.json`。结构阶段默认投影含 document_id、revision、stage、title、fingerprint、块数、atom 数、标题大纲（id + 文本摘要）、`issue_summary`（按 issue id 前缀或 type 的未解决/已解决计数和少量样例 id），以及每块摘要：id、kind、约 160 个码点的 text、是否已有 structure_note、source_ids 数量。不含 translation、history 或整份 atoms，也不含问题正文。`tasks ID` 在结构阶段同样只给一批块摘要和问题汇总。`progress ID` 在校验摘要上附加 stage、大纲长度、未解决 issue 数和 `projection: true`，不返回全量文档。只有必须核对 atoms、全文字符或全部译文哈希时才加 `--full`（`show --full` 与旧版一样返回整份文档、fingerprint 和 translation_hash）。结构调整前仍要查看原文页图，正文阅读顺序由你确认，不能盲信几何提取。合并断行/跨页、整理单双栏、标记标题、图注、公式、图表、参考文献与重复页眉。每个 atom 必须且只能被一个块引用；不要删除来源。改变源文字须给与本次改动对应的 `source_change` 解释（增量再次改文时不能沿用旧理由）。合并带图块时若会丢图须显式给出合并后的 `asset`。复杂公式/图表用 `crop ID --page N --bbox x0 top x1 bottom` 保存区域，再在结构块关联返回的 asset。整页备份不能取代正文核对。
3. 通过 `submit ID payload.json` 提交 `structure`。两条路径兼容：payload 含完整 `blocks` 且覆盖全部 atoms 时，行为与以前一致；不传 `blocks` 而设 `keep_extracted: true`（或 `mode` 为 `keep` / `patch`）时，以当前提取块为起点，接受 `updates`（按 id 改 kind/text/source_ids/structure_note/asset/source_change；改 text/source_ids 须带本次 source_change）、`merges`（`into` + `from`，并入 source_ids 并删除 from 块，必须写 structure_note；多图合并须显式 asset）和可选 `default_structure_note`（给仍无说明的块填同一理由）。显式 updates/merges 的 structure_note 优先于默认说明。最终仍要求每块有非空 structure_note、atoms 精确分区一次、kind 合法。翻译或笔记产生后结构冻结。处理不可靠来源，用 `resolve` 说明解决方法或残留读限；扫描页没有 OCR，不能凭空补全文。无法看清的正文须保留未解决状态，不能仅写“忽略”完成任务，也不能清空 issues 或提交空 resolution。
4. 全文结构读完后提交 `terms`：专业术语英文、中文及解释。没有专业词也须显式提交空列表。概念统一后，`tasks ID --limit 8` 获取待办、目录、术语、相邻上下文与完整所属章节。翻译/复核阶段这些块可以含正文；不附带整份 atoms，issues 只给未解决摘要。需要旧的全量 tasks 时用 `tasks ID --full`。章节过长时默认 `section_context` 最多约 24 块并返回 `section_window`；用 `--section-offset` / `--section-limit` 分段读取，不要一次吞整章。
5. 由你翻译并提供语义对齐，允许一对多、多对一及非连续片段。`pairs` 的字符偏移以 Unicode 码点计算（Python `len`），不是 JS UTF-16。每侧所有非空白字符必须准确覆盖一次。程序只能检查覆盖，不能证明语义正确；严禁用自动按序配对取代语义判断。忠实保留数字、单位、引用、公式、否定和限定条件；作者疑点分开说明。每批 `translate` 提交就是检查点。
6. 当前批次的译文哈希在默认 `show` / `tasks` 投影的块上（`translation_hash`）。要一次取全部块的哈希时用 `show ID --full`。第二轮从原文对照全部译文和术语，提交 `review`。`validate` 会检查数字、单位、引用和公式差异；合理换算须在对应块 `difference_explanation` 逐项解释，不能使用通用“差异可接受”掩盖错误。
7. `progress` 取当前全文 fingerprint（默认投影即含 fingerprint，不必拉取全量文档）。完成第二轮后 `full_review` 提交该指纹和复核说明。最后 `validate` 必须成功；完成状态仅代表流程与已记录检查通过，不等于原文内容真实或用户验收通过。
8. 默认 `export ID` 输出工作目录内单文件 HTML 并交付文件路径，不启动服务。仅在用户需要文献管理、修订或笔记时执行 `serve`。用户在网页修订、批注；你不得直接重写 `document.json` 或覆盖笔记。

## 中断、冲突与安全

- 换会话：重新读取此入口及 `tasks/progress`，只处理未完成项。
- 每次提交携带读取时的 `revision` 和唯一 `submission_id`。同 ID、同内容重试幂等；同 ID、不同内容拒绝。冲突时重新读取并核对差异，不能盲目改 revision 强行覆盖。
- 用户修订译文受保护，新的 Agent 翻译提交会拒绝该块；可以复核或给用户修改建议。术语用户修订同样受保护。
- 提交失败不会部分保存；禁止绕过 CLI 用编辑器改数据。笔记与阅读位置会增加文档版本，提交前应重新读最新状态。
- 全文 review 必须独立于 translate 的第二轮检查。不能批量写“已读”充当实际阅读。

字段、完整提交示例见 [数据格式](data-format.md)。

## 非论文文章

报告、技术文档与普通文章使用相同流程。保留原有标题、署名、日期、列表与引用，不补造摘要、方法或参考文献等论文结构。按实际语境解释关键表达；没有专业术语时提交空术语表。网页链接本身不是输入格式，需要提供本地文件（PDF / Markdown / txt / HTML 存档 / docx / 单文件 tex）。不抓取 URL，不做 OCR，不支持 EPUB。
