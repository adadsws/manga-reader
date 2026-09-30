# Material Files 设置页视觉参考

- 用途：作为 Reader Android 设置页的唯一视觉参考，采用顶部工具栏、`PreferenceCategory` 强调色标题、无外框设置行、标题与摘要两级文字、行尾开关或当前值。
- 使用方式：仅参考界面结构与视觉语言；Reader 未复制源码、资源或依赖，仍由项目现有原生 Java View 实现。
- 上游：https://github.com/zhanghai/MaterialFiles
- 固定提交：`61a3cffede303d159ee9ad805319b89c21c3aa04`
- 对应版本：1.7.4
- 许可证：GPL-3.0-or-later
- 参考文件：`app/src/main/java/me/zhanghai/android/files/settings/SettingsActivity.kt`、`SettingsFragment.kt`、`app/src/main/res/xml/settings.xml`。
- 重建参考：克隆上游后检出上述完整提交；本项目无需下载或构建上游。
