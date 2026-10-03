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

成功：`{"ok":true,"result":...}`；参数或提交错误 exit 1、`ok:false`。`validate` 不通过 exit 2，外层 ok 表示命令执行，内层 `result.ok` 表示完整性。`progress` 允许报告未完成状态，不返回失败退出码。`serve` 为长驻 HTTP 服务，不是 JSON 命令。CLI 默认打印紧凑 JSON（`separators=(',', ':')`，无缩进）；`--pretty` 恢复两格缩进，解析方式不变。成功的 `submit` 结果含 `document_id`、`revision`、`stage`、`pending_translate`、`pending_review`，不含整份文档。

`show`、`tasks`、`progress` 默认返回投影（`projection: true`），避免把整份文档灌进会话。这不改变磁盘上的 `document.json`，`schema_version` 仍是 1。显式 `--full` 时：

- `show --full` 与旧版一致：整份 document、顶层 fingerprint、每个块的 `translation_hash`。
- `tasks --full` 与旧版一致：限量全文块、整节上下文和全量 issues。
- `progress --full` 与旧版一致：document_id、revision、stage、fingerprint 和完整 validate 摘要（含全部 `errors`、`warnings` 与 `confirmed_limitations`）。不含默认投影的 `projection`、`outline_length`、`confirmed_limitations_summary`、`warning_summary`、`error_summary`、`pending_translate` 或 `pending_review`。

默认投影的公共字段：`document_id`、`id`、`revision`、`stage`、`title`、`fingerprint`、`projection`、`block_count`、`atom_count`、`source_file`、`schema_version`。

结构阶段的 `show` 另含 `outline`（heading 的 id + 至多约 160 码点文本）、`issue_summary` 和全部块的摘要。块摘要只有 `id`、`kind`、截断后的 `text`、`has_structure_note`、`source_count`，没有 translation、history 或 atoms。`issue_summary` 按 issue id 前缀（去掉末尾的 `-数字` 或 `-a/-b` 加数字；若 issue 自带 `type` 则用 type）统计 `unresolved` / `resolved`，并给每类最多 3 个样例 id，不含问题正文。结构阶段的 `tasks` 使用同一摘要：`blocks` 的待办数量由 `--limit` 限制（默认 16）。结构阶段的 `show` 仍列出全部块摘要。`context` 按待办块的邻接范围返回，`section_context` 按章节窗口返回（默认最多约 24 块，可用 `--section-limit` / `--section-offset` 分段读取），并保留 `terms`。

翻译、术语和复核阶段的 `show` 与 `tasks` 保持原任务窗口：`terms`、`outline`、`section_context`、相邻 `context` 和本批 `blocks`。默认投影的每个块只含 `id`、`kind`、`text`、`source_ids`、`asset`、`translation`、`translation_hash`、`review`、`user_edited`、`has_structure_note`，不含 `history`、长 `structure_note` 或 `source_change`；不附带整份 atoms。本批 `blocks` 仍是完整投影；`context` 与 `section_context` 里若某块 id 已出现在本批 `blocks`，该处只放引用壳 `{"id":...,"ref":true}`，不含 text / translation / translation_hash 等大字段。未进入本批的邻居或章节块仍是完整投影。`--full` 不去重，仍返回原始块。`issue_summary` 只统计未解决项。默认 `--limit` 为 16，只限制本批待办数量。默认投影的 `section_context` 有长度上限（默认 24 块），并附带 `section_window`（`section_total` / `section_offset` / `section_limit` / `truncated` 等）便于分段读取；CLI 可用 `--section-limit` / `--section-offset` 翻页。要整节、history 或全篇时用 `--full`，或加大 `--section-limit`。要核对全篇译文哈希时用 `show --full`。

`progress` 默认投影保留 validate 的 `ok`、`blocks`（仍是块数量，不是块列表）、`translated`、`reviewed`，并加上 `outline_length`、`unresolved_issues`、`pending_translate`、`pending_review`、`error_summary`、`warning_summary`、`confirmed_limitations_summary` 和上述公共字段。`errors` 只保留非重复的结构性/问题错误（例如未解决的提取问题、全文复核缺失、对齐或数字差异）。大量形如 `b00001: missing translation`、`b00001: second-pass review required` 以及其他 `missing …` 的逐块错误收进 `error_summary`：`total` 加 `by_kind`，每类有 `count`、最多 5 个 `sample_ids` 和对应 `sample_messages`。不返回块正文或 atoms。`ok` 仍只由完整 validate 的 errors 决定，不因折叠而变成 true。已知限制不在默认投影里逐条展开。`confirmed_limitations_summary` 为 `total` 加 `by_category`：每类有 `count`、最多 3 个 `sample_ids`，以及一条共用短 `summary`（该类 resolution 压缩空白后最长约 120 字，不附 `resolution_evidence`）。`warnings` 只保留非限制类原文（例如失锚笔记）再加每类一条短注（类别、条数、样例 id、同一条短 summary）。`warning_summary` 为 `total`（压缩前的 warning 条数）、`confirmed_limitations`（被折叠的逐条限制 warning 数）、`other`（其余 warning 条数）和 `by_category`（各类条数）。完整的 `confirmed_limitations`（每项 `id`、`category`、`resolution`、`resolution_evidence`）和逐条 warning 仍由 `validate`、阅读界面、离线 HTML 和 `progress --full` 返回。

