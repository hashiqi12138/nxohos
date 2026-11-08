/*
 * Index.ets — the game page: one full-screen surface, nothing else on top of it.
 *
 * The XComponent is the only way to get a native window that VK_OHOS_surface can present to (XComponentType.TEXTURE is not one), and this
 * SDK has no napi_value form of the XComponent callbacks any more, so the surface id is taken from the controller and handed to the native
 * module — the same path the M0 probe proved on the device.
 *
 * What starts: the game when its files are in the sandbox (files/dm/RomFS and the NSOs in files/dm), otherwise the self-test scene, so the
 * window chain can be checked on any build.
 */
import dmhost from 'libdmhost.so';
import { fileIo as fs } from '@kit.CoreFileKit';
import { util } from '@kit.ArkTS';
import { display } from '@kit.ArkUI';
import { resourceManager } from '@kit.LocalizationKit';

@Entry
@Component
struct Index {
  private controller: XComponentController = new XComponentController();
  private started: boolean = false;
  private starting: boolean = false;   // the first start is in flight: a second onLoad (rotation during a multi-GB first staging) must not stage beside it
  private isGame: boolean = false;
  @State status: string = 'dm: waiting for the surface';   // also the only way to tell a screenshot of this window from a blank one (a surface with no frame is black)

  // rmdir() answers 13900032 (Directory not empty) for a folder that has content — it is not recursive — so the old data set is walked and removed
  // bottom-up. false: nothing there. A removal that fails throws, and stageData turns that into 'fail': a half-cleared folder must not be recorded.
  private removeTree(path: string): boolean {
    let isDir: boolean = false;
    try {
      isDir = fs.statSync(path).isDirectory();
    } catch (e) {
      return false;
    }
    if (!isDir) {
      fs.unlinkSync(path);
      return true;
    }
    for (const name of fs.listFileSync(path)) {
      this.removeTree(path + '/' + name);
    }
    fs.rmdirSync(path);
    return true;
  }

  // A build that carries its own data packs the game under resources/rawfile/dm and lists it in dm.files.tsv (rawfile directories cannot be
  // listed at run time). The first start — or a start after the data set changed (its stamp is the file's header) — copies it into files/dm,
  // which is what startGame reads. Files are streamed through a fixed buffer: a RomFS holds files of hundreds of MB.
  // 'none': the build carries no data; 'ok': files/dm is complete; 'fail': the copy broke (a partial pack must not be started).
  private async stageData(root: string): Promise<string> {
    const ctx = this.getUIContext().getHostContext();
    if (!ctx) return 'none';
    let text: string = '';
    try {
      const man = await ctx.resourceManager.getRawFileContent('dm.files.tsv');
      text = util.TextDecoder.create('utf-8').decodeWithStream(man);
    } catch (e) {
      // no manifest: a build that carries no data pack (the canonical one), not a broken pack: the self-test scene is the right thing to run
      dmhost.log('staging: this build carries no data pack');
      return 'none';
    }
    try {
      const lines = text.split('\n');
      const stamp: string = lines.length && lines[0].startsWith('# DM_DATA ') ? lines[0].substring(10).trim() : '';
      const stampPath: string = root + '/.stamp';
      let cur: string = '';
      try {
        cur = fs.readTextSync(stampPath).trim();
      } catch (e) {
        cur = '';
      }
      if (stamp.length && cur === stamp) return 'ok';
      try {
        fs.mkdirSync(root, true);
      } catch (e) {
      }
      // A new data set replaces the old one whole: the pack owns these three folders (porter/datapack.py OWNED), and a file an older version had, or a
      // DLC the pack no longer carries, would otherwise stay behind and be read as if the pack still had it.
      let cleared: number = 0;
      for (const owned of ['mods', 'RomFS', 'aoc']) {
        if (!this.removeTree(root + '/' + owned)) continue;   // an error here stops the stage: a half-cleared folder must not be called done
        cleared++;
      }
      if (cleared) dmhost.log(`staging: removed the previous data set (${cleared} folders)`);
      let n: number = 0;
      const chunk: ArrayBuffer = new ArrayBuffer(1 << 20);
      // A pack larger than the 4 GB one signed HAP can carry is split over feature modules (data1, data2, ...): the manifest's third column names the
      // module that holds the file, and that module's resourceManager hands out its rawfd (createModuleContext is what reaches a sibling module's files).
      const rms: Map<string, resourceManager.ResourceManager> = new Map();
      for (let i = 1; i < lines.length; i++) {
        const line: string = lines[i];
        if (!line.length) continue;
        const parts: string[] = line.split('\t');
        const rel: string = parts[0];
        const mod: string = parts.length > 2 ? parts[2] : 'entry';
        const dst: string = root + '/' + rel;
        const dir: string = dst.substring(0, dst.lastIndexOf('/'));
        try {
          fs.mkdirSync(dir, true);
        } catch (e) {
        }
        let rm: resourceManager.ResourceManager = ctx.resourceManager;
        if (mod !== 'entry' && mod.length) {
          let part: resourceManager.ResourceManager | undefined = rms.get(mod);
          if (part === undefined) {
            part = ctx.createModuleContext(mod).resourceManager;
            rms.set(mod, part);
          }
          rm = part;
        }
        const rf = await rm.getRawFd('dm/' + rel);
        const f = fs.openSync(dst, fs.OpenMode.CREATE | fs.OpenMode.READ_WRITE | fs.OpenMode.TRUNC);
        try {
          // ReadOptions.offset is counted from the file pointer, which reading advances (start position = filePointer + offset), so seeking once and
          // then reading without an offset is the only form that stays correct across chunks
          fs.lseek(rf.fd, rf.offset, fs.WhenceType.SEEK_SET);
          let done: number = 0;
          while (done < rf.length) {
            const want: number = Math.min(chunk.byteLength, rf.length - done);
            const got: number = fs.readSync(rf.fd, chunk, { length: want });
            if (got <= 0) break;
            fs.writeSync(f.fd, chunk, { length: got });
            done += got;
          }
          if (done !== rf.length) throw new Error(`short copy of ${rel}: ${done}/${rf.length}`);
        } finally {
          fs.closeSync(f);
          await rm.closeRawFd('dm/' + rel);
        }
        n++;
        if (n % 200 === 0) dmhost.log(`staged ${n} files`);
      }
      const sf = fs.openSync(stampPath, fs.OpenMode.CREATE | fs.OpenMode.READ_WRITE | fs.OpenMode.TRUNC);
      try {
        fs.writeSync(sf.fd, stamp);
      } finally {
        fs.closeSync(sf);
      }
      dmhost.log(`staged ${n} files (stamp ${stamp.substring(0, 12)})`);
      return 'ok';
    } catch (e) {
      // a business exception carries only {code, message}: String(e) is '[object Object]' and says nothing (measured on this page's first versions)
      const why: string = e instanceof Error ? e.message : JSON.stringify(e);
      dmhost.log(`staging failed: ${why}`);
      return 'fail';
    }
  }

