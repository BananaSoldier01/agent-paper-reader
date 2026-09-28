# 公开 JSON 格式 v1

每份文档位于 `data/<sha256前24位>/`：不可变 `source.pdf/md`、`document.json`、原页 PNG 和区域 PNG。原始来源文件哈希存于 `source_sha256`；每次完整性检查重新计算。文档路径不含任意用户输入。`schema_version: 1`；后续不兼容变更须升级版本，禁止静默迁移。

- `atoms`：不可变的提取来源，`id/text/location`。PDF location 为 1 起算 `page` 和左上坐标系 `bbox: [x0,top,x1,bottom]`，单位 PDF point；Markdown 为码点 `start/end` 和 1 起算 `line_start/line_end`。
- `blocks`：稳定 `id`、`kind`、整理后的 `text`、`source_ids`、`structure_note`、可选 `source_change`、`asset`。正文等文本块引用完整来源；图表保留原图，页眉用 `excluded` 明示原因。允许跨页来源。atom 精度为提取行/片段，定位可逐片段切换。
- kind：`paragraph/heading/caption/figure/table/formula/reference/code/page/excluded/unclassified`。
- `translation`：`text` 和 `pairs`。每个 pair 包含 `id/source/target`，后两项为 `[start,end]` 数组的数组，左闭右开、Unicode 码点偏移。一个语义组可跨多个句子或非连续片段。偏移不能重叠、越界或漏掉非空白字符。
- `history`：前译文和编辑者类别；`user_edited` 后 Agent 重译被拒绝。用户每次改一个语义组的一个目标片段，其他偏移确定性调整。
- `review`：`translation_hash/agent/note/difference_explanation`，绑定当前译文；`full_review` 绑定正文、术语、问题的 fingerprint。用户修订会使旧复核失效。
- `terms`：`id/en/zh/definition/user_edited`；`notes`：`id/block_id/side/start/end/quote/text/difficult`。UI/API 派生 `status=attached/orphaned`，锚点失效不删除笔记。
- `reading`：`block_id/font/hide`，持久化于磁盘。`revision` 每次成功写入递增。`submissions` 保存请求哈希和提交版本。

写入由文件锁包围版本检查和原子替换；写临时文件后 flush/fsync，再在同目录 `os.replace`。读取只会看到旧完整版本或新完整版本。不要运行编辑器直接覆盖 JSON。

## CLI 返回

成功：`{"ok":true,"result":...}`；参数或提交错误 exit 1、`ok:false`。`validate` 不通过 exit 2，外层 ok 表示命令执行，内层 `result.ok` 表示完整性。`progress` 允许报告未完成状态，不返回失败退出码。`serve` 为长驻 HTTP 服务，不是 JSON 命令。

## 提交示例

所有提交都要求 `revision/submission_id/agent/operation`。先保存 UTF-8 JSON，再执行 `python "<SKILL_DIR>/scripts/paper_reader.py" --workspace "<WORKSPACE>" submit ID payload.json`。

结构（blocks 为全量，不能只交一个片段）：
```json
{"revision":0,"submission_id":"structure-1","agent":"current agent","operation":"structure","note":"核对原文页面与跨页次序","blocks":[{"id":"b00001","kind":"paragraph","text":"A. B.","source_ids":["a00001"],"structure_note":"源行顺序核对无误"}]}
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
{"revision":3,"submission_id":"review-1","agent":"current agent","operation":"review","blocks":[{"id":"b00001","translation_hash":"show命令返回的哈希","note":"第二轮核对原文含义和条件","difference_explanation":"仅在存在具体差异时说明换算或原因"}]}
```
全文复核：`operation: full_review`，附 `fingerprint`（取 progress）、`note`。
来源问题：`operation: resolve`，附 `issues: [{"id":"page-3","resolution":"实际检查结果和处理方式"}]`；无法识读则保持未解决，不可仅写跳过。

用户 HTTP 编辑使用 `POST /api/documents/ID/edit`，携带会话令牌 `X-Reader-Token` 和 revision。operation 为 `note/delete_note/translation/term/reading`。令牌由同源 `/api/session` 返回，只保存在运行内存；无 CORS、默认仅回环地址，非本地主机名拒绝。此服务不是多用户权限系统，不应暴露公网。

补齐遗漏原图：`operation: attach_asset`，附 `block_id/asset/note`。仅允许给尚无 asset 的 caption、figure、formula 挂接已存在的 `region-<16位哈希>.png` 裁剪；不可覆盖现有图、不改变冻结正文和批注锚点，并清除全文复核，需重新验收。
