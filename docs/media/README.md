# 实机演示素材

这些素材来自浏览器中运行的 Agent 文献译读，不是设计稿或模拟界面。使用两篇已由 Agent 处理的真实论文；录制针对已存在的译稿，不演示实时翻译速度。

- `2025_Wang_MPIML`：Toward Multiphysics-Informed Machine Learning for Sustainable Data Center Operations。用于离线阅读、句子联动、上下文术语和原页查看。
- `2026_Lin_HarnessProvisioning`：Task-Aware Harness Provisioning for LLM Agents in Mission-Critical Infrastructure Operations。用于本地文献库、已有复核笔记和译文修订界面。

论文著作权归各自权利人。此处仅展示有限界面片段；仓库不分发这两篇论文的完整原件、译文或工作数据。项目 MIT 许可不覆盖论文内容。

## 文件

| 文件 | 内容 |
| --- | --- |
| bilingual-reader.png | MPIML 离线阅读界面，1440 × 960 |
| sentence-linking.gif | 悬停联动、点击选中与取消，9 秒，1080 × 720 |
| reader-demo.mp4 | 两篇论文的实际操作，42 秒，1280 × 854，无音轨 |
| context-glossary.png | 本段工具中的 MPIML 术语解释 |
| source-location.png | 原始 PDF 页面查看 |
| harness-reader.png | HarnessProvisioning 对照阅读与术语 |
| review-notes.png | 切换笔记标签，查看已有复核记录 |
| translation-editor.png | 打开译文修订界面，没有保存新修改 |

## 录制与处理

在独立的临时工作目录中使用已有文献的副本，运行 Skill 随包服务；旧应用及原始阅读数据保持不变。离线 HTML 使用临时静态服务器打开，阅读逻辑使用内嵌数据，不连接阅读器 API。此次未通过工具直接导航 file://。

浏览器视口为 1440 × 960。截图直接来自实际页面；视频由浏览器录屏裁去开头等待和末尾停顿，保持正常速度，再缩小、压缩为 H.264 MP4。GIF 为同一录屏中的 9 秒片段。未用生成式图片替代界面，未添加虚构操作结果。

录屏仅展示浏览器内容，不包含桌面、用户文件路径或其他窗口。笔记画面使用已有的论文内容复核说明，未展示个人读书笔记。

## README 展示方式

README 内嵌 PNG 和 GIF，以相对路径链接 MP4。视频在 GitHub 上的内嵌播放取决于展示方式，下载后可播放；没有依赖尚不存在的 GitHub 上传附件地址。素材不进入精简 Skill ZIP，以免增大安装包。
