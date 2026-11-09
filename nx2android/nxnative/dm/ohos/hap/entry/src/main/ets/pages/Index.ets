/*
 * Index.ets — the game page: one full-screen surface with a transparent virtual gamepad over it.
 *
 * The XComponent is the only way to get a native window that VK_OHOS_surface can present to (XComponentType.TEXTURE is not one), and this
 * SDK has no napi_value form of the XComponent callbacks any more, so the surface id is taken from the controller and handed to the native
 * module — the same path the M0 probe proved on the device.
 *
 * Input: the ArkTS layer above the surface is the sole input layer. Its onTouch turns finger positions into the virtual gamepad's
 * sticks and buttons and its onKeyEvent maps a keyboard (for the desk); both push dmhost.setPad, which the native side feeds to
 * player 1's controller (dm_input_set; lock-free atomics, so the UI thread may call it directly). The game reads no touch itself
 * (BOTW), so a full-screen capture is safe.
 *
 * What starts: the game when its files are in the sandbox (files/dm/RomFS and the NSOs in files/dm), otherwise the self-test scene, so the
 * window chain can be checked on any build.
 */
import dmhost from 'libdmhost.so';
import { fileIo as fs } from '@kit.CoreFileKit';
import { util } from '@kit.ArkTS';
import { display } from '@kit.ArkUI';
import { resourceManager } from '@kit.LocalizationKit';

// Button bits mirror dm/host/dm_input.h (1:1 with nn::hid::NpadButton); sticks are ±DM_STICK_MAX with y flipped (up positive).
const DM_BTN_A: number = 1 << 0;
const DM_BTN_B: number = 1 << 1;
const DM_BTN_X: number = 1 << 2;
const DM_BTN_Y: number = 1 << 3;
const DM_BTN_L: number = 1 << 6;
const DM_BTN_R: number = 1 << 7;
const DM_BTN_ZL: number = 1 << 8;
const DM_BTN_ZR: number = 1 << 9;
const DM_BTN_PLUS: number = 1 << 10;
const DM_BTN_MINUS: number = 1 << 11;
const DM_BTN_LEFT: number = 1 << 12;
const DM_BTN_UP: number = 1 << 13;
const DM_BTN_RIGHT: number = 1 << 14;
const DM_BTN_DOWN: number = 1 << 15;
const DM_STICK_MAX: number = 32767;

// Stick zone centers/radii, normalized to the overlay's width (height follows the aspect ratio; hit tests are circular in vp space).
const LS_CX: number = 0.18;
const LS_CY: number = 0.72;
const LS_R: number = 0.15;
const RS_CX: number = 0.60;
const RS_CY: number = 0.66;
const RS_R: number = 0.10;

// The overlay's touch zones. Priority = array order (first match wins), sticks last so a finger aimed at a button never grabs a stick.
enum PadRole { LS, RS, A, B, X, Y, L, R, ZL, ZR, Plus, Minus, Up, Down, Left, Right }
interface PadZone {
  role: PadRole;
  cx: number;
  cy: number;
  r: number;
  bit: number;
  label: string;
}

@Entry
@Component
struct Index {
  private controller: XComponentController = new XComponentController();
  private started: boolean = false;
  private starting: boolean = false;   // the first start is in flight: a second onLoad (rotation during a multi-GB first staging) must not stage beside it
  private isGame: boolean = false;
  @State status: string = 'dm: waiting for the surface';   // also the only way to tell a screenshot of this window from a blank one (a surface with no frame is black)
  private overlayW: number = 1280;   // the overlay's size in vp: touch is normalized by this, the hit test lives here, the pad draws here
  private overlayH: number = 720;
  private aspect: number = 720 / 1280;   // overlayH / overlayW: zone radii are normalized to the width, so the y arm is r * aspect

