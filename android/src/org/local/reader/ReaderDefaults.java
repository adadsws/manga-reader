package org.local.reader;

// 用户确认的默认设置；设置页、后台服务和翻页服务必须共用同一份值。
final class ReaderDefaults {
  static final String ADDRESS = "http://10.0.2.2:8765";
  static final String TOKEN = "reader-local";
  static final String MODEL_LABEL = "日奈 · v4 · 日奈_e10_s160_l32";
  static final int SENTENCE_GAP_MS = 100;
  static final int SPEECH_SPEED_PERCENT = 90;
  static final boolean AUTO_FLIP = true;
  static final int OVERLAY_OPACITY_PERCENT = 95;
  static final boolean ASR_CHECK = true;
  static final boolean PARALLEL_VISION = true;
  static final boolean EAGER_FIRST_AUDIO = true;
  static final boolean EARLY_PREFETCH = true;
  static final boolean ADAPTIVE_CAPTURE = true;
  static final boolean HISTORY_REUSE = true;
  static final boolean HISTORY_PROMOTE = false;
  static final boolean NEXT_PAGE_LEFT = false;
  static final int SWIPE_Y_PERCENT = 55;
  static final int SWIPE_DURATION_MS = 400;

  private ReaderDefaults() {}
}
