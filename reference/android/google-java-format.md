# Google Java Format

仅开发时格式化自有 Java 源码，不是 APK 运行依赖。完整工具调用，版本 1.24.0，Apache-2.0。来源 https://github.com/google/google-java-format ，固定 Maven URL 和 SHA256 见 config/java-formatter.lock.json；从该 URL 下载后用 JDK 21 `java -jar <jar> --replace <源码>` 重建格式化结果。没有修改 reference 下的上游源码。
