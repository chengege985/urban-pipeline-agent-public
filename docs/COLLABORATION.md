# 同事协作操作说明

仓库：<https://github.com/catiseyeqaq/urban-pipeline-agent>，Private。不要共享仓库所有者账号、Token或SSH私钥。每人使用自己的GitHub账号，提交记录署名本人。

当前主要开发和完整测试在179 Linux服务器进行，Windows是代码接收与GitHub提交端。下列分支/PR操作不要求在Windows运行完整FastAPI；服务端改动先按[服务器同步流程](SERVER_WORKFLOW.md)验证和合入本地分支。使用服务器的同事需要各自授权和隔离工作目录，不能同时覆盖同一个工作树。

## 1. 所有者邀请同事

1. 打开仓库 → **Settings → Collaborators → Add people**。
2. 输入同事的GitHub用户名，确认邀请正确账号。
3. 同事接受邮件或GitHub通知中的邀请后即可访问。仅把链接发给未获邀的人，不能访问私有仓库。

本次没有替用户邀请未知账号或添加AI/机器人协作者。[GitHub官方邀请说明](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/repository-access-and-collaboration/inviting-collaborators-to-a-personal-repository)。

## 2. 同事首次下载

安装Git、Node.js和GitHub CLI；后端另按安装说明准备Python。HTTPS认证可使用Git Credential Manager，或先执行：

```powershell
gh auth login --hostname github.com --git-protocol https --web
git clone https://github.com/catiseyeqaq/urban-pipeline-agent.git
cd urban-pipeline-agent
git config user.name "同事自己的名字"
git config user.email "同事在GitHub验证过的邮箱或自己账户的noreply邮箱"
```

不要照抄仓库所有者的提交邮箱。GitHub网页账号密码不是Git HTTPS密码；按CLI浏览器授权完成登录，不把Token放进URL或文件。

## 3. 每次修改使用独立分支

以下示例修改地图标签；实际使用能概括本次工作的分支名：

```powershell
git switch main
git pull --ff-only origin main
git switch -c feature/map-labels

# 用IDE修改代码，然后按改动范围测试
cd web
npm ci
npm run typecheck
npm test
npm run build
cd ..

git status
git diff
git add web/src/实际修改的文件.tsx
git commit -m "fix: improve map labels"
git push -u origin feature/map-labels
```

`git add`示例路径必须替换成真实文件，只暂存已检查内容。不要提交环境目录、凭据、数据库或业务原件。GitHub上点 **Compare & pull request**，目标选 **main**，写清修改、测试和限制；也可运行：

```powershell
gh pr create --base main --head feature/map-labels --title "修复地图标签" --body "说明修改内容、验证结果及限制"
```

负责人审查后合并，同事再`git switch main`、`git pull --ff-only origin main`。不要直接覆盖main，不要force push，不要共享同一feature分支同时改相同文件。

## 4. 分支落后与冲突

```powershell
git fetch origin
git switch feature/map-labels
git merge origin/main
# 如有冲突，在IDE逐文件解决，保留两边需要的逻辑
git add 已解决的实际文件路径
git commit
git push
```

不确定如何解决时用`git merge --abort`退回，并请相关开发者共同核对。不要使用“全选本地/远程”覆盖业务契约。

## 5. UI候选协作

`ui-enterprise-01`保留动态管网和UI修复，尚未与main合并。如任务明确针对该候选：

```powershell
git fetch origin
git switch -c feature/ui-tooltip origin/ui-enterprise-01
# 修改、测试、提交后
git push -u origin feature/ui-tooltip
gh pr create --base ui-enterprise-01 --head feature/ui-tooltip
```

不要将整套候选目录直接复制到main。main中的报告接线与UI候选有不同门禁和共享文件，需单独受控集成。

## 6. 审查与权限

冻结Agent、评分及Report完整性变更需明确合同、失败回归和独立审查；合成测试不授权真实数据、真实模型或公网部署。CODEOWNERS列出这些路径的负责人。

分支保护/必需审批的实际启用状态以GitHub设置为准，不能仅凭本文或CODEOWNERS宣称已强制。若当前套餐支持，所有者可设置main要求Pull Request和至少一名审核者，并禁止force push。即使没有强制规则，团队仍按上述流程协作。
