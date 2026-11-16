/*
 * GameDetailSheet.ets - one game's facts and its three actions.
 *
 * Opened by holding a card. The sheet answers what the grid cannot: how big the data is, which version and which
 * pack stamp it came from, and whether it is actually intact. The three actions are the ones a player needs -
 * launch, check the data, delete it - and the destructive one is separated from the other two and says what it
 * costs and what it does not (the saves live elsewhere and are untouched).
 */

import { Bg, Text as TextToken, Semantic, CoverPalette, Space, Font, Radius, Motion, Z, Size } from '../theme/Tokens';
import { GameEntry, humanSize } from '../pages/GameLibrary';

/** One labelled line in the facts block. */
interface Fact {
  label: string;
  value: string;
}

@Component
export struct GameDetailSheet {
  @Prop visible: boolean = false;
  @Prop game: GameEntry | null = null;
  /** The pack's display version, from its own metadata. */
  @Prop version: string = '';
  /** The last data-check sentence, or ''. */
  @Prop verifyNote: string = '';
  /** Whether the last check passed (drives the note's colour). */
  @Prop verifyOk: boolean = true;
  /** A check is running: the button shows a spinner and disables. */
  @Prop verifying: boolean = false;
  onLaunch: () => void = () => {};
  onVerify: () => void = () => {};
  onDelete: () => void = () => {};
  onClose: () => void = () => {};
  /** S5: the player locked or unlocked this build. The page writes the marker and reloads. */
  onKeptChange: (on: boolean) => void = () => {};
  /** S5: rebuild this game, offered when its build is older than the host. */
  onRebuild: () => void = () => {};
  /** Write this game's save to a file the player picks (P2.3). */
  onExportSave: () => void = () => {};
  /** Replace this game's save from a file the player picks (P2.3). */
  onImportSave: () => void = () => {};
  /** A save operation is running: both buttons disable and the sheet says what it is doing. */
  @Prop saveBusy: boolean = false;
  @Prop saveNote: string = '';

  private facts(): Fact[] {
    const g: GameEntry | null = this.game;
    if (g === null) return [];
    const out: Fact[] = [];
    out.push({ label: '大小', value: humanSize(g.sizeBytes) });
    if (this.version.length > 0) out.push({ label: '版本', value: this.version });
    out.push({ label: '数据包', value: g.stamp.length > 0 ? g.stamp.substring(0, 12) : '未知' });
    out.push({ label: '状态', value: g.hasData ? '可运行' : '数据不完整' });
    return out;
  }

