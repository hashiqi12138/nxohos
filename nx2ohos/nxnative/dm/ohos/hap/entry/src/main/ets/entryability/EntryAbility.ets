import { AbilityConstant, UIAbility, Want } from '@kit.AbilityKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { window } from '@kit.ArkUI';

export default class EntryAbility extends UIAbility {
  onCreate(want: Want, launchParam: AbilityConstant.LaunchParam): void {
    hilog.info(0x0000, 'dmhost', 'Ability onCreate');
  }

  onWindowStageCreate(windowStage: window.WindowStage): void {
    const win: window.Window = windowStage.getMainWindowSync();
    win.setWindowLayoutFullScreen(true).catch((e: Error) => hilog.error(0x0000, 'dmhost', 'full screen: %{public}s', e.message));
    win.setWindowKeepScreenOn(true).catch((e: Error) => hilog.error(0x0000, 'dmhost', 'keep screen on: %{public}s', e.message));
    // the surface's rotation is the module's (landscape in module.json5); the game draws into it unrotated
    windowStage.loadContent('pages/Index', (err) => {
      if (err.code) hilog.error(0x0000, 'dmhost', 'loadContent: %{public}s', JSON.stringify(err));
    });
  }
}
