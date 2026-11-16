/*
 * LibraryPage.ets - the game library, the product's home screen (S2).
 *
 * Opens on the games that are installed rather than launching the last one: the library is the product, and a
 * player who wants to switch should not have to fight the app for it.
 *
 * Layout follows the width: four square covers across in landscape, three or two as the window narrows, and a
 * list of rows in portrait. Focus moves with the gamepad's arrows and never wraps at an edge, so the end of the
 * grid reads as the end rather than as a page turn.
 */

import dmhost from 'libdmhost.so';
import { picker } from '@kit.CoreFileKit';
import { router } from '@kit.ArkUI';
import { preferences } from '@kit.ArkData';
import { Bg, Text as TextToken, Semantic, CoverPalette, Space, Font, Radius, Motion, Size } from '../theme/Tokens';
import { GameEntry, gamesDir, listGames, deleteGame, humanSize, writeMeta, ImportStatus, verifyGame, gameVersion, VerifyResult } from './GameLibrary';
import { readPackHeader, importPack, readStamp, writeStamp, PackInfo, ImportProgress } from './PackImport';
import { saveDir, backupDir, packSave, importSave, readSaveHeader, saveSize, hasBackup, SaveInfo, SaveProgress } from './SaveData';
import { GameCard, CardState } from '../components/GameCard';
import { AddGameCard } from '../components/AddGameCard';
import { EmptyState } from '../components/EmptyState';
import { TopBar } from '../components/TopBar';
import { ImportBanner } from '../components/ImportBanner';
import { ImportPanel } from '../components/ImportPanel';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { GameDetailSheet } from '../components/GameDetailSheet';
import { RemotePacksSection } from '../components/RemotePacksSection';
import { RemoteDownloads } from '../components/RemoteDownloads';

/** A grid cell: either a game or the add tile. */
interface Cell {
  isAdd: boolean;
  game: GameEntry | null;
}

@Entry
@Component
struct LibraryPage {
  @State games: GameEntry[] = [];
  @State loading: boolean = true;
  /** Which cell the gamepad/keyboard pointer is on; -1 means the top bar's add button. */
  @State focusIndex: number = 0;
  /** Columns in the current layout: 1 (portrait list), 2, 3 or 4. */
  @State columns: number = 4;
  @State landscape: boolean = true;
  /** The card edge in vp, derived from the width and the column count. */
  @State coverSize: number = Size.cardCoverLandscape;
  @State viewWidth: number = 0;
  @State viewHeight: number = 0;
  /** The title ID currently launching, or '' - the card shows its overlay. */
  @State launchingTitle: string = '';
  /** A message shown at the bottom after a failure. */
  @State toast: string = '';
  private toastTimer: number = -1;
  private loaded: boolean = false;

  // ---- S3: the import surface ----
  @State importPct: number = 0;
  @State importName: string = '';
  @State importDone: number = 0;
  @State importTotal: number = 0;
  @State importRemaining: string = '';
  @State importFile: string = '';
  @State showPanel: boolean = false;
  /** The confirm dialog: '', 'cancelImport', 'replaceData'. */
  @State confirmKind: string = '';
  /** The pack a confirm is pending on. */
  private pendingUri: string = '';
  private pendingInfo: PackInfo | null = null;
  private importTimer: number = -1;
  /** Whether the running import wants cancelling; the loop checks between phases. */
  @State importCancelled: boolean = false;

  // ---- S4: a remote download, mirrored into the same banner ----
  // The download itself lives in RemoteDownloads (module level), so leaving this page does not stop it or lose its
  // progress; these three fields only carry it into the strip under the top bar.
  @State remoteBusy: boolean = false;
  @State remotePct: number = 0;
  @State remoteName: string = '';
  private remoteTimer: number = -1;

  // ---- S4: the detail sheet ----
  @State detailVisible: boolean = false;
  @State detailGame: GameEntry | null = null;
  @State detailVersion: string = '';
  @State detailVerifyNote: string = '';
  @State detailVerifyOk: boolean = true;
  @State detailVerifying: boolean = false;
  /** The title a delete confirm is pending on, or ''. */
  @State pendingDelete: string = '';
  // ---- P2.3: saves ----
  @State saveBusy: boolean = false;
  @State saveNote: string = '';
  /** The pack waiting on a "replace this save?" answer. */
  private pendingSaveUri: string = '';
  private pendingSaveTitle: string = '';

