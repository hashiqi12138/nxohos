/*
 * ImportPanel.ets - the import's detail sheet.
 *
 * The numbers the design asked for: a determinate progress bar, the byte count, the remaining-time estimate
 * (ImportStatus computes it over a sliding window and refuses to show a jumping or stalling number), and the file
 * being written - the last one mostly says "it is still moving", which is what a player actually wants to know
 * during minute six of a big pack.
 */

import { Bg, Text as TextToken, Semantic, Space, Font, Radius, Motion, Z, Size } from '../theme/Tokens';
import { ImportStatus, humanSize } from '../pages/GameLibrary';

@Component
export struct ImportPanel {
  /** Refreshed by the host page's timer while an import runs. */
  @Prop percent: number = 0;
  @Prop doneBytes: number = 0;
  @Prop totalBytes: number = 0;
  @Prop remaining: string = '';
  @Prop currentFile: string = '';
  @Prop gameName: string = '';
  /** The player asked for the sheet to go away while the import continues. */
  onBackground: () => void = () => {};
  /** The player asked for the import to stop; the host confirms first. */
  onCancel: () => void = () => {};

  build() {
    Column({ space: Space.s4 }) {
      // ---- handle ----
      Column()
        .width(Size.sheetHandleWidth)
        .height(Size.sheetHandleHeight)
        .borderRadius(Radius.full)
        .backgroundColor('rgba(255,255,255,0.15)')

      Column({ space: Space.s2 }) {
        Text('正在导入')
          .fontSize(Font.caption)
          .fontColor(TextToken.tertiary)
          .width('100%')
          .textAlign(TextAlign.Start)
        Text(this.gameName)
          .fontSize(Font.title)
          .fontColor(TextToken.primary)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
          .width('100%')
          .textAlign(TextAlign.Start)
      }
      .width('100%')

      // ---- progress ----
      Row() {
        Column()
          .layoutWeight(Math.max(1, this.percent))
          .height(Size.progressHeight)
          .linearGradient({ angle: 0, colors: [[Semantic.accent, 0.0], [Semantic.accentHover, 1.0]] })
          .borderRadius({ topLeft: 999, bottomLeft: 999 })
        Column()
          .layoutWeight(Math.max(1, 100 - this.percent))
          .height(Size.progressHeight)
          .backgroundColor('rgba(255,255,255,0.06)')
          .borderRadius({ topRight: 999, bottomRight: 999 })
      }
      .width('100%')
      .animation({ duration: Motion.duration(Motion.base), curve: Motion.curve })

      Row() {
        Text(`${humanSize(this.doneBytes)} / ${humanSize(this.totalBytes)}`)
          .fontSize(Font.label)
          .fontColor(TextToken.secondary)
        Blank()
        if (this.remaining.length > 0) {
          Text(this.remaining)
            .fontSize(Font.label)
            .fontColor(TextToken.secondary)
        }
      }
      .width('100%')

      if (this.currentFile.length > 0) {
        Text(`正在写入 ${this.currentFile}`)
          .fontSize(Font.caption)
          .fontColor(TextToken.tertiary)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
          .width('100%')
          .textAlign(TextAlign.Start)
      }

      Text('可以离开此页面，导入会在后台继续。中断后重新打开会从断点继续。')
        .fontSize(Font.caption)
        .fontColor(TextToken.secondary)
        .width('100%')
        .textAlign(TextAlign.Start)

      // ---- actions ----
      Row({ space: Space.s3 }) {
        Blank()
        Button('后台继续')
          .fontSize(Font.label)
          .fontColor(TextToken.primary)
          .backgroundColor('rgba(0,0,0,0)')
          .border({ width: 1, color: Semantic.borderSubtle, radius: Radius.md })
          .borderRadius(Radius.md)
          .height(Size.buttonMd)
          .padding({ left: Space.s5, right: Space.s5 })
          .onClick(() => this.onBackground())
        Button('取消导入')
          .fontSize(Font.label)
          .fontColor(Semantic.danger)
          .backgroundColor('rgba(0,0,0,0)')
          .border({ width: 1, color: Semantic.danger, radius: Radius.md })
          .borderRadius(Radius.md)
          .height(Size.buttonMd)
          .padding({ left: Space.s5, right: Space.s5 })
          .onClick(() => this.onCancel())
      }
      .width('100%')
    }
    .width('100%')
    .padding({ left: Space.s6, right: Space.s6, top: Space.s3, bottom: Space.s5 })
    .backgroundColor(Bg.elevated)
    .borderRadius({ topLeft: Radius.lg, topRight: Radius.lg })
    .zIndex(Z.sheet)
  }
}
