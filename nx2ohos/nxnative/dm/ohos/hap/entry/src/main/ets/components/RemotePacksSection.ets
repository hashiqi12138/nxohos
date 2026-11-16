/*
 * RemotePacksSection.ets - the packs a local server offers, under the installed library (S4).
 *
 * A player who has played everything looks right, and the way to get more belongs there. The section is a separate
 * component because a remote pack has states an installed game never has (listing, downloading, importing, failed),
 * and because its state must outlive this page: RemoteDownloads holds it, this only draws it.
 *
 * With no source set the section draws nothing at all - a device without a server looks exactly as S2 left it.
 */

import dmhost from 'libdmhost.so';
import { preferences } from '@kit.ArkData';
import { Bg, Text as TextToken, Semantic, Space, Font, Radius } from '../theme/Tokens';
import { RemotePack, PREF_SOURCE, listRemote } from '../pages/RemoteLibrary';
import { RemoteDownloads, PackProgress, PackState, speedLabel, etaLabel, sizeLabel } from './RemoteDownloads';

@Component
export struct RemotePacksSection {
  /** Called after an import finishes, so the library re-reads what is on disk. */
  onImported: () => void = () => {};

  @State rows: PackProgress[] = [];
  @State source: string = '';
  @State listing: boolean = false;
  @State status: string = '';        // the section's own line: '3 packs', 'nothing here', a failure
  @State failed: boolean = false;
  private tick: number = -1;         // repaints the speed and the ETA while a download runs

  aboutToAppear(): void {
    const bump: () => void = () => {
      this.rows = RemoteDownloads.list().slice();
      if (RemoteDownloads.busy() && this.tick < 0) {
        // the registry notifies on every chunk; a 4 Hz repaint is enough for a speed readout and costs nothing
        this.tick = setInterval(() => { this.rows = RemoteDownloads.list().slice(); }, 250);
      } else if (!RemoteDownloads.busy() && this.tick >= 0) {
        clearInterval(this.tick); this.tick = -1;
      }
    };
    RemoteDownloads.subscribe(bump);
    this.load();
  }

  aboutToDisappear(): void {
    if (this.tick >= 0) { clearInterval(this.tick); this.tick = -1; }
  }

  private async load(): Promise<void> {
    try {
      const ctx = this.getUIContext().getHostContext();
      if (!ctx) return;
      const lib = await preferences.getPreferences(ctx, 'library');
      const src = lib.getSync(PREF_SOURCE, '') as string;
      this.source = typeof src === 'string' ? src : '';
      if (!this.source.length) { dmhost.log('remote: no source set'); this.rows = []; this.status = ''; return; }
      dmhost.log('remote: source ' + this.source);
      RemoteDownloads.attach(this.source, ctx.filesDir, ctx.cacheDir);
      await this.refresh();
    } catch (e) {
      this.status = '读取设置失败';
      this.failed = true;
    }
  }

  /** Ask the server what it holds. A failure here is the section's line, never a dialog. */
  async refresh(): Promise<void> {
    if (!this.source.length || this.listing) return;
    this.listing = true;
    this.status = '正在连接…';
    this.failed = false;
    try {
      const packs: RemotePack[] = await listRemote(this.source);
      RemoteDownloads.setPacks(packs);
      this.rows = RemoteDownloads.list().slice();
      this.status = packs.length ? '' : '这台服务器上没有游戏包';
    } catch (e) {
      this.rows = [];
      this.failed = true;
      this.status = '连不上：' + (e instanceof Error ? e.message : JSON.stringify(e));
    } finally {
      this.listing = false;
    }
  }

  private async download(title: string): Promise<void> {
    await RemoteDownloads.start(title);
    this.rows = RemoteDownloads.list().slice();
    if (RemoteDownloads.list().some((r: PackProgress): boolean => r.state === PackState.Installed)) this.onImported();
  }

  /** '2.1 MB/s · 剩余 8 分 30 秒' for a running download; '' otherwise. */
  private paceLabel(r: PackProgress): string {
    if (r.state !== PackState.Downloading) return '';
    const sp: string = speedLabel(r.speed);
    const eta: string = etaLabel(r.total - r.got, r.speed);
    if (!sp.length) return '';
    return eta.length ? (sp + ' · 剩余 ' + eta) : sp;
  }

