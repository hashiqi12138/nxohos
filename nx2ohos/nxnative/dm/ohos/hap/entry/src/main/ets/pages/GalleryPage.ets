/*
 * GalleryPage.ets - the design system's proof page (S1).
 *
 * It renders every token and every component state on one scroll, so the palette, type scale, spacing rhythm and
 * focus treatment can be judged before any product screen is built. It is a development page: the game library
 * replaces it as the entry point, and it stays reachable for future design work.
 */

import { Bg, Text as TextToken, Semantic, CoverPalette, Space, Font, Radius, Motion, Size, Z } from '../theme/Tokens';

/** A row of a colour swatch, so a token's name and value stay visible next to it. */
interface Swatch {
  name: string;
  value: string;
  note: string;
}

@Entry
@Component
struct GalleryPage {
  private readonly bgSwatches: Swatch[] = [
    { name: 'bg/base', value: Bg.base, note: 'app background' },
    { name: 'bg/elevated', value: Bg.elevated, note: 'panels, sheets' },
    { name: 'bg/card', value: Bg.card, note: 'a card at rest' },
    { name: 'bg/cardHover', value: Bg.cardHover, note: 'focus / press' }
  ];

  private readonly textSwatches: Swatch[] = [
    { name: 'text/primary', value: TextToken.primary, note: '15.8:1' },
    { name: 'text/secondary', value: TextToken.secondary, note: '6.4:1' },
    { name: 'text/tertiary', value: TextToken.tertiary, note: '3.4:1 decorative' }
  ];

  private readonly semanticSwatches: Swatch[] = [
    { name: 'accent', value: Semantic.accent, note: 'focus, primary CTA' },
    { name: 'danger', value: Semantic.danger, note: 'destructive' },
    { name: 'success', value: Semantic.success, note: 'runnable' },
    { name: 'warning', value: Semantic.warning, note: 'incomplete data' }
  ];

  /** One card in the gallery, so the states can be compared side by side. */
  @Builder
  cardSample(state: string) {
    Column() {
      // cover: the placeholder a pack without an icon gets
      Stack() {
        Column()
          .width('100%')
          .height(Size.cardCoverLandscape)
          .backgroundColor(CoverPalette.forTitle('01007EF00011E000'))
          .borderRadius({ topLeft: Radius.lg, topRight: Radius.lg })
        Text('Z')
          .fontSize(Font.coverInitial)
          .fontColor('rgba(255,255,255,0.22)')
      }
      .width(Size.cardCoverLandscape)
      .height(Size.cardCoverLandscape)

      Column({ space: Space.s1 }) {
        Text('The Legend of Zelda')
          .fontSize(Font.body)
          .fontColor(state === 'disabled' ? TextToken.tertiary : TextToken.primary)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
        Text(state === 'disabled' ? 'incomplete' : '14.7 GB · ready')
          .fontSize(Font.label)
          .fontColor(state === 'disabled' ? TextToken.tertiary : TextToken.secondary)
      }
      .alignItems(HorizontalAlign.Start)
      .width('100%')
      .padding(Space.s3)
    }
    .width(Size.cardCoverLandscape)
    .backgroundColor(Bg.card)
    .borderRadius(Radius.lg)
    .border({
      width: state === 'focus' ? Size.focusRing : 0,
      color: Semantic.borderFocus,
      radius: Radius.lg
    })
    .scale({ x: state === 'focus' ? Motion.scaleFocus : (state === 'press' ? Motion.scalePress : 1),
             y: state === 'focus' ? Motion.scaleFocus : (state === 'press' ? Motion.scalePress : 1) })
    .shadow(state === 'focus'
      ? { radius: Size.focusGlow, color: Semantic.accentGlow, offsetX: 0, offsetY: 0 }
      : { radius: 0, color: 'rgba(0,0,0,0)', offsetX: 0, offsetY: 0 })
    .opacity(state === 'disabled' ? 0.55 : 1)
  }

  @Builder
  section(title: string, hint: string) {
    Column({ space: Space.s3 }) {
      Row({ space: Space.s2 }) {
        Text(title).fontSize(Font.title).fontColor(TextToken.primary)
        Text(hint).fontSize(Font.caption).fontColor(TextToken.tertiary)
      }
      .width('100%')
    }
    .alignItems(HorizontalAlign.Start)
    .width('100%')
    .margin({ top: Space.s6 })
  }

  @Builder
  swatchRow(s: Swatch) {
    Row({ space: Space.s3 }) {
      Column()
        .width(48)
        .height(48)
        .backgroundColor(s.value)
        .borderRadius(Radius.sm)
        .border({ width: 1, color: Semantic.borderSubtle, radius: Radius.sm })
      Column({ space: 2 }) {
        Text(s.name).fontSize(Font.label).fontColor(TextToken.primary)
        Text(s.value).fontSize(Font.caption).fontColor(TextToken.secondary)
        Text(s.note).fontSize(Font.caption).fontColor(TextToken.tertiary)
      }
      .alignItems(HorizontalAlign.Start)
    }
    .width('100%')
  }

