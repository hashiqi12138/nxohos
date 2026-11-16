# 内层仓库改成 submodule：方案与步骤

> 目标：内层 `nx2ohos/nxnative` 保持完整 git 历史，能随时 `git merge upstream/main` 跟上上游。
> 现状（2026-11-16 实测）：内层是独立仓库（1753 提交，完整历史），只有 `upstream` 远程，无 `origin`；
> 外层仓库直接追踪内层的 3951 个文件（普通目录，非 submodule）。

---

## 一、为什么必须先建远程仓库

submodule 在 `.gitmodules` 里存的是**一个 URL**，外层仓库只记录内层的 commit SHA。
克隆外层仓库时，git 会用那个 URL 去拉内层——**URL 不通，整个目录就是空的**。

所以顺序是：

1. 建远程仓库（GitHub 上，空仓库，不初始化 README）
2. 内层 push 上去（带完整历史）
3. 外层才把它改成 submodule

**不能跳过第 1、2 步。** 没有远程 URL 的 submodule 只是一个指向空目录的指针。

---

## 二、步骤

### 第 1 步：在 GitHub 建空仓库

名字建议 `nxnative-ohos`（区别于上游的 `nxnative`）。

- **不要**勾选 "Add a README file"
- **不要**加 .gitignore 或 license
- 可以是 private（如果代码不打算公开）

### 第 2 步：内层仓库 push 完整历史

```bash
cd F:\新建文件夹\nx2ohos\nxnative

# 加 origin（URL 换成你建的那个）
git remote add origin https://github.com/hashiqi12138/nxnative-ohos.git

# 推 main，带完整历史
git push -u origin main

# 上游的 tag 也推上去（如果有）
git push origin --tags
```

**验证**：`git ls-remote origin` 应该能看到 `refs/heads/main`。

### 第 3 步：外层仓库把内层改成 submodule

```bash
cd F:\新建文件夹

# 从索引里移除内层的 3951 个文件（不动磁盘上的文件）
git rm -r --cached nx2ohos/nxnative

# 提交这个移除
git commit -m "chore: 内层仓库从普通目录改为 submodule"

# 加 submodule（git 会把它克隆到 nx2ohos/nxnative）
git submodule add https://github.com/hashiqi12138/nxnative-ohos.git nx2ohos/nxnative

# 提交 .gitmodules 和 gitlink
git commit -m "chore: nx2ohos/nxnative 改为 submodule，指向 nxnative-ohos"
```

**注意**：`git submodule add` 会尝试 clone 到 `nx2ohos/nxnative`，但那个目录已经存在。
如果报错 `already exists and is not a valid git repo`，先临时移开：

```bash
# 备份内层（含 .git，44 MB 左右）
mv nx2ohos/nxnative F:\新建文件夹\nxnative-backup

git submodule add https://github.com/hashiqi12138/nxnative-ohos.git nx2ohos/nxnative

# 确认新 clone 的 HEAD 和备份一致
cd nx2ohos/nxnative && git log --oneline -1
cd F:\新建文件夹\nxnative-backup && git log --oneline -1

# 一致后删备份
rm -rf F:\新建文件夹\nxnative-backup
```

### 第 4 步：给 submodule 配 upstream

新的 submodule 只有 `origin`，要能跟上上游还得加 `upstream`：

```bash
cd F:\新建文件夹\nx2ohos\nxnative
git remote add upstream https://github.com/seedds/nxnative.git
```

### 第 5 步：以后同步上游

```bash
cd F:\新建文件夹\nx2ohos\nxnative
git fetch upstream
git merge upstream/main          # 用 merge，不要 rebase（rebase 会重写 64 个提交的 SHA）
git push origin main             # 推到自己的远程
```

然后外层要更新 gitlink：

```bash
cd F:\新建文件夹
git add nx2ohos/nxnative
git commit -m "chore: 内层跟到上游 <sha>"
git push
```

**这一步容易忘**：内层 push 了，外层的 gitlink 还指着旧 SHA。别人 clone 外层拿到的是旧的。

---

## 三、日常命令对照

| 想做的事 | 命令 |
|---|---|
| 外层 clone 后初始化内层 | `git submodule update --init --recursive` |
| 内层跟上游 | `cd nx2ohos/nxnative && git fetch upstream && git merge upstream/main` |
| 内层推到自己的远程 | `git push origin main` |
| 外层记录内层的新 SHA | `git add nx2ohos/nxnative && git commit` |
| 看内层当前指向 | `git submodule status` |
| 内层拉自己的远程（另一台机器改了） | `git submodule update --remote` |

---

## 四、代价

| 项 | 影响 |
|---|---|
| 磁盘 | 内层会被**再 clone 一份**（约 500 MB），旧的目录要删 |
| 协作 | 别人 clone 外层后必须跑 `git submodule update --init`，否则 `nx2ohos/nxnative` 是空目录 |
| CI | 外层 CI 要加 `submodules: recursive`（`.github/workflows/*.yml` 里的 checkout 步骤） |
| 忘记更新 gitlink | 内层改了、外层没 commit，别人拿到旧版本 |
| GitHub 私库 | 如果 `nxnative-ohos` 是 private，clone 外层的人要有那个库的权限 |

---

## 五、一个必须知道的风险

**上游 `seedds/nxnative` 是公开仓库。** 如果 `nxnative-ohos` 也是 public，等于把 OHOS 移植代码公开——包括 `dm/ohos/patches/` 里对上游文件的修改。

如果不想公开，`nxnative-ohos` 设成 **private**，但那样外层仓库也必须是 private（否则别人 clone 外层拿不到内层）。

---

## 六、网络前提

本文写于 2026-11-16 21:12，当时 GitHub **完全连不上**：

```
curl --proxy http://127.0.0.1:12334 https://github.com/... → 000
curl 直连                                                  → 000
git ls-remote origin → SSL_ERROR_SYSCALL in connection to github.com:443
```

代理端口 12334 在监听，但 `ProxyEnable=0`（系统代理关闭），且节点本身似乎没通。

**第 1、2 步需要 GitHub 可达。** 网络恢复后先验证：

```bash
git ls-remote https://github.com/seedds/nxnative.git
```

有输出才能往下走。