  // The surface is reported on every load: a rotation or a trip to the background destroys the XComponent and hands us a new one.
  private reportSurface(): void {
    const ctx = this.getUIContext().getHostContext();
    const files: string = ctx?.filesDir ?? '';
    const root: string = files + '/dm';
    const surfaceId: string = this.controller.getXComponentSurfaceId();
    // the surface's own pixels, not the layout's vp and not the display's size (which is the whole screen in split screen or a free-form window)
    let w: number = 0;
    let h: number = 0;
    try {
      const r = this.controller.getXComponentSurfaceRect();
      w = Math.round(r.surfaceWidth);
      h = Math.round(r.surfaceHeight);
    } catch (e) {
      dmhost.log(`surface rect failed: ${e}`);
    }
    if (!w || !h) {
      try {
        const d = display.getDefaultDisplaySync();
        w = d.width;
        h = d.height;
      } catch (e) {
        dmhost.log(`display query failed: ${e}`);
      }
    }
    if (!w || !h) {
      w = 1280;
      h = 720;
    }
    dmhost.log(`surfaceId=${surfaceId} ${w}x${h} root=${root}`);
    this.status = `dm: surface ${w}x${h}`;
    dmhost.setSurfaceId(surfaceId, w, h);
  }

  private async startOnce(): Promise<void> {
    if (this.starting || (this.started && this.isGame)) return;   // a game owns the native thread for the life of the process
    this.starting = true;
    const ctx = this.getUIContext().getHostContext();
    const files: string = ctx?.filesDir ?? '';
    const cache: string = ctx?.cacheDir ?? '';
    const root: string = files + '/dm';
    const staged: string = await this.stageData(root);
    let hasGame: boolean = false;
    try {
      // the packed layout mirrors the Android pack: mods/ (the rewritten modules) and RomFS/ beside env.txt and shaders.dmsv
      hasGame = staged !== 'fail' && fs.accessSync(root + '/mods/main.patched.bin') && fs.accessSync(root + '/RomFS');
    } catch (e) {
      hasGame = false;
    }
    if (hasGame) {
      this.isGame = true;
      this.status = 'dm: game';
      dmhost.log('starting the game');
      dmhost.startGame(root, files + '/save', cache);
    } else {
      if (staged === 'fail') dmhost.log('the data pack is incomplete: the game is not started');
      else if (!hasGame) dmhost.log('no game in the sandbox, starting the self-test scene');
      this.isGame = false;
      this.status = staged === 'fail' ? 'dm: self-test (the pack did not stage)' : 'dm: self-test';
      dmhost.stopDemo();   // a surface that came back after onDestroy starts the scene once more
      dmhost.startDemo();
    }
    this.started = true;
    this.starting = false;
  }

  build() {
    Stack() {
      XComponent({ id: 'dmhost', type: XComponentType.SURFACE, controller: this.controller })
        .width('100%')
        .height('100%')
        .onLoad(() => {
          this.reportSurface();
          this.startOnce();
        })
        .onDestroy(() => {
          dmhost.stopDemo();
          dmhost.surfaceLost();
        })
      // one line at the bottom, over the surface: what this build is doing, and proof for a screenshot that the window itself is on screen
      Text(this.status)
        .fontSize(12)
        .fontColor('#808080')
        .align(Alignment.BottomStart)
        .margin({ left: 12, bottom: 8 })
    }
    .width('100%')
    .height('100%')
    .backgroundColor(Color.Black)
  }
}
