#!/usr/bin/env python3
# run_cloud_probe.py — 在 AGC 云调试上跑 M0 探针（端到端，CDP 驱动）
#
# 流程：
#   1) 申请 Pura 70 Pro+（优先 API Level 22 那台）
#   2) 等设备连上（检测 "使用优惠时长中"）
#   3) 关闭隐私黑屏提示（点"启动"开辅助控件绘制）
#   4) 应用页：上传并安装 probe-signed.hap
#   5) HiLog 页：开始抓日志
#   6) 盲点桌面网格，直到 hilog 出现 probe 输出（隐私模式下无法读图标，
#      只能按几何位置试探；新装应用通常位于网格末尾）
#   7) 等探针跑完，导出日志
#
# 前置：Chrome 以 --remote-debugging-port=9222 启动且已登录华为账号；
#       out/probe-signed.hap 已构建（probe/build_hap.py）。
#
# 用法：python tools/verify/run_cloud_probe.py [--wait 150] [--dry-run]
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp as C   # noqa: E402
import verify_pixel as C_vp   # noqa: E402
import find_icons as C_fi     # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HAP = os.path.join(ROOT, "out", "probe-signed.hap")
DL_DIR = os.path.join(ROOT, "out", "device")

# 桌面图标网格（iframe 内 CSS 像素）。设备 1260x2844 → canvas 321x722，
# 锚点 (126,307)：x = 126 + dev_x*321/1260，y = 307 + dev_y*722/2844
COLS = [407, 347, 286, 226, 166]
ROWS = [878, 789, 700, 611, 523, 434]


