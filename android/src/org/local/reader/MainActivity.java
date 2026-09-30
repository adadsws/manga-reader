package org.local.reader;

// 原生设置与系统授权；计算全部交给用户的电脑服务。
import android.app.*;
import android.content.*;
import android.graphics.Color;
import android.graphics.Typeface;
import android.media.projection.*;
import android.net.Uri;
import android.os.*;
import android.provider.Settings;
import android.text.*;
import android.widget.*;

public class MainActivity extends Activity {
  static final int NORMAL_TEXT = Color.rgb(25, 40, 55);
  static final int CHANGED_TEXT = Color.rgb(20, 105, 210);
  static final int SECONDARY_TEXT = Color.rgb(95, 99, 104);
  static final int PAGE_BACKGROUND = Color.WHITE;
  static final int SECTION_HEADING_TEXT = Color.rgb(245, 124, 0);
  static final int DIVIDER_COLOR = Color.rgb(232, 234, 236);
  static final int SETTING_ROW_HEIGHT = 112;
  static final int CONNECTED_TEXT = Color.rgb(20, 135, 75);
  static final int WARNING_TEXT = Color.rgb(180, 120, 20);
  static final int DISCONNECTED_TEXT = Color.rgb(190, 45, 45);
  static final int MAX_PAIR_RESPONSE_BYTES = 65536;
  static class ConnectionVersionState {
    final String text;
    final int color;
    ConnectionVersionState(String text, int color) {
      this.text = text;
      this.color = color;
    }
  }
  EditText address, token, swipeY, swipeMs;
  TextView addressStatus, tokenStatus, modelStatus, connectionStatus;
  TextView gapStatus, speedStatus, autoStatus, opacityStatus, asrStatus;
  TextView lowLatencyStatus, parallelVisionStatus, eagerFirstAudioStatus, earlyPrefetchStatus;
  TextView historyReuseStatus, historyHiddenStatus, adaptiveCaptureStatus, leftStatus, swipeYStatus, swipeMsStatus;
  TextView overlayPermissionStatus, accessibilityStatus;
  Switch auto, left, asr, lowLatency, parallelVision, eagerFirstAudio, earlyPrefetch, adaptiveCapture, historyReuse, historyHidden;
  LinearLayout advancedOptions;
  android.view.View advancedButton;
  ScrollView mainScroll, advancedScroll;
  LinearLayout mainPage, advancedPage;
  AlertDialog activeDialog;
  boolean syncingOptimizations;
  android.content.SharedPreferences prefs;
  Handler connectionHandler;
  java.util.concurrent.ExecutorService connectionExecutor;
  boolean connectionVisible, connectionChecking;
  int connectionGeneration;

  interface BooleanSaver { void save(boolean value); }
  interface IntSaver { void save(int value); }
  interface IntFormatter { String format(int value); }
  interface TextSaver { void save(String value); }

  static class SettingRow {
    final LinearLayout view;
    final TextView status;
    SettingRow(LinearLayout view, TextView status) {
      this.view = view;
      this.status = status;
    }
  }

  void changed(TextView view, boolean value) {
    if (view != null) view.setTextColor(value ? CHANGED_TEXT : NORMAL_TEXT);
  }

  boolean optimizationDefault(String key) {
    if ("parallelVision".equals(key)) return ReaderDefaults.PARALLEL_VISION;
    if ("eagerFirstAudio".equals(key)) return ReaderDefaults.EAGER_FIRST_AUDIO;
    return ReaderDefaults.EARLY_PREFETCH;
  }

