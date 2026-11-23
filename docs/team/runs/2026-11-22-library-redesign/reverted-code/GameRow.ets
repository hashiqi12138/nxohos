/*
 * GameRow.ets - ONE row for one GameItem, whichever source it came from.
 *
 * The download tab had its own row (RemotePacksSection.ets:200) and the library its own card, with two different
 * vocabularies for the same facts. A row here reads GameItem (GameCatalog.ets) and switches on `state`, so an
 * installed game and a server-only game are the same widget: cover, name, one measured line, one action.
 *
 * What the row shows, in the order the eye needs it:
 *   cover    the captured frame (GamePerf.coverPath) or the generated placeholder, never an empty box
 *   name     the pack's own name, else the file name
 *   facts    "60 fps · 16.7 ms · HBN-AL80" - the measurement, with its device, or 暂无性能数据
 *   state    one pill: 已安装 / 可更新 / 可下载 / 下载中 42% / 导入中 / 数据不完整
 *   action   the one thing this row is for: 开始 / 更新 / 下载 / 暂停
 */
import { Bg, Text as TextToken, Semantic, CoverPalette, Space, Font, Radius, Size } from '../theme/Tokens';
import {
  GameItem, ItemState, ItemSource, stateLabel, canLaunch, canDownload
} from '../pages/GameCatalog';
import { humanSize } from '../pages/GameLibrary';

@Component
export struct GameRow {
  @Prop item: GameItem = {
    titleId: '', name: '', source: ItemSource.Local, state: ItemState.Unknown, root: '',
    installedStamp: '', remoteStamp: '', bytes: 0, sizeLabel: '', coverPath: '', perf: null,
    perfText: '', version: '', note: '', progress: -1, url: ''
  };
  /** Called when the row's main action is pressed (launch, download, or retry). */
  onAction: (it: GameItem) => void = () => {};
  /** Called on a long press: the detail sheet. */
  onDetails: (it: GameItem) => void = () => {};
  /** Called while a download runs: the pause button. */
  onCancel: (it: GameItem) => void = () => {};

  /** The cover: the captured frame when there is one, else the title's own palette with its initial. */
  @Builder
  cover() {
    Stack({ alignContent: Alignment.BottomEnd }) {
      if (this.item.coverPath.length > 0) {
        Image(this.item.coverPath)
          .width('100%')
          .height('100%')
          .objectFit(ImageFit.Cover)
          .interpolation(ImageInterpolation.High)
      } else {
        Column()
          .width('100%')
          .height('100%')
          .linearGradient({
            angle: 160,
            colors: [[CoverPalette.forTitle(this.item.titleId), 0.0],
              [CoverPalette.darken(CoverPalette.forTitle(this.item.titleId), 0.55), 1.0]]
          })
        Text(CoverPalette.initialOf(this.item.name))
          .fontSize(40)
          .fontWeight(FontWeight.Bold)
          .fontColor('rgba(255,255,255,0.14)')
          .margin({ right: 4, bottom: -6 })
      }
    }
    .width(96)
    .height(96)
    .borderRadius(Radius.md)
    .clip(true)
    .border({ width: 1, color: Semantic.borderSubtle, radius: Radius.md })
  }

  /** The pill: state, or the download's percentage while it runs. */
  private stateText(): string {
    if (this.item.state === ItemState.Downloading && this.item.progress >= 0) {
      return `下载中 ${Math.floor(this.item.progress * 100)}%`;
    }
    return stateLabel(this.item.state);
  }

  private stateColor(): string {
    switch (this.item.state) {
      case ItemState.Installed: return Semantic.success;
      case ItemState.UpdateAvailable: return Semantic.accent;
      case ItemState.Broken: return Semantic.danger;
      case ItemState.Failed: return Semantic.dangerHover;
      case ItemState.Downloading:
      case ItemState.Importing: return Semantic.warning;
      default: return TextToken.secondary;
    }
  }

  /** The action's label, from the state alone: one verb per state, never two buttons for one decision. */
  private actionLabel(): string {
    switch (this.item.state) {
      case ItemState.Installed: return '开始';
      case ItemState.UpdateAvailable: return '更新';
      case ItemState.Available: return '下载';
      case ItemState.Broken: return '重新导入';
      case ItemState.Failed: return '重试';
      default: return '';
    }
  }