  build() {
    if (this.visible && this.game !== null) {
      Stack({ alignContent: Alignment.Bottom }) {
        // The scrim: fills the sheet's own stack, so a tap anywhere it shows closes the sheet.
        Column()
          .width('100%')
          .height('100%')
          .backgroundColor(Bg.overlay)
          .onClick(() => this.onClose())

        // The sheet itself sits at the bottom. It is sized by its content up to 85% of the height, and it must
        // NOT fill the stack: when it did, its transparent upper half swallowed the taps meant for the scrim
        // (measured: tapping above the sheet did nothing, and only the ✕ could close it).
        Scroll() {
          Column({ space: Space.s4 }) {
            // ---- handle ----
            Column()
              .width(Size.sheetHandleWidth)
              .height(Size.sheetHandleHeight)
              .borderRadius(Radius.full)
              .backgroundColor('rgba(255,255,255,0.15)')

            // An explicit close: a sheet must always show a way out.
            Row() {
              Blank()
              Button('✕')
                .fontSize(Font.body)
                .fontColor(TextToken.secondary)
                .backgroundColor('rgba(0,0,0,0)')
                .width(40)
                .height(40)
                .onClick(() => this.onClose())
                .accessibilityText('关闭')
            }
            .width('100%')

            // ---- identity: the same cover the grid shows, small ----
            Row({ space: Space.s4 }) {
              Stack({ alignContent: Alignment.BottomEnd }) {
                Column()
                  .width('100%')
                  .height('100%')
                  .linearGradient({
                    angle: 160,
                    colors: [[CoverPalette.forTitle(this.game.titleId), 0.0],
                      [CoverPalette.darken(CoverPalette.forTitle(this.game.titleId), 0.55), 1.0]]
                  })
                Text(CoverPalette.initialOf(this.game.name))
                  .fontSize(28)
                  .fontWeight(FontWeight.Bold)
                  .fontColor('rgba(255,255,255,0.14)')
                  .lineHeight(30)
                  .margin({ right: 2, bottom: -2 })
              }
              .width(72)
              .height(72)
              .borderRadius(Radius.md)
              .clip(true)

              Column({ space: Space.s1 }) {
                Text(this.game.name)
                  .fontSize(Font.title)
                  .fontColor(TextToken.primary)
                  .maxLines(2)
                  .textOverflow({ overflow: TextOverflow.Ellipsis })
                  .width('100%')
                  .textAlign(TextAlign.Start)
                Text(this.game.titleId)
                  .fontSize(Font.caption)
                  .fontColor(TextToken.tertiary)
                  .width('100%')
                  .textAlign(TextAlign.Start)
              }
              .layoutWeight(1)
              .alignItems(HorizontalAlign.Start)
            }
            .width('100%')

            // ---- facts ----
            Column({ space: Space.s2 }) {
              ForEach(this.facts(), (f: Fact) => {
                Row() {
                  Text(f.label)
                    .fontSize(Font.label)
                    .fontColor(TextToken.tertiary)
                    .width(72)
                    .textAlign(TextAlign.Start)
                  Text(f.value)
                    .fontSize(Font.label)
                    .fontColor(TextToken.secondary)
                    .layoutWeight(1)
                    .textAlign(TextAlign.Start)
                }
                .width('100%')
              }, (f: Fact) => f.label)
            }
            .width('100%')

            // ---- the check's verdict ----
            if (this.verifyNote.length > 0) {
              Text(this.verifyNote)
                .fontSize(Font.caption)
                .fontColor(this.verifyOk ? Semantic.success : Semantic.warning)
                .width('100%')
                .textAlign(TextAlign.Start)
            }

            // ---- S5: the build's own facts, and the one switch that changes them ----
            // NX Play's "Keep This Build": a lock on the build so a host update does not replace it. The player's
            // reason is real - a build that plays is worth more than a build that is current - so it is a switch
            // here rather than a file only a terminal can make.
            if (this.game.hasData) {
              Row({ space: Space.s2 }) {
                Column({ space: 2 }) {
                  Text('锁定这个构建').fontSize(Font.label).fontColor(TextToken.primary).width('100%')
                  Text(this.game.kept
                    ? '已锁定：host 更新不会重建它，它保持现在能跑的样子。'
                    : '未锁定：host 更新后重新构建会替换它。')
                    .fontSize(Font.caption).fontColor(TextToken.tertiary).width('100%')
                }.layoutWeight(1).alignItems(HorizontalAlign.Start)
                Toggle({ type: ToggleType.Switch, isOn: this.game.kept })
                  .onChange((on: boolean) => this.onKeptChange(on))
              }
              .width('100%')
              .padding({ top: Space.s2, bottom: Space.s2 })

              // A build older than the host may be missing fixes the host now has. NX Play calls it "App out of
              // date" and offers a rebuild; saying it without offering the rebuild would be a dead end.
              if (this.game.stale && !this.game.kept) {
                Row({ space: Space.s2 }) {
                  Column({ space: 2 }) {
                    Text('这个构建比 host 旧').fontSize(Font.label).fontColor(Semantic.warning).width('100%')
                    Text('重新构建可以带上 host 的新修复；锁定中的构建不会重建。')
                      .fontSize(Font.caption).fontColor(TextToken.tertiary).width('100%')
                  }.layoutWeight(1).alignItems(HorizontalAlign.Start)
                  Text('重新构建').fontSize(Font.label).fontColor(Semantic.accent)
                    .onClick(() => this.onRebuild())
                }
                .width('100%')
                .padding({ top: Space.s2, bottom: Space.s2 })
              }
            }


            // ---- actions ----
            Button('启动游戏')
              .fontSize(Font.body)
              .fontColor(TextToken.inverse)
              .backgroundColor(Semantic.accent)
              .borderRadius(Radius.md)
              .height(Size.buttonLg)
              .width('100%')
              .enabled(this.game.hasData && !this.verifying)
              .opacity(this.game.hasData ? 1 : 0.4)
              .onClick(() => this.onLaunch())

            // ---- saves (P2.3): exporting is what a player does before a risky thing, importing is the recovery
            // path, so both belong with the game rather than buried in settings ----
            if (this.saveNote.length > 0) {
              Text(this.saveNote)
                .fontSize(Font.caption)
                .fontColor(TextToken.secondary)
                .width('100%')
                .textAlign(TextAlign.Start)
            }
            Row({ space: Space.s3 }) {
              Button('导出存档')
                .fontSize(Font.label)
                .fontColor(TextToken.primary)
                .backgroundColor('rgba(0,0,0,0)')
                .border({ width: 1, color: Semantic.borderSubtle, radius: Radius.md })
                .borderRadius(Radius.md)
                .height(Size.buttonMd)
                .layoutWeight(1)
                .enabled(!this.saveBusy && !this.verifying)
                .onClick(() => this.onExportSave())
              Button('导入存档')
                .fontSize(Font.label)
                .fontColor(TextToken.primary)
                .backgroundColor('rgba(0,0,0,0)')
                .border({ width: 1, color: Semantic.borderSubtle, radius: Radius.md })
                .borderRadius(Radius.md)
                .height(Size.buttonMd)
                .layoutWeight(1)
                .enabled(!this.saveBusy && !this.verifying)
                .onClick(() => this.onImportSave())
            }
            .width('100%')

            Row({ space: Space.s3 }) {
              Button(this.verifying ? '正在校验…' : '校验数据')
                .fontSize(Font.label)
                .fontColor(TextToken.primary)
                .backgroundColor('rgba(0,0,0,0)')
                .border({ width: 1, color: Semantic.borderSubtle, radius: Radius.md })
                .borderRadius(Radius.md)
                .height(Size.buttonMd)
                .layoutWeight(1)
                .enabled(!this.verifying)
                .onClick(() => this.onVerify())
              Button('删除游戏')
                .fontSize(Font.label)
                .fontColor(Semantic.danger)
                .backgroundColor('rgba(0,0,0,0)')
                .border({ width: 1, color: Semantic.danger, radius: Radius.md })
                .borderRadius(Radius.md)
                .height(Size.buttonMd)
                .layoutWeight(1)
                .enabled(!this.verifying)
                .onClick(() => this.onDelete())
            }
            .width('100%')

            Text('存档保存在单独的目录中，删除游戏数据不会影响存档。')
              .fontSize(Font.caption)
              .fontColor(TextToken.tertiary)
              .width('100%')
              .textAlign(TextAlign.Start)
          }
          .width('100%')
          .padding({ left: Space.s6, right: Space.s6, top: Space.s3, bottom: Space.s5 })
          .backgroundColor(Bg.elevated)
          .borderRadius({ topLeft: Radius.lg, topRight: Radius.lg })
          }
          // The sheet grew to six actions; on a 373 vp window that overflowed the screen, so it scrolls and
          // never takes more than 85% of the height (the design's own limit for a bottom sheet).
          .constraintSize({ maxHeight: '85%' })
          .scrollBar(BarState.Auto)
      }
      .width('100%')
      .height('100%')
      .zIndex(Z.sheet)
    }
  }
}