## 组装提交信封

`assemble` 只给 Agent 写好的块列表包上**读取任务时**的 `revision` 和 `submission_id/agent/operation`。revision 和复核哈希都来自当时的 `tasks`/`show` 快照（`--task`），不取实时文档上的最新 revision，也不根据当前译文现算哈希。它不发明译文、不自动写复核意见、不跳过检查。`submit` 仍执行对齐、数字、复核说明和全文复核等原有门禁。

```sh
assemble DOC_ID translate --blocks blocks.json --submission-id ID --agent NAME --task tasks.json [--out payload.json]
assemble DOC_ID review --blocks blocks.json --submission-id ID --agent NAME --task tasks.json [--out payload.json]
```

`blocks.json` 只是块列表。翻译：`[{"id":"b00001","translation":{...}}]`。复核：`[{"id":"b00001","note":"第二轮核对原文含义和条件"}]`。`tasks.json` 是当时 `tasks` 或 `show` 的 JSON（CLI 的 `{"ok":true,"result":...}` 或其中的 `result` 均可）。信封的 `revision` 用这份快照里的 revision。复核项若已写 `translation_hash` 则原样保留；若省略或为空，则按 id 取快照中对应块的 `translation_hash`（查 `blocks`、`context`、`section_context`），不会用实时文档的当前译文补哈希。默认投影的引用壳没有 `translation_hash`；此时用同一快照 `blocks` 里同 id 的完整块上的哈希。仍禁止用实时文档现算。快照须带有该字段：默认 `tasks`/`show` 投影的 `blocks` 或 `show --full` 含 `translation_hash`；`tasks --full` 的原始块没有这个字段，不能用来补哈希。没有快照、快照里没有整数 revision，或复核块在快照中没有 `translation_hash` 时，`assemble` 报错并要求提供读取任务时的快照，不会改用最新文档。`note` 仍必须在 `submit` 时非空。用户若在读取之后改了译文，用旧快照组装会带上旧 revision 和旧哈希，`submit` 因版本冲突或哈希不一致拒绝，不会把未复核的新译文标成已复核。默认把 UTF-8 JSON 写到工作目录 `submissions/<document_id>-<operation>-<submission_id>.json`；`--out` 可指定路径。成功时外层仍是 `{"ok":true,"result":...}`，`result` 只有 `path`、`revision`、`operation`、`block_count`，不返回文档。

## pair-offsets

`pair-offsets DOC_ID --blocks groups.json [--out aligned.json]` 从文档 `blocks` 读取同 id 的原文，按调用方给出的语义组计算 Unicode 码点偏移并检查覆盖。程序不算语义，禁止在没有语义组时按词序、标点或长度自动切分。`whole: true` 生成整块一组 `g1`（唯一允许的整块自动填充）。`groups` 的 source/target 为非空字符串、非空字符串列表，或 `{"text":"..."}` 对象。每一侧各自记下尚未占用的区间；每一组的每个片段都在剩余区间里独立做精确匹配，不用跨组或组内的单调游标，因此允许调序和交错（例如原文 `A. B.` 对译文 `乙。甲。`，或一组对应 `A` 与 `C`、另一组对应中间的 `B`）。未写消歧时，取起点最早且不与已占用区间重叠的一次匹配。同一子串多次出现时，用 `occurrence`（从 1 计）指定候选里的第几次：从左到右精确查找，上次命中的起点之后一个码点再继续，重叠出现也计数。可选 `anchor` 必须包含该 `text`；只保留落在某个锚点跨度内的匹配，`occurrence` 计的是过滤后的这一列。锚点本身不占用，只占用片段跨度。定位之后仍做与 `check_translation` 相同的覆盖和重叠检查（非空白恰好一次）。输出是 `[{id, translation:{text, pairs}}]`，可直接作为 `assemble translate --blocks`。不带 `--out` 时 CLI 打印 `{"ok":true,"result": <块列表>}`。带 `--out` 时把同一块列表写入该文件（不是信封），stdout 的 `result` 只有 `path` 和 `block_count`，不再回传译文。

