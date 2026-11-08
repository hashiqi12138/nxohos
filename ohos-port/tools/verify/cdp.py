#!/usr/bin/env python3
# cdp.py — 最小 Chrome DevTools Protocol 客户端（纯标准库，无第三方依赖）
#
# 为什么需要它：AGC 云调试页面的内容区在 Windows UI Automation 里是 disabled
# （系统级点击/键盘派发不进网页），只有 CDP 能可靠操作。REA 的 web 工具是只读的
# （明确不点击、不执行脚本），故自建这一薄层。
#
# 用法（Chrome 需以 --remote-debugging-port 启动）：
#   python cdp.py targets
#   python cdp.py eval "<js>"              # 执行 JS，打印返回值
#   python cdp.py text [--max 8000]        # 打印页面可见文本
#   python cdp.py click "<css selector>"   # 按选择器点击（JS 定位中心点 + 真实鼠标事件）
#   python cdp.py click-text "<可见文字>"   # 按可见文字点击（适合按钮）
#   python cdp.py file "<css>" "<文件路径>"  # 给 <input type=file> 赋值
#   python cdp.py shot "<png 路径>"
#
# 所有命令可加 --port 9222 / --index N 选择页面。
import argparse
import base64
import json
import os
import socket
import struct
import sys
import urllib.request

OP_TEXT, OP_CLOSE, OP_PING, OP_PONG = 0x1, 0x8, 0x9, 0xA


class WS:
    """够用的 WebSocket 客户端：文本帧收发，支持分片与 >64KB 负载。"""

    def __init__(self, url):
        # ws://127.0.0.1:9222/devtools/page/xxxx
        assert url.startswith("ws://"), "only ws:// supported"
        rest = url[5:]
        hostport, _, path = rest.partition("/")
        host, _, port = hostport.partition(":")
        self.sock = socket.create_connection((host, int(port or 80)), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            "GET /%s HTTP/1.1\r\n"
            "Host: %s\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ) % (path, hostport, key)
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("websocket handshake failed")
            resp += chunk
        if b"101" not in resp.split(b"\r\n")[0]:
            raise RuntimeError("handshake rejected: %r" % resp[:120])
        self.buf = resp.split(b"\r\n\r\n", 1)[1]

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("socket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text):
        data = text.encode()
        header = bytearray([0x80])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def recv(self):
        """收一条完整文本消息（自动处理分片）。"""
        parts = []
        while True:
            b0, b1 = self._read(2)
            fin = b0 & 0x80
            opcode = b0 & 0x0F
            masked = b1 & 0x80
            ln = b1 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._read(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if masked else b""
            payload = self._read(ln)
            if masked:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == OP_PING:
                continue
            if opcode == OP_CLOSE:
                raise RuntimeError("websocket closed by peer")
            parts.append(payload)
            if fin:
                return b"".join(parts).decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class CDP:
    def __init__(self, ws_url):
        self.ws = WS(ws_url)
        self.next_id = 0

    def call(self, method, **params):
        self.next_id += 1
        mid = self.next_id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("%s failed: %s" % (method, msg["error"]))
                return msg.get("result", {})

    def eval(self, expr, await_promise=True):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True,
                      awaitPromise=await_promise, userGesture=True)
        res = r.get("result", {})
        if r.get("exceptionDetails"):
            raise RuntimeError("JS exception: %s" % json.dumps(
                r["exceptionDetails"].get("exception", {}), ensure_ascii=False)[:400])
        return res.get("value")

    def eval_object(self, expr):
        """求值为对象引用（不序列化），返回 objectId —— 供 DOM.requestNode 用。"""
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=False,
                      awaitPromise=False, userGesture=True)
        if r.get("exceptionDetails"):
            raise RuntimeError("JS exception: %s" % json.dumps(
                r["exceptionDetails"].get("exception", {}), ensure_ascii=False)[:400])
        obj = r.get("result", {}).get("objectId")
        if not obj:
            raise RuntimeError("expression did not yield an object")
        return obj

    def set_files_on(self, expr, path):
        """对 expr 求值得到的 <input type=file> 设置文件（CDP 正规做法）。
        用 objectId 直传：跨 frame 的节点在顶层 DOM 树里没有 nodeId，
        requestNode 会返回失效 id（实测 -32000 Could not find node）。"""
        obj = self.eval_object(expr)
        self.call("DOM.setFileInputFiles", files=[path], objectId=obj)

    def click_point(self, x, y):
        for typ in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=typ, x=int(x), y=int(y),
                      button="left", clickCount=1)

    def close(self):
        self.ws.close()


