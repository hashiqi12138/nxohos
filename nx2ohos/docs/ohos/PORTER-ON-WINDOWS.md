# Windows 上跑 porter 的四处阻塞（2026-11-16 实测）

porter（`python -m porter ...`）是 macOS/Linux 上开发的：它把上游 `nxnative/scripts/build_ipa_from_nsp.py`
当库导入，那条链路假设 POSIX。在 Windows 上从零跑一次 Celeste 的 `inspect`，依次撞上四个问题，逐个修了。

## 1. `ModuleNotFoundError: fcntl`

`build_ipa_from_nsp.py` → `tree_tar.py` 导入 `fcntl`（advisory flock），Windows 没有。

修法：`.portshim/fcntl.py`（用 `msvcrt.locking` 实现），跑 porter 时把它放到 `PYTHONPATH`：

```powershell
$env:PYTHONPATH = 'F:\新建文件夹\.portshim'
```

## 2. `AttributeError: module 'signal' has no attribute 'SIGHUP'`

同一个脚本读 `signal.SIGHUP`（POSIX 专有）。

修法：`.portshim/sitecustomize.py`——**不能**放 `signal.py` 到 `PYTHONPATH`（那会整个遮蔽标准库，
脚本还要用真的 `signal.signal()`）；`sitecustomize` 在解释器启动时执行，就地给真模块补属性。

## 3. `TypeError: 'tuple' object is not callable`

`porter/pipeline.py` 调 `nsp.scan_nsps(Path(folder), dumps.EXTS, keys)`——但上游签名是
`scan_nsps(nsp_dir, contents_of, want=None)`，第二个参数要的是**能读 content meta 的函数**，
不是扩展名元组。真正的 reader 由 `nsp.package_reader(repo, keys, env)` 上下文管理器提供
（它内部跑 `nx_extract --contents`，需要 prod.keys 解密 NCA）。

这是 **porter 自身的 bug**（上游改了签名，porter 没跟），`inspect` 和 `Port.inputs` 两处都有。
修法：加 `scan_packages(folder, keys, env)` 辅助函数，用 `package_reader(NXNATIVE, ...)` 包住，
两处都改用它。注意 `repo` 要传 **`NXNATIVE`**（`nx2ohos/nxnative`），不是 `paths.REPO`（`nx2ohos`）——
`NX_EXTRACT` 常量是相对 nxnative 的。

## 4. `nx_extract does not start` / `不是有效的 Win32 应用程序`

仓库里提交的 `dm/tools/nx_extract_bin/nx_extract` 是 **macOS universal binary**（magic `CA FE BA BE`），
Windows 跑不了。

修法：从源码给 Windows 编一个，放在同一目录旁边：

```powershell
cd nxnative\dm\tools\nx_extract
F:\新建文件夹\nx2ohos\tools\dotnet\dotnet.exe publish -c Release -r win-x64 `
    --self-contained true -p:PublishSingleFile=true -o out_win
