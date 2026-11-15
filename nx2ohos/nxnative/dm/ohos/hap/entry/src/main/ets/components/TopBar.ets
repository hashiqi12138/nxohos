/*
 * TopBar.ets - the library's header: the product's name on the left, the add action on the right.
 *
 * It exists so the screen has an identity ("NXOHOS") rather than reading like a file browser, and so the add
 * action has a fixed home that does not move as the grid grows.
 */

import { Bg, Text as TextToken, Semantic, Space, Font, Radius, Size } from '../theme/Tokens';

@Component
export struct TopBar {
  /** The product name. */
  @Prop title: string = 'NXOHOS';
  /** Whether to draw the add button (hidden while an import runs). */
  @Prop showAdd: boolean = true;
  /** The bar's height: 64 in landscape, 56 in portrait. */
  @Prop barHeight: number = Size.topBar;
  onAdd: () => void = () => {};
  onSettings: () => void = () => {};

  build() {
    Row() {
      Text(this.title)
        .fontSize(Font.title)
        .fontColor(TextToken.primary)
        .fontWeight(FontWeight.Medium)
        .letterSpacing(0.5)

      Blank()

      // Settings sits left of Add: the add action is the primary one, and the gear is where a console puts it -
      // the far end of the bar, out of the way of the daily flow.
      Button('⚙')
        .fontSize(Font.body)
        .fontColor(TextToken.secondary)
        .backgroundColor('rgba(0,0,0,0)')
        .width(44)
        .height(44)
        .margin({ right: Space.s2 })
        .onClick(() => this.onSettings())
        .accessibilityText('设置')
        .accessibilityRole(AccessibilityRoleType.BUTTON)

      if (this.showAdd) {
        Button() {
          Row({ space: Space.s1 }) {
            Text('+')
              .fontSize(Font.body)
              .fontColor(TextToken.inverse)
              .fontWeight(FontWeight.Medium)
            Text('添加游戏')
              .fontSize(Font.label)
              .fontColor(TextToken.inverse)
          }
          .alignItems(VerticalAlign.Center)
        }
        .backgroundColor(Semantic.accent)
        .borderRadius(Radius.md)
        .height(Size.buttonMd)
        .padding({ left: Space.s4, right: Space.s4 })
        .onClick(() => this.onAdd())
        .accessibilityText('添加游戏')
        .accessibilityRole(AccessibilityRoleType.BUTTON)
      }
    }
    .width('100%')
    .height(this.barHeight)
    .padding({ left: Space.s6, right: Space.s6 })
    .backgroundColor('rgba(0,0,0,0)')   // transparent: the ambient light runs through the bar
  }
}
