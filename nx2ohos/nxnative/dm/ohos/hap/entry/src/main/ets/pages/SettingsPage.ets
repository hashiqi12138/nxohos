/*
 * SettingsPage.ets - the product's settings (P1.6).
 *
 * The rule this page follows: every row here is wired to something that actually happens. Render scale, frame
 * caps and vsync are absent because the host reads those from a per-game profile at build time, not at runtime;
 * a toggle that changes nothing would be worse than no toggle.
 *
 * Its most important job is not a setting at all: it is the discoverable way into the pad layout editor, which
 * until now could only be reached by holding a virtual button for 800 ms - a gesture nobody finds.
 *
 * The pad's opacity and size live in the same preferences the editor writes ('opacity', 'scale'), so the two
 * screens cannot disagree.
 */

import { Bg, Text as TextToken, Semantic, Space, Font, Radius, Size, Motion } from '../theme/Tokens';
import { humanSize, gamesSize, cacheSize, clearCache, listGames } from './GameLibrary';
import { listSaves, restoreBackup, SaveEntry } from './SaveData';
import { runSaveSelfTest, SelfTestResult } from './SaveSelfTest';
import { listInputDevices, DeviceRow, PAD_KEY_CANDIDATES, PadKeyMap } from './ControllerProbe';
import { preferences } from '@kit.ArkData';
import { PREF_SOURCE, normalizeSource } from './RemoteLibrary';
import { router } from '@kit.ArkUI';
import { fileIo as fs } from '@kit.CoreFileKit';
import { ConfirmDialog } from '../components/ConfirmDialog';

/**
 * The pad's preferences are per game ('pad_<titleId>'): a layout that suits a 2D platformer does not suit a game
 * whose right stick is the camera. The settings page edits the layout of the game that is installed - the one a
 * player would be adjusting for - and falls back to 'pad_default' when there is none.
 */
const PREF_NAME: string = 'pad';
const PREF_LIBRARY: string = 'library';   // the app-wide store: the remote source (P3.3)
const PREF_OPACITY: string = 'opacity';
const PREF_SCALE: string = 'scale';

@Entry
@Component
export struct SettingsPage {
  @State padOpacity: number = 1.0;
  @State padScale: number = 1.0;
  /** Bytes on disk; -1 while the walk runs. */
  @State gamesBytes: number = -1;
  @State cacheBytes: number = -1;
  @State busy: boolean = false;
  @State toast: string = '';
  @State confirmClear: boolean = false;
  // ---- P2.3: the save list ----
  @State saves: SaveEntry[] = [];
  @State savesLoaded: boolean = false;
  /** The title whose backup a confirm would restore, or ''. */
  @State pendingRestore: string = '';
  /** Taps on the version row: five run the save self test, three list the input devices. */
  private versionTaps: number = 0;
  @State selfTestLines: string[] = [];
  /** The controller probe's lines, shown after three taps on the version row. */
  @State padLines: string[] = [];
  /** Which group the nav has selected; the page shows all of them on a narrow screen. */
  @State group: number = 0;
  @State landscape: boolean = true;
  // P3.3: the library's remote source, as typed and as saved ('' = none)
  @State sourceText: string = '';
  @State sourceSaved: string = '';
  private toastTimer: number = -1;
  private prefs: preferences.Preferences | null = null;

  aboutToAppear(): void {
    this.loadPrefs();
    this.measure();
  }

  private async loadPrefs(): Promise<void> {
    try {
      const ctx = this.getUIContext().getHostContext();
      if (!ctx) return;
      // The same store Index.ets reads: 'pad_<titleId>' for the installed game.
      const name: string = PREF_NAME + '_' + this.currentTitleId(ctx.filesDir);
      this.prefs = await preferences.getPreferences(ctx, name);
      const op = this.prefs.getSync(PREF_OPACITY, 1.0) as number;
      const sc = this.prefs.getSync(PREF_SCALE, 1.0) as number;
      if (typeof op === 'number' && op > 0) this.padOpacity = op;
      if (typeof sc === 'number' && sc > 0) this.padScale = sc;
      // P3.3: the library's remote source. It belongs to the app rather than to a game (it lists every pack the
      // server holds), so it lives in its own store, not the per-title pad one.
      const lib = await preferences.getPreferences(ctx, PREF_LIBRARY);
      const src = lib.getSync(PREF_SOURCE, '') as string;
      if (typeof src === 'string' && src.length) { this.sourceSaved = src; this.sourceText = src; }
    } catch (e) {
      // defaults stand
    }
  }

