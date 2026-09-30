# Fossify Gallery 固定安装记录

- 用途：ReaderAosp35 专用模拟器的漫画相册；文件夹和图片统一按名称升序显示，并作为 JPEG 默认查看器。
- 方式：完整调用独立 APK，不复制或修改上游源码；原系统 `com.android.gallery3d` 保留。
- 上游：https://github.com/FossifyOrg/Gallery
- 固定版本：`1.13.1`（versionCode 28），tag 对应完整提交 `b28299dc33821eee8d108a9880ce87876cf31443`，GPL-3.0。
- 固定 APK：https://github.com/FossifyOrg/Gallery/releases/download/1.13.1/gallery-28-foss-release.apk
- APK 校验：38,606,538 字节，SHA-256 `AE7E699599E81F70E2B82626BB1DFA883FE096CD938387133E123C116E5641D9`；APK 仅放在忽略的 `~temp/`，不进入 Git。
- 重建：下载并校验 APK，用项目 Android SDK 的 `adb` 安装到 `ReaderAosp35`；仅授予照片媒体读取和系统“媒体管理应用”权限，不授予“所有文件”；文件夹与图片各选择 `Name / Ascending`，再将 `org.fossify.gallery/.activities.PhotoActivity` 设为 JPEG 默认查看器。
- 验收：重启应用后，分别用多页和三页测试目录核对首尾文件及完整名称升序。
