/*
 * GamePerfSampler.ets - collects what dmhost measures while a game runs, and takes the game's own picture.
 *
 * The numbers were already there and only reached the screen: dmhost.getFps() (napi_bridge.c:121) returns the last
 * closed second's frame count, its mean frame time and the 10 s mean, and Index draws them in a corner pill
 * (Index.ets:1531). A pill is gone when the game closes, so nothing could be compared between two titles. This
 * module turns the same poll into records on disk (GamePerf.ets' perf.json).
 *
 * The picture: an XComponent's pixels are not part of the ArkUI tree, so componentSnapshot returns a transparent
 * hole where the game is (the API's own note says to use createPixelMapFromSurface for XComponent streams). The
 * surface id Index already hands to dmhost (Index.ets:1299) is the same id this reads, so the shot is the frame the
 * player is looking at, not a composited screenshot of the app.
 *
 * Deliberately NOT hdc snapshot_display: that needs a host cable, captures the whole screen (status bar, the pad
 * guide, the fps pill) and cannot be taken by the player. It stays the fallback in the tooling
 * (dm/ohos/perf_capture.py) for a title that renders outside the XComponent.
 */
import dmhost from 'libdmhost.so';
import { fileIo as fs } from '@kit.CoreFileKit';
import { image } from '@kit.ImageKit';
import {
  PerfRecord, PerfDevice, MediaShot, thisDevice, fpsOf, isoNow, appendPerf, addShot,
  PERF_MIN_FRAMES, packStampOf
} from './GamePerf';

/** What one open sampling session knows: the title, the surface, and the window being accumulated. */
export class PerfSampler {
  private root: string = '';
  private titleId: string = '';
  private stamp: string = '';
  private appVersion: string = '';
  private scene: string = 'field';
  private surfaceId: string = '';
  private width: number = 0;
  private height: number = 0;
  private timer: number = -1;
  /** Frames counted since the window opened, and the sum of the per-second means, for the window's own p50-ish mean. */
  private winFrames: number = 0;
  private winMsSum: number = 0;
  private winSeconds: number = 0;
  /** The worst single second seen in the window: a stutter a mean would hide. */
  private winWorstMs: number = 0;
  /** The last shot's epoch, so two shots are never taken in the same second. */
  private lastShotAt: number = 0;

  /** Point the sampler at a running game. Called from the game page once its surface exists. */
  begin(root: string, titleId: string, appVersion: string, surfaceId: string, w: number, h: number): void {
    this.root = root;
    this.titleId = titleId;
    this.stamp = packStampOf(root);
    this.appVersion = appVersion;
    this.surfaceId = surfaceId;
    this.width = w;
    this.height = h;
    this.resetWindow();
    this.stop();
    // The native counters close a one-second window per poll; 500 ms cannot miss one and cannot pile up on the UI
    // thread (Index.ets:638 makes the same argument for its badge).
    this.timer = setInterval(() => this.poll(), 500);
  }

  /** The surface is re-created on a rotation or a trip to the background: keep sampling, with the new geometry. */
  setSurface(surfaceId: string, w: number, h: number): void {
    this.surfaceId = surfaceId;
    this.width = w;
    this.height = h;
  }

  /** The scene label a shot and its record carry. The page sets it from its own state (menu, cutscene, field). */
  setScene(scene: string): void {
    if (scene.length > 0 && scene !== this.scene) {
      // a scene change closes the window: mixing two scenes into one number is the mistake measure.py's header
      // names first (measure.py:3-7)
      this.flush();
      this.scene = scene;
    }
  }

  /** Stop polling and keep whatever the open window holds. Called when the game page goes away. */
  end(): void {
    this.stop();
    this.flush();
  }

  private stop(): void {
    if (this.timer >= 0) {
      clearInterval(this.timer);
      this.timer = -1;
    }
  }

  private resetWindow(): void {
    this.winFrames = 0;
    this.winMsSum = 0;
    this.winSeconds = 0;
    this.winWorstMs = 0;
  }

  /** One poll: accumulate the closed second, and write a record every 10 s of game time. */
  private poll(): void {
    let now: number = 0;
    let avgMs: number = 0;
    try {
      const r: Record<string, number> = dmhost.getFps() as Record<string, number>;
      now = Math.round(r['now']);
      avgMs = r['avgMs'];
    } catch (e) {
      return;
    }
    // A paused game reports zeros: a zero second is not a 0 fps measurement, it is no measurement
    if (now <= 0 || avgMs <= 0) return;
    this.winFrames += now;
    this.winMsSum += avgMs;
    this.winSeconds++;
    if (avgMs > this.winWorstMs) this.winWorstMs = avgMs;
    if (this.winSeconds >= 10) this.flush();
  }