copy out_win\nx_extract.exe ..\nx_extract_bin\nx_extract.exe
```

它自带 `--source-sha`，值与 `nx_extract_source_sha()` 的期望一致（源码没改时 `eb660696a669ac76`），
所以脚本的“必须由仓库内源码编译”校验照常通过。

`find_nx_extract()` 也改了：Windows 上优先用 `nx_extract.exe`。第一版用
`not (nx.exists() and os.access(nx, os.X_OK))` 判断，**永远不成立**——Windows 的 `os.access(..., X_OK)`
对任何存在的文件都返回 True——所以直接判断 `.exe` 是否存在。

## 顺带恢复的上游文件

`nxnative/scripts/` 在 fork 裁剪时被删得只剩 `build_ipa_from_nsp.py`，而 porter 需要
`pack_dump.py` / `pack_nup.py`（以及 `bench.py`、`bug_report.py` 等配套工具）。已按“保留上游版”的
规则从 `upstream/main` 全部恢复（8 个文件）。

## 还有一条环境要求

`doctor` 报 `C:\ 只剩 5 GB`。打包要占游戏文件的两倍空间，Celeste 本体 1.1 GB + update 443 MB，
F 盘足够，但 porter 的临时目录默认在系统盘，大文件游戏要确认 `TEMP` 所在盘有空间。

---

# 移植跑通后又撞上的五个问题（2026-11-16，Celeste 全流程实测）

`porter inspect` 修通之后 `porter port` 还有五处会在 Windows 上失败或静默产出错误结果。

## 5. `proc_group.stop()` 死循环

`porter inspect` 卡十分钟的真凶。栈追踪：

```
proc_group.py:71 in __exit__   -> self.stop(...)
proc_group.py:107 in stop      -> while True:
```

`stop()` 的重试循环靠 `_gone()` 判断"组已结束"，而 Windows 上 `members()` 恒返回 `[]`，
所以"组已结束"永远不是退出理由；子进程若已被 `finish()` 回收，`p.wait()` 立刻返回，
循环就无限空转。修法：`stop()` 加 Windows 分支，只 `wait(timeout=grace)`，超时则 SIGKILL 再等。

## 6. `lines()` 返回 bytes 而不是 str

`run()` 把每一行写进文本日志（`f.write(line)`）。上游 POSIX 分支用
`IncrementalNewlineDecoder` 解码，我的 Windows 分支直接 `for line in out` 拿到的是 **bytes**，
`f.write` 抛 TypeError，进入异常路径后撞上第 5 条的死循环。

修法：Windows 分支也走 `IncrementalNewlineDecoder`，保持"text=True 语义"的契约。

## 7. `romfs._resolve()` 硬编码 `/`

`_resolve` 用 `p.split('/')` 找 `.img` 组件。Windows 路径是 `F:\NS\...\RomFS.img`，
按 `/` 切只有一段且不以 `.img` 结尾，于是**镜像里的一切都解析不出来**，
`romfs.os.walk()` 退回真 `os.walk()`，把 1.1 GB 的镜像当成空目录。

后果很隐蔽：`shader_split.py` 从 1214 个文件里只找到 `main` 的 2 个着色器，而 profile 期望 26 个。

修法：先把 `os.sep` 统一成 `/` 再切；`.img` 之后的部分是**镜像内部路径，必须用 `/`**
（`Image.find()` 按镜像里存的原始名字查，`Content\Dialog` 不是任何镜像里的名字）。

## 8. `walk()` / `join()` / `relpath()` 产出 Windows 分隔符

修完第 7 条后路径能解析了，但 `shader_split.py` 用
`os.path.relpath(os.path.join(dp, f), romfs)` 拼 source 列，得到
`/Content\Effects\Border.xnb`——profile 的表是在 Mac 上拆的，写的是
`/Content/Effects/Border.xnb`。结果 24 个着色器的**内容哈希完全一致**，
却因为名字不同从 `ps_content.bin` 掉进 `ps_other.bin`。

修法：`romfs.os.path` 的 `join` 和 `relpath` 在路径落在镜像内时用 `/`，
镜像外的路径保持平台原生形式。

## 9. `os.pread` 不存在

`romfs._File.readinto` 用 `os.pread(fd, size, offset)`。Windows 没有 pread。
第一版回退用了 `lseek` + `os.read`，**读到 2446/9836 字节就停**（Windows 对
`os.open` 拿到的 fd 会短读），导致 4 个 `.xnb` 看起来是截断的：
`shader 0 claims 0xd58 bytes past the end of the file`。

修法：回退改用 `io.open(self.path, 'rb')` + `seek` + `read`（注意这个模块自己的
`open()` 遮蔽了内置的，必须写 `io.open`），并在 `_File.__init__` 里保存 `self.path`。

## 10. `nsp.build_nx_extract` 在上游不存在

`porter/pipeline.py` 调 `nsp.build_nx_extract(...)`，但上游脚本里只有
`find_nx_extract`。这个函数从来没存在过，所以在 Mac 上也会失败。
修法：改调 `find_nx_extract(NXNATIVE, self.env)`——它自带 `--source-sha` 校验，
正是"确保二进制可用"这一步要的保证。

## 11. data pack 里没有游戏内容

`datapack.build(..., romfs=self.game / 'RomFS')`，但 `nsp.extract` 产出的是
**`RomFS.img` 镜像**，`RomFS/` 目录从来不存在。`link_tree` 用 `os.walk` 走空目录，
数据包只有 `mods/` 和 `shaders.dmsv`（16 文件 / 0.1 GB），游戏内容 1.1 GB 全丢。

装上去之后应用显示游戏库，Celeste 不在列表里——因为沙箱里根本没有它的数据。

修法：`nx_extract` 不加 `--romfs-image` 会解成 `<out>/RomFS/` 目录，
先解到 `<build>/romfs_dir`，`datapack.build` 指向 `romfs_dir/RomFS`。

## 12. 安装报 version downgrade

`bm install` 报 `code:9568263 install version downgrade`。设备上已装的包
versionCode 是 **2000000010**（手设过），而新包用 Unix 秒（约 17.9 亿）比它小。

修法：`$env:DM_VERSION_CODE = '2000000011'` 覆盖，取一个比设备上更大的值。

---

# 一次成功移植的完整命令序列（Windows）

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = 'F:\新建文件夹\.portshim'      # fcntl / signal 补丁
$env:DM_VERSION_CODE = '2000000011'              # 必须高于设备上已装的

cd F:\新建文件夹\nx2ohos
python -m porter config keys F:\NS\_keys_http\prod.keys
python -m porter inspect '<游戏目录>'            # 先确认 title ID / 版本 / profile 状态
python -m porter port '<游戏目录>' --install --progress lines
```

Celeste（`01002B30028F6000`，v589824）实测：extract 1.1 GB → rewrite 1 分 11 秒 →
split 26 着色器 → host 1.9 MB → data pack 1.2 GB → HAP 1.18 GB → 安装启动，
运行 37-60 fps。

**注意**：`nx_extract.exe`（Windows 版，2.1 MB）和 `dm/tools/nx_extract/out_win/`
都不进仓——按上面的 `dotnet publish` 命令本地生成即可。`.portshim/` 同样不进仓。

