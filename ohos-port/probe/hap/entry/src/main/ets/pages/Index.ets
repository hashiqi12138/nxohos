/*
 * Index.ets — M0 探针页面（唯一职责：把 XComponent 的 surfaceId 交给 native）
 *
 * 为什么不是 "XComponent + libraryname 自动注入窗口"：
 *   本 SDK（HarmonyOS 6 / API 20）的 native_interface_xcomponent.h 只剩
 *   OH_NativeXComponent_GetNativeXComponent(ArkUI_NodeHandle)，旧的 napi_value
 *   版接口已移除，靠 exports 反查 XComponent 的老写法编不过。
 *   这里走官方 API 12+ 路径：
 *     getXComponentSurfaceId() → native.setSurfaceId(id)
 *     → OH_NativeWindow_CreateNativeWindowFromSurfaceId(id, &window)
 *
 * 必须是 XComponentType.SURFACE：VK_OHOS_surface 要的是真实 native surface，
 * TEXTURE 类型拿到的不是可 present 的窗口。
 */
import probe from 'libprobe.so';

@Entry
@Component
struct Index {
  private controller: XComponentController = new XComponentController();
  @State status: string = 'probe: waiting surface...';

  build() {
    Stack() {
      XComponent({
        id: 'probe',
        type: XComponentType.SURFACE,
        controller: this.controller
      })
        .width('100%')
        .height('100%')
        .onLoad(() => {
          const surfaceId: string = this.controller.getXComponentSurfaceId();
          console.info(`probe: surfaceId=${surfaceId}`);
          probe.setSurfaceId(surfaceId);
          this.status = 'probe: running (surfaceId=' + surfaceId + ')';
          console.info('probe: setSurfaceId called');
        })
        .onDestroy(() => {
          console.info('probe: xcomponent destroyed');
        })

      // 状态文字仅用于人工确认页面已加载；探针结论一律走 hilog / 报告 JSON
      Text(this.status)
        .fontSize(12)
        .fontColor('#888888')
        .align(Alignment.Bottom)
        .margin({ bottom: 8 })
    }
    .width('100%')
    .height('100%')
    .backgroundColor(Color.Black)
  }
}