  aboutToAppear(): void {
    this.reload();
    // S4: the download runs in RemoteDownloads, which outlives this page; the strip under the top bar mirrors it.
    // Polled rather than pushed: the registry notifies on every chunk, which is far more often than a strip needs.
    this.remoteTimer = setInterval(() => {
      const busy: boolean = RemoteDownloads.busy();
      const pct: number = RemoteDownloads.activePercent();
      const name: string = RemoteDownloads.activeName();
      if (busy !== this.remoteBusy) this.remoteBusy = busy;
      if (pct !== this.remotePct) this.remotePct = pct;
      if (name !== this.remoteName) this.remoteName = name;
    }, 500);
  }

  aboutToDisappear(): void {
    if (this.remoteTimer >= 0) { clearInterval(this.remoteTimer); this.remoteTimer = -1; }
  }

  /** Read the library. A cache hit per game makes this a few file reads, not a 41k-file walk. */
  private reload(): void {
    const ctx = this.getUIContext().getHostContext();
    const files: string = ctx?.filesDir ?? '';
    const t0: number = Date.now();
    this.games = listGames(files);
    const ms: number = Date.now() - t0;
    console.info(`library: ${this.games.length} game(s) in ${ms} ms`);
    if (this.games.length === 0) this.focusIndex = 0;
    else if (this.focusIndex >= this.games.length + 1) this.focusIndex = this.games.length;
    this.loading = false;
    this.loaded = true;
  }

  /**
   * Recompute the layout for a new window size.
   *
   * The cover is sized from BOTH axes. A phone reports its window in vp, which on this device is 842x373 for a
   * 2844x1260 panel (a 3.375 density): a grid sized from the width alone produced 377 vp covers in a 373 vp tall
   * window, so the caption fell outside the visible area entirely.
   */
  private relayout(w: number, h: number): void {
    if (w <= 0 || h <= 0) return;
    this.viewWidth = w;
    this.viewHeight = h;
    this.landscape = w > h;
    let cols: number;
    if (!this.landscape || w < 600) {
      cols = 1;
    } else if (w < 900) {
      cols = 2;
    } else if (w < 1200) {
      cols = 3;
    } else {
      cols = 4;
    }
    this.columns = cols;

    const side: number = Space.s6;                  // the content's left and right padding
    const gap: number = 28;                         // the breathing room a rounded cover needs
    const barHeight: number = this.landscape ? Size.topBar : 56;
    const usableW: number = w - side * 2;
    const usableH: number = h - barHeight - Space.s5 - Space.s4;   // below the bar, above the gesture area
    // the caption block's height, reserved before the cover takes its share
    const caption: number = 56;
    // rows that fit, at least one
    const rows: number = Math.max(1, Math.floor((usableH + gap) / (150 + caption + gap)));
    const fromWidth: number = cols === 1
      ? Math.min(usableW, 720)
      : (usableW - gap * (cols - 1)) / cols;
    const fromHeight: number = (usableH - gap * (rows - 1)) / rows - caption;
    const raw: number = Math.min(fromWidth, fromHeight);
    // never smaller than a legible tile, never larger than the width allows
    this.coverSize = Math.floor(Math.max(80, Math.min(raw, fromWidth)));
    console.info(`library: w=${w} h=${h} cols=${cols} rows=${rows} usable=${usableW}x${usableH} cover=${this.coverSize}`);
  }

  /** The cells in display order: the games, then the add tile. */
  private cells(): Cell[] {
    const out: Cell[] = [];
    for (const g of this.games) out.push({ isAdd: false, game: g });
    out.push({ isAdd: true, game: null });
    return out;
  }

  /** Move the focus. Arrow keys stop at the edges: wrapping makes the end of the grid look like a page turn. */
  private moveFocus(dx: number, dy: number): void {
    const n: number = this.games.length + 1;   // + the add tile
    if (n <= 0) return;
    const cols: number = this.columns;
    let i: number = this.focusIndex;
    if (dx !== 0) {
      const row: number = Math.floor(i / cols);
      const col: number = i % cols;
      const want: number = col + dx;
      if (want < 0 || want >= cols) return;             // same row, edge: stop
      const target: number = row * cols + want;
      if (target >= n) return;                          // past the last cell
      i = target;
    } else if (dy !== 0) {
      const target: number = i + dy * cols;
      if (target < 0) return;
      if (target >= n) {
        // the row below is short: land on its last cell instead of refusing to move
        const last: number = n - 1;
        if (Math.floor(i / cols) === Math.floor(last / cols)) return;
        i = last;
      } else {
        i = target;
      }
    }
    this.focusIndex = i;
  }