  build() {
    // Nothing to say without a source: a device with no server sees the plain library, as before.
    if (!this.source.length) {
      Column().height(0)
    } else {
      Column({ space: Space.s2 }) {
        // ---- the section's header: what it is, where it comes from, and a way to ask again ----
        Row({ space: Space.s2 }) {
          Text('可下载').fontSize(Font.label).fontColor(TextToken.secondary)
          Blank()
          Text(this.source.replace(/^https?:\/\//, ''))
            .fontSize(Font.caption).fontColor(TextToken.tertiary)
            .maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis })
          if (!this.listing) {
            Text('刷新').fontSize(Font.label).fontColor(Semantic.accent)
              .onClick(() => { this.refresh(); })
          } else {
            Text('…').fontSize(Font.label).fontColor(TextToken.tertiary)
          }
        }.width('100%')

        if (this.status.length) {
          Text(this.status)
            .fontSize(Font.caption)
            .fontColor(this.failed ? Semantic.danger : TextToken.tertiary)
            .width('100%')
        }

        ForEach(this.rows, (r: PackProgress) => {
          Column({ space: Space.s1 }) {
            Row({ space: Space.s2 }) {
              Column({ space: 2 }) {
                Text(r.pack.name).fontSize(Font.body).fontColor(TextToken.primary)
                  .maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis }).width('100%')
                Text(r.state === PackState.Failed ? ('失败：' + r.note) : r.pack.title)
                  .fontSize(Font.caption)
                  .fontColor(r.state === PackState.Failed ? Semantic.danger : TextToken.tertiary)
                  .maxLines(1).textOverflow({ overflow: TextOverflow.Ellipsis }).width('100%')
              }.layoutWeight(1).alignItems(HorizontalAlign.Start)

              // the right-hand slot is the row's one action: download, progress, retry, or a done mark
              if (r.state === PackState.Downloading) {
                Text(r.total > 0 ? (Math.floor(r.got * 100 / r.total) + '%') : sizeLabel(r.got))
                  .fontSize(Font.label).fontColor(Semantic.accent).width(72).textAlign(TextAlign.End)
                  .onClick(() => { RemoteDownloads.cancel(r.pack.title); })
              } else if (r.state === PackState.Importing) {
                Text('导入中').fontSize(Font.label).fontColor(TextToken.tertiary).width(72).textAlign(TextAlign.End)
              } else if (r.state === PackState.Installed) {
                Text('✓ 已安装').fontSize(Font.label).fontColor(Semantic.accent).width(72).textAlign(TextAlign.End)
              } else {
                Text(r.state === PackState.Failed ? '重试' : '下载')
                  .fontSize(Font.label)
                  .fontColor(Semantic.accent)
                  .width(72).textAlign(TextAlign.End)
                  .onClick(() => { this.download(r.pack.title); })
              }
            }.width('100%')

            // the running download's bar and pace: without an ETA a 1.17 GB pull is a guess
            if (r.state === PackState.Downloading) {
              Progress({ value: r.total > 0 ? r.got : 0, total: r.total > 0 ? r.total : 1, type: ProgressType.Linear })
                .width('100%').height(3).color(Semantic.accent).backgroundColor(Bg.cardHover)
              Row() {
                Text(sizeLabel(r.pack.bytes)).fontSize(Font.caption).fontColor(TextToken.tertiary)
                Blank()
                Text(this.paceLabel(r)).fontSize(Font.caption).fontColor(TextToken.tertiary)
              }.width('100%')
            }
          }
          .width('100%')
          .padding({ top: Space.s3, bottom: Space.s3, left: Space.s3, right: Space.s3 })
          .backgroundColor(Bg.card)
          .borderRadius(Radius.md)
        }, (r: PackProgress) => r.pack.title)
      }
      .width('100%')
      .padding({ top: Space.s3, bottom: Space.s3 })
    }
  }
}