## 提交示例

所有提交都要求 `revision/submission_id/agent/operation`。成功 `submit` 后按返回的 `stage`、`pending_translate`、`pending_review` 推进下一批。重试沿用原 payload 与 `submission_id`。版本冲突时先重新读取并核对，不要在队列已进入下一阶段时用同一批空转重交。先保存 UTF-8 JSON（或用上面的 `assemble`），再执行 `python "<SKILL_DIR>/scripts/paper_reader.py" --workspace "<WORKSPACE>" submit ID payload.json`。

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
{"revision":3,"submission_id":"review-1","agent":"current agent","operation":"review","blocks":[{"id":"b00001","translation_hash":"读取任务时 tasks/show 快照里该块的哈希","note":"第二轮核对原文含义和条件","difference_explanation":"仅在存在具体差异时说明换算或原因"}]}
```
全文复核：`operation: full_review`，附 `fingerprint`（取 progress）、`note`。
来源问题：`operation: resolve`，至少提供 `issues` 或 `limitations` 之一（可同时给）。逐条仍为 `issues: [{"id":"page-3","resolution":"实际检查结果和处理方式"}]`；空或空白 resolution 拒绝。无法识读的正文保持未解决，不可仅写跳过。

按类已知限制用同一 operation 的 `limitations`（`operation: resolve_limitations` 是只使用 `limitations` 的别名）。每条含非空 `category`、`resolution`、`evidence`；后两项去空白后均不少于 24 字，不能只是 `ignore` / `skip` / `n/a` / `ok` / `done` / `resolved` / `limitation` / `已知限制` / `忽略` / `跳过` 等套话。`evidence` 还须含数字、issue id 样例，或具体核对词（`sample` / `checked` / `inspected` / `confirmed` / `source` / `file` / `missing` / `remote` / `not embedded` / `single-file` / `no compile` / `核对` / `确认` / `源` / `缺`）。按 issue id 前缀（去掉末尾的 `-数字` 或 `-a/-b` 加数字；若 issue 自带 `type` 则用 type）匹配**当前未解决**项，写入同一 `resolution`（并记录 `resolution_evidence` / `resolved_by=limitation_batch`）；已解决项和其他类不动。该类零匹配则失败。允许的 category 仅：`tex-includegraphics`、`tex-input`、`tex-env`、`tex-unclosed`、`image`、`docx-ole`、`docx-omath`、`docx-image`、`docx-crop`。`page`（扫描/低文本/编码）和 `docx-sym`（正文未知符号）禁止按类批处理，只能逐条 `issues` 或保持未解决。没有按文档清空全部 issues 的接口。
```json
{"revision":4,"submission_id":"resolve-lim-1","agent":"current agent","operation":"resolve","limitations":[{"category":"tex-includegraphics","resolution":"单文件 TeX 不编译，\\includegraphics 未嵌入，属产品已知边界。","evidence":"核对 sample tex-includegraphics-1、tex-includegraphics-2，源文件无编译步骤。"}]}
```

按类写入后，这些 issue 不再算未解决，不单独阻止 `validate` 通过或导出。它们仍是已确认的已知限制，不是已经修复：`validate` 为每条 `resolved_by=limitation_batch` 且 resolution 非空的 issue 增加 warning，并在 `confirmed_limitations` 列出 id、category、resolution、resolution_evidence。阅读界面和离线 HTML 会显示这些残留限制。空 resolution 仍然是 error。逐条 `issues` 更新 resolution 时会删除该 issue 上的 `resolution_evidence` 和 `resolved_by`。同一次提交里若先 `limitations` 再按 id 覆盖，被覆盖的 id 以逐条为准，不保留批处理字段。默认 `progress` 不重复这份完整列表，只给 `confirmed_limitations_summary` 和压缩后的 `warnings` / `warning_summary`；要逐条内容用 `validate` 或 `progress --full`。

用户 HTTP 编辑使用 `POST /api/documents/ID/edit`，携带会话令牌 `X-Reader-Token` 和 revision。operation 为 `note/delete_note/translation/term/reading`。令牌由同源 `/api/session` 返回，只保存在运行内存；无 CORS、默认仅回环地址，非本地主机名拒绝。此服务不是多用户权限系统，不应暴露公网。

补齐遗漏原图：`operation: attach_asset`，附 `block_id/asset/note`。仅允许给尚无 asset 的 caption、figure、formula 挂接已存在的 `region-<16位哈希>.png` 裁剪；不可覆盖现有图、不改变冻结正文和批注锚点，并清除全文复核，需重新验收。