  /** Activate a cell: launch a game, or open the picker for the add tile. */
  private activate(cell: Cell): void {
    if (cell.isAdd) {
      this.pickPack();
      return;
    }
    const g: GameEntry = cell.game as GameEntry;
    if (!g.hasData) {
      this.showToast('游戏数据不完整，请重新导入');
      return;
    }
    this.launch(g);
  }

  /**
   * Start a game.
   *
   * The library has no surface: only Index owns the XComponent the guest presents to. So the launch is a handoff -
   * the chosen title is written to preferences, Index is pushed, and its onLoad reads the title and calls
   * startGame once its surface is up. Calling startGame from here would start the guest headless (measured: "no
   * window after 12 s").
   */
  private launch(g: GameEntry): void {
    if (this.launchingTitle.length > 0) return;
    this.launchingTitle = g.titleId;
    console.info(`library: handing off ${g.titleId} to the game page`);
    this.saveLaunchTarget(g.titleId).then((_v: void) => {
      router.pushUrl({ url: 'pages/Index' }).catch((e: Error) => {
        this.launchingTitle = '';
        this.showToast('无法启动游戏页面');
        console.error(`library: pushUrl failed: ${e.message}`);
      });
    });
  }

  /** Record which title the game page should start, so it survives the page change. */
  private async saveLaunchTarget(titleId: string): Promise<void> {
    try {
      const ctx = this.getUIContext().getHostContext();
      if (!ctx) return;
      const p = await preferences.getPreferences(ctx, 'library');
      p.putSync('launchTitle', titleId);
      await p.flush();
    } catch (e) {
      console.error(`library: could not record the launch target: ${e}`);
    }
  }

  /**
   * Pick a pack and import it (S3).
   *
   * Three endings, cheapest first: a stamp match returns without touching a byte; a different pack for an
   * installed title asks before replacing; otherwise the import runs under ImportStatus while the banner and
   * the panel show what it is doing, and the library refreshes when it lands.
   */
  private pickPack(): void {
    this.doPick().then((_ok: void) => {});
  }

  private async doPick(): Promise<void> {
    const ctx = this.getUIContext().getHostContext();
    const files: string = ctx?.filesDir ?? '';
    // ---- the picker ----
    let uri: string = '';
    try {
      if (!ctx) return;
      const opt = new picker.DocumentSelectOptions();
      opt.maxSelectNumber = 1;
      opt.fileSuffixFilters = ['Game pack|.nxpk'];
      const p = new picker.DocumentViewPicker(ctx);
      const uris: string[] = await p.select(opt);
      if (!uris || uris.length === 0) return;   // cancelled
      uri = uris[0];
    } catch (e) {
      this.showToast('选择文件失败，请重试');
      return;
    }
    // ---- the header: reject non-packs before any work ----
    let info: PackInfo;
    try {
      info = readPackHeader(uri);
    } catch (e) {
      this.showToast('这不是游戏包，请选择 .nxpk 文件');
      return;
    }
    const dst: string = gamesDir(files) + '/' + info.title;
    const cur: string = readStamp(dst);
    if (cur.length > 0 && cur === info.stamp) {
      this.showToast('该游戏已是最新版本');
      this.refreshAfterImport(files, info.title);
      return;
    }
    // ---- a different pack for an installed title: ask before replacing ----
    const installed: GameEntry | undefined = this.games.find((g: GameEntry): boolean => g.titleId === info.title);
    if (installed !== undefined && installed.stamp.length > 0) {
      this.pendingUri = uri;
      this.pendingInfo = info;
      this.confirmKind = 'replaceData';
      return;   // the dialog's confirm calls back into this.runImport
    }
    await this.runImport(files, uri, info);
  }