class Runner:
    def __init__(self, port):
        page = C.pick_page(port, 0)
        self.c = C.CDP(page["webSocketDebuggerUrl"])
        self.port = port

    # ---- 基础 ----
    def js(self, expr):
        return self.c.eval(expr)

    def fr(self, body):
        """在 iframe 内执行 body（自动注入 d = iframe.contentDocument）。"""
        return self.js("(() => { const f = document.querySelector('iframe');"
                       " if (!f) return 'NO_IFRAME'; const d = f.contentDocument;"
                       " const w = f.contentWindow;" + body + "})()")

    def text(self):
        return self.js("document.querySelector('iframe').contentDocument.body.textContent") or ""

    def click_frame(self, x, y):
        rect = self.js("""(() => { const f=document.querySelector('iframe');
          const r=f.getBoundingClientRect();
          const sy=document.scrollingElement?document.scrollingElement.scrollTop:0;
          return {l:r.left, t:r.top-sy}; })()""")
        ax, ay = rect["l"] + x, rect["t"] + y
        self.c.call("Input.dispatchMouseEvent", type="mouseMoved", x=ax, y=ay)
        self.c.click_point(ax, ay)

    def click_btn_text(self, label):
        """点 iframe 内可见、文本精确匹配的按钮/元素。"""
        return self.fr("""
          const els=[...d.querySelectorAll('button,div,span,li,a')].filter(n=>{
            const t=(n.innerText||'').trim(); return t===%s && n.getBoundingClientRect().width>0; });
          if(!els.length) return 'NOTFOUND';
          els[els.length-1].click(); return 'OK';""" % ('"' + label + '"'))

    def confirm_all(self):
        return self.fr("""
          const bs=[...d.querySelectorAll('button')].filter(n=>
            (n.innerText||'').trim()==='确定' && n.getBoundingClientRect().width>0);
          bs.forEach(b=>b.click()); return bs.length;""")

    def tab(self, name):
        return self.fr("""
          const els=[...d.querySelectorAll('div,li,span')].filter(n=>
            (n.innerText||'').trim()===%s && n.getBoundingClientRect().width>0);
          if(!els.length) return 'NOTFOUND';
          els[els.length-1].click(); return 'OK';""" % ('"' + name + '"'))

    # ---- 步骤 ----
    def apply_device(self):
        print("[1] 申请设备")
        self.tab("机型选择")
        time.sleep(2)
        self.fr("""
          const inp=[...d.querySelectorAll('input')].find(n=>n.type==='text' &&
            n.placeholder && n.placeholder.indexOf('关键字')>-1);
          if(!inp) return 'NOINPUT';
          const setter=Object.getOwnPropertyDescriptor(w.HTMLInputElement.prototype,'value').set;
          setter.call(inp,'Pura 70');
          inp.dispatchEvent(new w.Event('input',{bubbles:true})); inp.focus();
          for (const k of ['keydown','keyup'])
            inp.dispatchEvent(new w.KeyboardEvent(k,{key:'Enter',code:'Enter',
              keyCode:13,which:13,bubbles:true}));
          return 'OK';""")
        time.sleep(4)
        # 搜索后可能仍在同一列表：先确认过滤结果已缩小，再选卡片
        r0 = self.fr("const t=d.body.innerText;"
                     " const m=t.match(/过滤结果 \\((\\d+)\\)/); return m?m[1]:'?';")
        print("     过滤结果:", r0)
        # 目标机型 Pura 70 Pro+（项目固定机型）；若被占用则回退到同系列机型，
        # 此时仅用于验证链路，结果不得计入 G0.x 闸门（脚本会打印实际机型）
        r = self.fr("""
          const btns=[...d.querySelectorAll('span.ues_btn')];
          let first=-1, firstCard='', plus=-1, plusCard='';
          for (let i=0;i<btns.length;i++){
            if((btns[i].innerText||'').trim()!=='开始测试') continue;
            let p=btns[i], t='';
            for(let k=0;k<6&&p;k++){
              const s=(p.innerText||'').replace(/\\s+/g,' ').trim();
              if(s.indexOf('设备型号')>-1){t=s;break;} p=p.parentElement;
            }
            if(t.indexOf('Pura 70')<0) continue;
            if(first<0){first=i;firstCard=t;}
            if(t.indexOf('Pura 70 Pro+')>-1){plus=i;plusCard=t;break;}
          }
          let pick = plus>=0 ? plus : first, card = plus>=0?plusCard:firstCard;
          if (pick < 0) {   // the keyword filter did not narrow the list (measured: 159 cards, no match)
            for (let i=0;i<btns.length;i++) {
              if((btns[i].innerText||'').trim()!=='开始测试') continue;
              let p=btns[i], t='';
              for(let k=0;k<10&&p;k++){
                const s=(p.innerText||'').replace(/\\s+/g,' ').trim();
                if(s.indexOf('设备型号')>-1){t=s;break;} p=p.parentElement;
              }
              pick = i; card = '[fallback] ' + t; break;
            }
          }
          if(pick<0) return 'NO_MATCH';
          btns[pick].click();
          return 'OK #'+pick+' '+card.slice(0,70);""")
        print("     ", r)
        if r == "NO_MATCH":
            return False
        time.sleep(3)
        print("     confirm x%s" % self.confirm_all())
        for _ in range(20):
            time.sleep(2)
            if "使用优惠时长中" in self.text():
                print("[2] 设备已连上")
                return True
        print("[2] 超时：未连上")
        return False

    def dismiss_privacy(self):
        """点"启动"开辅助控件绘制，并用红色像素验证确实生效。
        隐私模式下画面是纯灰/黑，只有辅助绘制开启后才会画出红色控件框；
        没有红框就无法定位图标，盲点全靠猜（首次失败即因此）。"""
        r = self.click_btn_text("启动")
        print("[3] 辅助绘制:", r)
        time.sleep(2)
        return self.has_red_boxes()

    def shot_canvas(self, path, scale=3):
        rect = self.js("""(() => { const f=document.querySelector('iframe');
          const r=f.getBoundingClientRect();
          const sy=document.scrollingElement?document.scrollingElement.scrollTop:0;
          return {l:r.left, t:r.top-sy}; })()""")
        # canvas_parent 在 iframe 内的相对位置
        cp = self.fr("""const c=d.getElementById('canvas_parent');
          const r=c.getBoundingClientRect();
          return {x:Math.round(r.left), y:Math.round(r.top),
                  w:Math.round(r.width), h:Math.round(r.height)};""")
        import base64
        data = self.c.call("Page.captureScreenshot", format="png",
                           clip={"x": rect["l"] + cp["x"], "y": rect["t"] + cp["y"],
                                 "width": cp["w"], "height": cp["h"],
                                 "scale": scale})["data"]
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))
        return path, cp

    def has_red_boxes(self):
        png, _ = self.shot_canvas(os.path.join(ROOT, "out", "probe-canvas.png"))
        try:
            w, h, px = C_vp.decode_png(open(png, "rb").read())
            n = sum(1 for i in range(0, w * h, 7)
                    if px[i * 4] > 150 and px[i * 4 + 1] < 110 and px[i * 4 + 2] < 110)
            return n > 50
        except Exception:
            return False

    def wait_user_launch(self, seconds):
        """隐私模式下无法辨识图标（辅助绘制未生效时画面是灰/黑），
        自动化点击不可靠；此模式等待人工在投屏上点一次应用图标。"""
        print("[6] 等待人工启动：请在云调试设备投屏上点击应用图标 'label'")
        t0 = time.time()
        while time.time() - t0 < seconds:
            time.sleep(4)
            if "probe: started" in self.text():
                print("[6] 检测到 probe: started（用时 %.0fs）" % (time.time() - t0))
                return True
        print("[6] 等待超时（%ds）" % seconds)
        return False

    def launch_by_grid(self):
        """隐私模式下无红框时，按几何网格盲点（首轮曾据此命中）。"""
        print("[6b] 网格盲点")
        for row in ROWS:
            for col in COLS:
                self.click_frame(col, row)
                time.sleep(1.5)
                if "probe: started" in self.text():
                    print("[6b] 命中 @%d,%d" % (col, row))
                    return True
        print("[6b] 未命中")
        return False

    def launch_by_icons(self):
        """按红框检测出的图标坐标点击，直到 hilog 出现 probe: started。"""
        print("[6] 红框检测定位图标")
        cp = self.fr("""const c=d.getElementById('canvas_parent');
          if(!c) return null;
          const r=c.getBoundingClientRect();
          return {x:Math.round(r.left), y:Math.round(r.top),
                  w:Math.round(r.width), h:Math.round(r.height)};""")
        if not cp:      # 设备已释放/页面切走：投屏容器不存在
            print("[6] 无投屏容器，跳过图标定位")
            return False
        png, cp = self.shot_canvas(os.path.join(ROOT, "out", "probe-canvas.png"))
        w, h, px = C_vp.decode_png(open(png, "rb").read())
        boxes = C_fi.boxes(px, w, h, 40)
        cands = []
        for (x0, y0, bw, bh) in boxes:
            if bw < 220 and bh < 220 and 0.55 <= bw / float(bh) <= 1.8:
                ix = cp["x"] + (x0 + bw / 2.0) * cp["w"] / w
                iy = cp["y"] + (y0 + bh / 2.0) * cp["h"] / h
                cands.append((round(ix), round(iy)))
        cands.sort(key=lambda t: (-t[1], -t[0]))   # 从右下（新装应用常在末尾）开始
        print("     候选图标 %d 个: %s" % (len(cands), cands[:8]))
        for i, (ix, iy) in enumerate(cands):
            self.click_frame(ix, iy)
            time.sleep(2)
            if "probe: started" in self.text():
                print("[6] 命中（第 %d 个 @%d,%d）" % (i + 1, ix, iy))
                return True
        print("[6] 未命中")
        return False

    def install_hap(self):
        print("[4] 安装 hap")
        # 同版本号 hap 会被"已经存在相同版本应用"拦下，装不上新代码：先卸载
        # （bm 在云调试 shell 白名单内；ls /data/app 这类命令会被拒）
        self.shell("aa force-stop dev.eden_emu.ohos", 3)
        _, uo = self.shell("bm uninstall -n dev.eden_emu.ohos", 6)
        print("     uninstall:", (uo or "").strip().split("\n")[1:2])
        self.tab("应用")
        time.sleep(2)
        expr = "document.querySelector('iframe').contentDocument.querySelector('input[type=file]')"
        self.c.set_files_on(expr, HAP)
        time.sleep(4)
        for _ in range(3):
            self.confirm_all()
            time.sleep(1)
        # 卡片按上传时间【倒序】排列（最新在最前）——点最后一个会装成最旧的包，
        # 实测报 "install version downgrade"。必须点第一个。
        r = self.fr("const a=[...d.querySelectorAll('.icon-install')];"
                    " if(!a.length) return 'NOBTN'; a[0].click(); return 'OK';")
        print("     install:", r)
        time.sleep(2)
        for _ in range(3):
            self.confirm_all()
            time.sleep(1)
        # 以设备侧 bm 查询为准（页面"安装成功"是日志面板里的历史文本 → 假阳性）
        ok = False
        for _ in range(20):
            time.sleep(3)
            _, out = self.shell("bm dump -n dev.eden_emu.ohos", 6)
            if "appId" in (out or ""):
                ok = True
                break
        if not ok:
            print("[4] 安装未确认成功（继续尝试启动）")
            return True
        # 安装后还有"应用安全扫描中"，期间 aa start 会成功返回但进程不启动（实测），
        # 必须等这段文案从面板消失再拉起。
        for _ in range(20):
            t = self.text()
            if "应用安全扫描中" not in t and "应用正在安装中" not in t:
                break
            time.sleep(3)
        print("[4] 安装成功（bm 已确认，扫描已结束）")
        return True

    def start_logging(self):
        """开日志面板 + 【启动前】清空。

        清空必须放在 aa start 之前：面板只留最近 500 行，上一轮的分块与新一轮
        seq 会重号，抓回来会拼出脏 JSON / 旧结论（实测两次误判）。若清空没生效，
        宁可返回 False 让上层报错，也不要交出旧报告。"""
        print("[5] 开日志")
        for _ in range(3):
            self.tab("HiLog")
            time.sleep(2)
            r = self.click_btn_text("获取日志")
            if r == "NOTFOUND":
                # 已在抓日志时按钮变成「停止」，此时无需再点「获取日志」
                has_stop = self.fr("const els=[...d.querySelectorAll('button,div,span')]"
                                   ".filter(n=>(n.innerText||'').trim()==='停止' &&"
                                   " n.getBoundingClientRect().width>0); return els.length;")
                if has_stop:
                    print("     已在抓日志（按钮为「停止」）")
                    r = "OK"
            print("     获取日志:", r)
            if r == "OK":
                break
            time.sleep(2)
        self.c.call("Browser.setDownloadBehavior", behavior="allow",
                    downloadPath=DL_DIR)
        print("     清空:", self.click_btn_text("清空"))
        time.sleep(1.5)
        state = self.fr("const e=d.querySelector('.log_content');"
                        " if(!e) return 'NO_PANEL';"
                        " return e.innerText.indexOf('probe: J|')>-1 ? 'STALE' : 'CLEAN';")
        print("     面板:", state)
        return state == "CLEAN"

    def shell(self, cmd, wait=5):
        """云调试「控制参数 → Shell脚本」执行 hdc shell 命令。
        这是唯一可靠的启动方式：隐私模式下投屏全黑，按图标坐标盲点成功率极低；
        `aa start` 直接拉起 ability（注意管道命令受白名单限制，`hilog -x` 会被拒）。"""
        self.tab("控制参数")
        time.sleep(1.2)
        self.click_btn_text("Shell脚本")
        time.sleep(1.2)
        r = self.fr("""
          const ta=[...d.querySelectorAll('textarea')].find(
            n=>n.placeholder && n.placeholder.indexOf('shell')>-1);
          if(!ta) return 'NOTA';
          const setter=Object.getOwnPropertyDescriptor(w.HTMLTextAreaElement.prototype,'value').set;
          setter.call(ta,%s); ta.dispatchEvent(new w.Event('input',{bubbles:true}));
          const bs=[...d.querySelectorAll('button,span,div')].filter(
            n=>(n.innerText||'').trim()==='执行' && n.offsetWidth>0);
          if(!bs.length) return 'NOBTN';
          bs[bs.length-1].click(); return 'run';""" % ("'" + cmd.replace("'", "\\'") + "'"))
        time.sleep(wait)
        out = self.fr("const t=d.body.innerText; const i=t.indexOf('执行命令');"
                      " return t.slice(i+8,i+600);")
        return r, out

    def launch_by_shell(self, bundle="dev.eden_emu.ohos", ability="EntryAbility"):
        print("[6] Shell 启动（aa start）")
        self.shell("aa force-stop %s" % bundle, 4)
        for i in range(3):
            r, out = self.shell("aa start -b %s -a %s" % (bundle, ability), 4)
            print("     ", (out or "").strip().replace("\n", " / ")[:120])
            ok = "start ability successfully" in (out or "")
            if not ok:
                return False
            # 安装/安全扫描窗口期内 aa start 会"成功"但进程不启动（实测），
            # 看到这两句就等一会儿再拉一次。
            t = self.text()
            if "应用正在安装中" not in t and "应用安全扫描中" not in t:
                return True
            print("     仍在安装/扫描中，%ds 后重试 aa start" % (10 * (i + 1)))
            time.sleep(10 * (i + 1))
        return True

    def grab_report(self, timeout=200):
        """从 HiLog 面板抓 probe: J|seq|total|chunk 并重组 JSON。
        面板只保留最近 500 行、系统日志刷得很快，必须启动后立刻轮询。
        报告在 probe_run() 末尾一次性分块输出，整体耗时取决于最慢项：
        aot_nso 的 fork 超时（60s）会把它推到 ~130s，窗口必须留够。"""
        import json as _json
        import re as _re
        # 注意：不要在这里清空——清空已移到 start_logging（启动前）。启动后再清
        # 会把已经输出的分块抹掉，出现"报告残缺"的假故障。
        chunks, total, t0 = {}, 0, time.time()
        while time.time() - t0 < timeout:
            self.tab("HiLog")
            txt = self.fr("const e=d.querySelector('.log_content');"
                          " return e?e.innerText:'';") or ""
            for line in txt.split("\n"):
                m = _re.search(r"probe: J\|(\d+)\|(\d+)\|(.*)$", line)
                if m:
                    chunks[int(m.group(1))] = m.group(3)
                    total = int(m.group(2))
            if total and len(chunks) >= total:
                break
            time.sleep(0.4)
        if total and len(chunks) == total:
            return _json.loads("".join(chunks[i] for i in range(total)))
        return None

    def launch_by_grid(self):
        print("[6] 盲点桌面网格找探针图标")
        self.tab("应用")
        time.sleep(2)
        tries = 0
        for row in ROWS:
            for col in COLS:
                self.click_frame(col, row)
                tries += 1
                time.sleep(2)
                # 注意：不能只匹配 "probe" —— netmanager 的 "Net[] probe failed"
                # 也在日志流里，会误判命中（上一轮即因此空跑）
                if "probe: started" in self.text():
                    print("[6] 命中 probe（第 %d 次点击 @%d,%d）" % (tries, col, row))
                    return True
        print("[6] 未命中（%d 次）" % tries)
        return False

    def export_logs(self):
        print("[8] 导出日志")
        self.tab("HiLog")
        time.sleep(2)
        print("     ", self.click_btn_text("导出日志"))
        time.sleep(6)
        files = sorted((os.path.join(DL_DIR, f) for f in os.listdir(DL_DIR)),
                       key=os.path.getmtime) if os.path.isdir(DL_DIR) else []
        print("     downloads:", [os.path.basename(f) for f in files[-3:]])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=int, default=150, help="探针运行等待秒数")
    ap.add_argument("--user-wait", type=int, default=0,
                    help="红框可见但自动未命中时，等待人工点击应用图标的秒数（0=不等）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--reuse", action="store_true",
                    help="复用当前已连上的调试设备（跳过机型申请）")
    a = ap.parse_args()
    if not os.path.isfile(HAP):
        print("ERROR: %s 不存在（先跑 probe/build_hap.py）" % HAP)
        return 1
    os.makedirs(DL_DIR, exist_ok=True)
    r = Runner(a.port)
    if a.dry_run:
        print("dry-run: page =", C.pick_page(a.port, 0)["url"][:100])
        return 0
    if a.reuse:
        # 已有会话：机型申请会被"离开调试页面"弹窗挡住，直接复用当前设备
        r.tab("单机调试")
        time.sleep(2)
        if "使用优惠时长中" not in r.text():
            print("[1] 当前无会话，请去掉 --reuse")
            return 2
        print("[1] 复用当前调试设备")
    elif not r.apply_device():
        return 2
    ok = r.dismiss_privacy()
    print("[3] 红框可见:", ok)
    r.install_hap()
    if not r.start_logging():
        print("WARN: 日志面板未就绪或未清空——抓到的分块可能来自上一轮")
    # 首选 Shell 启动；图标盲点仅作兜底（隐私黑屏下几乎不可用）
    launched = r.launch_by_shell()
    if not launched:
        launched = r.launch_by_icons() or r.launch_by_grid()
    if not launched and a.user_wait > 0:
        launched = r.wait_user_launch(a.user_wait)
    if not launched:
        print("提示：自动化与等待均未启动探针，仅导出当前日志")
        r.export_logs()
        return 3
    print("[7] 抓取报告（HiLog 面板仅留 500 行，需即时轮询）")
    rep = r.grab_report()
    if rep:
        import json as _json
        out = os.path.join(DL_DIR, "probe_report.json")
        _json.dump(rep, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        m = rep.get("meta", {})
        print("[7] report -> %s" % out)
        print("     build=%s started=%s device=%s" %
              (m.get("build", "?"), m.get("started_at", "?"),
               m.get("device_model", "?")))
        print("     aot_nso=%s" % rep.get("checks", {}).get("aot_nso"))
    else:
        print("[7] 未抓到完整报告块")
    r.export_logs()
    return 0 if rep else 4


if __name__ == "__main__":
    sys.exit(main())
