# 开发与维护

这是以离线HTML为默认成果的Skill源码包。根目录SKILL.md是用户任务入口，references是配套资料，scripts/reader是确定性运行程序，assets/reader是已构建界面，dev/web是界面源码。

只改本目录；旁边的旧agent-paper-reader应用及其数据不属于本次改造。不可复制个人文献、译稿或日志进发布包。测试用合成文献。

运行时必须显式提供PAPER_READER_WORKSPACE（使用scripts/paper_reader.py会自动设置），所有可写状态须位于安装目录外。不得自动配置全局Skill或Agent。原件、来源台账、用户修订和笔记不可绕过工作流覆盖。

界面修改在dev/web中运行npm ci与npm run build，再将dev/web/dist同步到assets/reader；缓存和依赖留在本目录，发布包排除它们。日常使用不需要Node。

测试：设置独立PAPER_READER_WORKSPACE及PYTHONPATH=scripts，运行pytest tests。打包：python dev/package_skill.py；按白名单收集运行资源，不带个人数据。不要自动发布GitHub或包注册表。
