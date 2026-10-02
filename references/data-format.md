# 公开 JSON 格式 v1

每份文档位于 `data/<sha256前24位>/`：不可变 `source.<ext>`（pdf/md/markdown/txt/html/htm/docx/tex）、`document.json`、原页 PNG 和区域 PNG。原始来源文件哈希存于 `source_sha256`；每次完整性检查重新计算。文档路径不含任意用户输入。`schema_version: 1`；本批新增格式只扩展 `location` 字段约定，不改 atoms/blocks 顶层形状，故保持 v1。后续不兼容变更须升级版本，禁止静默迁移。

- `atoms`：不可变的提取来源，`id/text/location`。PDF location 为 1 起算 `page` 和左上坐标系 `bbox: [x0,top,x1,bottom]`，单位 PDF point；Markdown / 纯文本 / HTML / TeX 为码点 `start/end` 和 1 起算 `line_start/line_end`（HTML/TeX 尽量映射回原文件；找不到片段时退化为顺序锚点）。DOCX location 为虚拟纯文本拼接视图上的码点 `start/end`，另含 1 起算 `paragraph_index`（按正文段落/表格顺序）。
- `blocks`：稳定 `id`、`kind`、整理后的 `text`、`source_ids`、`structure_note`、可选 `source_change`、`asset`。正文等文本块引用完整来源；图表保留原图，页眉用 `excluded` 明示原因。允许跨页来源。atom 精度为提取行/片段，定位可逐片段切换。Word.Picture.8 的 EMF 预览另存 `image-<block>.emf`、`image-<block>.conversion.json`（回放记录，不含本机绝对路径）；只有保真检查通过时 `asset` 才指向同名 PNG，并另存 SVG。
- kind：`paragraph/heading/caption/figure/table/formula/reference/code/page/excluded/unclassified`。
- `translation`：`text` 和 `pairs`。每个 pair 包含 `id/source/target`，后两项为 `[start,end]` 数组的数组，左闭右开、Unicode 码点偏移。一个语义组可跨多个句子或非连续片段。偏移不能重叠、越界或漏掉非空白字符。
- `history`：前译文和编辑者类别；`user_edited` 后 Agent 重译被拒绝。用户每次改一个语义组的一个目标片段，其他偏移确定性调整。
- `review`：`translation_hash/agent/note/difference_explanation`，绑定当前译文；`full_review` 绑定正文、术语、问题的 fingerprint。用户修订会使旧复核失效。
- `terms`：`id/en/zh/definition/user_edited`；`notes`：`id/block_id/side/start/end/quote/text/difficult`。UI/API 派生 `status=attached/orphaned`，锚点失效不删除笔记。
- `reading`：`block_id/font/hide`，持久化于磁盘。`revision` 每次成功写入递增。`submissions` 保存请求哈希和提交版本。

写入由文件锁包围版本检查和原子替换；写临时文件后 flush/fsync，再在同目录 `os.replace`。读取只会看到旧完整版本或新完整版本。不要运行编辑器直接覆盖 JSON。

## CLI 返回

成功：`{"ok":true,"result":...}`；参数或提交错误 exit 1、`ok:false`。`validate` 不通过 exit 2，外层 ok 表示命令执行，内层 `result.ok` 表示完整性。`progress` 允许报告未完成状态，不返回失败退出码。`serve` 为长驻 HTTP 服务，不是 JSON 命令。

`show`、`tasks`、`progress` 默认返回投影（`projection: true`），避免把整份文档灌进会话。这不改变磁盘上的 `document.json`，`schema_version` 仍是 1。显式 `--full` 时：

- `show --full` 与旧版一致：整份 document、顶层 fingerprint、每个块的 `translation_hash`。
- `tasks --full` 与旧版一致：限量全文块、整节上下文和全量 issues。
- `progress --full` 与旧版一致：document_id、revision、stage、fingerprint 和 validate 摘要。

默认投影的公共字段：`document_id`、`id`、`revision`、`stage`、`title`、`fingerprint`、`projection`、`block_count`、`atom_count`、`source_file`、`schema_version`。

结构阶段的 `show` 另含 `outline`（heading 的 id + 至多约 160 码点文本）、`issue_summary` 和全部块的摘要。块摘要只有 `id`、`kind`、截断后的 `text`、`has_structure_note`、`source_count`，没有 translation、history 或 atoms。`issue_summary` 按 issue id 前缀（去掉末尾的 `-数字` 或 `-a/-b` 加数字；若 issue 自带 `type` 则用 type）统计 `unresolved` / `resolved`，并给每类最多 3 个样例 id，不含问题正文。结构阶段的 `tasks` 使用同一摘要，但 `blocks` / `context` / `section_context` 仍受 `--limit` 限制，并保留 `terms`。

