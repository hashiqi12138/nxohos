/*
 * ImportBanner.ets - the 4 dp progress strip under the top bar.
 *
 * The import is a long task but not a modal one: the strip says "work is happening" at a glance without taking
 * the screen, and the panel (ImportPanel) is there when the player wants the details. Non-interactive, so it
 * never takes focus from the grid.
 */

import { Semantic, Text as TextToken, Font, Motion, Space, Z } from '../theme/Tokens';
import { ImportStatus } from '../pages/GameLibrary';

@Component
export struct ImportBanner {
  /** Refreshed by the host page's timer while an import runs. */
  @Prop percent: number = 0;
  @Prop gameName: string = '';

  build() {
    Column({ space: 2 }) {
      Row() {
        Column()
          .layoutWeight(Math.max(1, this.percent))
          .height(4)
          .linearGradient({
            angle: 0,
            colors: [[Semantic.accent, 0.0], [Semantic.accentHover, 1.0]]
          })
          .borderRadius({ topLeft: 999, bottomLeft: 999 })
        Column()
          .layoutWeight(Math.max(1, 100 - this.percent))
          .height(4)
          .backgroundColor('rgba(255,255,255,0.06)')
          .borderRadius({ topRight: 999, bottomRight: 999 })
      }
      .width('100%')
      Text(`正在导入 ${this.gameName} ${this.percent}%`)
        .fontSize(Font.caption)
        .fontColor(TextToken.secondary)
        .width('100%')
        .textAlign(TextAlign.Start)
        .padding({ left: Space.s6 })
    }
    .width('100%')
    .zIndex(Z.sticky)
    .animation({ duration: Motion.duration(Motion.base), curve: Motion.curve })
  }
}

/** Whether the banner has anything to say right now. */
export function importBannerVisible(): boolean {
  return ImportStatus.active;
}