  // Virtual gamepad state. Touch events arrive on the UI thread and dmhost.setPad is lock-free, so state is pushed on every change without a
  // timer: the game's controller read sees the newest state (the host holds it in atomics). Buttons are ORed; sticks are per-zone and the
  // most-deflected zone wins, matching the Mac front end's stick semantics.
  private padButtons: number = 0;
  private lx: number = 0;
  private ly: number = 0;
  private rx: number = 0;
  private ry: number = 0;
  // Per-finger tracking: which zone a finger grabbed (id → role), which button bits it holds, and which finger owns each stick (the last one
  // to press a stick wins). Buttons are ORed from every finger's bits on every push, so two fingers on one button release it only when both lift.
  private fingerZones: Map<number, PadRole> = new Map();
  private fingerButtons: Map<number, number> = new Map();
  private lsOwner: number = -1;
  private rsOwner: number = -1;
  private keyBits: number = 0;   // keyboard bits live apart from touch bits; pushPad ORs both
  // The overlay draws the pad on a Canvas above the surface. CanvasRenderingContext2D/RenderingContextSettings are global declarations.
  private ctx: CanvasRenderingContext2D = new CanvasRenderingContext2D(new RenderingContextSettings(true));
  private readonly zones: PadZone[] = [
    { role: PadRole.A, cx: 0.86, cy: 0.70, r: 0.050, bit: DM_BTN_A, label: 'A' },
    { role: PadRole.B, cx: 0.77, cy: 0.79, r: 0.050, bit: DM_BTN_B, label: 'B' },
    { role: PadRole.X, cx: 0.77, cy: 0.61, r: 0.050, bit: DM_BTN_X, label: 'X' },
    { role: PadRole.Y, cx: 0.68, cy: 0.70, r: 0.050, bit: DM_BTN_Y, label: 'Y' },
    { role: PadRole.ZL, cx: 0.24, cy: 0.20, r: 0.040, bit: DM_BTN_ZL, label: 'ZL' },
    { role: PadRole.ZR, cx: 0.76, cy: 0.20, r: 0.040, bit: DM_BTN_ZR, label: 'ZR' },
    { role: PadRole.L, cx: 0.30, cy: 0.28, r: 0.040, bit: DM_BTN_L, label: 'L' },
    { role: PadRole.R, cx: 0.70, cy: 0.28, r: 0.040, bit: DM_BTN_R, label: 'R' },
    { role: PadRole.Plus, cx: 0.92, cy: 0.42, r: 0.036, bit: DM_BTN_PLUS, label: '+' },
    { role: PadRole.Minus, cx: 0.08, cy: 0.42, r: 0.036, bit: DM_BTN_MINUS, label: '-' },
    { role: PadRole.Up, cx: 0.50, cy: 0.16, r: 0.035, bit: DM_BTN_UP, label: '▲' },
    { role: PadRole.Down, cx: 0.50, cy: 0.26, r: 0.035, bit: DM_BTN_DOWN, label: '▼' },
    { role: PadRole.Left, cx: 0.44, cy: 0.21, r: 0.035, bit: DM_BTN_LEFT, label: '◀' },
    { role: PadRole.Right, cx: 0.56, cy: 0.21, r: 0.035, bit: DM_BTN_RIGHT, label: '▶' },
  ];
  // Keyboard → gamepad. Buttons are ORed into padButtons; Enter and space map to A (confirm), Backspace to B (cancel), Esc to Plus (pause),
  // the DPAD and WASD both to the DPAD, and Shift/Control to ZL/ZR.
  private readonly keyMap: Map<number, number> = new Map([
    [2039, DM_BTN_UP], [2035, DM_BTN_DOWN], [2017, DM_BTN_LEFT], [2020, DM_BTN_RIGHT],   // W S A D
    [2012, DM_BTN_UP], [2013, DM_BTN_DOWN], [2014, DM_BTN_LEFT], [2015, DM_BTN_RIGHT],   // DPAD arrows
    [2054, DM_BTN_A], [2052, DM_BTN_A],                                                    // Enter, Space
    [2053, DM_BTN_B],                                                                     // Backspace
    [2047, DM_BTN_ZL], [2048, DM_BTN_ZR],                                                 // Left/Right Shift
    [1, DM_BTN_PLUS],                                                                     // Esc
  ]);

  // The overlay's normalized hit test: a touch point (x/w, y/h, origin top-left). Buttons and the dpad live in zones[];
  // the sticks have their own geometry and are tested separately after them, so a finger aimed at a button never grabs a stick.
  private hitZone(x: number, y: number): PadZone | null {
    for (const z of this.zones) {
      const dx = (x - z.cx) / z.r;
      const dy = (y - z.cy) / (z.r * this.aspect);
      if (dx * dx + dy * dy <= 1.0) return z;
    }
    return null;
  }