  void divider(LinearLayout layout) {
    android.view.View divider = new android.view.View(this);
    divider.setBackgroundColor(DIVIDER_COLOR);
    LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, 2);
    params.leftMargin = 24;
    params.rightMargin = 24;
    layout.addView(divider, params);
  }

  SettingRow settingRow(LinearLayout layout, String title, String status, Runnable click) {
    LinearLayout row = new LinearLayout(this);
    row.setOrientation(LinearLayout.HORIZONTAL);
    row.setGravity(android.view.Gravity.CENTER_VERTICAL);
    row.setContentDescription("设置项 " + title);
    if (click != null) {
      row.setClickable(true);
      row.setFocusable(true);
      selectableBackground(row);
      row.setOnClickListener(v -> click.run());
    }
    LinearLayout text = new LinearLayout(this);
    text.setOrientation(LinearLayout.VERTICAL);
    TextView titleView = new TextView(this);
    titleView.setText(title);
    titleView.setTextSize(16);
    titleView.setTextColor(NORMAL_TEXT);
    titleView.setGravity(android.view.Gravity.START | android.view.Gravity.CENTER_VERTICAL);
    titleView.setPadding(24, 4, 8, 0);
    text.addView(titleView, new LinearLayout.LayoutParams(-1, 56));
    TextView statusView = new TextView(this);
    statusView.setText(status);
    statusView.setTextSize(14);
    statusView.setTextColor(SECONDARY_TEXT);
    statusView.setGravity(android.view.Gravity.START | android.view.Gravity.CENTER_VERTICAL);
    statusView.setPadding(24, 0, 8, 4);
    statusView.setSingleLine(true);
    statusView.setEllipsize(android.text.TextUtils.TruncateAt.END);
    text.addView(statusView, new LinearLayout.LayoutParams(-1, 56));
    row.addView(text, new LinearLayout.LayoutParams(0, SETTING_ROW_HEIGHT, 1));
    if (click != null) {
      TextView arrow = new TextView(this);
      arrow.setText("›");
      arrow.setTextSize(24);
      arrow.setTextColor(SECONDARY_TEXT);
      arrow.setGravity(android.view.Gravity.CENTER);
      row.addView(arrow, new LinearLayout.LayoutParams(56, SETTING_ROW_HEIGHT));
    }
    layout.addView(row, new LinearLayout.LayoutParams(-1, SETTING_ROW_HEIGHT));
    divider(layout);
    return new SettingRow(row, statusView);
  }

  void status(TextView view, String text, boolean changed) {
    if (view == null) return;
    view.setText(text);
    view.setTextColor(changed ? CHANGED_TEXT : SECONDARY_TEXT);
  }

  void changedStatus(TextView view, boolean changed) {
    if (view != null) status(view, view.getText().toString(), changed);
  }

  String enabled(boolean value) {
    return value ? "已开启" : "已关闭";
  }

  String gapText(int value) {
    int clamped = Math.max(0, Math.min(3000, value));
    return String.format(java.util.Locale.CHINA, "%.1f 秒", clamped / 1000.0);
  }

  String speedText(int value) {
    int clamped = Math.max(50, Math.min(150, value));
    return String.format(java.util.Locale.CHINA, "%.2f×", clamped / 100.0);
  }

  // 设置页视觉参考 Material Files v1.7.4（61a3cffede303d159ee9ad805319b89c21c3aa04）；未复制其源码。
  // https://github.com/zhanghai/MaterialFiles/tree/61a3cffede303d159ee9ad805319b89c21c3aa04
  void selectableBackground(android.view.View view) {
    android.util.TypedValue value = new android.util.TypedValue();
    if (getTheme().resolveAttribute(android.R.attr.selectableItemBackground, value, true))
      view.setBackgroundResource(value.resourceId);
  }

  LinearLayout page(String title, boolean back, ScrollView content) {
    LinearLayout page = new LinearLayout(this);
    page.setOrientation(LinearLayout.VERTICAL);
    page.setBackgroundColor(PAGE_BACKGROUND);
    LinearLayout toolbar = new LinearLayout(this);
    toolbar.setOrientation(LinearLayout.HORIZONTAL);
    toolbar.setGravity(android.view.Gravity.CENTER_VERTICAL);
    toolbar.setPadding(8, 24, 16, 0);
    toolbar.setBackgroundColor(PAGE_BACKGROUND);
    toolbar.setElevation(4);
    if (back) {
      Button up = new Button(this);
      up.setText("←");
      up.setTextSize(24);
      up.setTextColor(NORMAL_TEXT);
      up.setContentDescription("返回常用设置");
      up.setMinWidth(0);
      up.setPadding(0, 0, 0, 0);
      up.setGravity(android.view.Gravity.CENTER);
      up.setBackgroundColor(Color.TRANSPARENT);
      up.setOnClickListener(v -> setAdvancedVisible(false));
      toolbar.addView(up, new LinearLayout.LayoutParams(64, 64));
    }
    TextView toolbarTitle = new TextView(this);
    toolbarTitle.setText(title);
    toolbarTitle.setTextSize(20);
    toolbarTitle.setTypeface(Typeface.DEFAULT_BOLD);
    toolbarTitle.setTextColor(NORMAL_TEXT);
    toolbarTitle.setGravity(android.view.Gravity.CENTER_VERTICAL);
    toolbar.addView(toolbarTitle, new LinearLayout.LayoutParams(0, 64, 1));
    page.addView(toolbar, new LinearLayout.LayoutParams(-1, 88));
    page.addView(content, new LinearLayout.LayoutParams(-1, 0, 1));
    return page;
  }

  LinearLayout section(LinearLayout root, String title) {
    TextView heading = new TextView(this);
    heading.setText(title);
    heading.setTextSize(14);
    heading.setTypeface(Typeface.DEFAULT_BOLD);
    heading.setTextColor(SECTION_HEADING_TEXT);
    heading.setPadding(24, 20, 24, 8);
    LinearLayout.LayoutParams headingParams = new LinearLayout.LayoutParams(-1, -2);
    root.addView(heading, headingParams);
    LinearLayout content = new LinearLayout(this);
    content.setOrientation(LinearLayout.VERTICAL);
    content.setPadding(0, 0, 0, 4);
    LinearLayout.LayoutParams contentParams = new LinearLayout.LayoutParams(-1, -2);
    root.addView(content, contentParams);
    return content;
  }

  void setAdvancedVisible(boolean visible) {
    if (advancedOptions == null || advancedButton == null || mainScroll == null || advancedScroll == null) return;
    advancedOptions.setVisibility(visible ? android.view.View.VISIBLE : android.view.View.GONE);
    advancedButton.setContentDescription("进入高级选项");
    setContentView(visible ? advancedPage : mainPage);
    if (visible) advancedScroll.scrollTo(0, 0);
  }

  android.view.View advancedEntry(LinearLayout root) {
    SettingRow entry =
        settingRow(
            root,
            "高级选项",
            "语音质量、性能优化、历史复用与自动翻页细节",
            () -> setAdvancedVisible(true));
    entry.view.setContentDescription("进入高级选项");
    return entry.view;
  }

  public void onCreate(Bundle state) {
    super.onCreate(state);
    prefs = getSharedPreferences("reader", 0);
    prefs.edit().remove("historyPromote").apply();
    getWindow().setStatusBarColor(PAGE_BACKGROUND);
    getWindow().setNavigationBarColor(PAGE_BACKGROUND);
    if (Build.VERSION.SDK_INT >= 26)
      getWindow().getDecorView().setSystemUiVisibility(
          android.view.View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | android.view.View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR);
    LinearLayout l = new LinearLayout(this);
    l.setOrientation(1);
    l.setPadding(0, 0, 0, 35);
    l.setBackgroundColor(PAGE_BACKGROUND);
    mainScroll = new ScrollView(this);
    mainScroll.addView(l);
    mainPage = page("漫画朗读", false, mainScroll);
    setContentView(mainPage);
    LinearLayout connection = section(l, "电脑连接与模型");
    address = new EditText(this);
    address.setSingleLine();
    address.setText(prefs.getString("address", ReaderDefaults.ADDRESS));
    token = new EditText(this);
    token.setSingleLine();
    token.setText(prefs.getString("token", ReaderDefaults.TOKEN));
    addressStatus =
        settingRow(
                connection,
                "电脑服务地址",
                address.getText().toString(),
                () ->
                    showTextSetting(
                        "电脑服务地址",
                        address.getText().toString(),
                        true,
                        value -> address.setText(value.trim().replaceAll("/$", ""))))
            .status;
    tokenStatus =
        settingRow(
                connection,
                "配对口令",
                token.getText().toString(),
                () ->
                    showTextSetting(
                        "配对口令",
                        token.getText().toString(),
                        false,
                        value -> token.setText(value.trim())))
            .status;
    connectionStatus = settingRow(connection, "电脑连接", "正在检查电脑连接…", null).status;
    modelStatus =
        settingRow(
                connection,
                "朗读模型",
                prefs.getString("modelLabel", ReaderDefaults.MODEL_LABEL),
                () -> showModelPicker())
            .status;
    changedStatus(modelStatus, !ReaderDefaults.MODEL_LABEL.contentEquals(modelStatus.getText()));
    modelStatus.setContentDescription("当前朗读模型 " + modelStatus.getText());
    TextWatcher connectionWatcher = new TextWatcher() {
      public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
      public void onTextChanged(CharSequence s, int start, int before, int count) {}
      public void afterTextChanged(Editable value) {
        saveConnectionFields();
        status(
            addressStatus,
            address.getText().toString().trim().replaceAll("/$", ""),
            !ReaderDefaults.ADDRESS.equals(address.getText().toString().trim().replaceAll("/$", "")));
        status(tokenStatus, token.getText().toString(), !ReaderDefaults.TOKEN.contentEquals(token.getText()));
        restartConnectionMonitor(350);
      }
    };
    address.addTextChangedListener(connectionWatcher);
    token.addTextChangedListener(connectionWatcher);
    LinearLayout reading = section(l, "常用朗读设置");
    int savedGap = Math.max(0, Math.min(3000, prefs.getInt("sentenceGapMs", ReaderDefaults.SENTENCE_GAP_MS)));
    gapStatus =
        settingRow(
                reading,
                "句间停顿",
                gapText(savedGap),
                () ->
                    showSliderSetting(
                        "句间停顿",
                        0,
                        3000,
                        100,
                        prefs.getInt("sentenceGapMs", ReaderDefaults.SENTENCE_GAP_MS),
                        value -> gapText(value),
                        value -> {
                          prefs.edit().putInt("sentenceGapMs", value).apply();
                          status(gapStatus, gapText(value), value != ReaderDefaults.SENTENCE_GAP_MS);
                        }))
            .status;
    changedStatus(gapStatus, savedGap != ReaderDefaults.SENTENCE_GAP_MS);
    int savedSpeed = Math.max(50, Math.min(150, prefs.getInt("speechSpeedPercent", ReaderDefaults.SPEECH_SPEED_PERCENT)));
    speedStatus =
        settingRow(
                reading,
                "朗读速度",
                speedText(savedSpeed),
                () ->
                    showSliderSetting(
                        "朗读速度",
                        50,
                        150,
                        5,
                        prefs.getInt("speechSpeedPercent", ReaderDefaults.SPEECH_SPEED_PERCENT),
                        value -> speedText(value),
                        value -> {
                          prefs.edit().putInt("speechSpeedPercent", value).apply();
                          status(speedStatus, speedText(value), value != ReaderDefaults.SPEECH_SPEED_PERCENT);
                        }))
            .status;
    changedStatus(speedStatus, savedSpeed != ReaderDefaults.SPEECH_SPEED_PERCENT);
    auto = new Switch(this);
    auto.setChecked(prefs.getBoolean("auto", ReaderDefaults.AUTO_FLIP));
    autoStatus =
        settingRow(
                reading,
                "读完一页后自动翻页",
                enabled(auto.isChecked()),
                () -> showBooleanSetting("读完一页后自动翻页", auto.isChecked(), value -> auto.setChecked(value)))
            .status;
    changedStatus(autoStatus, auto.isChecked() != ReaderDefaults.AUTO_FLIP);
    auto.setOnCheckedChangeListener((button, checked) -> {
      prefs.edit().putBoolean("auto", checked).apply();
      status(autoStatus, enabled(checked), checked != ReaderDefaults.AUTO_FLIP);
    });

    advancedButton = advancedEntry(l);
    advancedOptions = new LinearLayout(this);
    advancedOptions.setOrientation(LinearLayout.VERTICAL);
    advancedOptions.setPadding(0, 0, 0, 35);
    advancedOptions.setBackgroundColor(PAGE_BACKGROUND);
    advancedScroll = new ScrollView(this);
    advancedScroll.addView(advancedOptions);
    advancedPage = page("高级选项", true, advancedScroll);
    LinearLayout voiceAdvanced = section(advancedOptions, "语音质量与浮窗");
    int savedOpacity = Math.max(30, Math.min(100, prefs.getInt("overlayOpacityPercent", ReaderDefaults.OVERLAY_OPACITY_PERCENT)));
    savedOpacity = 30 + Math.round((savedOpacity - 30) / 5.0f) * 5;
    final int initialOpacity = savedOpacity;
    opacityStatus =
        settingRow(
                voiceAdvanced,
                "浮窗透明度",
                initialOpacity + "%",
                () ->
                    showSliderSetting(
                        "浮窗透明度",
                        30,
                        100,
                        5,
                        prefs.getInt("overlayOpacityPercent", ReaderDefaults.OVERLAY_OPACITY_PERCENT),
                        value -> value + "%",
                        value -> {
                          prefs.edit().putInt("overlayOpacityPercent", value).apply();
                          status(opacityStatus, value + "%", value != ReaderDefaults.OVERLAY_OPACITY_PERCENT);
                        }))
            .status;
    changedStatus(opacityStatus, initialOpacity != ReaderDefaults.OVERLAY_OPACITY_PERCENT);
    asr = new Switch(this);
    asr.setChecked(prefs.getBoolean("asrCheck", ReaderDefaults.ASR_CHECK));
    asrStatus =
        settingRow(
                voiceAdvanced,
                "ASR 完整性检查",
                enabled(asr.isChecked()),
                () -> showBooleanSetting("ASR 完整性检查", asr.isChecked(), value -> asr.setChecked(value)))
            .status;
    changedStatus(asrStatus, asr.isChecked() != ReaderDefaults.ASR_CHECK);
    asr.setOnCheckedChangeListener((button, checked) -> {
      prefs.edit().putBoolean("asrCheck", checked).apply();
      status(asrStatus, enabled(checked), checked != ReaderDefaults.ASR_CHECK);
    });
    LinearLayout performanceAdvanced = section(advancedOptions, "低等待优化");
    lowLatency = new Switch(this);
    parallelVision = new Switch(this);
    eagerFirstAudio = new Switch(this);
    earlyPrefetch = new Switch(this);
    parallelVision.setChecked(prefs.getBoolean("parallelVision", ReaderDefaults.PARALLEL_VISION));
    eagerFirstAudio.setChecked(prefs.getBoolean("eagerFirstAudio", ReaderDefaults.EAGER_FIRST_AUDIO));
    earlyPrefetch.setChecked(prefs.getBoolean("earlyPrefetch", ReaderDefaults.EARLY_PREFETCH));
    lowLatency.setChecked(allOptimizationsEnabled());
    lowLatencyStatus =
        settingRow(
                performanceAdvanced,
                "低等待优化总开关",
                allOptimizationsEnabled() ? "全部已开启" : "部分或全部关闭",
                () -> showBooleanSetting("低等待优化总开关", allOptimizationsEnabled(), value -> lowLatency.setChecked(value)))
            .status;
    parallelVisionStatus =
        settingRow(
                performanceAdvanced,
                "OCR 与分镜并行",
                enabled(parallelVision.isChecked()),
                () -> showBooleanSetting("OCR 与分镜并行", parallelVision.isChecked(), value -> parallelVision.setChecked(value)))
            .status;
    eagerFirstAudioStatus =
        settingRow(
                performanceAdvanced,
                "首段语音提前生成",
                enabled(eagerFirstAudio.isChecked()),
                () -> showBooleanSetting("首段语音提前生成", eagerFirstAudio.isChecked(), value -> eagerFirstAudio.setChecked(value)))
            .status;
    earlyPrefetchStatus =
        settingRow(
                performanceAdvanced,
                "按顺序预取本页全部语音",
                enabled(earlyPrefetch.isChecked()),
                () -> showBooleanSetting("按顺序预取本页全部语音", earlyPrefetch.isChecked(), value -> earlyPrefetch.setChecked(value)))
            .status;
    configureOptimizationSwitch(parallelVision, parallelVisionStatus, "parallelVision");
    configureOptimizationSwitch(eagerFirstAudio, eagerFirstAudioStatus, "eagerFirstAudio");
    configureOptimizationSwitch(earlyPrefetch, earlyPrefetchStatus, "earlyPrefetch");
    updateOptimizationStatuses();
    lowLatency.setOnCheckedChangeListener((button, checked) -> {
      if (syncingOptimizations) return;
      syncingOptimizations = true;
      parallelVision.setChecked(checked);
      eagerFirstAudio.setChecked(checked);
      earlyPrefetch.setChecked(checked);
      syncingOptimizations = false;
      prefs.edit()
          .putBoolean("parallelVision", checked)
          .putBoolean("eagerFirstAudio", checked)
          .putBoolean("earlyPrefetch", checked)
          .apply();
      updateOptimizationStatuses();
    });
    LinearLayout historyAdvanced = section(advancedOptions, "历史结果复用");
    historyReuse = new Switch(this);
    historyReuse.setChecked(prefs.getBoolean("historyReuse", ReaderDefaults.HISTORY_REUSE));
    historyReuseStatus =
        settingRow(
                historyAdvanced,
                "匹配历史结果时跳过处理",
                enabled(historyReuse.isChecked()),
                () -> showBooleanSetting("匹配历史结果时跳过处理", historyReuse.isChecked(), value -> historyReuse.setChecked(value)))
            .status;
    changedStatus(historyReuseStatus, historyReuse.isChecked() != ReaderDefaults.HISTORY_REUSE);
    historyReuse.setOnCheckedChangeListener((button, checked) -> {
      prefs.edit().putBoolean("historyReuse", checked).apply();
      status(historyReuseStatus, enabled(checked), checked != ReaderDefaults.HISTORY_REUSE);
    });
    historyHidden = new Switch(this);
    historyHidden.setChecked(prefs.getBoolean("historyHidden", ReaderDefaults.HISTORY_HIDDEN));
    historyHiddenStatus =
        settingRow(
                historyAdvanced,
                "隐藏本次记录",
                enabled(historyHidden.isChecked()),
                () -> showBooleanSetting("隐藏本次记录", historyHidden.isChecked(), value -> historyHidden.setChecked(value)))
            .status;
    changedStatus(historyHiddenStatus, historyHidden.isChecked() != ReaderDefaults.HISTORY_HIDDEN);
    historyHidden.setOnCheckedChangeListener((button, checked) -> {
      prefs.edit().putBoolean("historyHidden", checked).apply();
      status(historyHiddenStatus, enabled(checked), checked != ReaderDefaults.HISTORY_HIDDEN);
    });
    LinearLayout flipAdvanced = section(advancedOptions, "自动翻页细节");
    adaptiveCapture = new Switch(this);
    adaptiveCapture.setChecked(prefs.getBoolean("adaptiveCapture", ReaderDefaults.ADAPTIVE_CAPTURE));
    adaptiveCaptureStatus =
        settingRow(
                flipAdvanced,
                "翻页后自适应抓帧",
                adaptiveCapture.isChecked() ? "已开启 · 画面稳定后截图" : "已关闭 · 固定等待 800ms",
                () -> showBooleanSetting("翻页后自适应抓帧", adaptiveCapture.isChecked(), value -> adaptiveCapture.setChecked(value)))
            .status;
    changedStatus(adaptiveCaptureStatus, adaptiveCapture.isChecked() != ReaderDefaults.ADAPTIVE_CAPTURE);
    adaptiveCapture.setOnCheckedChangeListener((button, checked) -> {
      prefs.edit().putBoolean("adaptiveCapture", checked).apply();
      status(
          adaptiveCaptureStatus,
          checked ? "已开启 · 画面稳定后截图" : "已关闭 · 固定等待 800ms",
          checked != ReaderDefaults.ADAPTIVE_CAPTURE);
    });
    left = new Switch(this);
    left.setChecked(prefs.getBoolean("left", ReaderDefaults.NEXT_PAGE_LEFT));
    leftStatus =
        settingRow(
                flipAdvanced,
                "下一页翻页方向",
                left.isChecked() ? "向左翻" : "向右翻",
                () -> showBooleanSetting("下一页向左翻", left.isChecked(), value -> left.setChecked(value)))
            .status;
    changedStatus(leftStatus, left.isChecked() != ReaderDefaults.NEXT_PAGE_LEFT);
    left.setOnCheckedChangeListener((button, checked) -> {
      prefs.edit().putBoolean("left", checked).apply();
      status(leftStatus, checked ? "向左翻" : "向右翻", checked != ReaderDefaults.NEXT_PAGE_LEFT);
    });
    swipeY = new EditText(this);
    swipeY.setSingleLine();
    swipeY.setInputType(2);
    swipeY.setText(String.valueOf(prefs.getInt("swipeY", ReaderDefaults.SWIPE_Y_PERCENT)));
    swipeYStatus =
        settingRow(
                flipAdvanced,
                "翻页滑动高度",
                number(swipeY, ReaderDefaults.SWIPE_Y_PERCENT, 10, 90) + "%",
                () ->
                    showNumberSetting(
                        "翻页滑动高度",
                        number(swipeY, ReaderDefaults.SWIPE_Y_PERCENT, 10, 90),
                        10,
                        90,
                        "%",
                        value -> swipeY.setText(String.valueOf(value))))
            .status;
    changedStatus(swipeYStatus, number(swipeY, ReaderDefaults.SWIPE_Y_PERCENT, 10, 90) != ReaderDefaults.SWIPE_Y_PERCENT);
    swipeMs = new EditText(this);
    swipeMs.setSingleLine();
    swipeMs.setInputType(2);
    swipeMs.setText(String.valueOf(prefs.getInt("swipeMs", ReaderDefaults.SWIPE_DURATION_MS)));
    swipeMsStatus =
        settingRow(
                flipAdvanced,
                "翻页滑动时长",
                number(swipeMs, ReaderDefaults.SWIPE_DURATION_MS, 100, 1500) + " 毫秒",
                () ->
                    showNumberSetting(
                        "翻页滑动时长",
                        number(swipeMs, ReaderDefaults.SWIPE_DURATION_MS, 100, 1500),
                        100,
                        1500,
                        " 毫秒",
                        value -> swipeMs.setText(String.valueOf(value))))
            .status;
    changedStatus(swipeMsStatus, number(swipeMs, ReaderDefaults.SWIPE_DURATION_MS, 100, 1500) != ReaderDefaults.SWIPE_DURATION_MS);
    TextWatcher flipWatcher = new TextWatcher() {
      public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
      public void onTextChanged(CharSequence s, int start, int before, int count) {}
      public void afterTextChanged(Editable value) {
        int y = number(swipeY, ReaderDefaults.SWIPE_Y_PERCENT, 10, 90);
        int ms = number(swipeMs, ReaderDefaults.SWIPE_DURATION_MS, 100, 1500);
        prefs.edit().putInt("swipeY", y).putInt("swipeMs", ms).apply();
        status(swipeYStatus, y + "%", y != ReaderDefaults.SWIPE_Y_PERCENT);
        status(swipeMsStatus, ms + " 毫秒", ms != ReaderDefaults.SWIPE_DURATION_MS);
      }
    };
    swipeY.addTextChangedListener(flipWatcher);
    swipeMs.addTextChangedListener(flipWatcher);
    setAdvancedVisible(false);
    LinearLayout startup = section(l, "启动与权限");
    overlayPermissionStatus =
        settingRow(
                startup,
                "允许浮窗",
                overlayPermissionText(),
                () ->
                    startActivity(
                        new Intent(
                            Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                            Uri.parse("package:" + getPackageName()))))
            .status;
    accessibilityStatus =
        settingRow(
                startup,
                "开启翻页无障碍",
                accessibilityPermissionText(),
                () -> startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)))
            .status;
    settingRow(
        startup,
        "保存并启动朗读浮窗",
        "点击后申请共享整个屏幕",
        () -> {
          save();
          if (!Settings.canDrawOverlays(this)) {
            Toast.makeText(this, "请先允许浮窗", 0).show();
            return;
          }
          startActivityForResult(screenCaptureIntent(), 7);
        });
    settingRow(
        startup,
        "关闭朗读服务和浮窗",
        "停止播放、后台处理和浮窗服务",
        () -> stopService(new Intent(this, ReaderService.class)));
  }

  @Override public void onBackPressed() {
    if (advancedOptions != null && advancedOptions.getVisibility() == android.view.View.VISIBLE) {
      setAdvancedVisible(false);
      return;
    }
    super.onBackPressed();
  }

  void showBooleanSetting(String title, boolean current, BooleanSaver saver) {
    int[] selected = {current ? 0 : 1};
    AlertDialog dialog =
        new AlertDialog.Builder(this)
            .setTitle(title)
            .setSingleChoiceItems(
                new String[] {"开启", "关闭"},
                selected[0],
                (choiceDialog, which) -> selected[0] = which)
            .setNegativeButton("取消", null)
            .setPositiveButton("保存", null)
            .create();
    dialog.setOnShowListener(
        ignored ->
            dialog
                .getButton(AlertDialog.BUTTON_POSITIVE)
                .setOnClickListener(
                    view -> {
                      saver.save(selected[0] == 0);
                      dialog.dismiss();
                    }));
    activeDialog = dialog;
    dialog.show();
  }

  void showTextSetting(String title, String current, boolean uri, TextSaver saver) {
    EditText input = new EditText(this);
    input.setSingleLine();
    input.setText(current);
    input.setSelectAllOnFocus(true);
    input.setPadding(24, 12, 24, 12);
    input.setInputType(
        android.text.InputType.TYPE_CLASS_TEXT
            | (uri
                ? android.text.InputType.TYPE_TEXT_VARIATION_URI
                : android.text.InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD));
    AlertDialog dialog =
        new AlertDialog.Builder(this)
            .setTitle(title)
            .setView(input)
            .setNegativeButton("取消", null)
            .setPositiveButton("保存", null)
            .create();
    dialog.setOnShowListener(
        ignored ->
            dialog
                .getButton(AlertDialog.BUTTON_POSITIVE)
                .setOnClickListener(
                    view -> {
                      saver.save(input.getText().toString());
                      dialog.dismiss();
                    }));
    activeDialog = dialog;
    dialog.show();
  }

  void showSliderSetting(
      String title,
      int min,
      int max,
      int step,
      int current,
      IntFormatter formatter,
      IntSaver saver) {
    LinearLayout content = new LinearLayout(this);
    content.setOrientation(LinearLayout.VERTICAL);
    content.setPadding(24, 8, 24, 8);
    TextView value = new TextView(this);
    value.setTextSize(18);
    value.setTextColor(NORMAL_TEXT);
    value.setGravity(android.view.Gravity.CENTER);
    content.addView(value, new LinearLayout.LayoutParams(-1, 56));
    SeekBar slider = new SeekBar(this);
    slider.setMax((max - min) / step);
    int clamped = Math.max(min, Math.min(max, current));
    slider.setProgress((clamped - min) / step);
    value.setText(formatter.format(clamped));
    slider.setContentDescription(title + " " + value.getText());
    slider.setOnSeekBarChangeListener(
        new SeekBar.OnSeekBarChangeListener() {
          public void onProgressChanged(SeekBar bar, int progress, boolean fromUser) {
            int selected = min + progress * step;
            value.setText(formatter.format(selected));
            bar.setContentDescription(title + " " + value.getText());
          }
          public void onStartTrackingTouch(SeekBar bar) {}
          public void onStopTrackingTouch(SeekBar bar) {}
        });
    content.addView(slider);
    AlertDialog dialog =
        new AlertDialog.Builder(this)
            .setTitle(title)
            .setView(content)
            .setNegativeButton("取消", null)
            .setPositiveButton("保存", null)
            .create();
    dialog.setOnShowListener(
        ignored ->
            dialog
                .getButton(AlertDialog.BUTTON_POSITIVE)
                .setOnClickListener(
                    view -> {
                      saver.save(min + slider.getProgress() * step);
                      dialog.dismiss();
                    }));
    activeDialog = dialog;
    dialog.show();
  }

  void showNumberSetting(
      String title, int current, int min, int max, String suffix, IntSaver saver) {
    EditText input = new EditText(this);
    input.setSingleLine();
    input.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
    input.setText(String.valueOf(current));
    input.setSelectAllOnFocus(true);
    input.setPadding(24, 12, 24, 12);
    activeDialog =
        new AlertDialog.Builder(this)
            .setTitle(title + "（" + min + "–" + max + suffix + "）")
            .setView(input)
            .setNegativeButton("取消", null)
            .setPositiveButton("保存", null)
            .create();
    activeDialog.setOnShowListener(
        dialog ->
            activeDialog
                .getButton(AlertDialog.BUTTON_POSITIVE)
                .setOnClickListener(
                    view -> {
                      try {
                        int value = Integer.parseInt(input.getText().toString().trim());
                        if (value < min || value > max) throw new NumberFormatException();
                        saver.save(value);
                        activeDialog.dismiss();
                      } catch (NumberFormatException e) {
                        input.setError("请输入 " + min + "–" + max + suffix);
                      }
                    }));
    activeDialog.show();
  }

  Intent screenCaptureIntent() {
    MediaProjectionManager manager =
        (MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE);
    // Android 14+ 默认允许只共享单个应用；Reader 切到相册后必须仍能捕获整屏。
    if (Build.VERSION.SDK_INT >= 34)
      return manager.createScreenCaptureIntent(MediaProjectionConfig.createConfigForDefaultDisplay());
    return manager.createScreenCaptureIntent();
  }

  @Override protected void onResume() {
    super.onResume();
    // 返回设置页时刷新权限和浮窗可能修改过的偏好状态。
    if (auto != null) {
      auto.setChecked(prefs.getBoolean("auto", ReaderDefaults.AUTO_FLIP));
      status(autoStatus, enabled(auto.isChecked()), auto.isChecked() != ReaderDefaults.AUTO_FLIP);
    }
    if (asr != null) {
      asr.setChecked(prefs.getBoolean("asrCheck", ReaderDefaults.ASR_CHECK));
      status(asrStatus, enabled(asr.isChecked()), asr.isChecked() != ReaderDefaults.ASR_CHECK);
    }
    if (overlayPermissionStatus != null) status(overlayPermissionStatus, overlayPermissionText(), false);
    if (accessibilityStatus != null) status(accessibilityStatus, accessibilityPermissionText(), false);
    startConnectionMonitor();
  }

  void configureOptimizationSwitch(Switch box, TextView rowStatus, String key) {
    boolean defaultValue = optimizationDefault(key);
    box.setOnCheckedChangeListener((button, checked) -> {
      prefs.edit().putBoolean(key, checked).apply();
      status(rowStatus, enabled(checked), checked != defaultValue);
      if (!syncingOptimizations && lowLatency != null) {
        syncingOptimizations = true;
        lowLatency.setChecked(allOptimizationsEnabled());
        syncingOptimizations = false;
      }
      updateOptimizationStatuses();
    });
  }

  void updateOptimizationStatuses() {
    if (parallelVision == null || eagerFirstAudio == null || earlyPrefetch == null) return;
    status(
        parallelVisionStatus,
        enabled(parallelVision.isChecked()),
        parallelVision.isChecked() != ReaderDefaults.PARALLEL_VISION);
    status(
        eagerFirstAudioStatus,
        enabled(eagerFirstAudio.isChecked()),
        eagerFirstAudio.isChecked() != ReaderDefaults.EAGER_FIRST_AUDIO);
    status(
        earlyPrefetchStatus,
        enabled(earlyPrefetch.isChecked()),
        earlyPrefetch.isChecked() != ReaderDefaults.EARLY_PREFETCH);
    boolean defaultsEnabled =
        ReaderDefaults.PARALLEL_VISION
            && ReaderDefaults.EAGER_FIRST_AUDIO
            && ReaderDefaults.EARLY_PREFETCH;
    status(
        lowLatencyStatus,
        allOptimizationsEnabled() ? "全部已开启" : "部分或全部关闭",
        allOptimizationsEnabled() != defaultsEnabled);
  }

  boolean allOptimizationsEnabled() {
    return parallelVision != null && eagerFirstAudio != null && earlyPrefetch != null
        && parallelVision.isChecked() && eagerFirstAudio.isChecked() && earlyPrefetch.isChecked();
  }

  String overlayPermissionText() {
    return Settings.canDrawOverlays(this) ? "已允许" : "未允许 · 点击进入系统设置";
  }

  String accessibilityPermissionText() {
    String enabledServices =
        Settings.Secure.getString(
            getContentResolver(), Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
    boolean enabled =
        TurnService.enabledServicesContain(
            enabledServices, new ComponentName(getPackageName(), TurnService.class.getName()));
    return enabled ? "已开启" : "未开启 · 自动翻页时需要";
  }

  @Override protected void onPause() {
    stopConnectionMonitor();
    super.onPause();
  }

  @Override protected void onDestroy() {
    stopConnectionMonitor();
    if (connectionExecutor != null) connectionExecutor.shutdownNow();
    super.onDestroy();
  }

  void showModelPicker() {
    save();
    String endpoint = address.getText().toString().trim().replaceAll("/$", "");
    String secret = token.getText().toString().trim();
    new ModelPicker(
            this,
            endpoint,
            secret,
            model -> {
              String label =
                  model.optString("name", "未命名")
                      + " · "
                      + model.optString("version", "未知型号")
                      + " · "
                      + model.optString("checkpoint", "");
              prefs.edit().putString("modelLabel", label).apply();
              status(modelStatus, label, !ReaderDefaults.MODEL_LABEL.equals(label));
              modelStatus.setContentDescription("当前朗读模型 " + label);
            })
        .show();
  }
  void setConnectionStatus(String text, int color) {
    if (connectionStatus == null) return;
    connectionStatus.setText(text);
    connectionStatus.setTextColor(color);
    connectionStatus.setContentDescription("电脑连接状态 " + text);
  }

  void saveConnectionFields() {
    if (prefs == null || address == null || token == null) return;
    prefs.edit()
        .putString("address", address.getText().toString().trim().replaceAll("/$", ""))
        .putString("token", token.getText().toString().trim())
        .apply();
  }

  void startConnectionMonitor() {
    if (connectionHandler == null) connectionHandler = new Handler(Looper.getMainLooper());
    if (connectionExecutor == null || connectionExecutor.isShutdown())
      connectionExecutor = java.util.concurrent.Executors.newSingleThreadExecutor();
    connectionVisible = true;
    restartConnectionMonitor(0);
  }

  void stopConnectionMonitor() {
    connectionVisible = false;
    connectionChecking = false;
    connectionGeneration++;
    if (connectionHandler != null) connectionHandler.removeCallbacksAndMessages(null);
  }

  void restartConnectionMonitor(long delayMs) {
    if (!connectionVisible || connectionHandler == null) return;
    connectionGeneration++;
    connectionChecking = false;
    connectionHandler.removeCallbacksAndMessages(null);
    connectionHandler.postDelayed(() -> checkConnection(), delayMs);
  }

  void checkConnection() {
    if (!connectionVisible || connectionChecking || connectionExecutor == null) return;
    connectionChecking = true;
    setConnectionStatus("正在检查电脑连接…", WARNING_TEXT);
    int requestGeneration = connectionGeneration;
    String endpoint = address.getText().toString().trim().replaceAll("/$", "");
    String secret = token.getText().toString().trim();
    connectionExecutor.execute(
            () -> {
              ConnectionVersionState state = null;
              String message = "";
              try {
                java.net.HttpURLConnection c =
                    (java.net.HttpURLConnection)
                        new java.net.URL(endpoint + "/pair").openConnection();
                c.setRequestProperty("X-Reader-Token", secret);
                c.setRequestProperty(
                    "X-Reader-Version", String.valueOf(ReaderDefaults.READER_PROTOCOL_VERSION));
                c.setConnectTimeout(2500);
                c.setReadTimeout(2500);
                try {
                  int status = c.getResponseCode();
                  if (status != 200) throw new java.io.IOException("HTTP " + status);
                  String body = "";
                  try {
                    body = readBoundedText(c.getInputStream(), MAX_PAIR_RESPONSE_BYTES);
                  } catch (Exception ignored) {
                  }
                  state = connectionVersionState(body);
                } finally {
                  c.disconnect();
                }
              } catch (Exception e) {
                message = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
              }
              ConnectionVersionState result = state;
              String detail = message;
              runOnUiThread(
                  () -> {
                    if (!connectionVisible || requestGeneration != connectionGeneration) return;
                    connectionChecking = false;
                    if (result != null)
                      setConnectionStatus(result.text, result.color);
                    else
                      setConnectionStatus("电脑未连接：" + detail, DISCONNECTED_TEXT);
                    connectionHandler.postDelayed(() -> checkConnection(), 5000);
                  });
            });
  }

  static String readBoundedText(java.io.InputStream input, int limit) throws java.io.IOException {
    java.io.ByteArrayOutputStream output = new java.io.ByteArrayOutputStream();
    try (java.io.InputStream stream = input) {
      byte[] buffer = new byte[4096];
      int read;
      while ((read = stream.read(buffer)) != -1) {
        if (output.size() + read > limit) throw new java.io.IOException("响应过大");
        output.write(buffer, 0, read);
      }
    }
    return output.toString("UTF-8");
  }

  static ConnectionVersionState connectionVersionState(String body) {
    try {
      Object value = new org.json.JSONObject(body).opt("version");
      if (value instanceof Number) {
        int computerVersion = ((Number) value).intValue();
        if (computerVersion == ReaderDefaults.READER_PROTOCOL_VERSION)
          return new ConnectionVersionState(
              "电脑已连接 · 版本 " + computerVersion, CONNECTED_TEXT);
        return new ConnectionVersionState(
            "电脑已连接 · 版本警告：安卓 " + ReaderDefaults.READER_PROTOCOL_VERSION
                + "，电脑 " + computerVersion,
            WARNING_TEXT);
      }
    } catch (Exception ignored) {
    }
    return new ConnectionVersionState(
        "电脑已连接 · 版本警告：电脑未报告有效版本（安卓 "
            + ReaderDefaults.READER_PROTOCOL_VERSION + "）",
        WARNING_TEXT);
  }

  int number(EditText field, int fallback, int min, int max) {
    try {
      return Math.max(min, Math.min(max, Integer.parseInt(field.getText().toString())));
    } catch (Exception e) {
      return fallback;
    }
  }

  void save() {
    saveConnectionFields();
    prefs
        .edit()
        .putBoolean("left", left.isChecked())
        .putInt("swipeY", number(swipeY, ReaderDefaults.SWIPE_Y_PERCENT, 10, 90))
        .putInt("swipeMs", number(swipeMs, ReaderDefaults.SWIPE_DURATION_MS, 100, 1500))
        .apply();
  }

  protected void onActivityResult(int r, int c, Intent data) {
    super.onActivityResult(r, c, data);
    if (r == 7 && c == RESULT_OK && data != null) {
      stopService(new Intent(this, ReaderService.class));
      new Handler()
          .postDelayed(
              () -> {
                Intent i = new Intent(this, ReaderService.class);
                i.putExtra("code", c);
                i.putExtra("data", data);
                startForegroundService(i);
                moveTaskToBack(true);
              },
              200);
    }
  }
}