  /** Keep the typed address, normalized ('192.168.1.10' -> 'http://192.168.1.10:28899'), and say what was stored. */
  private async saveSource(): Promise<void> {
    try {
      const ctx = this.getUIContext().getHostContext();
      if (!ctx) return;
      const norm: string = normalizeSource(this.sourceText);
      const lib = await preferences.getPreferences(ctx, PREF_LIBRARY);
      lib.putSync(PREF_SOURCE, norm);
      await lib.flush();
      this.sourceSaved = norm;
      this.sourceText = norm;
      this.toast = norm.length ? ('游戏库来源已保存：' + norm) : '游戏库来源已清除';
    } catch (e) {
      this.toast = '保存失败：' + (e instanceof Error ? e.message : JSON.stringify(e));
    }
  }

  /** The installed game's title ID, or 'default' - the same rule Index.ets uses. */
  private currentTitleId(filesDir: string): string {
    try {
      const names: string[] = fs.listFileSync(filesDir + '/games');
      const tid: string | undefined = names.find((n: string): boolean => /^[0-9A-F]{16}$/.test(n));
      return tid !== undefined ? tid : 'default';
    } catch (e) {
      return 'default';
    }
  }

  private savePref(key: string, value: number): void {
    if (this.prefs === null) return;
    try {
      this.prefs.putSync(key, value);
      this.prefs.flush();
    } catch (e) {
      // a preference that cannot be written only costs the next launch
    }
  }

  /**
   * Measure the sandbox in slices.
   *
   * The walk yields every few hundred files: 41k stat() calls on the UI thread trip HarmonyOS's
   * THREAD_BLOCK_3S watchdog, which force-exits the app - measured on this device, where opening settings killed
   * the process three seconds later.
   */
  private measure(): void {
    this.gamesBytes = -1;
    this.cacheBytes = -1;
    const ctx = this.getUIContext().getHostContext();
    const files: string = ctx?.filesDir ?? '';
    const cache: string = ctx?.cacheDir ?? '';
    this.cacheBytes = cacheSize(files, cache);
    gamesSize(files).then((n: number) => {
      this.gamesBytes = n;
    }).catch((e: Error) => {
      this.gamesBytes = 0;
    });
    this.loadSaves(files);
  }

  /** The save list: one row per game that has one. Cheap - a save is a handful of files. */
  private loadSaves(files: string): void {
    this.savesLoaded = false;
    setTimeout(() => {
      const games = listGames(files);
      const nameOf: (titleId: string) => string = (titleId: string): string => {
        const hit = games.find((g) => g.titleId === titleId);
        return hit !== undefined ? hit.name : titleId;
      };
      this.saves = listSaves(files, nameOf);
      this.savesLoaded = true;
    }, 40);
  }

  /**
   * Five taps on the version row run the save container's round trip.
   *
   * The picker cannot be driven from a test on this device, so this is how the export/import pair is proven:
   * pack the live save, unpack it, compare every byte. Results go to the log and to a toast summary.
   */
  private onVersionTap(): void {
    this.versionTaps++;
    // Three taps list the input devices (P2.4: does a paired gamepad appear as 'joystick'?); five run the save
    // self test. The count resets at either, so the two probes cannot be confused.
    if (this.versionTaps === 3) {
      this.listDevices();
      return;
    }
    if (this.versionTaps < 5) return;
    this.versionTaps = 0;
    const ctx = this.getUIContext().getHostContext();
    const files: string = ctx?.filesDir ?? '';
    if (this.saves.length === 0) {
      this.showToast('没有存档可测');
      return;
    }
    const titleId: string = this.saves[0].titleId;
    setTimeout(() => {
      const r: SelfTestResult = runSaveSelfTest(files, titleId);
      this.selfTestLines = r.lines;
      for (const l of r.lines) console.info('save self test: ' + l);
      this.showToast(r.ok ? '存档自检通过（见日志）' : '存档自检失败（见日志）');
    }, 40);
  }

