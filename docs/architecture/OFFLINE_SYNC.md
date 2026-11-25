# GitHub—Windows—179离线共同历史

原stage_server_changes.py只支持逐文件179→Windows暂存，仍保留。它不是双向Bundle工具。旧179前端历史与GitHub不同，本轮先用包含main/ui分支的GitHub Bundle创建新的179隔离仓库，328个文件SHA一致；不覆盖旧树、dirty文件或ui候选。

本轮之后可在共同Git历史的隔离仓库使用scripts/offline_git_bundle.py：
1. Windows fetch GitHub，审阅已合并提交和共同祖先；通过Bundle或显式文件回灌新的179隔离分支。
2. 179开发测试并commit，禁止直接部署；对确定共同祖先导出：
   `python scripts/offline_git_bundle.py export --repo . --base <完整祖先SHA> --branch refactor/module-boundaries-v1 --bundle <新路径>.bundle`。
3. 人工/SSH传输bundle和旁边的.bundle.json，不传凭据、运行数据库或未提交文件。
4. Windows清洁仓库接收：
   `python <已审阅脚本> receive --repo <repo> --branch refactor/module-boundaries-v1 --bundle <bundle> --manifest <bundle.json>`。
5. 工具校验hash、唯一分支、完整SHA、祖先与Diff；只导入新review分支，不切换当前分支、不merge、不覆盖文件。核对后显式checkout、push该分支并创建PR。
6. 目标分支已存在、dirty、Hash不符、祖先不符、main作为接收目标均拒绝；不要加force绕过。并发更新由后续受控合并处理。

manifest是传输一致性凭据，不是签名或权限证明，必须来自本轮已授权SSH渠道。传输工具不管理身份/Token，不联网、不自动push或发布。不拼接旧服务器历史。

本轮PR目标main，禁止直接合并。共享文件修改由A安排独立集成PR，B参与合同审阅。