  private inStick(x: number, y: number, cx: number, cy: number, r: number): boolean {
    const dx = (x - cx) / r;
    const dy = (y - cy) / (r * this.aspect);
    return dx * dx + dy * dy <= 1.0;
  }

  private handleTouch(event: TouchEvent): void {
    const ty: number = event.type;
    // Normalize by the component's own vp size (touches are reported in vp). onAreaChange keeps overlayW/H in sync for drawing,
    // but the live value here is authoritative. area.width is a Length (string | number): a percentage string turns into NaN here,
    // and NaN > 0 is false, so the fallback below also catches that.
    const aw: number = event.target.area.width as number;
    const ah: number = event.target.area.height as number;
    const w: number = aw > 0 ? aw : this.overlayW;
    const h: number = ah > 0 ? ah : this.overlayH;
    // changedTouches carries only the fingers that moved in this event, and carries their current position; touches is the whole set.
    const changed: TouchObject[] = event.changedTouches ?? [];
    const all: TouchObject[] = event.touches ?? [];
    if (ty === TouchType.Down) {
      for (const t of changed) {
        const x: number = t.x / w;
        const y: number = t.y / h;
        const zone = this.hitZone(x, y);
        if (zone) {
          this.fingerZones.set(t.id, zone.role);
          this.fingerButtons.set(t.id, zone.bit);
          this.padButtons |= zone.bit;
        } else if (this.inStick(x, y, LS_CX, LS_CY, LS_R)) {
          this.lsOwner = t.id;
          this.fingerZones.set(t.id, PadRole.LS);
          this.updateStickFromFinger(t);
        } else if (this.inStick(x, y, RS_CX, RS_CY, RS_R)) {
          this.rsOwner = t.id;
          this.fingerZones.set(t.id, PadRole.RS);
          this.updateStickFromFinger(t);
        }
      }
    } else if (ty === TouchType.Move) {
      // Move events list the whole touch set; update each stick from its owning finger.
      for (const t of all) {
        if (t.id === this.lsOwner) this.updateStickFromFinger(t);
        if (t.id === this.rsOwner) this.updateStickFromFinger(t);
      }
    } else if (ty === TouchType.Up || ty === TouchType.Cancel) {
      for (const t of changed) {
        const role: PadRole | undefined = this.fingerZones.get(t.id);
        this.fingerZones.delete(t.id);
        this.fingerButtons.delete(t.id);
        if (role === PadRole.LS || role === PadRole.RS) {
          if (t.id === this.lsOwner) {
            this.lsOwner = -1;
            this.lx = 0;
            this.ly = 0;
          }
          if (t.id === this.rsOwner) {
            this.rsOwner = -1;
            this.rx = 0;
            this.ry = 0;
          }
        } else {
          // Rebuild padButtons from every remaining finger so a shared button clears only when all of its fingers lift.
          this.padButtons = 0;
          for (const bit of this.fingerButtons.values()) this.padButtons |= bit;
        }
      }
    }
    this.pushPad();
  }

  private updateStickFromFinger(t: TouchObject): void {
    if (t.id === this.lsOwner) {
      this.lx = this.stickDelta(t.x, t.y, LS_CX, LS_CY, LS_R, this.overlayW, this.overlayH);
      this.ly = -this.stickDelta(t.x, t.y, LS_CX, LS_CY, LS_R, this.overlayW, this.overlayH);
    } else if (t.id === this.rsOwner) {
      this.rx = this.stickDelta(t.x, t.y, RS_CX, RS_CY, RS_R, this.overlayW, this.overlayH);
      this.ry = -this.stickDelta(t.x, t.y, RS_CX, RS_CY, RS_R, this.overlayW, this.overlayH);
    }
  }

  private stickDelta(x: number, y: number, cx: number, cy: number, r: number, w: number, h: number): number {
    const nx: number = (x - cx * w) / (r * w);
    const ny: number = (y - cy * h) / (r * w);   // the stick's radius is normalized to the width, so the y arm uses the same pixel radius
    const n: number = Math.sqrt(nx * nx + ny * ny);
    if (n < 0.05) return 0;
    const mag: number = Math.min(n, 1.0);
    return Math.round((nx / n) * mag * DM_STICK_MAX);
  }

  private pushPad(): void {
    dmhost.setPad(this.padButtons | this.keyBits, this.lx, this.ly, this.rx, this.ry);
  }

