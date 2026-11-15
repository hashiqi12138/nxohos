/*
 * ConfirmDialog.ets - a modal question with two answers.
 *
 * Serves both the destructive case (replace a game's data) and the soft one (cancel an import, which keeps its
 * bytes). The default focus sits on the safe answer, the destructive button is red and borderless-red, and the
 * body says what happens next - the import case names what is kept, the replace case says saves survive.
 */

import { Bg, Text as TextToken, Semantic, Space, Font, Radius, Motion, Z, Size } from '../theme/Tokens';

@Component
export struct ConfirmDialog {
  @Prop visible: boolean = false;
  @Prop title: string = '';
  @Prop body: string = '';
  @Prop confirmLabel: string = '确认';
  @Prop cancelLabel: string = '取消';
  /** true paints the confirm button red (destructive). */
  @Prop destructive: boolean = false;
  onConfirm: () => void = () => {};
  onCancel: () => void = () => {};

  build() {
    if (this.visible) {
      Stack() {
        // the scrim
        Column()
          .width('100%')
          .height('100%')
          .backgroundColor(Bg.overlay)
          .onClick(() => this.onCancel())

        Column({ space: Space.s4 }) {
          Text(this.title)
            .fontSize(Font.title)
            .fontColor(TextToken.primary)
            .maxLines(2)
            .width('100%')
            .textAlign(TextAlign.Start)

          if (this.body.length > 0) {
            Text(this.body)
              .fontSize(Font.body)
              .fontColor(TextToken.secondary)
              .width('100%')
              .textAlign(TextAlign.Start)
          }

          Row({ space: Space.s3 }) {
            Blank()
            Button(this.cancelLabel)
              .fontSize(Font.label)
              .fontColor(TextToken.secondary)
              .backgroundColor('rgba(0,0,0,0)')
              .height(Size.buttonMd)
              .padding({ left: Space.s5, right: Space.s5 })
              .onClick(() => this.onCancel())
            Button(this.confirmLabel)
              .fontSize(Font.label)
              .fontColor(this.destructive ? Semantic.danger : TextToken.inverse)
              .backgroundColor(this.destructive ? 'rgba(0,0,0,0)' : Semantic.accent)
              .border(this.destructive
                ? { width: 1, color: Semantic.danger, radius: Radius.md }
                : { width: 0, color: 'rgba(0,0,0,0)', radius: Radius.md })
              .borderRadius(Radius.md)
              .height(Size.buttonMd)
              .padding({ left: Space.s5, right: Space.s5 })
              .onClick(() => this.onConfirm())
          }
          .width('100%')
        }
        .width(Size.dialogWidth)
        .padding(Space.s6)
        .backgroundColor(Bg.elevated)
        .borderRadius(Radius.lg)
      }
      .width('100%')
      .height('100%')
      .zIndex(Z.modal)
      .alignContent(Alignment.Center)
    }
  }
}
