/*
 * Index.ets --the game page: one full-screen surface with a transparent virtual gamepad over it.
 *
 * The XComponent is the only way to get a native window that VK_OHOS_surface can present to (XComponentType.TEXTURE is not one), and this
 * SDK has no napi_value form of the XComponent callbacks any more, so the surface id is taken from the controller and handed to the native
 * module --the same path the M0 probe proved on the device.
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
import { preferences } from '@kit.ArkData';
import { http } from '@kit.NetworkKit';
import { picker } from '@kit.CoreFileKit';
import { importPack, readPackHeader, readStamp, writeStamp, removeTree, PackInfo, ImportProgress } from './PackImport';

// The dev data import's loopback port (P2.1): devpush.py serves the pack here and `hdc fport` forwards the
// device's 127.0.0.1:IMPORT_PORT to it, so a 14 GB pack reaches the sandbox without a HAP module.
const IMPORT_PORT: number = 28899;

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
// The serialized zone (preferences): the bit and label are rebuilt from the role on load.
interface SavedZone {
  role: PadRole;
  cx: number;
  cy: number;
  r: number;
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
  // Per-finger tracking: which zone a finger grabbed (id ->role), which button bits it holds, and which finger owns each stick (the last one
  // to press a stick wins). Buttons are ORed from every finger's bits on every push, so two fingers on one button release it only when both lift.
  private fingerZones: Map<number, PadRole> = new Map();
  private fingerButtons: Map<number, number> = new Map();
  private lsOwner: number = -1;
  private rsOwner: number = -1;
  private keyBits: number = 0;   // keyboard bits live apart from touch bits; pushPad ORs both
  // The overlay draws the pad on a Canvas above the surface. CanvasRenderingContext2D/RenderingContextSettings are global declarations.
  private ctx: CanvasRenderingContext2D = new CanvasRenderingContext2D(new RenderingContextSettings(true));

  // ---- P1.4 pad configuration: per-game default from the pack, user layout persisted in preferences ----
  // The zones are the drawn/hit geometry. cx/cy are normalized to the overlay (0..1), r to the width; the y arm is r * aspect.
  // Zones are mutable here: the user's layout (edit mode) moves them, and preferences persist the result per game.
  private zones: PadZone[] = [
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
    { role: PadRole.Up, cx: 0.50, cy: 0.16, r: 0.035, bit: DM_BTN_UP, label: 'UP' },
    { role: PadRole.Down, cx: 0.50, cy: 0.26, r: 0.035, bit: DM_BTN_DOWN, label: 'DN' },
    { role: PadRole.Left, cx: 0.44, cy: 0.21, r: 0.035, bit: DM_BTN_LEFT, label: 'LT' },
    { role: PadRole.Right, cx: 0.56, cy: 0.21, r: 0.035, bit: DM_BTN_RIGHT, label: 'RT' },
  ];
  // Sticks stay geometric constants of the layout; only the button zones are user-movable (a moved stick needs its
  // own dead-zone math and is P2 polish; the profile's layout decides whether they show at all).
  private padLayout: string = 'full';       // 'full' (sticks + buttons) | 'touch' (buttons only, profile-keyed)
  private padOpacity: number = 1.0;        // user opacity multiplier for the drawn guide (0.3..1)
  private padScale: number = 1.0;          // user size multiplier for every zone radius
  @State editMode: boolean = false;         // layout editing on: touches move zones instead of pressing them
  // ---- P2.1 import surface: the launcher shows when the sandbox holds no pack, and the import runs here ----
  @State showLauncher: boolean = false;     // the "no game yet" panel with the Add Game button
  @State importing: boolean = false;        // an import is running (the panel shows the progress)
  @State importFiles: number = 0;           // files written so far
  @State importPct: number = 0;             // 0..100, from the tar bytes consumed
  @State importNote: string = '';           // the current file, or the error after a failed import
  private editZone: PadZone | null = null; // the zone a finger is dragging in edit mode
  private longPressTimer: number = -1;    // a press-and-hold on a zone in play mode enters edit mode
  private prefs: preferences.Preferences | null = null;   // per-game pad layout store
  private static readonly PREF_ZONES = 'zones';
  private static readonly PREF_OPACITY = 'opacity';
  private static readonly PREF_SCALE = 'scale';

  // The default zones as packed (the literal above); restoring the default layout in edit mode starts from here.
  // ArkTS strict mode has no object spread: copy the fields explicitly into a new PadZone each.
  private readonly defaultZones: PadZone[] = this.zones.map((z: PadZone): PadZone =>
    ({ role: z.role, cx: z.cx, cy: z.cy, r: z.r, bit: z.bit, label: z.label }));

  // Load the per-game default (pad_default.json, written by mkhap from the profile's pad_layout/op_mode) and the
  // user's saved layout on top. The title id is the pack's folder name under files/dm (nx2a-data.json names it too).
  private async loadPadConfig(root: string): Promise<void> {
    const ctx = this.getUIContext().getHostContext();
    try {
      if (ctx) {
        // the pack's default: rawfile, the same resourceManager the staging reads
        const rm = ctx.createModuleContext('entry').resourceManager;
        const raw: Uint8Array = await rm.getRawFileContent('pad_default.json');
        const j: object = JSON.parse(new util.TextDecoder().decodeWithStream(raw));
        const layout = (j as Record<string, Object>)['layout'];
        if (typeof layout === 'string') this.padLayout = layout;
      }
    } catch (e) {
      // no pad_default.json in the pack: keep the compiled-in default ('full')
    }
    try {
      if (ctx) {
        const names: string[] = fs.listFileSync(root);
        const tid = names.find((n: string): boolean => /^[0-9A-F]{16}$/.test(n));
        const titleId = tid !== undefined ? tid : 'default';
        // preferences: the per-game store; the file lives in the app's preferences dir
        this.prefs = await preferences.getPreferences(ctx, 'pad_' + titleId);
        const saved = this.prefs.getSync(Index.PREF_ZONES, '') as string;
        if (saved.length) {
          const zs: object[] = JSON.parse(saved) as object[];
          // adopt by role: the saved set wins for every role it names, sizes and all
          for (const o of zs) {
            const s = o as Record<string, Object>;
            const role = s['role'] as number;
            const hit = this.zones.find((q: PadZone): boolean => q.role === role);
            if (hit !== undefined) { hit.cx = s['cx'] as number; hit.cy = s['cy'] as number; hit.r = s['r'] as number; }
          }
        }
        const op: preferences.ValueType = this.prefs.getSync(Index.PREF_OPACITY, 1.0);
        const sc: preferences.ValueType = this.prefs.getSync(Index.PREF_SCALE, 1.0);
        if (typeof op === 'number' && op > 0) this.padOpacity = op;
        if (typeof sc === 'number' && sc > 0) this.padScale = sc;
      }
    } catch (e) {
      // preferences unavailable: the compiled defaults stand, edits just do not survive
    }
    this.drawPad();
  }

  private savePadConfig(): void {
    if (!this.prefs) return;
    try {
      // ArkTS strict: no untyped object literals - a declared shape for the serialized zone
      const saved: SavedZone[] = this.zones.map((z: PadZone): SavedZone =>
        ({ role: z.role, cx: z.cx, cy: z.cy, r: z.r }));
      this.prefs.putSync(Index.PREF_ZONES, JSON.stringify(saved));
      this.prefs.putSync(Index.PREF_OPACITY, this.padOpacity);
      this.prefs.putSync(Index.PREF_SCALE, this.padScale);
      this.prefs.flush();
      dmhost.log(`pad layout saved (${this.zones.length} zones, x${this.padScale.toFixed(2)}, ${(this.padOpacity * 100) | 0}%)`);
    } catch (e) {
      dmhost.log(`pad layout save failed: ${e}`);
    }
  }
  // Keyboard ->gamepad. Buttons are ORed into padButtons; Enter and space map to A (confirm), Backspace to B (cancel), Esc to Plus (pause),
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

    // ---- edit mode: touches move zones instead of pressing them (P1.4) ----
    if (this.editMode) {
      if (ty === TouchType.Down) {
        for (const t of changed) {
          const x: number = t.x / w;
          const y: number = t.y / h;
          const zone = this.hitZone(x, y);
          if (zone) {
            // A second finger on the dragged zone resizes it: track both, the pinch distance changes r.
            if (this.editZone === zone && this.editPinch === -1 && t.id !== this.editDragFinger) {
              this.editPinch = t.id;
              this.editPinchStart = this.editDist(t, all, w, h) / Math.max(zone.r, 0.001);
            } else if (!this.editZone) {
              this.editZone = zone;
              this.editDragFinger = t.id;
            }
          } else {
            // empty space: nothing to edit, but the touch must not reach the game either
          }
        }
      } else if (ty === TouchType.Move) {
        if (this.editZone) {
          for (const t of all) {
            if (t.id === this.editDragFinger) {
              // the zone's center follows the finger (clamped into the overlay)
              this.editZone.cx = Math.min(0.99, Math.max(0.01, t.x / w));
              this.editZone.cy = Math.min(0.99, Math.max(0.01, t.y / h));
            } else if (t.id === this.editPinch) {
              // the pinch finger's distance to the drag finger scales the radius
              const d: number = this.editDist(t, all, w, h);
              const nr: number = Math.min(0.15, Math.max(0.02, d / Math.max(this.editPinchStart, 0.001)));
              this.editZone.r = nr;
            }
          }
          this.drawPad();
        }
      } else if (ty === TouchType.Up || ty === TouchType.Cancel) {
        for (const t of changed) {
          if (t.id === this.editDragFinger) { this.editZone = null; this.editDragFinger = -1; }
          if (t.id === this.editPinch) this.editPinch = -1;
        }
      }
      return;   // edit mode never reaches the game
    }

    // ---- play mode ----
    if (ty === TouchType.Down) {
      for (const t of changed) {
        const x: number = t.x / w;
        const y: number = t.y / h;
        const zone = this.hitZone(x, y);
        if (zone) {
          this.fingerZones.set(t.id, zone.role);
          this.fingerButtons.set(t.id, zone.bit);
          this.padButtons |= zone.bit;
          // A press-and-hold on a zone enters edit mode (P1.4): hold past LONG_PRESS_MS with no move, and the layout editor opens
          if (this.longPressTimer === -1 && this.isGame) {
            const zRole: PadRole = zone.role;
            this.longPressTimer = setTimeout(() => {
              this.longPressTimer = -1;
              // the hold must still own this finger and nothing else may have joined
              if (this.fingerZones.size === 1 && this.fingerZones.get(t.id) === zRole) {
                for (const r of this.fingerZones.keys()) { this.fingerZones.delete(r); this.fingerButtons.delete(r); }
                this.padButtons = 0;
                this.pushPad();
                this.editMode = true;
                this.editZone = null;
                this.editDragFinger = -1;
                this.editPinch = -1;
                dmhost.log('pad layout editor on (drag to move, second finger to resize, +/End to save)');
              }
            }, Index.LONG_PRESS_MS);
          }
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
      // A move cancels the pending long-press (the finger went somewhere else)
      if (this.longPressTimer !== -1) { clearTimeout(this.longPressTimer); this.longPressTimer = -1; }
      // Move events list the whole touch set; update each stick from its owning finger.
      for (const t of all) {
        if (t.id === this.lsOwner) this.updateStickFromFinger(t);
        if (t.id === this.rsOwner) this.updateStickFromFinger(t);
      }
    } else if (ty === TouchType.Up || ty === TouchType.Cancel) {
      if (this.longPressTimer !== -1) { clearTimeout(this.longPressTimer); this.longPressTimer = -1; }
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

  // Edit-mode helpers: the distance between the two editing fingers in normalized units, and the long-press budget.
  private editDragFinger: number = -1;
  private editPinch: number = -1;
  private editPinchStart: number = 1.0;
  private static readonly LONG_PRESS_MS: number = 800;

  private editDist(t: TouchObject, all: TouchObject[], w: number, h: number): number {
    for (const o of all) {
      if (o.id === this.editDragFinger) {
        const dx: number = (t.x - o.x) / w;
        const dy: number = (t.y - o.y) / h;
        return Math.sqrt(dx * dx + dy * dy * this.aspect * this.aspect) / Math.max(this.aspect, 0.001);
      }
    }
    return 0;
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
  // The user's opacity multiplies every alpha (P1.4); the size multiplier pads every radius at draw time (zones stay normalized).
  private drawPad(): void {
    const w: number = this.overlayW;
    const h: number = this.overlayH;
    if (w <= 0 || h <= 0) return;
    const c: CanvasRenderingContext2D = this.ctx;
    c.clearRect(0, 0, w, h);
    const op: number = this.padOpacity;
    const drawSticks: boolean = this.padLayout !== 'touch';
    if (drawSticks) {
      this.drawStick(c, w, h, LS_CX, LS_CY, LS_R, this.lx, this.ly);
      this.drawStick(c, w, h, RS_CX, RS_CY, RS_R, this.rx, this.ry);
    }
    for (const z of this.zones) {
      const px: number = z.cx * w;
      const py: number = z.cy * h;
      const pr: number = z.r * this.padScale * w;
      c.beginPath();
      c.arc(px, py, pr, 0, Math.PI * 2);
      c.fillStyle = `rgba(255,255,255,${(this.editMode ? 0.18 : 0.10) * op})`;
      c.fill();
      c.strokeStyle = `rgba(255,255,255,${(this.editMode ? 0.80 : 0.35) * op})`;
      c.lineWidth = 1;
      c.stroke();
      c.fillStyle = `rgba(255,255,255,${0.60 * op})`;
      c.font = `${Math.round(pr * 0.9)}px sans-serif`;
      c.textAlign = 'center';
      c.textBaseline = 'middle';
      c.fillText(z.label, px, py);
    }
  }

  // Leave edit mode: save by default (the drag ends are the layout the user wanted); Del+End discards.
  private exitEditMode(save: boolean): void {
    this.editMode = false;
    this.editZone = null;
    this.editDragFinger = -1;
    this.editPinch = -1;
    if (save) {
      this.savePadConfig();
    } else {
      // restore the pack default for every role
      for (const z of this.zones) {
        const d = this.defaultZones.find(q => q.role === z.role);
        if (d) { z.cx = d.cx; z.cy = d.cy; z.r = d.r; }
      }
      this.padOpacity = 1.0;
      this.padScale = 1.0;
      if (this.prefs) {
        try {
          this.prefs.deleteSync(Index.PREF_ZONES);
          this.prefs.deleteSync(Index.PREF_OPACITY);
          this.prefs.deleteSync(Index.PREF_SCALE);
          this.prefs.flush();
          dmhost.log('pad layout restored to the pack default');
        } catch (e) { /* preferences read-only here: the in-memory restore stands */ }
      }
    }
    this.drawPad();
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
    // In edit mode keys belong to the editor: Esc (or Enter) saves and leaves; nothing reaches the game (P1.4).
    if (this.editMode) {
      if (down && (key === 1 || key === 2054)) this.exitEditMode(true);   // Esc, Enter
      return;
    }
    const bit: number | undefined = this.keyMap.get(key);
    if (bit !== undefined) {
      this.keyBits = down ? (this.keyBits | bit) : (this.keyBits & ~bit);
      this.pushPad();
    }
  }

  // rmdir() answers 13900032 (Directory not empty) for a folder that has content --it is not recursive --so the old data set is walked and removed
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
  // listed at run time). The first start --or a start after the data set changed (its stamp is the file's header) --copies it into files/dm,
  // which is what startGame reads. Files are streamed through a fixed buffer: a RomFS holds files of hundreds of MB.
  // 'none': the build carries no data; 'ok': files/dm is complete; 'fail': the copy broke (a partial pack must not be started).
  private async stageData(root: string): Promise<string> {
    const ctx = this.getUIContext().getHostContext();
    if (!ctx) return 'none';
    let text: string = '';
    // ---- P2.1 dev data import over loopback, single stream ----
    // The shell domain cannot write the app sandbox and the app cannot read /data/local/tmp (both measured: the
    // SELinux labels data_local_tmp vs debug_hap_data_file do not meet). The pack comes over http instead:
    // devpush serves ONE .nxpk and `hdc rport` forwards the device's loopback to it. The app streams that file to
    // a temp path, then imports it through the same tar reader the picker path uses (PackImport.importPack), so
    // the developer round and the user path share every line of the importer.
    let importBase: string = '';   // '' = no import channel
    dmhost.log('staging: probing the loopback channel');
    try {
      const probe = http.createHttp();
      const r = await probe.request('http://127.0.0.1:' + IMPORT_PORT + '/ping', { method: http.RequestMethod.GET, connectTimeout: 1500, readTimeout: 1500 });
      probe.destroy();
      dmhost.log(`staging: probe answered ${r.responseCode}`);
      if (r.responseCode === 200) importBase = 'http://127.0.0.1:' + IMPORT_PORT;
    } catch (e) {
      // no server: the ordinary rawfile path stands (or the self-test scene)
      const why: string = e instanceof Error ? e.message : JSON.stringify(e);
      dmhost.log('staging: no loopback channel (' + why.substring(0, 80) + ')');
    }
    if (importBase.length) {
      // Stamp first, download second: the container's 80-byte header carries the same stamp the sandbox keeps,
      // so a round whose data is already imported costs one tiny request instead of 14.75 GB. This is what makes
      // a debug round (ship the 4.6 MB entry, restart) independent of the 14.75 GB of game data.
      try {
        const probe2 = http.createHttp();
        const r2 = await probe2.request(importBase + '/stamp', {
          method: http.RequestMethod.GET,
          expectDataType: http.HttpDataType.ARRAY_BUFFER,
          connectTimeout: 5000,
          readTimeout: 10000
        });
        probe2.destroy();
        if (r2.responseCode === 200 && r2.result instanceof ArrayBuffer) {
          const hb: Uint8Array = new Uint8Array(r2.result as ArrayBuffer);
          if (hb.length >= 80) {
            let magic: string = '';
            for (let i = 0; i < 4; i++) magic += String.fromCharCode(hb[i]);
            if (magic === 'NXPK') {
              let st: string = '';
              for (let i = 24; i < 24 + 48; i++) {
                if (hb[i] === 0) break;
                st += String.fromCharCode(hb[i]);
              }
              const cur0: string = readStamp(root);
              dmhost.log(`staging: server pack stamp ${st.substring(0, 12)}, sandbox ${cur0.substring(0, 12) || 'empty'}`);
              if (st.length && cur0.length && st === cur0) {
                dmhost.log('staging: stamp match, skipping the download entirely');
                return 'ok';
              }
            }
          }
        }
      } catch (e) {
        // no /stamp (an older server): fall through to the download, which validates the header itself
      }
      // the single-stream path: requestInStream writes each received chunk straight to disk (an ARRAY_BUFFER
      // result would buffer all 14.75 GB in memory - measured: it dies at ~52 MB with code 2300023), then the
      // shared tar reader imports the temp file.
      // Resume: the download lands in .part and is kept across failures. The next attempt measures it and sends
      // "Range: bytes=<have>-", so a 14.75 GB pull over a flaky link continues where it stopped instead of
      // starting over (the server answers 206 with Content-Range; see pack_server.py).
      let tmp: string = '';
      try {
        const cache: string = this.getUIContext().getHostContext()?.cacheDir ?? '';
        tmp = cache + '/pack.nxpk';
        const part: string = tmp + '.part';
        // A container already downloaded by an earlier round is the cheapest source of all: its header says
        // whether it is complete, so skip both the download and the resume bookkeeping.
        try {
          const done: number = fs.statSync(tmp).size;
          if (done > 80) {
            const infoD: PackInfo = readPackHeader(tmp);
            if (done === 80 + infoD.tarSize) {
              const curD: string = readStamp(root);
              if (curD.length && curD === infoD.stamp) {
                dmhost.log('staging: the cached pack matches the sandbox stamp, nothing to do');
                return 'ok';
              }
              dmhost.log(`staging: using the cached container (${Math.floor(done / (1 << 20))} MB, stamp ${infoD.stamp.substring(0, 12)})`);
              this.importing = true;
              this.showLauncher = true;
              this.importPct = 0;
              this.importFiles = 0;
              this.importNote = 'unpacking the cached pack';
              const nC: number = await importPack(tmp, root, infoD, (p: ImportProgress) => {
                this.importFiles = p.files;
                this.importPct = p.totalBytes > 0 ? Math.min(100, Math.floor(p.bytes * 100 / p.totalBytes)) : 0;
                this.importNote = p.currentName.length ? p.currentName : 'finishing';
              });
              writeStamp(root, infoD.stamp);
              this.importing = false;
              this.importNote = '';
              dmhost.log(`staging: unpacked ${nC} files from the cached pack`);
              return 'ok';
            }
            dmhost.log(`staging: the cached pack is incomplete (${done} of ${80 + infoD.tarSize})`);
          }
        } catch (e) {
          // no cached container: the download path runs
        }
        // how much of a previous attempt survives
        let have: number = 0;
        try {
          have = fs.statSync(part).size;
        } catch (e) {
          have = 0;
        }
        if (have > 0) {
          dmhost.log(`staging: resuming the download at ${have} bytes`);
        } else {
          try { fs.unlinkSync(part); } catch (e) { }
        }
        const h = http.createHttp();
        let outFile: fs.File | null = null;
        let wrote: number = have;
        let writeErr: string = '';
        let watchdog: number = -1;
        try {
          // append when resuming, truncate when starting fresh
          outFile = fs.openSync(part, have > 0
            ? (fs.OpenMode.CREATE | fs.OpenMode.READ_WRITE | fs.OpenMode.APPEND)
            : (fs.OpenMode.CREATE | fs.OpenMode.READ_WRITE | fs.OpenMode.TRUNC));
          dmhost.log(`staging: download target ${part} (fd ${outFile.fd}, from ${have})`);
          // Progress is driven from dataReceive, not from dataReceiveProgress: the latter does not fire for
          // requestInStream on this device (measured: the panel sat at 0% through 5.5 GB), while every chunk
          // arrives in dataReceive. expectTotal comes from the container header once 80 bytes are in.
          let expectTotal: number = 0;
          let lastByteAt: number = Date.now();
          h.on('dataReceive', (data: ArrayBuffer) => {
            if (!outFile || writeErr.length) return;
            try {
              fs.writeSync(outFile.fd, data);
              wrote += data.byteLength;
              lastByteAt = Date.now();
            } catch (e) {
              writeErr = e instanceof Error ? e.message : JSON.stringify(e);
              return;
            }
            if (expectTotal === 0 && wrote >= 80) {
              // read the header back to learn the whole file's size (80 + tarSize)
              try {
                const info0: PackInfo = readPackHeader(part);
                expectTotal = 80 + info0.tarSize;
              } catch (e) {
              }
            }
            if (expectTotal > 0) {
              this.importPct = Math.min(100, Math.floor(wrote * 100 / expectTotal));
              this.importNote = `downloading ${Math.floor(wrote / (1 << 20))} / ${Math.floor(expectTotal / (1 << 20))} MB`;
            } else {
              this.importNote = `downloading ${Math.floor(wrote / (1 << 20))} MB`;
            }
          });
          h.on('dataReceiveProgress', (p: http.DataReceiveProgressInfo) => {
            const total: number = p.totalSize > 0 ? p.totalSize : 0;
            const sofar: number = p.receiveSize;
            if (total > 0) {
              this.importPct = Math.min(100, Math.floor((have + sofar) * 100 / (have + total)));
              this.importNote = `downloading ${Math.floor((have + sofar) / (1 << 20))} / ${Math.floor((have + total) / (1 << 20))} MB`;
            }
            if (sofar > 0 && Math.floor(sofar / (128 << 20)) !== Math.floor((sofar - 1) / (128 << 20))) {
              dmhost.log(`staging: downloaded ${Math.floor((have + sofar) / (1 << 20))} MB`);
            }
          });
          this.importing = true;
          this.importPct = 0;
          this.showLauncher = true;   // the panel carries the progress: show it while the pack streams in
          this.importNote = have > 0 ? `resuming at ${Math.floor(have / (1 << 20))} MB` : 'downloading the pack';
          dmhost.log('staging: requestInStream begins');
          // A TCP half-open socket (the WiFi link drops, the device's socket stays Established) delivers nothing
          // and never errors: measured, the pull sat at 11.32 GB for 17 minutes with a live connection. This
          // watchdog destroys the request after 60 s without a byte, which surfaces as an error, keeps .part, and
          // the next start resumes from exactly there.
          watchdog = setInterval(() => {
            if (Date.now() - lastByteAt > 60000) {
              dmhost.log(`staging: no data for 60 s at ${wrote} bytes - restarting the pull (resumes from .part)`);
              try { h.destroy(); } catch (e) { }
            }
          }, 10000);
          let code: number = 0;
          try {
            const opts: http.HttpRequestOptions = {
              method: http.RequestMethod.GET,
              connectTimeout: 10000,
              // readTimeout is the whole request's budget, not one read: 14.75 GB at the measured 4 MB/s is
              // ~62 minutes, and a 1-hour budget expired with every byte received (dlSz == Content-Length) and
              // still threw 2300028. Six hours covers a slow link with room to spare.
              readTimeout: 21600000
            };
            if (have > 0) {
              opts.header = { 'Range': `bytes=${have}-` };
            }
            code = await h.requestInStream(importBase + '/pack.nxpk', opts);
          } catch (e) {
            // A timeout after the last byte is still a complete file: keep it and let the tar reader judge it.
            const why: string = e instanceof Error ? e.message : JSON.stringify(e);
            dmhost.log(`staging: requestInStream raised (${why.substring(0, 60)}), wrote ${wrote} bytes`);
          }
          dmhost.log(`staging: requestInStream -> ${code}, total ${wrote} bytes${writeErr.length ? ', write error: ' + writeErr : ''}`);
          if (writeErr.length) throw new Error('writing the pack failed: ' + writeErr);
          if (code !== 0 && code !== 200 && code !== 206) throw new Error(`GET /pack.nxpk -> ${code}`);
          if (wrote < 80) throw new Error(`the pack is too short (${wrote} bytes)`);
          // The container's header says how long the file must be. A short file stays as .part and the next
          // attempt resumes it with a Range request; only a complete one is renamed and handed to the reader.
          const hdr: PackInfo = readPackHeader(part);
          const expect: number = 80 + hdr.tarSize;
          dmhost.log(`staging: have ${wrote} of ${expect} bytes (${Math.floor(wrote * 100 / expect)}%)`);
          if (wrote < expect) {
            throw new Error(`incomplete download (${wrote} of ${expect} bytes) - the next start resumes it`);
          }
          try { fs.renameSync(part, tmp); } catch (e) { }
        } finally {
          clearInterval(watchdog);
          h.off('dataReceive');
          h.off('dataReceiveProgress');
          h.destroy();
          if (outFile) {
            try { fs.closeSync(outFile); } catch (e) { }
          }
        }
        if (wrote > 0) {
          const info: PackInfo = readPackHeader(tmp);
          const cur: string = readStamp(root);
          dmhost.log(`staging: pack ${info.title} stamp ${info.stamp.substring(0, 12)} (sandbox ${cur.substring(0, 12) || 'empty'})`);
          if (cur.length && cur === info.stamp) {
            dmhost.log('staging: stamp match, the sandbox already holds this pack');
            try { fs.unlinkSync(tmp); } catch (e) { }
            this.importing = false;
            return 'ok';
          }
          this.importNote = 'importing the pack';
          this.importPct = 0;
          this.importFiles = 0;
          const n: number = await importPack(tmp, root, info, (p: ImportProgress) => {
            this.importFiles = p.files;
            this.importPct = p.totalBytes > 0 ? Math.min(100, Math.floor(p.bytes * 100 / p.totalBytes)) : 0;
            this.importNote = p.currentName.length ? p.currentName : 'finishing';
          });
          writeStamp(root, info.stamp);
          this.importing = false;
          this.importNote = '';
          dmhost.log(`staging: imported ${n} files over the loopback (stamp ${info.stamp.substring(0, 12)})`);
          try { fs.unlinkSync(tmp); } catch (e) { }
          return 'ok';
        }
      } catch (e) {
        const why: string = e instanceof Error ? e.message : JSON.stringify(e);
        this.importing = false;
        dmhost.log('staging: the loopback pack import failed: ' + why);
        if (tmp.length) { try { fs.unlinkSync(tmp); } catch (e2) { } }
      }
    }
    if (!text.length) {
      try {
        const man = await ctx.resourceManager.getRawFileContent('dm.files.tsv');
        text = util.TextDecoder.create('utf-8').decodeWithStream(man);
      } catch (e) {
        // no manifest: a build that carries no data pack (the canonical one), not a broken pack: the self-test scene is the right thing to run
        dmhost.log('staging: this build carries no data pack');
        return 'none';
      }
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
        // The loopback import: fetch the file over http into the sandbox (the shell domain cannot write here,
        // and the app cannot read the shell's tmp - the loopback is the one channel both sides reach)
        if (importBase.length) {
          const h = http.createHttp();
          try {
            const url: string = importBase + '/' + rel;
            const r = await h.request(url, { method: http.RequestMethod.GET, expectDataType: http.HttpDataType.ARRAY_BUFFER, connectTimeout: 10000, readTimeout: 120000 });
            if (r.responseCode !== 200 || !(r.result instanceof ArrayBuffer)) throw new Error(`GET ${rel} -> ${r.responseCode}`);
            const buf: ArrayBuffer = r.result as ArrayBuffer;
            const f = fs.openSync(dst, fs.OpenMode.CREATE | fs.OpenMode.READ_WRITE | fs.OpenMode.TRUNC);
            try {
              fs.writeSync(f.fd, buf);
            } finally {
              fs.closeSync(f);
            }
          } finally {
            h.destroy();
          }
          n++;
          if (n % 2000 === 0) dmhost.log(`staged ${n} files`);
          continue;
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

  /**
   * P2.1: pick a .nxpk with the system document picker and import it into the sandbox.
   *
   * The picker needs no permission declaration: it is a system UI that hands the app a URI it may read. The
   * import itself is a single sequential pass over the tar (PackImport.importPack), so a 41k-file pack costs one
   * read instead of 41k requests. The stamp makes a repeat pick a no-op.
   */
  private async addGame(): Promise<void> {
    if (this.importing) return;
    this.importNote = '';
    let uri: string = '';
    try {
      const ctx = this.getUIContext().getHostContext();
      if (!ctx) throw new Error('no host context');
      const opt = new picker.DocumentSelectOptions();
      opt.maxSelectNumber = 1;
      opt.fileSuffixFilters = ['Game pack|.nxpk'];
      const viewPicker = new picker.DocumentViewPicker(ctx);
      // the picker's document method is select() (the document options pick the document UI, not the photo one)
      const uris: string[] = await viewPicker.select(opt);
      if (!uris || uris.length === 0) return;   // the user cancelled
      uri = uris[0];
    } catch (e) {
      this.importNote = `the picker failed: ${e}`;
      dmhost.log(this.importNote);
      return;
    }
    const ctx = this.getUIContext().getHostContext();
    const files: string = ctx?.filesDir ?? '';
    const root: string = files + '/dm';
    this.importing = true;
    this.importFiles = 0;
    this.importPct = 0;
    this.importNote = 'reading the pack header';
    try {
      const info: PackInfo = readPackHeader(uri);
      dmhost.log(`pack: title ${info.title}, stamp ${info.stamp.substring(0, 12)}, tar ${info.tarSize} bytes`);
      const cur: string = readStamp(root);
      if (cur.length && cur === info.stamp) {
        this.importNote = `this pack is already imported (stamp ${info.stamp.substring(0, 12)})`;
        dmhost.log('import: stamp match, nothing to do');
        this.importing = false;
        this.showLauncher = false;
        await this.startOnce();
        return;
      }
      this.importNote = 'importing';
      const n: number = await importPack(uri, root, info, (p: ImportProgress) => {
        this.importFiles = p.files;
        this.importPct = p.totalBytes > 0 ? Math.min(100, Math.floor(p.bytes * 100 / p.totalBytes)) : 0;
        this.importNote = p.currentName.length ? p.currentName : 'finishing';
      });
      writeStamp(root, info.stamp);
      this.importNote = `imported ${n} files (stamp ${info.stamp.substring(0, 12)})`;
      dmhost.log(`import: ${n} files, stamp ${info.stamp.substring(0, 12)}`);
      this.importing = false;
      this.showLauncher = false;
      this.started = false;   // let startOnce run again now that data is present
      await this.startOnce();
    } catch (e) {
      const why: string = e instanceof Error ? e.message : JSON.stringify(e);
      this.importNote = `import failed: ${why}`;
      dmhost.log(`import failed: ${why}`);
      this.importing = false;
    }
  }

  private async startOnce(): Promise<void> {
    if (this.starting || (this.started && this.isGame)) return;   // a game owns the native thread for the life of the process
    this.starting = true;
    const ctx = this.getUIContext().getHostContext();
    const files: string = ctx?.filesDir ?? '';
    const cache: string = ctx?.cacheDir ?? '';
    const root: string = files + '/dm';
    dmhost.log(`startOnce: root=${root} starting=${this.starting} started=${this.started} isGame=${this.isGame}`);
    // P2.1: the sandbox is the source of truth. A pack imported through the picker (or a loopback round) leaves
    // its stamp in .stamp, and the game starts straight from it - no HAP staging, no per-file copy, no import.
    const staged: string = await this.stageData(root);
    dmhost.log('startOnce: stageData -> ' + staged);
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
      this.loadPadConfig(root);   // P1.4: the pack's default layout and the user's saved layout (async, draws when loaded)
      dmhost.startGame(root, files + '/save', cache);
    } else {
      if (staged === 'fail') dmhost.log('the data pack is incomplete: the game is not started');
      else if (!hasGame) dmhost.log('no game in the sandbox, starting the self-test scene');
      this.isGame = false;
      this.status = staged === 'fail' ? 'dm: self-test (the pack did not stage)' : 'dm: self-test';
      this.showLauncher = true;   // no data: the launcher is where the user adds a pack (P2.1)
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
      // ---- P2.1 launcher: shown when the sandbox holds no pack. The user picks a .nxpk with the system
      // document picker and the import streams it into the sandbox (one sequential pass, progress below).
      if (this.showLauncher) {
        Column({ space: 14 }) {
          Text('No game data yet').fontSize(20).fontColor('#FFFFFF')
          Text('Pick a .nxpk game pack to import it. It is copied once; later starts need no import.')
            .fontSize(13).fontColor('#B0B0B0').textAlign(TextAlign.Center).width(420)
          if (!this.importing) {
            Button('Add game (.nxpk)').fontSize(15).height(40).width(200)
              .onClick(() => { this.addGame(); })
          } else {
            Progress({ value: this.importPct, total: 100, type: ProgressType.Linear })
              .width(360).height(8)
            Text(`${this.importPct}%  -  ${this.importFiles} files`)
              .fontSize(13).fontColor('#E0E0E0')
            Text(this.importNote).fontSize(11).fontColor('#909090')
              .maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis }).width(360)
          }
          if (this.importNote.length && !this.importing) {
            Text(this.importNote).fontSize(12).fontColor('#FFA0A0')
              .textAlign(TextAlign.Center).width(420)
          }
        }
        .align(Alignment.Center)
        .padding(20)
        .borderRadius(12)
        .backgroundColor('rgba(18,18,26,0.90)')
      }
      // one line at the bottom, over the surface: what this build is doing, and proof for a screenshot that the window itself is on screen
      Text(this.status)
        .fontSize(12)
        .fontColor('#808080')
        .align(Alignment.BottomStart)
        .margin({ left: 12, bottom: 8 })
      // ---- P1.4 layout editor: visible only in edit mode. Drag a zone to move it, a second finger to resize; the
      // sliders tune the whole pad (opacity = the guide's alpha, scale = every radius); Save keeps the layout per
      // game (preferences), Reset restores the pack default, and Esc leaves saving what is on screen.
      if (this.editMode) {
        Column({ space: 10 }) {
          Text('pad layout editor - drag to move, second finger to resize')
            .fontSize(14).fontColor('#FFFFFF').margin({ top: 10 })
          Row({ space: 14 }) {
            Text(`opacity ${(this.padOpacity * 100) | 0}%`).fontSize(12).fontColor('#E0E0E0').width(96)
            Slider({ value: this.padOpacity * 100, min: 20, max: 100 })
              .width(220).onChange((v: number) => { this.padOpacity = v / 100; this.drawPad(); })
          }
          Row({ space: 14 }) {
            Text(`size x${this.padScale.toFixed(2)}`).fontSize(12).fontColor('#E0E0E0').width(96)
            Slider({ value: this.padScale * 100, min: 60, max: 160 })
              .width(220).onChange((v: number) => { this.padScale = v / 100; this.drawPad(); })
          }
          Row({ space: 16 }) {
            Button('Save & exit').fontSize(14).height(36)
              .onClick(() => this.exitEditMode(true))
            Button('Reset').fontSize(14).height(36)
              .onClick(() => this.exitEditMode(false))
            Button('Exit (keep, unsaved)').fontSize(14).height(36)
              .onClick(() => { this.editMode = false; this.drawPad(); })
          }
        }
        .align(Alignment.Top)
        .margin({ top: 14 })
        .padding(12)
        .borderRadius(10)
        .backgroundColor('rgba(20,20,28,0.72)')
      }
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
