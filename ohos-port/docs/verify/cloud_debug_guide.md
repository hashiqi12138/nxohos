# AGC 云调试跑 M0 探针 — 操作手册

无实体真机时，用华为 AGC「云调试」远程真机执行 M0 真机项。云真机**无 hdc**，
探针已内建适配：报告 JSON 无条件经 hilog 分块输出（`probe: J|seq|total|chunk`），
PC 端用 reassemble_hilog_json.py 重组；TC0-4-2 的 VmSize/RSS 由探针进程内
自取写入 note——云真机上**无需任何环境变量注入**。

## 0. 前置（一次性）

1. 华为开发者账号 + 个人实名认证（免费）。
2. AGC 创建 HarmonyOS 应用（拿 appId/projectId）——已就绪。
3. OHOS SDK（本机：DevEco Studio 自带），构建探针：

```powershell
$env:OHOS_SDK = "D:\dev-echo\DevEco Studio\sdk\default\openharmony"
python probe/build_probe.py      # → out/libs/arm64/libprobe.so + out/bin/t_mini_nso
python probe/build_hap.py        # → out/probe-unsigned.hap（自动拷贝 so 并处理中文路径）
```

4. **签名（云调试必需）**——DevEco 自动签名的密钥库口令存放在 IDE 安全存储里
   （不可导出），故这一步在 IDE 内完成：

```
DevEco Studio → File → Open → f:\新建文件夹\ohos-port\probe\hap
  → File → Project Structure → Signing Configs
  → 勾选 "Automatically generate signature"（需登录华为账号）
  → Build → Build Hap(s)
产物：probe/hap/entry/build/default/outputs/default/entry-default-signed.hap
```

> 注：`build_hap.py` 之所以先把工程复制到 `F:\ohos_build_hap` 再构建，是因为
> hvigor 拒绝含非 ASCII 字符的工程路径（本仓路径含中文），且临时目录的
> `LIUJIE~1` 短名含 `~` 同样被拒。

## 1. 申请云真机

**目标机型固定为 HUAWEI P70 Pro+**（项目约束，见 project_memory）。云调试必须
选到该机型；若列表无 P70 Pro+，先按 Pura 系列筛选（P70 属 Pura 70 家族），
仍无则记录 SKIP 并说明「无目标机型可用」，不得用 Mate 系列结果替代判定。

1. 打开 HarmonyOS 开发者中心 → 应用管理 → 切换至项目 → 质量和运维 > 云调试
   → 单机调试。
2. 筛选条件：品牌 华为 + 系统版本 HarmonyOS 5 及以上 + 优惠机型，
   机型名确认含 **P70 Pro+**（不是 Mate 70）。
3. 单次调试 20 分钟，弹框可「延长一次」+20 分钟；到时设备释放、重新申请即可。
   高峰 13:00–23:00，建议 8:00–12:00 错峰。
4. 申请后在报告 `meta.device_model` 核对机型串，与 P70 Pro+ 不符则该次结果
   只作参考、不计入 G0.x 判定。

## 2. 上传安装与运行

1. 调试界面左侧「应用」→ 上传 probe.hap → 安装。**实测坑（2026-11-06）：**

   - 上传卡片按时间**倒序**（最新在最前）。点最后一个 `.icon-install` 会装最旧的包，
     报 `code:9568263 install version downgrade`；**必须点第一个**。
   - 同版本号 hap 会被 `已经存在相同版本应用，请确认。` 拦下 → 先
     `aa force-stop` + `bm uninstall -n dev.eden_emu.ohos`，且 `build_hap.py` 已改为
     每次构建自动递增 `versionCode`。
   - 页面上的「安装成功」是日志面板里的历史文本，**不能当判据**；以
     `bm dump -n dev.eden_emu.ohos` 里出现 `appId` 为准。
   - 安装后还有「应用安全扫描中,请稍后...」，这段期间 `aa start` 会返回
     `start ability successfully.` 但进程根本不启动（本轮踩坑半小时），必须等该文案消失。
2. **启动一律走「控制参数 → Shell脚本」**（2026-11-06 实测确认，唯一可靠路径）：

