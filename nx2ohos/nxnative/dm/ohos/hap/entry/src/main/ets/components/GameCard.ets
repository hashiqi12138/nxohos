/*
 * GameCard.ets - one game in the library grid.
 *
 * The card is the cover: a rounded square that carries only art, with the name and state centred beneath it
 * (the console home-screen language). A pack with no icon gets a diagonal gradient in its own palette hue with
 * the initial as a large, faint watermark in the corner - a texture, not a placeholder poster.
 *
 * Focus reads at arm's length: a white 2 dp ring, a glow in the focused game's own palette colour and a 1.08
 * scale. Every game brings its own ambience; nothing about the treatment is shared pink.
 */

import { Bg, Text as TextToken, Semantic, CoverPalette, Space, Font, Radius, Motion, Size } from '../theme/Tokens';

/** What the card is doing right now. */
export enum CardState {
  /** At rest. */
  Idle,
  /** The gamepad or keyboard pointer is here. */
  Focus,
  /** A finger is down. */
  Press,
  /** The data is incomplete: RomFS or the patched modules are missing. */
  Incomplete,
  /** startGame has been called and the first frame has not arrived yet. */
  Launching,
  /** A data check is running. */
  Verifying
}

@Component
export struct GameCard {
  /** The display name. */
  @Prop name: string = '';
  /** The title ID: the placeholder colour and the watermark come from it. */
  @Prop titleId: string = '';
  /** A human size such as "14.7 GB". */
  @Prop sizeLabel: string = '';
  /** The cover image's absolute path, or '' for the placeholder. */
  @Prop iconPath: string = '';
  /** The cover's edge length; the portrait list passes a smaller value. */
  @Prop coverSize: number = Size.cardCoverLandscape;
  /** The current state. */
  @State state: CardState = CardState.Idle;
  /** Called when the card is activated (tap, or the gamepad's A). */
  onActivate: () => void = () => {};
  /** Called on a long press (600 ms) or the gamepad's menu key. */
  onDetails: () => void = () => {};

  /** The placeholder: a diagonal gradient in the game's hue with the initial as a corner watermark. */
  @Builder
  placeholderCover() {
    Stack({ alignContent: Alignment.BottomEnd }) {
      Column()
        .width('100%')
        .height('100%')
        .linearGradient({
          angle: 160,
          colors: [[CoverPalette.forTitle(this.titleId), 0.0],
            [CoverPalette.darken(CoverPalette.forTitle(this.titleId), 0.55), 1.0]]
        })
      // The initial at 12% white: a texture in the corner, not a title in the centre.
      Text(CoverPalette.initialOf(this.name))
        .fontSize(Math.round(this.coverSize * 0.62))
        .fontWeight(FontWeight.Bold)
        .fontColor('rgba(255,255,255,0.12)')
        .lineHeight(Math.round(this.coverSize * 0.62) * 1.1)
        .margin({ right: this.coverSize * 0.02, bottom: -this.coverSize * 0.08 })
    }
    .width('100%')
    .height('100%')
  }

  /** The real cover, when the pack shipped one. */
  @Builder
  realCover() {
    Image(this.iconPath)
      .width('100%')
      .height('100%')
      .objectFit(ImageFit.Cover)
      .interpolation(ImageInterpolation.High)
  }

  /** The launching overlay: the wait between the tap and the first frame is 3-8 s, so it must be visible. */
  @Builder
  launchingOverlay() {
    Column({ space: Space.s2 }) {
      LoadingProgress()
        .width(32)
        .height(32)
        .color(Semantic.accent)
      Text('正在启动')
        .fontSize(Font.caption)
        .fontColor(TextToken.primary)
    }
    .width('100%')
    .height('100%')
    .justifyContent(FlexAlign.Center)
    .backgroundColor(Bg.overlay)
  }

  /** The verifying overlay: a data check, shorter than a launch but not instant. */
  @Builder
  verifyingOverlay() {
    Column({ space: Space.s2 }) {
      LoadingProgress()
        .width(24)
        .height(24)
        .color(Semantic.warning)
      Text('正在校验')
        .fontSize(Font.caption)
        .fontColor(TextToken.primary)
    }
    .width('100%')
    .height('100%')
    .justifyContent(FlexAlign.Center)
    .backgroundColor(Bg.overlay)
  }

  build() {
    // The card is a cover plus a centred caption beneath it; both live in one column so the grid's cells stay
    // one rectangle (the Grid measures the cell before the children, and a caption outside the card's own bounds
    // was the layout that lost the caption on the first pass).
    Column({ space: Space.s2 }) {
      Stack() {
        if (this.iconPath.length > 0) {
          this.realCover()
        } else {
          this.placeholderCover()
        }
        if (this.state === CardState.Launching) {
          this.launchingOverlay()
        } else if (this.state === CardState.Verifying) {
          this.verifyingOverlay()
        }
      }
      .width(this.coverSize)
      .height(this.coverSize)
      .flexShrink(0)
      .borderRadius(Radius.lg)
      .clip(true)
      // an incomplete pack reads as dimmed: the cover still shows what the game is, the state line says why
      .opacity(this.state === CardState.Incomplete ? 0.4 : 1)

      // ---- caption, centred under the cover ----
      Text(this.name.length > 0 ? this.name : this.titleId)
        .fontSize(Font.body)
        .fontColor(this.state === CardState.Incomplete ? TextToken.tertiary : TextToken.primary)
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
        .width(this.coverSize)
        .textAlign(TextAlign.Center)
      Text(this.state === CardState.Incomplete
        ? `${this.sizeLabel} · 数据不完整`
        : `${this.sizeLabel} · 可运行`)
        .fontSize(Font.label)
        .fontColor(TextToken.secondary)
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
        .width(this.coverSize)
        .textAlign(TextAlign.Center)
    }
    .width(this.coverSize)
    .height(this.coverSize + 56)
    .alignItems(HorizontalAlign.Center)
    // A white ring stays out of every game's way; the glow carries the game's own hue, so focus changes the
    // room's light rather than painting the same pink over every title.
    .border({
      width: this.state === CardState.Focus || this.state === CardState.Verifying ? Size.focusRing : 0,
      color: this.state === CardState.Verifying ? Semantic.warning : TextToken.primary,
      radius: Radius.lg
    })
    .scale({
      x: this.state === CardState.Focus ? 1.08 : (this.state === CardState.Press ? Motion.scalePress : 1),
      y: this.state === CardState.Focus ? 1.08 : (this.state === CardState.Press ? Motion.scalePress : 1)
    })
    .shadow(this.state === CardState.Focus
      ? { radius: 32, color: CoverPalette.rgba(CoverPalette.forTitle(this.titleId), 0.45), offsetX: 0, offsetY: 0 }
      : { radius: 0, color: 'rgba(0,0,0,0)', offsetX: 0, offsetY: 0 })
    .animation({ duration: Motion.duration(Motion.base), curve: Motion.curve })
    .onClick(() => {
      if (this.state === CardState.Incomplete || this.state === CardState.Launching) return;
      this.onActivate();
    })
    .gesture(
      LongPressGesture({ repeat: false, duration: 600 })
        .onAction(() => {
          if (this.state === CardState.Launching) return;
          this.onDetails();
        })
    )
    .accessibilityText(`${this.name}，${this.sizeLabel}，${this.state === CardState.Incomplete ? '数据不完整' : '可运行'}`)
    .accessibilityRole(AccessibilityRoleType.BUTTON)
  }
}