  /** The import itself, with the shared progress state feeding the banner and the panel. */
  private async runImport(files: string, uri: string, info: PackInfo): Promise<void> {
    const dst: string = gamesDir(files) + '/' + info.title;
    ImportStatus.begin(info.title, info.title, 80 + info.tarSize);
    this.importPct = 0;
    this.importName = info.title;
    this.importDone = 0;
    this.importTotal = 80 + info.tarSize;
    this.importRemaining = '';
    this.importFile = '';
    this.showPanel = true;
    this.importCancelled = false;
    this.startImportTimer();
    try {
      const n: number = await importPack(uri, dst, info, (p: ImportProgress) => {
        ImportStatus.tick(p.bytes, 'unpack', p.currentName);
        this.importFile = p.currentName;
      });
      writeStamp(dst, info.stamp);
      writeMeta(dst, info.stamp, info.title, ImportStatus.doneBytes, true, '');
      ImportStatus.finish();
      this.stopImportTimer();
      this.importPct = 100;
      this.showPanel = false;
      this.showToast(`已导入 ${info.title}（${n} 个文件）`);
      this.refreshAfterImport(files, info.title);
    } catch (e) {
      const raw: string = e instanceof Error ? e.message : JSON.stringify(e);
      ImportStatus.fail(this.humanError(raw));
      this.stopImportTimer();
      this.showPanel = false;
      if (this.importCancelled) {
        this.showToast('已取消导入，下次会从断点继续');
      } else {
        this.showToast(this.humanError(raw));
      }
    }
  }

  /** Refresh the list and put the focus on the game that just landed. */
  private refreshAfterImport(files: string, titleId: string): void {
    this.reload();
    const idx: number = this.games.findIndex((g: GameEntry): boolean => g.titleId === titleId);
    if (idx >= 0) this.focusIndex = idx;
  }

  /** Poll ImportStatus into the panel's props; a cancelled flag stops the import between phases. */
  private startImportTimer(): void {
    if (this.importTimer !== -1) clearInterval(this.importTimer);
    this.importTimer = setInterval(() => {
      this.importPct = ImportStatus.percent();
      this.importDone = ImportStatus.doneBytes;
      this.importTotal = ImportStatus.totalBytes;
      this.importRemaining = ImportStatus.remaining();
      if (this.importCancelled) ImportStatus.error = 'cancelled';
    }, 2000);
  }

  private stopImportTimer(): void {
    if (this.importTimer !== -1) {
      clearInterval(this.importTimer);
      this.importTimer = -1;
    }
  }

  /** The user-facing form of a technical failure (the design's §6 table). */
  private humanError(raw: string): string {
    if (raw.includes('2300023') || raw.toLowerCase().includes('enospc')) return '存储空间不足，清理后重试';
    if (raw.includes('2300028')) return '传输超时，下次会从断点继续';
    if (raw.includes('2300018')) return '网络中断，下次会从断点继续';
    if (raw.includes('NXPK')) return '这不是游戏包，请选择 .nxpk 文件';
    return `导入失败：${raw.substring(0, 60)}`;
  }

  /** The confirm dialog's one entry point: which question it asked decides what confirm does. */
  private onConfirm(): void {
    const kind: string = this.confirmKind;
    this.confirmKind = '';
    if (kind === 'replaceData') {
      const ctx = this.getUIContext().getHostContext();
      const files: string = ctx?.filesDir ?? '';
      const uri: string = this.pendingUri;
      const info: PackInfo = this.pendingInfo as PackInfo;
      this.pendingUri = '';
      this.pendingInfo = null;
      if (uri.length > 0 && info !== null) {
        this.runImport(files, uri, info).then((_v: void) => {});
      }
    }
    // 'cancelImport' never confirms here: cancelling an import is the cancel button's job
  }

  private onCancelConfirm(): void {
    this.confirmKind = '';
    this.pendingUri = '';
    this.pendingInfo = null;
  }

  /** The display name of the pack awaiting a replace confirm, for the dialog's body. */
  private pendingTitle(): string {
    const installed: GameEntry | undefined =
      this.games.find((g: GameEntry): boolean => this.pendingInfo !== null && g.titleId === this.pendingInfo.title);
    return installed !== undefined ? installed.name : (this.pendingInfo !== null ? this.pendingInfo.title : '');
  }

  // ---- S4: the detail sheet, its check, and delete ----

  /** Open the sheet for one game. */
  private openDetail(g: GameEntry): void {
    this.detailGame = g;
    this.detailVersion = gameVersion(g.root);
    this.detailVerifyNote = '';
    this.detailVerifyOk = true;
    this.detailVerifying = false;
    this.detailVisible = true;
  }