  // The pad is drawn only to show where the zones are: the game reads no touch, so the overlay is a capture-safe translucent guide.
  private drawPad(): void {
    const w: number = this.overlayW;
    const h: number = this.overlayH;
    if (w <= 0 || h <= 0) return;
    const c: CanvasRenderingContext2D = this.ctx;
    c.clearRect(0, 0, w, h);
    this.drawStick(c, w, h, LS_CX, LS_CY, LS_R, this.lx, this.ly);
    this.drawStick(c, w, h, RS_CX, RS_CY, RS_R, this.rx, this.ry);
    for (const z of this.zones) {
      const px: number = z.cx * w;
      const py: number = z.cy * h;
      const pr: number = z.r * w;
      c.beginPath();
      c.arc(px, py, pr, 0, Math.PI * 2);
      c.fillStyle = 'rgba(255,255,255,0.10)';
      c.fill();
      c.strokeStyle = 'rgba(255,255,255,0.35)';
      c.lineWidth = 1;
      c.stroke();
      c.fillStyle = 'rgba(255,255,255,0.60)';
      c.font = `${Math.round(pr * 0.9)}px sans-serif`;
      c.textAlign = 'center';
      c.textBaseline = 'middle';
      c.fillText(z.label, px, py);
    }
  }

  private drawStick(c: CanvasRenderingContext2D, w: number, h: number, cx: number, cy: number, r: number, vx: number, vy: number): void {
    const px: number = cx * w;
    const py: number = cy * h;
    const pr: number = r * w;
    c.beginPath();
    c.arc(px, py, pr, 0, Math.PI * 2);
    c.fillStyle = 'rgba(255,255,255,0.08)';
    c.fill();
    c.strokeStyle = 'rgba(255,255,255,0.35)';
    c.lineWidth = 1;
    c.stroke();
    // The knob follows the stick value; ly is up-positive, the screen y is down-positive, so the knob's y is inverted.
    const kx: number = px + (vx / DM_STICK_MAX) * pr * 0.6;
    const ky: number = py - (vy / DM_STICK_MAX) * pr * 0.6;
    c.beginPath();
    c.arc(kx, ky, pr * 0.35, 0, Math.PI * 2);
    c.fillStyle = 'rgba(255,255,255,0.30)';
    c.fill();
  }

  private onKey(event: KeyEvent): void {
    const key: number = event.keyCode;
    const down: boolean = event.type === KeyType.Down;
    const bit: number | undefined = this.keyMap.get(key);
    if (bit !== undefined) {
      this.keyBits = down ? (this.keyBits | bit) : (this.keyBits & ~bit);
      this.pushPad();
    }
  }

  // rmdir() answers 13900032 (Directory not empty) for a folder that has content — it is not recursive — so the old data set is walked and removed
  // bottom-up. false: nothing there. A removal that fails throws, and stageData turns that into 'fail': a half-cleared folder must not be recorded.
  private removeTree(path: string): boolean {
    let isDir: boolean = false;
    try {
      isDir = fs.statSync(path).isDirectory();
    } catch (e) {
      return false;
    }
    if (!isDir) {
      fs.unlinkSync(path);
      return true;
    }
    for (const name of fs.listFileSync(path)) {
      this.removeTree(path + '/' + name);
    }
    fs.rmdirSync(path);
    return true;
  }