  @Builder
  buttonSample(label: string, bg: string, fg: string, border: string, disabled: boolean) {
    Button(label)
      .fontSize(Font.body)
      .fontColor(fg)
      .backgroundColor(bg)
      .border({ width: border.length ? 1 : 0, color: border, radius: Radius.md })
      .borderRadius(Radius.md)
      .height(Size.buttonLg)
      .padding({ left: Space.s5, right: Space.s5 })
      .enabled(!disabled)
      .opacity(disabled ? 0.4 : 1)
  }

  build() {
    Scroll() {
      Column({ space: Space.s4 }) {
        Text('Design system').fontSize(Font.display).fontColor(TextToken.primary)
        Text('S1 proof page: every token and component state on one scroll.')
          .fontSize(Font.body).fontColor(TextToken.secondary)

        // ---- backgrounds ----
        this.section('Background layers', 'depth from these four, not from borders')
        ForEach(this.bgSwatches, (s: Swatch) => { this.swatchRow(s) }, (s: Swatch) => s.name)

        // ---- text ----
        this.section('Text', 'ratios against bg/base')
        ForEach(this.textSwatches, (s: Swatch) => { this.swatchRow(s) }, (s: Swatch) => s.name)

        // ---- semantic ----
        this.section('Semantic', 'focus, actions, state')
        ForEach(this.semanticSwatches, (s: Swatch) => { this.swatchRow(s) }, (s: Swatch) => s.name)

        // ---- cover palette ----
        this.section('Placeholder covers', 'stable per title ID')
        Row({ space: Space.s2 }) {
          ForEach(CoverPalette.colors, (c: string) => {
            Column()
              .width(56)
              .height(56)
              .backgroundColor(c)
              .borderRadius(Radius.sm)
          }, (c: string) => c)
        }
        .width('100%')

        // ---- type scale ----
        this.section('Type scale', 'system family: it covers Chinese')
        Text('display 32').fontSize(Font.display).fontColor(TextToken.primary)
        Text('title 20').fontSize(Font.title).fontColor(TextToken.primary)
        Text('body 16').fontSize(Font.body).fontColor(TextToken.primary)
        Text('label 14').fontSize(Font.label).fontColor(TextToken.secondary)
        Text('caption 12').fontSize(Font.caption).fontColor(TextToken.tertiary)

        // ---- card states ----
        this.section('Game card states', 'focus grows 1.06 and glows; press shrinks 0.97')
        Row({ space: Space.s4 }) {
          Column({ space: Space.s2 }) {
            this.cardSample('default')
            Text('default').fontSize(Font.caption).fontColor(TextToken.tertiary)
          }
          Column({ space: Space.s2 }) {
            this.cardSample('focus')
            Text('focus').fontSize(Font.caption).fontColor(TextToken.tertiary)
          }
          Column({ space: Space.s2 }) {
            this.cardSample('press')
            Text('press').fontSize(Font.caption).fontColor(TextToken.tertiary)
          }
          Column({ space: Space.s2 }) {
            this.cardSample('disabled')
            Text('incomplete').fontSize(Font.caption).fontColor(TextToken.tertiary)
          }
        }
        .alignItems(VerticalAlign.Top)

        // ---- buttons ----
        this.section('Buttons', 'every one is at least a 48 dp touch target')
        Row({ space: Space.s3 }) {
          this.buttonSample('Launch', Semantic.accent, TextToken.inverse, '', false)
          this.buttonSample('Verify', 'transparent', TextToken.primary, Semantic.borderSubtle, false)
          this.buttonSample('Delete', 'transparent', Semantic.danger, Semantic.danger, false)
          this.buttonSample('Cancel', 'transparent', TextToken.secondary, '', false)
          this.buttonSample('Disabled', Semantic.accent, TextToken.inverse, '', true)
        }

        // ---- progress ----
        this.section('Progress', 'determinate: a long task needs a known position')
        Column({ space: Space.s2 }) {
          Row() {
            Column()
              .layoutWeight(42)
              .height(Size.progressHeight)
              .backgroundColor(Semantic.accent)
              .borderRadius({ topLeft: Radius.full, bottomLeft: Radius.full })
            Column()
              .layoutWeight(58)
              .height(Size.progressHeight)
              .backgroundColor(Semantic.track)
              .borderRadius({ topRight: Radius.full, bottomRight: Radius.full })
          }
          .width('100%')
          Row() {
            Text('6.2 GB / 14.7 GB').fontSize(Font.label).fontColor(TextToken.secondary)
            Blank()
            Text('about 18 min').fontSize(Font.label).fontColor(TextToken.secondary)
          }
          .width('100%')
        }
        .width('100%')

        // ---- spacing rhythm ----
        this.section('Spacing', 'a 4 dp rhythm')
        Row({ space: Space.s3 }) {
          ForEach([Space.s1, Space.s2, Space.s3, Space.s4, Space.s5, Space.s6], (v: number) => {
            Column({ space: Space.s1 }) {
              Column().width(v * 2).height(Space.s4).backgroundColor(Semantic.accent).borderRadius(2)
              Text(`${v}`).fontSize(Font.caption).fontColor(TextToken.tertiary)
            }
          }, (v: number) => `${v}`)
        }
        .alignItems(VerticalAlign.Bottom)

        Blank().height(Space.s8)
      }
      .width('100%')
      .padding(Space.s6)
      .alignItems(HorizontalAlign.Start)
    }
    .width('100%')
    .height('100%')
    .backgroundColor(Bg.base)
    .scrollBar(BarState.Auto)
  }
}