  /**
   * Check the game's tree. Cheap by design: the loader's two entries plus the metadata and stamp, not a hash of
   * 14 GB (which would take minutes and answer nothing the player asked).
   */
  private verifyDetail(): void {
    const g: GameEntry | null = this.detailGame;
    if (g === null) return;
    this.detailVerifying = true;
    // let the button's disabled state paint before the (fast) filesystem work
    setTimeout(() => {
      const r: VerifyResult = verifyGame(g.root);
      this.detailVerifyNote = r.detail;
      this.detailVerifyOk = r.ok;
      this.detailVerifying = false;
      console.info(`library: verify ${g.titleId} -> ${r.ok ? 'ok' : r.detail}`);
    }, 80);
  }

  /** Ask before deleting: the confirm names the game and the size, and says the saves survive. */
  private askDelete(): void {
    const g: GameEntry | null = this.detailGame;
    if (g === null) return;
    this.pendingDelete = g.titleId;
    this.confirmKind = 'deleteGame';
  }

  /** Delete one game's tree after the confirm. */
  private doDelete(titleId: string): void {
    const ctx = this.getUIContext().getHostContext();
    const files: string = ctx?.filesDir ?? '';
    const g: GameEntry | undefined = this.games.find((x: GameEntry): boolean => x.titleId === titleId);
    const ok: boolean = deleteGame(files, titleId);
    console.info(`library: delete ${titleId} -> ${ok}`);
    this.detailVisible = false;
    this.detailGame = null;
    this.reload();
    if (ok) {
      this.showToast(g !== undefined ? `已删除 ${g.name}` : '已删除');
    } else {
      this.showToast('删除失败，请重试');
    }
  }

  /** The delete confirm's body: the game's name and size, and the reassurance about saves. */
  private deleteBody(): string {
    const g: GameEntry | undefined = this.games.find((x: GameEntry): boolean => x.titleId === this.pendingDelete);
    if (g === undefined) return '将删除该游戏的数据。存档不受影响。';
    return `${g.name} 占用 ${humanSize(g.sizeBytes)}。删除后需要重新导入才能再玩。存档不受影响。`;
  }

  // ---- P2.3: export and import a save ----

  /** Write this game's save to a file the player chooses. */
  private exportSave(): void {
    const g: GameEntry | null = this.detailGame;
    if (g === null || this.saveBusy) return;
    const ctx = this.getUIContext().getHostContext();
    const files: string = ctx?.filesDir ?? '';
    this.saveBusy = true;
    this.saveNote = '正在导出存档…';
    this.doExport(files, g).then((_v: void) => {
      this.saveBusy = false;
    }).catch((e: Error) => {
      this.saveBusy = false;
      this.saveNote = '';
      this.showToast(`导出失败：${e.message}`);
    });
  }

