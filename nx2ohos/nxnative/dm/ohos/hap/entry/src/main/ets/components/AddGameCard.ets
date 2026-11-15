/*
 * AddGameCard.ets - the "add a game" tile at the end of the library grid.
 *
 * It sits in the grid rather than only in the top bar because it is the next action after the installed games: a
 * player who has played everything looks right, and the way to get more is there. A dashed border separates it
 * from a real game without making it look broken.
 */

import { Bg, Text as TextToken, Semantic, Space, Font, Radius, Motion, Size } from '../theme/Tokens';

@Component
export struct AddGameCard {
  /** The cover's edge length, matching GameCard's so the grid rows line up. */
  @Prop coverSize: number = Size.cardCoverLandscape;
  @State focused: boolean = false;
  @State pressed: boolean = false;
  onActivate: () => void = () => {};

  build() {
    Column({ space: Space.s2 }) {
      Column() {
        Text('+')
          .fontSize(Math.round(this.coverSize * 0.2))
          .fontColor(TextToken.tertiary)
          .fontWeight(FontWeight.Lighter)
          .lineHeight(Math.round(this.coverSize * 0.2) * 1.2)
      }
      .width(this.coverSize)
      .height(this.coverSize)
      .flexShrink(0)
      .justifyContent(FlexAlign.Center)
      .alignItems(HorizontalAlign.Center)

      Text('添加游戏')
        .fontSize(Font.body)
        .fontColor(TextToken.secondary)
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
        .width(this.coverSize)
        .textAlign(TextAlign.Center)
      Text('选择一个 .nxpk 游戏包')
        .fontSize(Font.label)
        .fontColor(TextToken.tertiary)
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
        .width(this.coverSize)
        .textAlign(TextAlign.Center)
    }
    .width(this.coverSize)
    .height(this.coverSize + 56)
    .alignItems(HorizontalAlign.Center)
    .borderRadius(Radius.lg)
    // dashed: unmistakably not a game, without reading as an error
    .border({
      width: this.focused ? Size.focusRing : 1,
      color: this.focused ? TextToken.primary : Semantic.borderSubtle,
      radius: Radius.lg,
      style: BorderStyle.Dashed
    })
    .scale({ x: this.focused ? 1.08 : (this.pressed ? Motion.scalePress : 1),
             y: this.focused ? 1.08 : (this.pressed ? Motion.scalePress : 1) })
    .shadow(this.focused
      ? { radius: 32, color: 'rgba(148,163,184,0.35)', offsetX: 0, offsetY: 0 }
      : { radius: 0, color: 'rgba(0,0,0,0)', offsetX: 0, offsetY: 0 })
    .animation({ duration: Motion.duration(Motion.base), curve: Motion.curve })
    .onClick(() => this.onActivate())
    .onTouch((e: TouchEvent) => {
      if (e.type === TouchType.Down) this.pressed = true;
      else if (e.type === TouchType.Up || e.type === TouchType.Cancel) this.pressed = false;
    })
    .accessibilityText('添加游戏')
    .accessibilityRole(AccessibilityRoleType.BUTTON)
  }
}
