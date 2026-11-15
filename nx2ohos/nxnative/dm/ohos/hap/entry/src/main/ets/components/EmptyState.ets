/*
 * EmptyState.ets - what a player sees before importing anything.
 *
 * The one screen where the product has to explain itself. It names the situation in plain words ("还没有游戏"),
 * says what to do about it in one line, and offers exactly one primary action. A secondary link goes to the
 * import guide for someone who does not yet have a pack.
 */

import { Bg, Text as TextToken, Semantic, Space, Font, Radius, Motion, Size } from '../theme/Tokens';

@Component
export struct EmptyState {
  onAdd: () => void = () => {};
  onGuide: () => void = () => {};

  /** An empty box, drawn with two strokes: no icon font, no emoji, so it renders identically everywhere. */
  @Builder
  emptyBox() {
    Stack() {
      // the open lid
      Row()
        .width(44)
        .height(10)
        .border({ width: 2, color: TextToken.tertiary, radius: 2 })
        .margin({ bottom: 26 })
      // the body
      Column()
        .width(52)
        .height(28)
        .border({ width: 2, color: TextToken.tertiary, radius: 2 })
        .margin({ top: 12 })
    }
    .width(64)
    .height(64)
    .alignContent(Alignment.Center)
  }

  build() {
    Column({ space: Space.s4 }) {
      this.emptyBox()

      Column({ space: Space.s2 }) {
        Text('还没有游戏')
          .fontSize(Font.display)
          .fontColor(TextToken.primary)
          .textAlign(TextAlign.Center)
        Text('导入一个游戏包（.nxpk）就可以开始玩了')
          .fontSize(Font.body)
          .fontColor(TextToken.secondary)
          .textAlign(TextAlign.Center)
      }
      .alignItems(HorizontalAlign.Center)

      Button('选择游戏包')
        .fontSize(Font.body)
        .fontColor(TextToken.inverse)
        .backgroundColor(Semantic.accent)
        .borderRadius(Radius.md)
        .height(Size.buttonLg)
        .padding({ left: Space.s6, right: Space.s6 })
        .margin({ top: Space.s3 })
        .onClick(() => this.onAdd())

      Text('查看导入指南 →')
        .fontSize(Font.label)
        .fontColor(Semantic.accent)
        .margin({ top: Space.s1 })
        .onClick(() => this.onGuide())
    }
    .width(480)
    .alignItems(HorizontalAlign.Center)
    .justifyContent(FlexAlign.Center)
    .accessibilityGroup(true)
    .accessibilityText('还没有游戏。导入一个游戏包就可以开始玩了。')
  }
}