  /**
   * List the attached input devices (P2.4).
   *
   * The product's controller support depends on one unknown: whether a paired gamepad reaches this app as a
   * 'joystick' device whose buttons arrive as key codes. This prints what the platform actually reports.
   */
  private listDevices(): void {
    this.versionTaps = 0;
    listInputDevices().then((rows: DeviceRow[]) => {
      const lines: string[] = [];
      if (rows.length === 0) {
        lines.push('没有检测到输入设备（或缺少读取权限）');
      } else {
        for (const r of rows) {
          const tag: string = r.isPad ? '  <- 手柄' : '';
          lines.push('#' + r.id + ' ' + r.name + ' [' + r.sources + ']' + tag);
        }
      }
      let pads: number = 0;
      for (const r of rows) {
        if (r.isPad) pads++;
      }
      if (rows.length > 0) {
        lines.push('共 ' + rows.length + ' 个设备，其中 ' + pads + ' 个手柄');
      }
      this.padLines = lines;
      for (const l of lines) console.info('pad probe: ' + l);
      this.showToast(pads > 0 ? '检测到手柄（见下方）' : '未检测到手柄');
    }).catch((e: Error) => {
      this.padLines = ['读取输入设备失败: ' + e.message];
      console.info('pad probe: failed ' + e.message);
    });
  }

  /** Put a game's save back from the backup an earlier import left. */
  private doRestore(): void {
    const titleId: string = this.pendingRestore;
    this.pendingRestore = '';
    if (titleId.length === 0) return;
    const ctx = this.getUIContext().getHostContext();
    const files: string = ctx?.filesDir ?? '';
    const ok: boolean = restoreBackup(files, titleId);
    this.loadSaves(files);
    this.showToast(ok ? '已恢复上次导入前的存档' : '没有可恢复的备份');
  }

  private showToast(msg: string): void {
    this.toast = msg;
    if (this.toastTimer !== -1) clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(() => {
      this.toast = '';
      this.toastTimer = -1;
    }, 3000);
  }

  /** Open the pad editor: the game page reads this flag on load and turns edit mode on. */
  private openPadEditor(): void {
    this.savePref(PREF_OPACITY, this.padOpacity);
    this.savePref(PREF_SCALE, this.padScale);
    try {
      const ctx = this.getUIContext().getHostContext();
      if (ctx) {
        // A separate preferences file keeps the flag out of the pad's own keys.
        preferences.getPreferences(ctx, 'library').then((p: preferences.Preferences) => {
          p.putSync('editPadOnLoad', true);
          p.flush();
          router.pushUrl({ url: 'pages/Index' }).catch((e: Error) => {
            this.showToast('无法打开游戏页面');
          });
        }).catch((e: Error) => {
          this.showToast('无法打开游戏页面');
        });
      }
    } catch (e) {
      this.showToast('无法打开游戏页面');
    }
  }

  private doClear(): void {
    this.confirmClear = false;
    this.busy = true;
    const ctx = this.getUIContext().getHostContext();
    const cache: string = ctx?.cacheDir ?? '';
    setTimeout(() => {
      const freed: number = clearCache(cache);
      this.busy = false;
      this.cacheBytes = 0;
      this.showToast(freed > 0 ? `已清理 ${humanSize(freed)}` : '没有可清理的缓存');
    }, 80);
  }

  /** A label + value row. */
  @Builder
  infoRow(label: string, value: string, dim: boolean) {
    Row() {
      Text(label)
        .fontSize(Font.label)
        .fontColor(TextToken.secondary)
        .width(120)
        .textAlign(TextAlign.Start)
      Text(value)
        .fontSize(Font.label)
        .fontColor(dim ? TextToken.tertiary : TextToken.primary)
        .layoutWeight(1)
        .textAlign(TextAlign.End)
    }
    .width('100%')
    .padding({ top: Space.s2, bottom: Space.s2 })
  }

  /** A label + stepper row: what a gamepad can drive without a drag. */
  @Builder
  stepRow(label: string, value: string, onMinus: () => void, onPlus: () => void) {
    Row({ space: Space.s3 }) {
      Text(label)
        .fontSize(Font.label)
        .fontColor(TextToken.secondary)
        .width(120)
        .textAlign(TextAlign.Start)
      Button('−')
        .fontSize(Font.body)
        .fontColor(TextToken.primary)
        .backgroundColor('rgba(255,255,255,0.10)')
        .borderRadius(Radius.sm)
        .width(40)
        .height(36)
        .onClick(onMinus)
      Text(value)
        .fontSize(Font.label)
        .fontColor(TextToken.primary)
        .layoutWeight(1)
        .textAlign(TextAlign.Center)
      Button('+')
        .fontSize(Font.body)
        .fontColor(TextToken.primary)
        .backgroundColor('rgba(255,255,255,0.10)')
        .borderRadius(Radius.sm)
        .width(40)
        .height(36)
        .onClick(onPlus)
    }
    .width('100%')
    .padding({ top: Space.s2, bottom: Space.s2 })
  }