def http_json(port, path):
    url = "http://127.0.0.1:%d%s" % (port, path)
    with urllib.request.urlopen(url, timeout=8) as f:
        return json.loads(f.read().decode())


def pick_page(port, index):
    pages = [t for t in http_json(port, "/json/list") if t.get("type") == "page"]
    if not pages:
        raise SystemExit("no page targets")
    if index >= len(pages):
        raise SystemExit("index out of range (%d pages)" % len(pages))
    return pages[index]


def main():
    ap = argparse.ArgumentParser(description="minimal CDP client")
    ap.add_argument("cmd", choices=["targets", "eval", "text", "click",
                                    "click-text", "file", "setfile", "hover",
                                    "clickframe", "swipe", "shot"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--max", type=int, default=8000)
    a = ap.parse_args()

    if a.cmd == "targets":
        for i, t in enumerate(http_json(a.port, "/json/list")):
            print("[%d] %s | %s | %s" % (i, t.get("type"), t.get("title"),
                                         t.get("url", "")[:110]))
        return 0

    page = pick_page(a.port, a.index)
    cdp = CDP(page["webSocketDebuggerUrl"])
    try:
        if a.cmd == "eval":
            expr = a.args[0] if a.args else ""
            if expr == "-":
                # PowerShell 会吞掉命令行参数里的双引号，复杂 JS 走标准输入
                expr = sys.stdin.read()
            print(json.dumps(cdp.eval(expr), ensure_ascii=False, indent=2))
        elif a.cmd == "text":
            txt = cdp.eval("document.body.innerText") or ""
            print(txt[:a.max])
        elif a.cmd == "click":
            sel = a.args[0]
            js = """(() => {
              const el = document.querySelector(%s);
              if (!el) return null;
              el.scrollIntoView({block:'center'});
              const r = el.getBoundingClientRect();
              return {x: r.left + r.width/2, y: r.top + r.height/2,
                      w: r.width, h: r.height};
            })()""" % json.dumps(sel)
            box = cdp.eval(js)
            if not box:
                raise SystemExit("selector not found: %s" % sel)
            cdp.click_point(box["x"], box["y"])
            print("clicked %s at (%.0f,%.0f) size %.0fx%.0f"
                  % (sel, box["x"], box["y"], box["w"], box["h"]))
        elif a.cmd == "click-text":
            needle = a.args[0]
            js = """(() => {
              const target = %s;
              const nodes = [...document.querySelectorAll(
                  'button, a, div[role=button], span, div')];
              const hits = nodes.filter(n => (n.innerText||'').trim() === target);
              const el = hits[hits.length-1];
              if (!el) return null;
              el.scrollIntoView({block:'center'});
              const r = el.getBoundingClientRect();
              return {x: r.left + r.width/2, y: r.top + r.height/2,
                      w: r.width, h: r.height, tag: el.tagName};
            })()""" % json.dumps(needle)
            box = cdp.eval(js)
            if not box:
                raise SystemExit("text not found: %s" % needle)
            cdp.click_point(box["x"], box["y"])
            print("clicked <%s> '%s' at (%.0f,%.0f)"
                  % (box["tag"], needle, box["x"], box["y"]))
        elif a.cmd == "file":
            sel, path = a.args[0], os.path.abspath(a.args[1])
            if not os.path.isfile(path):
                raise SystemExit("file not found: %s" % path)
            doc = cdp.call("DOM.getDocument", depth=0)
            node = cdp.call("DOM.querySelector", nodeId=doc["root"]["nodeId"],
                            selector=sel)
            if not node.get("nodeId"):
                raise SystemExit("selector not found: %s" % sel)
            cdp.call("DOM.setFileInputFiles", files=[path], nodeId=node["nodeId"])
            print("set file on %s -> %s" % (sel, path))
        elif a.cmd == "setfile":
            # setfile "<iframe 内 JS 表达式>" "<文件>"：对表达式求值得到的
            # <input type=file> 直接注入文件（同域 iframe 用 contentDocument 取）
            expr, path = a.args[0], os.path.abspath(a.args[1])
            if not os.path.isfile(path):
                raise SystemExit("file not found: %s" % path)
            cdp.set_files_on(expr, path)
            print("set file -> %s" % path)
        elif a.cmd == "hover":
            # hover "<iframe 内可见文字>" [click]
            # Element UI 的 tooltip/dropdown 只认真实指针事件（合成 MouseEvent 无效），
            # 故按文本在 iframe 内定位后发 Input.dispatchMouseEvent.mouseMoved。
            needle = a.args[0]
            info = cdp.eval("""(() => {
              const f = document.querySelector('iframe');
              const d = f.contentDocument, fr = f.getBoundingClientRect();
              const sy = document.scrollingElement ? document.scrollingElement.scrollTop : 0;
              const els = [...d.querySelectorAll('*')].filter(
                  n => (n.innerText || '').trim() === %s && n.children.length === 0);
              if (!els.length) return null;
              const el = els[els.length - 1], r = el.getBoundingClientRect();
              return {x: fr.left + r.left + r.width / 2,
                      y: fr.top + r.top + r.height / 2 - sy};
            })()""" % json.dumps(needle, ensure_ascii=False))
            if not info:
                raise SystemExit("text not found: %s" % needle)
            cdp.call("Input.dispatchMouseEvent", type="mouseMoved",
                     x=info["x"], y=info["y"])
            print("hover '%s' at (%.0f,%.0f)" % (needle, info["x"], info["y"]))
            if len(a.args) > 1 and a.args[1] == "click":
                cdp.click_point(info["x"], info["y"])
                print("clicked '%s'" % needle)
        elif a.cmd == "clickframe":
            # clickframe <iframe 内 x> <y>：在云调试 iframe 内按 CSS 像素坐标点击
            # （canvas 投屏、图标按钮等无法用选择器命中的目标）
            ix, iy = float(a.args[0]), float(a.args[1])
            rect = cdp.eval("""(() => {
              const f = document.querySelector('iframe'), r = f.getBoundingClientRect();
              const sy = document.scrollingElement ? document.scrollingElement.scrollTop : 0;
              const sx = document.scrollingElement ? document.scrollingElement.scrollLeft : 0;
              return {l: r.left - sx, t: r.top - sy};
            })()""")
            cdp.call("Input.dispatchMouseEvent", type="mouseMoved",
                     x=rect["l"] + ix, y=rect["t"] + iy)
            cdp.click_point(rect["l"] + ix, rect["t"] + iy)
            print("clicked iframe(%.0f,%.0f) -> viewport(%.0f,%.0f)"
                  % (ix, iy, rect["l"] + ix, rect["t"] + iy))
        elif a.cmd == "swipe":
            # swipe <iframe内 x1> <y1> <x2> <y2> --touch
            # 云调试投屏：鼠标拖拽或触摸滑动。先按住起点，分步移动，再抬起。
            x1, y1, x2, y2 = (float(v) for v in a.args[:4])
            rect = cdp.eval("""(() => {
              const f = document.querySelector('iframe'), r = f.getBoundingClientRect();
              const sy = document.scrollingElement ? document.scrollingElement.scrollTop : 0;
              return {l: r.left, t: r.top - sy};
            })()""")
            ax, ay = rect["l"] + x1, rect["t"] + y1
            bx, by = rect["l"] + x2, rect["t"] + y2
            use_touch = len(a.args) > 4 and a.args[4] == "--touch"
            steps = 8
            if use_touch:
                cdp.call("Input.dispatchTouchEvent", type="touchStart",
                         touchPoints=[{"x": ax, "y": ay}])
                for i in range(1, steps + 1):
                    cdp.call("Input.dispatchTouchEvent", type="touchMove",
                             touchPoints=[{"x": ax + (bx - ax) * i / steps,
                                           "y": ay + (by - ay) * i / steps}])
                cdp.call("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
            else:
                cdp.call("Input.dispatchMouseEvent", type="mouseMoved", x=ax, y=ay)
                cdp.call("Input.dispatchMouseEvent", type="mousePressed", x=ax, y=ay,
                         button="left", clickCount=1)
                for i in range(1, steps + 1):
                    cdp.call("Input.dispatchMouseEvent", type="mouseMoved",
                             x=ax + (bx - ax) * i / steps,
                             y=ay + (by - ay) * i / steps, button="left")
                cdp.call("Input.dispatchMouseEvent", type="mouseReleased", x=bx, y=by,
                         button="left", clickCount=1)
            print("swipe (%.0f,%.0f)->(%.0f,%.0f) %s"
                  % (x1, y1, x2, y2, "touch" if use_touch else "mouse"))
        elif a.cmd == "shot":
            kw = {}
            if len(a.args) > 1:
                # shot <path> x,y,w,h[,scale]：截取指定视口区域（用于看清投屏画面）
                x, y, w, h, *rest = (a.args[1].split(",") + ["1"])[:5]
                kw["clip"] = {"x": float(x), "y": float(y), "width": float(w),
                              "height": float(h), "scale": float(rest[0] or 1)}
            data = cdp.call("Page.captureScreenshot", format="png", **kw)["data"]
            with open(a.args[0], "wb") as f:
                f.write(base64.b64decode(data))
            print("screenshot: %s" % a.args[0])
    finally:
        cdp.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