  /**
   * Write the open window as one record.
   *
   * The window's frame time is the mean of its seconds, which is the p50 of the per-frame gaps to the precision a
   * card shows; `frames` says how much evidence stands behind it (PERF_MIN_FRAMES rejects a window that is too
   * short to be worth a claim).
   */
  flush(): boolean {
    if (this.winSeconds <= 0 || this.winFrames < PERF_MIN_FRAMES) {
      this.resetWindow();
      return false;
    }
    const frameMs: number = this.winMsSum / this.winSeconds;
    const dev: PerfDevice = this.deviceForRecord();
    const rec: PerfRecord = {
      schema: 1,
      titleId: this.titleId,
      source: 'device',
      scene: this.scene,
      device: dev,
      fpsP50: fpsOf(frameMs),
      frameMsP50: Math.round(frameMs * 100) / 100,
      // dmhost's getFps() carries no GPU/replay/set split: those live in the hilog timing report (hle_nvn.c's
      // "timing up to frame" line) and are filled by the tooling path, not here. -1 means "not measured", which a
      // reader must not draw as 0.0 ms.
      gpuMsP50: -1,
      replayMsP50: -1,
      setsMsP50: -1,
      frames: this.winFrames,
      capturedAt: isoNow(),
      appVersion: this.appVersion,
      stamp: this.stamp
    };
    appendPerf(this.root, rec);
    this.resetWindow();
    return true;
  }

  private deviceForRecord(): PerfDevice {
    const d: PerfDevice = thisDevice();
    d.width = this.width;
    d.height = this.height;
    return d;
  }

  /**
   * Capture the game's own frame into <root>/shots/<scene>_<epoch>.jpg, and index it in media.json.
   *
   * `kind` 'cover' also writes <root>/cover.jpg, which is what the card draws (GamePerf.coverPath).
   * Returns the relative path written, or '' when the surface could not be read (no surface yet, or the game
   * presents through a window this page does not own).
   */
  async shoot(kind: string): Promise<string> {
    if (this.surfaceId.length === 0 || this.width <= 0 || this.height <= 0) return '';
    const at: number = Date.now();
    if (at - this.lastShotAt < 1000) return '';
    this.lastShotAt = at;
    let pm: image.PixelMap | null = null;
    try {
      const region: image.Region = {
        size: { width: this.width, height: this.height },
        x: 0,
        y: 0
      };
      pm = await image.createPixelMapFromSurface(this.surfaceId, region);
      const rel: string = this.writePath(kind, at);
      const abs: string = this.root + '/' + rel;
      try { fs.mkdirSync(this.root + '/shots', true); } catch (e) { }
      const f = fs.openSync(abs, fs.OpenMode.CREATE | fs.OpenMode.READ_WRITE | fs.OpenMode.TRUNC);
      try {
        const packer: image.ImagePacker = image.createImagePacker();
        const opt: image.PackingOption = { format: 'image/jpeg', quality: 92 };
        await packer.packToFile(pm, f.fd, opt);
        packer.release();
      } finally {
        fs.closeSync(f);
      }
      if (kind === 'cover') {
        try { fs.copyFileSync(abs, this.root + '/cover.jpg'); } catch (e) { }
      }
      const shot: MediaShot = {
        file: rel,
        kind: kind,
        scene: this.scene,
        titleId: this.titleId,
        at: isoNow(),
        width: this.width,
        height: this.height
      };
      addShot(this.root, shot);
      return rel;
    } catch (e) {
      return '';
    } finally {
      if (pm !== null) {
        try { pm.release(); } catch (e) { }
      }
    }
  }

  private writePath(kind: string, at: number): string {
    const safe: string = this.scene.replace(/[^a-zA-Z0-9_-]/g, '') || 'field';
    return kind === 'cover' ? 'shots/cover_' + at + '.jpg' : 'shots/' + safe + '_' + at + '.jpg';
  }
}

/** The one sampler: the game page owns it, the library only reads what it wrote. */
export const GamePerf: PerfSampler = new PerfSampler();