  /** A section heading. */
  @Builder
  heading(text: string) {
    Text(text)
      .fontSize(Font.title)
      .fontColor(TextToken.primary)
      .width('100%')
      .textAlign(TextAlign.Start)
      .margin({ top: Space.s5, bottom: Space.s2 })
  }

  build() {
    Column() {
      // ---- the bar ----
      Row({ space: Space.s3 }) {
        Button('←')
          .fontSize(Font.body)
          .fontColor(TextToken.primary)
          .backgroundColor('rgba(0,0,0,0)')
          .width(44)
          .height(44)
          .onClick(() => {
            router.back();
          })
        Text('设置')
          .fontSize(Font.title)
          .fontColor(TextToken.primary)
        Blank()
      }
      .width('100%')
      .height(Size.topBar)
      .padding({ left: Space.s4, right: Space.s6 })

      Scroll() {
        Column({ space: Space.s2 }) {
          // ---- 控制 ----
          this.heading('控制')

          Row() {
            Column({ space: Space.s1 }) {
              Text('按键布局')
                .fontSize(Font.label)
                .fontColor(TextToken.primary)
                .width('100%')
                .textAlign(TextAlign.Start)
              Text('拖动调整虚拟按键的位置与大小')
                .fontSize(Font.caption)
                .fontColor(TextToken.tertiary)
                .width('100%')
                .textAlign(TextAlign.Start)
            }
            .layoutWeight(1)
            .alignItems(HorizontalAlign.Start)
            Button('调整 →')
              .fontSize(Font.label)
              .fontColor(TextToken.inverse)
              .backgroundColor(Semantic.accent)
              .borderRadius(Radius.md)
              .height(Size.buttonMd)
              .padding({ left: Space.s4, right: Space.s4 })
              .onClick(() => this.openPadEditor())
          }
          .width('100%')
          .padding({ top: Space.s2, bottom: Space.s2 })

          this.stepRow('按键不透明度', `${Math.round(this.padOpacity * 100)}%`,
            () => {
              this.padOpacity = Math.max(0.2, Math.round((this.padOpacity - 0.1) * 10) / 10);
              this.savePref(PREF_OPACITY, this.padOpacity);
            },
            () => {
              this.padOpacity = Math.min(1.0, Math.round((this.padOpacity + 0.1) * 10) / 10);
              this.savePref(PREF_OPACITY, this.padOpacity);
            })

          this.stepRow('按键大小', `${this.padScale.toFixed(2)}×`,
            () => {
              this.padScale = Math.max(0.6, Math.round((this.padScale - 0.05) * 100) / 100);
              this.savePref(PREF_SCALE, this.padScale);
            },
            () => {
              this.padScale = Math.min(1.6, Math.round((this.padScale + 0.05) * 100) / 100);
              this.savePref(PREF_SCALE, this.padScale);
            })

          // ---- 数据 ----
          this.heading('数据')
          // Written inline rather than through a @Builder: a builder's parameters are captured when it is called,
          // so the row kept showing 计算中… after gamesBytes arrived (measured: the log said 15808253577 while the
          // label never changed).
          Row() {
            Text('游戏数据')
              .fontSize(Font.label)
              .fontColor(TextToken.secondary)
              .width(120)
              .textAlign(TextAlign.Start)
            Text(this.gamesBytes < 0 ? '计算中…' : humanSize(this.gamesBytes))
              .fontSize(Font.label)
              .fontColor(this.gamesBytes < 0 ? TextToken.tertiary : TextToken.primary)
              .layoutWeight(1)
              .textAlign(TextAlign.End)
          }
          .width('100%')
          .padding({ top: Space.s2, bottom: Space.s2 })

          Row() {
            Text('缓存')
              .fontSize(Font.label)
              .fontColor(TextToken.secondary)
              .width(120)
              .textAlign(TextAlign.Start)
            Text(this.cacheBytes < 0 ? '计算中…' : humanSize(this.cacheBytes))
              .fontSize(Font.label)
              .fontColor(this.cacheBytes < 0 ? TextToken.tertiary : TextToken.primary)
              .layoutWeight(1)
              .textAlign(TextAlign.End)
          }
          .width('100%')
          .padding({ top: Space.s2, bottom: Space.s2 })

          Row() {
            Blank()
            Button(this.busy ? '清理中…' : '清理缓存')
              .fontSize(Font.label)
              .fontColor(this.cacheBytes > 0 ? Semantic.danger : TextToken.tertiary)
              .backgroundColor('rgba(0,0,0,0)')
              .border({
                width: 1,
                color: this.cacheBytes > 0 ? Semantic.danger : Semantic.borderSubtle,
                radius: Radius.md
              })
              .borderRadius(Radius.md)
              .height(Size.buttonMd)
              .padding({ left: Space.s5, right: Space.s5 })
              .enabled(this.cacheBytes > 0 && !this.busy)
              .onClick(() => { this.confirmClear = true; })
          }
          .width('100%')
          .padding({ top: Space.s2, bottom: Space.s2 })

          // ---- 存档 (P2.3) ----
          // One row per game that has a save, with the restore action only where an import left a backup - the
          // list is how a player finds their way back after a bad import without hunting through files.
          this.heading('存档')
          if (!this.savesLoaded) {
            Text('读取中…')
              .fontSize(Font.label)
              .fontColor(TextToken.tertiary)
              .width('100%')
              .textAlign(TextAlign.Start)
              .padding({ top: Space.s2, bottom: Space.s2 })
          } else if (this.saves.length === 0) {
            Text('还没有存档。进入游戏并保存一次后，这里会列出来。')
              .fontSize(Font.label)
              .fontColor(TextToken.tertiary)
              .width('100%')
              .textAlign(TextAlign.Start)
              .padding({ top: Space.s2, bottom: Space.s2 })
          } else {
            ForEach(this.saves, (s: SaveEntry) => {
              Row({ space: Space.s3 }) {
                Column({ space: 2 }) {
                  Text(s.name)
                    .fontSize(Font.label)
                    .fontColor(TextToken.primary)
                    .maxLines(1)
                    .textOverflow({ overflow: TextOverflow.Ellipsis })
                    .width('100%')
                    .textAlign(TextAlign.Start)
                  Text(`${humanSize(s.bytes)} · ${s.files} 个文件${s.hasBackup ? ' · 有备份' : ''}`)
                    .fontSize(Font.caption)
                    .fontColor(TextToken.tertiary)
                    .width('100%')
                    .textAlign(TextAlign.Start)
                }
                .layoutWeight(1)
                .alignItems(HorizontalAlign.Start)
                if (s.hasBackup) {
                  Button('恢复备份')
                    .fontSize(Font.caption)
                    .fontColor(TextToken.secondary)
                    .backgroundColor('rgba(0,0,0,0)')
                    .border({ width: 1, color: Semantic.borderSubtle, radius: Radius.md })
                    .borderRadius(Radius.md)
                    .height(32)
                    .padding({ left: Space.s3, right: Space.s3 })
                    .onClick(() => { this.pendingRestore = s.titleId; })
                }
              }
              .width('100%')
              .padding({ top: Space.s2, bottom: Space.s2 })
            }, (s: SaveEntry) => s.titleId)
          }

          // ---- 游戏库来源 (P3.3) ----
          // A pack server on the same network publishes what it holds at /list; the library reads that list and
          // streams a container down. Empty = only the packs already imported.
          this.heading('游戏库来源')
          Column({ space: Space.s2 }) {
            Text('填入电脑上 pack_server 的地址，游戏库会列出它上面的游戏包，可以直接下载。')
              .fontSize(Font.caption).fontColor(TextToken.secondary).width('100%')
            Row({ space: Space.s2 }) {
              TextInput({ text: this.sourceText, placeholder: '192.168.1.10:28899' })
                .fontSize(Font.label)
                .layoutWeight(1)
                .type(InputType.Normal)
                .onChange((v: string) => { this.sourceText = v; })
              Button('保存')
                .fontSize(Font.label)
                .onClick(() => this.saveSource())
            }.width('100%')
            if (this.sourceSaved.length > 0) {
              Text('已保存：' + this.sourceSaved)
                .fontSize(Font.caption).fontColor(TextToken.secondary).width('100%')
            }
          }.width('100%').padding({ top: Space.s2, bottom: Space.s3 })

          // ---- 关于 ----
          this.heading('关于')
          Row() {
            Text('版本').fontSize(Font.label).fontColor(TextToken.secondary).width(120).textAlign(TextAlign.Start)
            Text('1.0.0 (1000000)').fontSize(Font.label).fontColor(TextToken.primary).layoutWeight(1).textAlign(TextAlign.End)
          }.width('100%').padding({ top: Space.s2, bottom: Space.s2 })
            .onClick(() => this.onVersionTap())
          // The self test's lines, shown only after it has run: five taps on the version row.
          // The controller probe's answer (three taps on the version row).
          if (this.padLines.length > 0) {
            Column({ space: 2 }) {
              ForEach(this.padLines, (l: string) => {
                Text(l)
                  .fontSize(Font.caption)
                  .fontColor(l.indexOf('手柄') >= 0 ? Semantic.success : TextToken.tertiary)
                  .width('100%')
                  .textAlign(TextAlign.Start)
              }, (l: string, i: number) => 'p' + i)
            }
            .width('100%')
            .padding({ left: Space.s2, right: Space.s2, top: Space.s2, bottom: Space.s2 })
          }
          if (this.selfTestLines.length > 0) {
            Column({ space: 2 }) {
              ForEach(this.selfTestLines, (l: string) => {
                Text(l)
                  .fontSize(Font.caption)
                  .fontColor(l.startsWith('FAIL') ? Semantic.warning : TextToken.tertiary)
                  .width('100%')
                  .textAlign(TextAlign.Start)
              }, (l: string, i: number) => `${i}`)
            }
            .width('100%')
            .padding({ left: Space.s2, right: Space.s2, top: Space.s2, bottom: Space.s2 })
          }
          Row() {
            Text('日志').fontSize(Font.label).fontColor(TextToken.secondary).width(120).textAlign(TextAlign.Start)
            Text('游戏目录下的 log.txt').fontSize(Font.label).fontColor(TextToken.primary).layoutWeight(1).textAlign(TextAlign.End)
          }.width('100%').padding({ top: Space.s2, bottom: Space.s2 })
          Row() {
            Blank()
            Button('复制日志路径')
              .fontSize(Font.label)
              .fontColor(TextToken.secondary)
              .backgroundColor('rgba(0,0,0,0)')
              .border({ width: 1, color: Semantic.borderSubtle, radius: Radius.md })
              .borderRadius(Radius.md)
              .height(Size.buttonMd)
              .padding({ left: Space.s5, right: Space.s5 })
              .onClick(() => this.showToast('日志在游戏数据目录下：log.txt'))
          }
          .width('100%')
          .padding({ top: Space.s2 })

          Blank().height(Space.s6)
        }
        .width('100%')
        .padding({ left: Space.s6, right: Space.s6 })
        .alignItems(HorizontalAlign.Start)
      }
      .layoutWeight(1)
      .width('100%')
      .scrollBar(BarState.Auto)
      .align(Alignment.TopStart)

      if (this.toast.length > 0) {
        Text(this.toast)
          .fontSize(Font.label)
          .fontColor(TextToken.primary)
          .backgroundColor(Bg.elevated)
          .borderRadius(Radius.md)
          .padding({ left: Space.s4, right: Space.s4, top: Space.s3, bottom: Space.s3 })
          .margin({ bottom: Space.s5 })
      }

      ConfirmDialog({
        visible: this.confirmClear,
        title: '清理缓存？',
        body: this.cacheBytes > 0
          ? `将删除 ${humanSize(this.cacheBytes)} 临时文件（未完成的导入会丢失进度）。游戏数据和存档不受影响。`
          : '没有可清理的缓存。',
        confirmLabel: '清理',
        cancelLabel: '取消',
        destructive: true,
        onConfirm: () => this.doClear(),
        onCancel: () => { this.confirmClear = false; }
      })

      // Restoring a backup replaces the live save, so it asks first like every other destructive action.
      ConfirmDialog({
        visible: this.pendingRestore.length > 0,
        title: '恢复备份？',
        body: '将用上次导入前的备份替换当前存档。当前存档会被备份覆盖，但游戏数据不受影响。',
        confirmLabel: '恢复',
        cancelLabel: '取消',
        destructive: true,
        onConfirm: () => this.doRestore(),
        onCancel: () => { this.pendingRestore = ''; }
      })
    }
    .width('100%')
    .height('100%')
    .backgroundColor(Bg.base)
    .onAreaChange((_o: Area, n: Area) => {
      const w: number = n.width as number;
      const h: number = n.height as number;
      if (w > 0 && h > 0) this.landscape = w > h;
    })
  }
}