```
aa force-stop dev.eden_emu.ohos
aa start -b dev.eden_emu.ohos -a EntryAbility        # 返回 start ability successfully.
```

   隐私保护下投屏恒为灰/黑（`#playerVideo` 的 readyState 一直是 0），按图标坐标
   盲点几乎不可能命中——此前一轮的"命中"其实是日志里 `netmanager probe failed`
   造成的误判。**判据必须精确匹配 `probe: started`。**
3. 进入「HiLog」页点「获取日志」，面板只保留最近 500 行且系统日志刷得极快
   （约 3–5 秒冲掉），所以顺序是：先开日志 → **点「清空」**（否则上一轮的分块
   会与新报告 seq 重号混在一起，拼出脏 JSON）→ 再 `aa start` → 立刻轮询
   `.log_content` 抓 `probe: J|seq|total|chunk`。
   抓取窗口按 **200 秒**留：报告在 `probe_run()` 末尾一次性分块输出，最慢的
   `aot_nso` fork 超时（60s）会把整轮推到 ~130s，40s 窗口必然漏。
4. Shell 白名单限制：`ps -ef | grep xxx`、`aa ...`、`cat` 可执行；
   `hilog -x` 会返回「无效命令或被限制执行」。
5. 应用沙盒文件 `/data/storage/el2/base/files/probe_report.json` 存在但 shell
   `cat` 被拒（Permission denied），取报告只能走 hilog 分块。

## 3. 取回结论（无 hdc 替代路径）

| 数据 | 途径 |
|---|---|
| probe_report.json | `python tools/verify/run_cloud_probe.py` 会自动 `aa start` + 轮询 HiLog 重组，落 `out/device/probe_report.json` |
| hilog `probe: started` | 同上；或导出日志 → `python tools/verify/assert_hilog.py --file hilog.txt --must 'probe: started'` |
| TC0-2-2 截屏 | 投屏黑屏时不可用；以 `checks.vk_surface` 的 swapchain 返回值判定（本轮 rc=-1000000000 即 FAIL） |
| TC0-4-2 VmSize/RSS | 探针进程内自取（note 带 `vm=/rss=`） |
| TC0-4-3 切后台 | 投屏不可见，无法可靠操作 → 留档待实体设备 |

## 4. TC 覆盖矩阵（云真机）

| TC | 可跑 | 备注 |
|---|---|---|
| TC0-1-1 设备构建 | ✅ | 构建+签名在 PC；云真机只负责安装运行 |
| TC0-1-2 hilog started | ✅ | 日志导出判定 |
| TC0-1-3 hdc 写沙盒 | ⚠️ | 无 hdc；Shell脚本可执行 `aa`/`ps`/`cat`，但沙盒文件 `cat` 被拒 |
| TC0-2-1 | ✅ | 重组报告 → verify_report --tc tc0-2-1 |
| TC0-2-2 | ⚠️ | 投屏黑屏取不到截屏；改以 `checks.vk_surface.note` 的 swapchain 返回值判定 |
| TC0-2-3 | ⚠️ | probe_frames.jsonl 无法取回，改为留 present_ms（report 内） |
| TC0-3-1/3 | ✅ | 重组报告判定 |
| TC0-3-2 | ❌/⏳ | 需 fixtures + 帧文件导出通道，云调试先记录 TC0-3-1 结果 |
| TC0-4-1 | ✅ | 重组报告判定 |
| TC0-4-2 | ✅ | note 内 vm/rss（进程内自取） |
| TC0-5（探针内七项） | ✅ | 重组报告 --tc tc0-5-regs；独立 t_mini_nso 二进制无 hdc 不推，非必需 |

## 5. 注意事项

- 免费额度按账号计，每日 0 点重置；主账号与子账号额度独立。
- 云真机为公用环境，**不要在探针里放敏感数据**；调试结束应用/数据即回收。
- hilog 分块输出依赖日志导出完整性：若导出日志缺 `probe: J|END|` 行，
  reassemble 会报 missing chunks——在设备释放前重跑一次探针。
- 若云真机机型非优惠机型会扣付费额度，申请前确认「优惠机型」筛选。
