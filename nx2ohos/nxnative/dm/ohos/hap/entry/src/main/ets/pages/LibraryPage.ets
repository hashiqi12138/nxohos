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
import { Bg, Text as TextToken, Semantic, CoverPalette, Space, Font, Radius, Motion, Size } from '../theme/Tokens';
import { GameEntry, gamesDir, listGames, deleteGame, humanSize, writeMeta, ImportStatus } from './GameLibrary';
import { readPackHeader, importPack, readStamp, writeStamp, PackInfo, ImportProgress } from './PackImport';
import { GameCard, CardState } from '../components/GameCard';
import { AddGameCard } from '../components/AddGameCard';
import { EmptyState } from '../components/EmptyState';
import { TopBar } from '../components/TopBar';
import { ImportBanner } from '../components/ImportBanner';
import { ImportPanel } from '../components/ImportPanel';
import { ConfirmDialog } from '../components/ConfirmDialog';

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

  aboutToAppear(): void {
    this.reload();
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
   * Start a game. The native layer takes the directory and owns the thread for the life of the process, so this
   * hands over and never returns; a failure surfaces within a few seconds if the guest never starts.
   */
  private launch(g: GameEntry): void {
    if (this.launchingTitle.length > 0) return;
    const ctx = this.getUIContext().getHostContext();
    const files: string = ctx?.filesDir ?? '';
    const cache: string = ctx?.cacheDir ?? '';
    this.launchingTitle = g.titleId;
    console.info(`library: launching ${g.titleId} from ${g.root}`);
    dmhost.startGame(g.root, files + '/save', cache);
    // If the guest never reaches nnosInitialize the card would sit on its overlay for good: clear it after a
    // generous window so the player can try again.
    setTimeout(() => {
      if (this.launchingTitle === g.titleId) {
        this.launchingTitle = '';
        this.showToast('启动失败，请检查游戏数据');
      }
    }, 20000);
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
          onAdd: () => this.pickPack()
        })

        // ---- the import heartbeat: a strip under the bar while work runs, non-modal ----
        if (this.importPct > 0 && this.importPct < 100 && this.importName.length > 0) {
          ImportBanner({
            percent: this.importPct,
            gameName: this.importName
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

      // ---- the confirm dialog: replace-data (destructive) or cancel-import (soft) ----
      ConfirmDialog({
        visible: this.confirmKind.length > 0,
        title: this.confirmKind === 'replaceData' ? '更新游戏数据？' : '取消导入？',
        body: this.confirmKind === 'replaceData'
          ? `${this.pendingTitle()} 已安装。新数据包将替换旧数据。存档不受影响。`
          : '已下载的部分将保留，下次导入从断点继续。',
        confirmLabel: this.confirmKind === 'replaceData' ? '替换' : '取消导入',
        cancelLabel: this.confirmKind === 'replaceData' ? '取消' : '继续导入',
        destructive: this.confirmKind === 'replaceData',
        onConfirm: () => this.onConfirm(),
        onCancel: () => {
          if (this.confirmKind === 'cancelImport') {
            this.importCancelled = true;
          }
          this.onCancelConfirm();
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
              onDetails: () => this.showToast('详情在 S4 中实现')
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