  build() {
    Row({ space: Space.s4 }) {
      this.cover()
      Column({ space: 3 }) {
        Text(this.item.name.length > 0 ? this.item.name : this.item.titleId)
          .fontSize(Font.body)
          .fontWeight(FontWeight.Medium)
          .fontColor(TextToken.primary)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
        // the measurement, with the device that produced it: a number without a phone is not a claim
        Text(this.item.perfText)
          .fontSize(Font.caption)
          .fontColor(this.item.perf !== null ? Semantic.accent : TextToken.tertiary)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
        Text(this.item.note.length > 0
          ? this.item.note
          : `${this.item.sizeLabel}${this.item.version.length > 0 ? ' · ' + this.item.version : ''}`)
          .fontSize(Font.caption)
          .fontColor(this.item.state === ItemState.Broken || this.item.state === ItemState.Failed
            ? Semantic.dangerHover : TextToken.tertiary)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
      }
      .layoutWeight(1)
      .alignItems(HorizontalAlign.Start)

      Text(this.stateText())
        .fontSize(Font.label)
        .fontColor(this.stateColor())
        .padding({ left: Space.s3, right: Space.s3, top: 6, bottom: 6 })
        .backgroundColor(Bg.elevated)
        .borderRadius(Radius.full)

      if (this.item.state === ItemState.Downloading || this.item.state === ItemState.Importing) {
        Button('暂停')
          .height(36)
          .fontSize(Font.caption)
          .fontColor(Semantic.warning)
          .backgroundColor(Semantic.warningSoft)
          .borderRadius(Radius.full)
          .padding({ left: Space.s4, right: Space.s4 })
          .onClick(() => this.onCancel(this.item))
      } else if (this.actionLabel().length > 0) {
        Button(this.actionLabel())
          .height(36)
          .fontSize(Font.label)
          .fontWeight(FontWeight.Medium)
          .fontColor(TextToken.inverse)
          .backgroundColor(this.item.state === ItemState.Installed ? Semantic.accent : Semantic.coral)
          .borderRadius(Radius.full)
          .padding({ left: Space.s5, right: Space.s5 })
          .enabled(this.item.state === ItemState.Installed ? canLaunch(this.item) : true)
          .onClick(() => this.onAction(this.item))
      }

      // the progress bar sits under the whole row, not inside a cell: a download is progress on the game, not a
      // fourth fact beside it
    }
    .width('100%')
    .padding(Space.s4)
    .backgroundColor(Bg.card)
    .borderRadius(Radius.lg)
    .border({ width: 1, color: Semantic.borderSubtle })
    .gesture(
      LongPressGesture({ repeat: false, duration: 600 })
        .onAction(() => this.onDetails(this.item))
    )
    .accessibilityText(`${this.item.name}，${this.stateText()}，${this.item.perfText}`)
  }
}

/** A row plus the running job's bar, so the list stays one shape. */
@Component
export struct GameRowWithProgress {
  @Prop item: GameItem = {
    titleId: '', name: '', source: ItemSource.Local, state: ItemState.Unknown, root: '',
    installedStamp: '', remoteStamp: '', bytes: 0, sizeLabel: '', coverPath: '', perf: null,
    perfText: '', version: '', note: '', progress: -1, url: ''
  };
  onAction: (it: GameItem) => void = () => {};
  onDetails: (it: GameItem) => void = () => {};
  onCancel: (it: GameItem) => void = () => {};

  build() {
    Column({ space: Space.s2 }) {
      GameRow({ item: this.item, onAction: this.onAction, onDetails: this.onDetails, onCancel: this.onCancel })
      if (this.item.state === ItemState.Downloading || this.item.state === ItemState.Importing) {
        Progress({
          value: this.item.progress >= 0 ? this.item.progress * 100 : 0,
          total: 100,
          type: ProgressType.Linear
        })
          .width('100%')
          .height(4)
          .color(Semantic.accent)
          .backgroundColor(Semantic.track)
      }
    }
    .width('100%')
  }
}

/** The one-line summary the library's header shows: how many are installed, how many can be updated. */
export function catalogSummary(items: GameItem[]): string {
  let installed: number = 0;
  let updates: number = 0;
  let available: number = 0;
  let bytes: number = 0;
  for (const it of items) {
    if (it.state === ItemState.Installed) { installed++; bytes += it.bytes; }
    else if (it.state === ItemState.UpdateAvailable) { installed++; updates++; bytes += it.bytes; }
    else if (it.state === ItemState.Available) available++;
  }
  const parts: string[] = [`已安装 ${installed} 个 · ${humanSize(bytes)}`];
  if (updates > 0) parts.push(`可更新 ${updates}`);
  if (available > 0) parts.push(`可下载 ${available}`);
  return parts.join(' · ');
}