翻译、术语和复核阶段的 `show` 与 `tasks` 保持原任务窗口：`terms`、`outline`、`section_context`、相邻 `context` 和本批 `blocks`。这些块可以含翻译所需的完整字段，并带计算出来的 `translation_hash`；不附带整份 atoms。`issue_summary` 只统计未解决项。默认投影的 `section_context` 有长度上限（默认 24 块），并附带 `section_window`（`section_total` / `section_offset` / `section_limit` / `truncated` 等）便于分段读取；CLI 可用 `--section-limit` / `--section-offset` 翻页。要整节或全篇时用 `--full`，或加大 `--section-limit`。要核对全篇译文哈希时用 `show --full`。

`progress` 默认投影是 validate 摘要加上 `outline_length`、`unresolved_issues` 和上述公共字段，不返回块正文或 atoms。validate 原有的 `blocks` 仍是块数量，不是块列表。

## 提交示例

所有提交都要求 `revision/submission_id/agent/operation`。先保存 UTF-8 JSON，再执行 `python "<SKILL_DIR>/scripts/paper_reader.py" --workspace "<WORKSPACE>" submit ID payload.json`。

结构有两种提交，磁盘上的块形状不变。

全量（payload 含 `blocks` 时始终走这条，即使同时写了 `keep_extracted`）：blocks 覆盖全部 atoms，不能只交一个片段。
```json
{"revision":0,"submission_id":"structure-1","agent":"current agent","operation":"structure","note":"核对原文页面与跨页次序","blocks":[{"id":"b00001","kind":"paragraph","text":"A. B.","source_ids":["a00001"],"structure_note":"源行顺序核对无误"}]}
```
增量（不要带 `blocks`）：`keep_extracted: true`，或 `mode` 为 `keep` / `patch`。以当前提取块为起点。`updates` 按 id 修改可选字段 kind、text、source_ids、structure_note、asset、source_change。给出 `asset` 时必须是文档目录中已存在的非空文件名，不能用 `null` 或空字符串清空。每次改 `text` 或 `source_ids` 且结果与 atoms 不一致时，必须在**本次** update 里提供对应的 `source_change`，不能沿用旧理由。`merges` 把 `from` 各块的 source_ids 按顺序并入 `into`，删除 `from` 块，并且必须给 structure_note；可选 text、kind、source_change、asset。若参与合并的块带有多张不同图片，必须显式给出合并后的 `asset`：非空字符串，且是文档目录里已有的文件名（不含 `/` 或 `\`）。只写上 `asset` 键，或填 `null`、空字符串、`false`，都不算显式资源，会被拒绝。禁止静默丢图。未提供 `asset` 且只有一张图时会保留该资源。`default_structure_note` 只填仍为空的说明，显式 updates/merges 的说明优先。提取已经正确时，可以只交这一条默认说明。最终每块仍要有非空 structure_note，atoms 仍须精确分区一次；改变源文字仍要 `source_change`。已有翻译或笔记后结构冻结。
```json
{"revision":0,"submission_id":"structure-keep-1","agent":"current agent","operation":"structure","note":"提取已核对","keep_extracted":true,"default_structure_note":"提取块与原文一致，予以保留","updates":[{"id":"b00002","kind":"heading","structure_note":"此行是标题"}],"merges":[{"into":"b00003","from":["b00004"],"text":"合并后的原文","structure_note":"跨页断行合并"}]}
```
术语（空数组也允许，表示明确无专业词）：
```json
{"revision":1,"submission_id":"terms-1","agent":"current agent","operation":"terms","terms":[{"id":"t1","en":"PUE","zh":"电能利用效率","definition":"数据中心总能耗与 IT 设备能耗之比。"}]}
```
语义翻译（一对多/多对一由 spans 表达，示例两句合为一句）：
```json
{"revision":2,"submission_id":"translation-1","agent":"current agent","operation":"translate","blocks":[{"id":"b00001","translation":{"text":"甲和乙。","pairs":[{"id":"g1","source":[[0,2],[3,5]],"target":[[0,4]]}]}}]}
```
复核：
```json
{"revision":3,"submission_id":"review-1","agent":"current agent","operation":"review","blocks":[{"id":"b00001","translation_hash":"show --full 或当前批次投影返回的哈希","note":"第二轮核对原文含义和条件","difference_explanation":"仅在存在具体差异时说明换算或原因"}]}
```
全文复核：`operation: full_review`，附 `fingerprint`（取 progress）、`note`。
来源问题：`operation: resolve`，附 `issues: [{"id":"page-3","resolution":"实际检查结果和处理方式"}]`；无法识读则保持未解决，不可仅写跳过。

用户 HTTP 编辑使用 `POST /api/documents/ID/edit`，携带会话令牌 `X-Reader-Token` 和 revision。operation 为 `note/delete_note/translation/term/reading`。令牌由同源 `/api/session` 返回，只保存在运行内存；无 CORS、默认仅回环地址，非本地主机名拒绝。此服务不是多用户权限系统，不应暴露公网。

补齐遗漏原图：`operation: attach_asset`，附 `block_id/asset/note`。仅允许给尚无 asset 的 caption、figure、formula 挂接已存在的 `region-<16位哈希>.png` 裁剪；不可覆盖现有图、不改变冻结正文和批注锚点，并清除全文复核，需重新验收。