  // A build that carries its own data packs the game under resources/rawfile/dm and lists it in dm.files.tsv (rawfile directories cannot be
  // listed at run time). The first start — or a start after the data set changed (its stamp is the file's header) — copies it into files/dm,
  // which is what startGame reads. Files are streamed through a fixed buffer: a RomFS holds files of hundreds of MB.
  // 'none': the build carries no data; 'ok': files/dm is complete; 'fail': the copy broke (a partial pack must not be started).
  private async stageData(root: string): Promise<string> {
    const ctx = this.getUIContext().getHostContext();
    if (!ctx) return 'none';
    let text: string = '';
    try {
      const man = await ctx.resourceManager.getRawFileContent('dm.files.tsv');
      text = util.TextDecoder.create('utf-8').decodeWithStream(man);
    } catch (e) {
      // no manifest: a build that carries no data pack (the canonical one), not a broken pack: the self-test scene is the right thing to run
      dmhost.log('staging: this build carries no data pack');
      return 'none';
    }
    try {
      const lines = text.split('\n');
      const stamp: string = lines.length && lines[0].startsWith('# DM_DATA ') ? lines[0].substring(10).trim() : '';
      const stampPath: string = root + '/.stamp';
      let cur: string = '';
      try {
        cur = fs.readTextSync(stampPath).trim();
      } catch (e) {
        cur = '';
      }
      if (stamp.length && cur === stamp) return 'ok';
      try {
        fs.mkdirSync(root, true);
      } catch (e) {
      }
      // A new data set replaces the old one whole: the pack owns these three folders (porter/datapack.py OWNED), and a file an older version had, or a
      // DLC the pack no longer carries, would otherwise stay behind and be read as if the pack still had it.
      let cleared: number = 0;
      for (const owned of ['mods', 'RomFS', 'aoc']) {
        if (!this.removeTree(root + '/' + owned)) continue;   // an error here stops the stage: a half-cleared folder must not be called done
        cleared++;
      }
      if (cleared) dmhost.log(`staging: removed the previous data set (${cleared} folders)`);
      let n: number = 0;
      const chunk: ArrayBuffer = new ArrayBuffer(1 << 20);
      // A pack larger than the 4 GB one signed HAP can carry is split over feature modules (data1, data2, ...): the manifest's third column names the
      // module that holds the file, and that module's resourceManager hands out its rawfd (createModuleContext is what reaches a sibling module's files).
      const rms: Map<string, resourceManager.ResourceManager> = new Map();
      for (let i = 1; i < lines.length; i++) {
        const line: string = lines[i];
        if (!line.length) continue;
        const parts: string[] = line.split('\t');
        const rel: string = parts[0];
        const mod: string = parts.length > 2 ? parts[2] : 'entry';
        const dst: string = root + '/' + rel;
        const dir: string = dst.substring(0, dst.lastIndexOf('/'));
        try {
          fs.mkdirSync(dir, true);
        } catch (e) {
        }
        let rm: resourceManager.ResourceManager = ctx.resourceManager;
        if (mod !== 'entry' && mod.length) {
          let part: resourceManager.ResourceManager | undefined = rms.get(mod);
          if (part === undefined) {
            part = ctx.createModuleContext(mod).resourceManager;
            rms.set(mod, part);
          }
          rm = part;
        }
        const rf = await rm.getRawFd('dm/' + rel);
        const f = fs.openSync(dst, fs.OpenMode.CREATE | fs.OpenMode.READ_WRITE | fs.OpenMode.TRUNC);
        try {
          // ReadOptions.offset is counted from the file pointer, which reading advances (start position = filePointer + offset), so seeking once and
          // then reading without an offset is the only form that stays correct across chunks
          fs.lseek(rf.fd, rf.offset, fs.WhenceType.SEEK_SET);
          let done: number = 0;
          while (done < rf.length) {
            const want: number = Math.min(chunk.byteLength, rf.length - done);
            const got: number = fs.readSync(rf.fd, chunk, { length: want });
            if (got <= 0) break;
            fs.writeSync(f.fd, chunk, { length: got });
            done += got;
          }
          if (done !== rf.length) throw new Error(`short copy of ${rel}: ${done}/${rf.length}`);
        } finally {
          fs.closeSync(f);
          await rm.closeRawFd('dm/' + rel);
        }
        n++;
        if (n % 200 === 0) dmhost.log(`staged ${n} files`);
      }
      const sf = fs.openSync(stampPath, fs.OpenMode.CREATE | fs.OpenMode.READ_WRITE | fs.OpenMode.TRUNC);
      try {
        fs.writeSync(sf.fd, stamp);
      } finally {
        fs.closeSync(sf);
      }
      dmhost.log(`staged ${n} files (stamp ${stamp.substring(0, 12)})`);
      return 'ok';
    } catch (e) {
      // a business exception carries only {code, message}: String(e) is '[object Object]' and says nothing (measured on this page's first versions)
      const why: string = e instanceof Error ? e.message : JSON.stringify(e);
      dmhost.log(`staging failed: ${why}`);
      return 'fail';
    }
  }

