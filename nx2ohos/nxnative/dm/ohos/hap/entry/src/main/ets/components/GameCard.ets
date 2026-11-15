/*
 * GameCard.ets - one game in the library grid.
 *
 * The card carries six states (default, focus, press, incomplete, launching, verifying). It is the element a
 * player's eye lands on first, so its cover is the largest thing on screen and its focus treatment is the most
 * emphatic: a 2 dp accent ring, an 8 dp glow and a 1.06 scale, all of which read at arm's length on a TV-sized
 * display.
 *
 * A pack with no icon gets a placeholder: the title ID picks one of six muted hues, so a game keeps its colour
 * across restarts and two games never look alike.
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
  /** The title ID: the placeholder colour and the initial come from it. */
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

  /** The placeholder cover: a hue from the title ID with the name's first character centred. */
  @Builder
  placeholderCover() {
    Stack({ alignContent: Alignment.Center }) {
      Column()
        .width('100%')
        .height('100%')
        .backgroundColor(CoverPalette.forTitle(this.titleId))
      // The initial gets an explicit line height: inside a Stack a Text otherwise stretches to the container,
      // which measured 296 vp tall for an 83 vp glyph and left the card with no room for its caption.
      Text(CoverPalette.initialOf(this.name))
        .fontSize(Math.round(this.coverSize * 0.28))
        .lineHeight(Math.round(this.coverSize * 0.34))
        .fontColor('rgba(255,255,255,0.22)')
        .textAlign(TextAlign.Center)
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
    Column() {
      // ---- cover ----
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
      .flexShrink(0)   // the cover keeps its square: without this it grew to fill the card and left no room
      .borderRadius({ topLeft: Radius.lg, topRight: Radius.lg })
      .clip(true)
      // an incomplete pack reads as dimmed: the cover still shows what the game is, the state line says why
      .opacity(this.state === CardState.Incomplete ? 0.4 : 1)

      // ---- caption ----
      // Kept to two plain Texts with fixed heights: a Row with layoutWeight inside a wrapping Flex measured to
      // zero height on the device, so the name and size never appeared while the cover did.
      Text(this.name.length > 0 ? this.name : this.titleId)
        .fontSize(Font.body)
        .fontColor(this.state === CardState.Incomplete ? TextToken.tertiary : TextToken.primary)
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
        .width(this.coverSize)
        .height(28)
        .padding({ left: Space.s3, right: Space.s3 })
        .textAlign(TextAlign.Start)
      Text(this.state === CardState.Incomplete
        ? `${this.sizeLabel} · 数据不完整`
        : `${this.sizeLabel} · 可运行`)
        .fontSize(Font.label)
        .fontColor(TextToken.secondary)
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
        .width(this.coverSize)
        .height(22)
        .padding({ left: Space.s3, right: Space.s3 })
        .textAlign(TextAlign.Start)
    }
    .width(this.coverSize)
    .height(this.coverSize + 52)
    .justifyContent(FlexAlign.Start)
    .alignItems(HorizontalAlign.Start)
    .backgroundColor(Bg.card)
    .borderRadius(Radius.lg)
    // the focus ring sits on the card's edge; a border of 0 keeps the layout identical when unfocused
    .border({
      width: this.state === CardState.Focus ? Size.focusRing
        : (this.state === CardState.Verifying ? Size.focusRing : 0),
      color: this.state === CardState.Verifying ? Semantic.warning : Semantic.borderFocus,
      radius: Radius.lg
    })
    .scale({
      x: this.state === CardState.Focus ? Motion.scaleFocus : (this.state === CardState.Press ? Motion.scalePress : 1),
      y: this.state === CardState.Focus ? Motion.scaleFocus : (this.state === CardState.Press ? Motion.scalePress : 1)
    })
    .shadow(this.state === CardState.Focus
      ? { radius: Size.focusGlow, color: Semantic.accentGlow, offsetX: 0, offsetY: 0 }
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