  private async doExport(files: string, g: GameEntry): Promise<void> {
    const ctx = this.getUIContext().getHostContext();
    if (!ctx) return;
    const root: string = saveDir(files, g.titleId);
    const bytes: number = saveSize(root);
    if (bytes === 0) {
      this.saveBusy = false;
      this.saveNote = '';
      this.showToast('这个游戏还没有存档');
      return;
    }
    // A filename the player can recognise later: the game and the day it was written.
    const d: Date = new Date();
    const stamp: string = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, '0')}${String(d.getDate()).padStart(2, '0')}`;
    const safe: string = g.name.replace(/[\\/:*?"<>|]/g, '_').substring(0, 40);
    const opt = new picker.DocumentSaveOptions();
    opt.newFileNames = [`${safe}-${stamp}.nxsave`];
    opt.fileSuffixChoices = ['.nxsave'];
    const p = new picker.DocumentViewPicker(ctx);
    const uris: string[] = await p.save(opt);
    if (!uris || uris.length === 0) {
      this.saveBusy = false;
      this.saveNote = '';
      return;   // cancelled
    }
    const n: number = packSave(root, uris[0], g.titleId, (pr: SaveProgress) => {
      this.saveNote = `正在导出 ${pr.files} 个文件…`;
    });
    this.saveBusy = false;
    this.saveNote = `已导出 ${n} 个文件`;
    this.showToast(`已导出 ${g.name} 的存档（${humanSize(bytes)}）`);
  }

  /** Replace this game's save from a file the player chooses, after confirming. */
  private importSave(): void {
    const g: GameEntry | null = this.detailGame;
    if (g === null || this.saveBusy) return;
    const ctx = this.getUIContext().getHostContext();
    if (!ctx) return;
    const opt = new picker.DocumentSelectOptions();
    opt.maxSelectNumber = 1;
    // No suffix filter: the picker's own categories (Documents, Downloads, Recents) disagree about where a
    // .nxsave lives, and a filtered picker showed an empty list on this device even with the file present in
    // Download. The header check below is the real gate - it rejects anything that is not a save.
    const p = new picker.DocumentViewPicker(ctx);
    p.select(opt).then((uris: string[]) => {
      if (!uris || uris.length === 0) return;
      const uri: string = uris[0];
      let info: SaveInfo;
      try {
        info = readSaveHeader(uri);
      } catch (e) {
        this.showToast('这不是存档文件（没有 NXSAVE 头）');
        return;
      }
      if (info.title !== g.titleId) {
        this.showToast(`这是 ${info.title} 的存档，不是这个游戏的`);
        return;
      }
      this.pendingSaveUri = uri;
      this.pendingSaveTitle = g.titleId;
      this.confirmKind = 'importSave';
    }).catch((e: Error) => {
      this.showToast('选择文件失败');
    });
  }

  /** The import confirm's body: what is replaced, and that the old save is kept. */
  private importSaveBody(): string {
    const g: GameEntry | undefined = this.games.find((x: GameEntry): boolean => x.titleId === this.pendingSaveTitle);
    const name: string = g !== undefined ? g.name : this.pendingSaveTitle;
    const ctx = this.getUIContext().getHostContext();
    const files: string = ctx?.filesDir ?? '';
    const keep: boolean = hasBackup(backupDir(files, this.pendingSaveTitle));
    return keep
      ? `将用文件里的进度替换 ${name} 当前的存档。当前存档会先备份，可以再导入回来。`
      : `将用文件里的进度替换 ${name} 当前的存档。当前存档会先备份到 ${name}.backup，可以再导入回来。`;
  }

  private doImportSave(): void {
    const uri: string = this.pendingSaveUri;
    const titleId: string = this.pendingSaveTitle;
    this.pendingSaveUri = '';
    this.pendingSaveTitle = '';
    if (uri.length === 0) return;
    const ctx = this.getUIContext().getHostContext();
    const files: string = ctx?.filesDir ?? '';
    this.saveBusy = true;
    this.saveNote = '正在导入存档…';
    setTimeout(() => {
      try {
        const n: number = importSave(uri, saveDir(files, titleId), titleId, backupDir(files, titleId),
          (pr: SaveProgress) => {
            this.saveNote = `正在导入 ${pr.files} 个文件…`;
          });
        this.saveBusy = false;
        this.saveNote = `已导入 ${n} 个文件，重新进入游戏生效`;
        this.showToast('存档已导入，重新进入游戏生效');
        // the save size changed: refresh the cached number the sheet shows
        this.detailGame = this.games.find((x: GameEntry): boolean => x.titleId === titleId) ?? this.detailGame;
      } catch (e) {
        const why: string = e instanceof Error ? e.message : JSON.stringify(e);
        this.saveBusy = false;
        this.saveNote = '';
        this.showToast(`导入失败：${why}`);
      }
    }, 60);
  }

  private showToast(msg: string): void {
    this.toast = msg;
    if (this.toastTimer !== -1) clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => {
      this.toast = '';
      this.toastTimer = -1;
    }, 3000);
  }

  /** The state a game's card shows. */
  private cardState(g: GameEntry, index: number): CardState {
    if (this.launchingTitle === g.titleId) return CardState.Launching;
    if (!g.hasData) return CardState.Incomplete;
    if (this.focusIndex === index) return CardState.Focus;
    return CardState.Idle;
  }

  /** The focused game's palette hue, or a neutral slate when the focus is on the add tile. */
  private ambientHue(): string {
    const cells: Cell[] = this.cells();
    const i: number = Math.min(this.focusIndex, cells.length - 1);
    const cell: Cell = cells[i];
    if (cell.isAdd || cell.game === null) return '#334155';
    return CoverPalette.forTitle(cell.game.titleId);
  }

  build() {
    Stack() {
      // ---- ambient light: the focused game tints the room (Apple TV's backdrop) ----
      Column()
        .width('100%')
        .height('100%')
        .linearGradient({
          angle: 160,
          colors: [[CoverPalette.rgba(this.ambientHue(), 0.16), 0.0],
            [CoverPalette.rgba(this.ambientHue(), 0.05), 0.5],
            [CoverPalette.rgba(this.ambientHue(), 0.0), 1.0]]
        })
        .animation({ duration: Motion.duration(400), curve: Motion.curve })

      Column() {
        TopBar({
          showAdd: !this.loading,
          barHeight: this.landscape ? Size.topBar : 56,
          onAdd: () => this.pickPack(),
          onSettings: () => {
            router.pushUrl({ url: 'pages/SettingsPage' }).catch((e: Error) => {
              this.showToast('无法打开设置');
            });
          }
        })

        // ---- the import heartbeat: a strip under the bar while work runs, non-modal ----
        // A remote download uses the same strip (S4): it is the same promise to the player - work is happening,
        // keep browsing - and one strip is easier to read than two.
        if (this.importPct > 0 && this.importPct < 100 && this.importName.length > 0) {
          ImportBanner({
            percent: this.importPct,
            gameName: this.importName
          })
        } else if (this.remoteBusy && this.remotePct > 0) {
          ImportBanner({
            percent: this.remotePct,
            gameName: this.remoteName
          })
        }

      if (this.loading) {
        // a skeleton, not a spinner: the shape of what is coming reads as faster than an empty screen
        Row({ space: Space.s5 }) {
          ForEach([0, 1, 2], (i: number) => {
            Column()
              .width(this.coverSize > 0 ? this.coverSize : 260)
              .height(this.coverSize > 0 ? this.coverSize : 260)
              .backgroundColor(Bg.card)
              .borderRadius(Radius.lg)
              .opacity(0.5)
          }, (i: number) => `${i}`)
        }
        .padding(Space.s6)
        .layoutWeight(1)
        .alignItems(VerticalAlign.Top)
      } else if (this.games.length === 0) {
        Column() {
          EmptyState({
            onAdd: () => this.pickPack(),
            onGuide: () => this.showToast('导入指南见 docs/ohos/GAMEDATA.md')
          })
        }
        .width('100%')
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
      } else {
        this.grid()
      }

      // ---- S4: what a local pack server offers, under the installed games ----
      // The section draws nothing when no source is set, so a device without a server looks exactly as before.
      // It sits here rather than in a settings sub-page because downloading a pack is a library action.
      if (!this.loading) {
        RemotePacksSection({
          // The grid must pick up the new game; the section re-reads its own rows from RemoteDownloads (which set
          // the row to Installed), so a plain reload is enough - no second refresh round trip to the server.
          onImported: () => { this.reload(); }
        })
      }

      if (this.toast.length > 0) {
        Text(this.toast)
          .fontSize(Font.label)
          .fontColor(TextToken.primary)
          .backgroundColor(Bg.elevated)
          .borderRadius(Radius.md)
          .padding({ left: Space.s4, right: Space.s4, top: Space.s3, bottom: Space.s3 })
          .margin({ bottom: Space.s6 })
      }
      }
      .width('100%')
      .height('100%')

      // ---- the import detail sheet: sits at the bottom while the import runs ----
      if (this.showPanel && this.importName.length > 0) {
        Column() {
          Blank()
          ImportPanel({
            percent: this.importPct,
            doneBytes: this.importDone,
            totalBytes: this.importTotal,
            remaining: this.importRemaining,
            currentFile: this.importFile,
            gameName: this.importName,
            onBackground: () => { this.showPanel = false; },
            onCancel: () => { this.confirmKind = 'cancelImport'; }
          })
        }
        .width('100%')
        .height('100%')
      }

      // ---- the confirm dialog: replace-data, cancel-import, or delete-game ----
      ConfirmDialog({
        visible: this.confirmKind.length > 0,
        title: this.confirmKind === 'replaceData' ? '更新游戏数据？'
          : (this.confirmKind === 'deleteGame' ? '删除游戏？'
            : (this.confirmKind === 'importSave' ? '导入存档？' : '取消导入？')),
        body: this.confirmKind === 'replaceData'
          ? `${this.pendingTitle()} 已安装。新数据包将替换旧数据。存档不受影响。`
          : (this.confirmKind === 'deleteGame' ? this.deleteBody()
            : (this.confirmKind === 'importSave' ? this.importSaveBody()
              : '已下载的部分将保留，下次导入从断点继续。')),
        confirmLabel: this.confirmKind === 'replaceData' ? '替换'
          : (this.confirmKind === 'deleteGame' ? '删除'
            : (this.confirmKind === 'importSave' ? '导入' : '取消导入')),
        cancelLabel: this.confirmKind === 'replaceData' ? '取消'
          : (this.confirmKind === 'deleteGame' ? '取消'
            : (this.confirmKind === 'importSave' ? '取消' : '继续导入')),
        destructive: this.confirmKind === 'replaceData' || this.confirmKind === 'deleteGame',
        onConfirm: () => {
          if (this.confirmKind === 'deleteGame') {
            const t: string = this.pendingDelete;
            this.pendingDelete = '';
            this.confirmKind = '';
            this.doDelete(t);
          } else if (this.confirmKind === 'importSave') {
            this.confirmKind = '';
            this.doImportSave();
          } else {
            this.onConfirm();
          }
        },
        onCancel: () => {
          if (this.confirmKind === 'cancelImport') {
            this.importCancelled = true;
          }
          if (this.confirmKind === 'deleteGame') this.pendingDelete = '';
          if (this.confirmKind === 'importSave') {
            this.pendingSaveUri = '';
            this.pendingSaveTitle = '';
          }
          this.onCancelConfirm();
        }
      })

      // ---- the detail sheet ----
      GameDetailSheet({
        visible: this.detailVisible,
        game: this.detailGame,
        version: this.detailVersion,
        verifyNote: this.detailVerifyNote,
        verifyOk: this.detailVerifyOk,
        verifying: this.detailVerifying,
        saveBusy: this.saveBusy,
        saveNote: this.saveNote,
        onExportSave: () => this.exportSave(),
        onImportSave: () => this.importSave(),
        onLaunch: () => {
          const g: GameEntry | null = this.detailGame;
          this.detailVisible = false;
          if (g !== null) this.launch(g);
        },
        onVerify: () => this.verifyDetail(),
        onDelete: () => this.askDelete(),
        onClose: () => {
          this.detailVisible = false;
          this.detailGame = null;
        }
      })
    }
    .width('100%')
    .height('100%')
    .backgroundColor(Bg.base)
    .onAreaChange((_o: Area, n: Area) => {
      this.relayout(n.width as number, n.height as number);
    })
    .focusable(true)
    .defaultFocus(true)
    .onKeyEvent((e: KeyEvent) => {
      if (e.type !== KeyType.Down) return;
      const cells: Cell[] = this.cells();
      if (cells.length === 0) return;
      if (e.keyCode === 2014) { this.moveFocus(-1, 0); return; }   // Left
      if (e.keyCode === 2015) { this.moveFocus(1, 0); return; }    // Right
      if (e.keyCode === 2012) { this.moveFocus(0, -1); return; }   // Up
      if (e.keyCode === 2013) { this.moveFocus(0, 1); return; }    // Down
      if (e.keyCode === 2054 || e.keyCode === 2052) {              // Enter, Space
        const i: number = Math.min(this.focusIndex, cells.length - 1);
        this.activate(cells[i]);
      }
    })
  }

  /** The grid itself. */
  @Builder
  grid() {
    Grid() {
      ForEach(this.cells(), (cell: Cell, index: number) => {
        GridItem() {
          if (cell.isAdd) {
            AddGameCard({
              coverSize: this.coverSize,
              focused: this.focusIndex === index,
              onActivate: () => this.activate(cell)
            })
          } else {
            GameCard({
              name: (cell.game as GameEntry).name,
              titleId: (cell.game as GameEntry).titleId,
              sizeLabel: humanSize((cell.game as GameEntry).sizeBytes),
              iconPath: (cell.game as GameEntry).iconPath,
              coverSize: this.coverSize,
              state: this.cardState(cell.game as GameEntry, index),
              onActivate: () => this.activate(cell),
              onDetails: () => this.openDetail(cell.game as GameEntry)
            })
          }
        }
      }, (cell: Cell, index: number) => cell.isAdd ? 'add' : (cell.game as GameEntry).titleId)
    }
    // Grid sizes its cells; a wrapping Flex measured the cover taller than its own height, which squeezed the
    // caption to nothing (measured: the initial rendered 296 vp tall inside a 260 vp cover).
    .columnsTemplate(this.columnsTemplate())
    .columnsGap(28)
    .rowsGap(28)
    .width('100%')
    .layoutWeight(1)
    .padding({ left: Space.s6, right: Space.s6, top: Space.s5 })
    .scrollBar(BarState.Auto)
  }

  /** One "1fr" per column, so the cards divide the width evenly. */
  private columnsTemplate(): string {
    const parts: string[] = [];
    for (let i = 0; i < this.columns; i++) parts.push('1fr');
    return parts.join(' ');
  }
}