  // The surface is reported on every load: a rotation or a trip to the background destroys the XComponent and hands us a new one.
  private reportSurface(): void {
    const ctx = this.getUIContext().getHostContext();
    const files: string = ctx?.filesDir ?? '';
    const root: string = files + '/dm';
    const surfaceId: string = this.controller.getXComponentSurfaceId();
    // the surface's own pixels, not the layout's vp and not the display's size (which is the whole screen in split screen or a free-form window)
    let w: number = 0;
    let h: number = 0;
    try {
      const r = this.controller.getXComponentSurfaceRect();
      w = Math.round(r.surfaceWidth);
      h = Math.round(r.surfaceHeight);
    } catch (e) {
      dmhost.log(`surface rect failed: ${e}`);
    }
    if (!w || !h) {
      try {
        const d = display.getDefaultDisplaySync();
        w = d.width;
        h = d.height;
      } catch (e) {
        dmhost.log(`display query failed: ${e}`);
      }
    }
    if (!w || !h) {
      w = 1280;
      h = 720;
    }
    dmhost.log(`surfaceId=${surfaceId} ${w}x${h} root=${root}`);
    this.status = `dm: surface ${w}x${h}`;
    dmhost.setSurfaceId(surfaceId, w, h);
  }

  private async startOnce(): Promise<void> {
    if (this.starting || (this.started && this.isGame)) return;   // a game owns the native thread for the life of the process
    this.starting = true;
    const ctx = this.getUIContext().getHostContext();
    const files: string = ctx?.filesDir ?? '';
    const cache: string = ctx?.cacheDir ?? '';
    const root: string = files + '/dm';
    const staged: string = await this.stageData(root);
    let hasGame: boolean = false;
    try {
      // the packed layout mirrors the Android pack: mods/ (the rewritten modules) and RomFS/ beside env.txt and shaders.dmsv
      hasGame = staged !== 'fail' && fs.accessSync(root + '/mods/main.patched.bin') && fs.accessSync(root + '/RomFS');
    } catch (e) {
      hasGame = false;
    }
    if (hasGame) {
      this.isGame = true;
      this.status = 'dm: game';
      dmhost.log('starting the game');
      dmhost.startGame(root, files + '/save', cache);
    } else {
      if (staged === 'fail') dmhost.log('the data pack is incomplete: the game is not started');
      else if (!hasGame) dmhost.log('no game in the sandbox, starting the self-test scene');
      this.isGame = false;
      this.status = staged === 'fail' ? 'dm: self-test (the pack did not stage)' : 'dm: self-test';
      dmhost.stopDemo();   // a surface that came back after onDestroy starts the scene once more
      dmhost.startDemo();
    }
    this.started = true;
    this.starting = false;
  }

  build() {
    Stack() {
      XComponent({ id: 'dmhost', type: XComponentType.SURFACE, controller: this.controller })
        .width('100%')
        .height('100%')
        .onLoad(() => {
          this.reportSurface();
          this.startOnce();
        })
        .onDestroy(() => {
          dmhost.stopDemo();
          dmhost.surfaceLost();
        })
      // The translucent pad guide over the surface. It is drawn once the Canvas is sized (onReady) and again whenever the
      // layout size changes (onAreaChange); touches and keys are handled on the Stack, so the guide never needs to know them.
      Canvas(this.ctx)
        .width('100%')
        .height('100%')
        .onReady(() => this.drawPad())
      // one line at the bottom, over the surface: what this build is doing, and proof for a screenshot that the window itself is on screen
      Text(this.status)
        .fontSize(12)
        .fontColor('#808080')
        .align(Alignment.BottomStart)
        .margin({ left: 12, bottom: 8 })
    }
    .width('100%')
    .height('100%')
    .backgroundColor(Color.Black)
    // The Stack is the input layer: it takes the focus (the first focusable on load, and on touch) so a keyboard reaches onKeyEvent,
    // its onTouch feeds the pad, and onAreaChange keeps the overlay's vp size in sync for both hit tests and drawing.
    .focusable(true)
    .focusOnTouch(true)
    .defaultFocus(true)
    .onAreaChange((oldValue: Area, newValue: Area) => {
      const w = newValue.width as number;
      const h = newValue.height as number;
      if (w > 0 && h > 0) {
        this.overlayW = w;
        this.overlayH = h;
        this.aspect = h / w;
        this.drawPad();
      }
    })
    .onTouch((event: TouchEvent) => this.handleTouch(event))
    .onKeyEvent((event: KeyEvent) => this.onKey(event))
  }
}
