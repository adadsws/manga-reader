package org.local.reader;

// 原生设置与系统授权；计算全部交给用户的电脑服务。
import android.app.*;
import android.content.*;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.media.projection.*;
import android.net.Uri;
import android.os.*;
import android.provider.Settings;
import android.text.*;
import android.widget.*;

public class MainActivity extends Activity {
  static final int NORMAL_TEXT = Color.rgb(25, 40, 55);
  static final int CHANGED_TEXT = Color.rgb(20, 105, 210);
  EditText address, token, swipeY, swipeMs;
  TextView modelStatus, connectionStatus;
  CheckBox auto, left, asr, lowLatency, parallelVision, eagerFirstAudio, earlyPrefetch, adaptiveCapture, historyReuse, historyPromote;
  LinearLayout advancedOptions;
  Button advancedButton;
  boolean syncingOptimizations;
  android.content.SharedPreferences prefs;
  Handler connectionHandler;
  java.util.concurrent.ExecutorService connectionExecutor;
  boolean connectionVisible, connectionChecking;
  int connectionGeneration;

  void changed(TextView view, boolean value) {
    if (view != null) view.setTextColor(value ? CHANGED_TEXT : NORMAL_TEXT);
  }

  boolean optimizationDefault(String key) {
    if ("parallelVision".equals(key)) return ReaderDefaults.PARALLEL_VISION;
    if ("eagerFirstAudio".equals(key)) return ReaderDefaults.EAGER_FIRST_AUDIO;
    return ReaderDefaults.EARLY_PREFETCH;
  }

  void label(LinearLayout l, String s, int size) {
    TextView v = new TextView(this);
    v.setText(s);
    v.setTextSize(size);
    v.setTextColor(Color.rgb(25, 40, 55));
    v.setPadding(0, 18, 0, 12);
    l.addView(v);
  }

  Button button(LinearLayout l, String s, Runnable r) {
    Button b = new Button(this);
    b.setText(s);
    b.setContentDescription(s);
    b.setOnClickListener(v -> r.run());
    l.addView(b);
    return b;
  }

  LinearLayout section(LinearLayout root, String title) {
    TextView heading = new TextView(this);
    heading.setText(title);
    heading.setTextSize(20);
    heading.setTypeface(Typeface.DEFAULT_BOLD);
    heading.setTextColor(Color.rgb(20, 55, 80));
    heading.setPadding(4, 28, 4, 10);
    root.addView(heading);
    LinearLayout content = new LinearLayout(this);
    content.setOrientation(LinearLayout.VERTICAL);
    content.setPadding(22, 12, 22, 18);
    GradientDrawable background = new GradientDrawable();
    background.setColor(Color.WHITE);
    background.setCornerRadius(18);
    background.setStroke(1, Color.rgb(215, 224, 232));
    content.setBackground(background);
    root.addView(content, new LinearLayout.LayoutParams(-1, -2));
    return content;
  }

  void setAdvancedVisible(boolean visible) {
    if (advancedOptions == null || advancedButton == null) return;
    advancedOptions.setVisibility(visible ? android.view.View.VISIBLE : android.view.View.GONE);
    advancedButton.setText(visible ? "收起高级选项" : "高级选项");
    advancedButton.setContentDescription(visible ? "收起高级选项" : "展开高级选项");
  }

  public void onCreate(Bundle state) {
    super.onCreate(state);
    prefs = getSharedPreferences("reader", 0);
    LinearLayout l = new LinearLayout(this);
    l.setOrientation(1);
    l.setPadding(36, 65, 36, 35);
    l.setBackgroundColor(Color.rgb(242, 246, 250));
    ScrollView scroll = new ScrollView(this);
    scroll.addView(l);
    setContentView(scroll);
    label(l, "漫画朗读", 30);
    label(l, "安卓截屏 · 电脑识别 · 整页朗读", 16);
    LinearLayout connection = section(l, "电脑连接与模型");
    label(connection, "电脑服务地址", 16);
    address = new EditText(this);
    address.setSingleLine();
    address.setText(prefs.getString("address", ReaderDefaults.ADDRESS));
    changed(address, !ReaderDefaults.ADDRESS.equals(address.getText().toString()));
    connection.addView(address);
    label(connection, "配对口令", 16);
    token = new EditText(this);
    token.setSingleLine();
    token.setText(prefs.getString("token", ReaderDefaults.TOKEN));
    changed(token, !ReaderDefaults.TOKEN.equals(token.getText().toString()));
    connection.addView(token);
    connectionStatus = new TextView(this);
    connectionStatus.setTextSize(16);
    connectionStatus.setPadding(0, 18, 0, 12);
    setConnectionStatus("正在检查电脑连接…", Color.rgb(180, 120, 20));
    connection.addView(connectionStatus);
    TextWatcher connectionWatcher = new TextWatcher() {
      public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
      public void onTextChanged(CharSequence s, int start, int before, int count) {}
      public void afterTextChanged(Editable value) {
        saveConnectionFields();
        changed(address, !ReaderDefaults.ADDRESS.equals(address.getText().toString().trim().replaceAll("/$", "")));
        changed(token, !ReaderDefaults.TOKEN.equals(token.getText().toString().trim()));
        restartConnectionMonitor(350);
      }
    };
    address.addTextChangedListener(connectionWatcher);
    token.addTextChangedListener(connectionWatcher);
    label(connection, "朗读模型", 16);
    modelStatus = new TextView(this);
    modelStatus.setText(prefs.getString("modelLabel", ReaderDefaults.MODEL_LABEL));
    modelStatus.setTextSize(15);
    changed(modelStatus, !ReaderDefaults.MODEL_LABEL.contentEquals(modelStatus.getText()));
    modelStatus.setPadding(0, 4, 0, 8);
    modelStatus.setContentDescription("当前朗读模型 " + modelStatus.getText());
    connection.addView(modelStatus);
    button(connection, "选择朗读模型", () -> showModelPicker());
    label(connection, "模型列表由电脑动态读取；展开目录可查看头像、型号和错误状态。", 13);
    LinearLayout reading = section(l, "常用朗读设置");
    TextView gapLabel = new TextView(this);
    gapLabel.setTextSize(16);
    gapLabel.setPadding(0, 18, 0, 8);
    reading.addView(gapLabel);
    SeekBar gap = new SeekBar(this);
    gap.setMax(30);
    gap.setProgress(Math.max(0, Math.min(3000, prefs.getInt("sentenceGapMs", ReaderDefaults.SENTENCE_GAP_MS))) / 100);
    gapLabel.setText(String.format(java.util.Locale.CHINA, "句间停顿：%.1f 秒", gap.getProgress() / 10.0));
    changed(gapLabel, gap.getProgress() * 100 != ReaderDefaults.SENTENCE_GAP_MS);
    gap.setContentDescription(gapLabel.getText());
    gap.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
      public void onProgressChanged(SeekBar bar, int progress, boolean fromUser) {
        gapLabel.setText(String.format(java.util.Locale.CHINA, "句间停顿：%.1f 秒", progress / 10.0));
        changed(gapLabel, progress * 100 != ReaderDefaults.SENTENCE_GAP_MS);
        bar.setContentDescription(gapLabel.getText());
        if (fromUser) prefs.edit().putInt("sentenceGapMs", progress * 100).apply();
      }
      public void onStartTrackingTouch(SeekBar bar) {}
      public void onStopTrackingTouch(SeekBar bar) {}
    });
    reading.addView(gap);
    label(reading, "按上一句实际播放结束和下一句语音就绪时间计算最小间隔；处理等待不会再叠加停顿。自动保存。", 13);
    TextView speedLabel = new TextView(this);
    speedLabel.setTextSize(16);
    speedLabel.setPadding(0, 18, 0, 8);
    reading.addView(speedLabel);
    SeekBar speed = new SeekBar(this);
    speed.setMax(20);
    int savedSpeed = Math.max(50, Math.min(150, prefs.getInt("speechSpeedPercent", ReaderDefaults.SPEECH_SPEED_PERCENT)));
    speed.setProgress((savedSpeed - 50) / 5);
    speedLabel.setText(String.format(java.util.Locale.CHINA, "朗读速度：%.2f×", savedSpeed / 100.0));
    changed(speedLabel, savedSpeed != ReaderDefaults.SPEECH_SPEED_PERCENT);
    speed.setContentDescription(speedLabel.getText());
    speed.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
      public void onProgressChanged(SeekBar bar, int progress, boolean fromUser) {
        int percent = 50 + progress * 5;
        speedLabel.setText(String.format(java.util.Locale.CHINA, "朗读速度：%.2f×", percent / 100.0));
        changed(speedLabel, percent != ReaderDefaults.SPEECH_SPEED_PERCENT);
        bar.setContentDescription(speedLabel.getText());
        if (fromUser) prefs.edit().putInt("speechSpeedPercent", percent).apply();
      }
      public void onStartTrackingTouch(SeekBar bar) {}
      public void onStopTrackingTouch(SeekBar bar) {}
    });
    reading.addView(speed);
    label(reading, "范围 0.50×–1.50×；自动保存，从下一条新合成的语音生效。", 13);
    auto = new CheckBox(this);
    auto.setText("读完一页后自动翻页");
    auto.setChecked(prefs.getBoolean("auto", ReaderDefaults.AUTO_FLIP));
    changed(auto, auto.isChecked() != ReaderDefaults.AUTO_FLIP);
    auto.setOnCheckedChangeListener((button, checked) -> {
      prefs.edit().putBoolean("auto", checked).apply();
      changed(auto, checked != ReaderDefaults.AUTO_FLIP);
    });
    reading.addView(auto);
    label(reading, "暂停只停播放，本页识别、语音处理和预取继续；全部停止会清空本页。", 13);

    advancedButton = button(l, "高级选项", () -> setAdvancedVisible(advancedOptions.getVisibility() != android.view.View.VISIBLE));
    label(l, "语音检查、性能、浮窗外观和翻页细节。", 13);
    advancedOptions = new LinearLayout(this);
    advancedOptions.setOrientation(LinearLayout.VERTICAL);
    l.addView(advancedOptions);
    LinearLayout voiceAdvanced = section(advancedOptions, "语音质量与浮窗");
    TextView opacityLabel = new TextView(this);
    opacityLabel.setTextSize(16);
    opacityLabel.setPadding(0, 18, 0, 8);
    voiceAdvanced.addView(opacityLabel);
    SeekBar opacity = new SeekBar(this);
    opacity.setMax(14);
    int savedOpacity = Math.max(30, Math.min(100, prefs.getInt("overlayOpacityPercent", ReaderDefaults.OVERLAY_OPACITY_PERCENT)));
    savedOpacity = 30 + Math.round((savedOpacity - 30) / 5.0f) * 5;
    opacity.setProgress((savedOpacity - 30) / 5);
    opacityLabel.setText("浮窗透明度：" + savedOpacity + "%");
    changed(opacityLabel, savedOpacity != ReaderDefaults.OVERLAY_OPACITY_PERCENT);
    opacity.setContentDescription(opacityLabel.getText());
    opacity.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
      public void onProgressChanged(SeekBar bar, int progress, boolean fromUser) {
        int percent = 30 + progress * 5;
        opacityLabel.setText("浮窗透明度：" + percent + "%");
        changed(opacityLabel, percent != ReaderDefaults.OVERLAY_OPACITY_PERCENT);
        bar.setContentDescription(opacityLabel.getText());
        if (fromUser) prefs.edit().putInt("overlayOpacityPercent", percent).apply();
      }
      public void onStartTrackingTouch(SeekBar bar) {}
      public void onStopTrackingTouch(SeekBar bar) {}
    });
    voiceAdvanced.addView(opacity);
    label(voiceAdvanced, "范围 30%–100%；运行中的浮窗会立即更新。", 13);
    asr = new CheckBox(this);
    asr.setText("每个朗读单元都做 ASR 完整性检查");
    asr.setChecked(prefs.getBoolean("asrCheck", ReaderDefaults.ASR_CHECK));
    changed(asr, asr.isChecked() != ReaderDefaults.ASR_CHECK);
    asr.setOnCheckedChangeListener((button, checked) -> {
      prefs.edit().putBoolean("asrCheck", checked).apply();
      changed(asr, checked != ReaderDefaults.ASR_CHECK);
    });
    voiceAdvanced.addView(asr);
    label(voiceAdvanced, "开启时逐个检查并补救漏读；关闭时全部不检查。", 13);
    LinearLayout performanceAdvanced = section(advancedOptions, "低等待优化");
    lowLatency = new CheckBox(this);
    lowLatency.setText("全部开启/关闭");
    performanceAdvanced.addView(lowLatency);
    parallelVision = optimizationCheck(performanceAdvanced, "OCR 与分镜并行", "parallelVision");
    eagerFirstAudio = optimizationCheck(performanceAdvanced, "首段语音提前生成", "eagerFirstAudio");
    earlyPrefetch = optimizationCheck(performanceAdvanced, "按顺序预取本页全部语音", "earlyPrefetch");
    lowLatency.setChecked(allOptimizationsEnabled());
    changed(lowLatency, !allOptimizationsEnabled());
    lowLatency.setOnCheckedChangeListener((button, checked) -> {
      if (syncingOptimizations) return;
      syncingOptimizations = true;
      parallelVision.setChecked(checked);
      eagerFirstAudio.setChecked(checked);
      earlyPrefetch.setChecked(checked);
      syncingOptimizations = false;
      changed(lowLatency, !allOptimizationsEnabled());
    });
    label(performanceAdvanced, "各项可独立关闭；全部关闭即使用优化前流水线。电脑启动预热由电脑配置单独控制。", 13);
    LinearLayout historyAdvanced = section(advancedOptions, "历史结果复用");
    historyReuse = new CheckBox(this);
    historyReuse.setText("匹配历史结果时跳过处理");
    historyReuse.setChecked(prefs.getBoolean("historyReuse", ReaderDefaults.HISTORY_REUSE));
    changed(historyReuse, historyReuse.isChecked() != ReaderDefaults.HISTORY_REUSE);
    historyReuse.setOnCheckedChangeListener((button, checked) -> {
      prefs.edit().putBoolean("historyReuse", checked).apply();
      changed(historyReuse, checked != ReaderDefaults.HISTORY_REUSE);
    });
    historyAdvanced.addView(historyReuse);
    historyPromote = new CheckBox(this);
    historyPromote.setText("将本次新结果设为后续默认");
    historyPromote.setChecked(prefs.getBoolean("historyPromote", ReaderDefaults.HISTORY_PROMOTE));
    changed(historyPromote, historyPromote.isChecked() != ReaderDefaults.HISTORY_PROMOTE);
    historyPromote.setOnCheckedChangeListener((button, checked) -> {
      prefs.edit().putBoolean("historyPromote", checked).apply();
      changed(historyPromote, checked != ReaderDefaults.HISTORY_PROMOTE);
    });
    historyAdvanced.addView(historyPromote);
    label(historyAdvanced, "关闭复用会重新处理并保存新版本；设为默认只切换后续选择，旧版本仍保留。", 13);
    LinearLayout flipAdvanced = section(advancedOptions, "自动翻页细节");
    adaptiveCapture = new CheckBox(this);
    adaptiveCapture.setText("翻页后自适应抓帧");
    adaptiveCapture.setChecked(prefs.getBoolean("adaptiveCapture", ReaderDefaults.ADAPTIVE_CAPTURE));
    changed(adaptiveCapture, adaptiveCapture.isChecked() != ReaderDefaults.ADAPTIVE_CAPTURE);
    adaptiveCapture.setOnCheckedChangeListener((button, checked) -> {
      prefs.edit().putBoolean("adaptiveCapture", checked).apply();
      changed(adaptiveCapture, checked != ReaderDefaults.ADAPTIVE_CAPTURE);
    });
    flipAdvanced.addView(adaptiveCapture);
    label(flipAdvanced, "开启：120ms 后观察，画面稳定即截图；关闭：固定等待 800ms 后截图。", 13);
    left = new CheckBox(this);
    left.setText("下一页向左翻（默认向右翻）");
    left.setChecked(prefs.getBoolean("left", ReaderDefaults.NEXT_PAGE_LEFT));
    changed(left, left.isChecked() != ReaderDefaults.NEXT_PAGE_LEFT);
    left.setOnCheckedChangeListener((button, checked) -> {
      prefs.edit().putBoolean("left", checked).apply();
      changed(left, checked != ReaderDefaults.NEXT_PAGE_LEFT);
    });
    flipAdvanced.addView(left);
    label(flipAdvanced, "翻页滑动高度（10–90%）和时长（100–1500 毫秒）", 14);
    swipeY = new EditText(this);
    swipeY.setSingleLine();
    swipeY.setInputType(2);
    swipeY.setText(String.valueOf(prefs.getInt("swipeY", ReaderDefaults.SWIPE_Y_PERCENT)));
    changed(swipeY, number(swipeY, ReaderDefaults.SWIPE_Y_PERCENT, 10, 90) != ReaderDefaults.SWIPE_Y_PERCENT);
    flipAdvanced.addView(swipeY);
    swipeMs = new EditText(this);
    swipeMs.setSingleLine();
    swipeMs.setInputType(2);
    swipeMs.setText(String.valueOf(prefs.getInt("swipeMs", ReaderDefaults.SWIPE_DURATION_MS)));
    changed(swipeMs, number(swipeMs, ReaderDefaults.SWIPE_DURATION_MS, 100, 1500) != ReaderDefaults.SWIPE_DURATION_MS);
    flipAdvanced.addView(swipeMs);
    TextWatcher flipWatcher = new TextWatcher() {
      public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
      public void onTextChanged(CharSequence s, int start, int before, int count) {}
      public void afterTextChanged(Editable value) {
        changed(swipeY, number(swipeY, ReaderDefaults.SWIPE_Y_PERCENT, 10, 90) != ReaderDefaults.SWIPE_Y_PERCENT);
        changed(swipeMs, number(swipeMs, ReaderDefaults.SWIPE_DURATION_MS, 100, 1500) != ReaderDefaults.SWIPE_DURATION_MS);
      }
    };
    swipeY.addTextChangedListener(flipWatcher);
    swipeMs.addTextChangedListener(flipWatcher);
    setAdvancedVisible(false);
    LinearLayout startup = section(l, "启动与权限");
    button(
        startup,
        "1  允许浮窗",
        () ->
            startActivity(
                new Intent(
                    Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                    Uri.parse("package:" + getPackageName()))));
    button(
        startup,
        "2  开启翻页无障碍（自动翻页时需要）",
        () -> startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)));
    button(
        startup,
        "3  保存并启动朗读浮窗",
        () -> {
          save();
          if (!Settings.canDrawOverlays(this)) {
            Toast.makeText(this, "请先允许浮窗", 0).show();
            return;
          }
          startActivityForResult(screenCaptureIntent(), 7);
        });
    button(startup, "关闭朗读服务和浮窗", () -> stopService(new Intent(this, ReaderService.class)));
    label(startup, "确认共享整个屏幕后，切换到漫画阅读器，点击浮窗“读”。截图时浮窗会短暂隐藏。", 15);
    label(startup, "暂停只停止播放，后台继续处理本页；继续从原位置恢复。全部停止会取消并清空本页，重新开始时再次截图。×：关闭整个浮窗服务。", 14);
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
    // 返回设置页时读取浮窗的新选择，避免保存旧勾选覆盖开关。
    if (auto != null) auto.setChecked(prefs.getBoolean("auto", ReaderDefaults.AUTO_FLIP));
    if (asr != null) asr.setChecked(prefs.getBoolean("asrCheck", ReaderDefaults.ASR_CHECK));
    startConnectionMonitor();
  }

  CheckBox optimizationCheck(LinearLayout layout, String text, String key) {
    CheckBox box = new CheckBox(this);
    box.setText(text);
    boolean defaultValue = optimizationDefault(key);
    box.setChecked(prefs.getBoolean(key, defaultValue));
    changed(box, box.isChecked() != defaultValue);
    box.setOnCheckedChangeListener((button, checked) -> {
      prefs.edit().putBoolean(key, checked).apply();
      changed(box, checked != defaultValue);
      if (!syncingOptimizations && lowLatency != null) {
        syncingOptimizations = true;
        lowLatency.setChecked(allOptimizationsEnabled());
        changed(lowLatency, !allOptimizationsEnabled());
        syncingOptimizations = false;
      }
    });
    layout.addView(box);
    return box;
  }

  boolean allOptimizationsEnabled() {
    return parallelVision != null && eagerFirstAudio != null && earlyPrefetch != null
        && parallelVision.isChecked() && eagerFirstAudio.isChecked() && earlyPrefetch.isChecked();
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
              modelStatus.setText(label);
              changed(modelStatus, !ReaderDefaults.MODEL_LABEL.equals(label));
              modelStatus.setContentDescription("当前朗读模型 " + label);
            })
        .show();
  }
  void setConnectionStatus(String text, int color) {
    if (connectionStatus == null) return;
    connectionStatus.setText("●  " + text);
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
    setConnectionStatus("正在检查电脑连接…", Color.rgb(180, 120, 20));
    int requestGeneration = connectionGeneration;
    String endpoint = address.getText().toString().trim().replaceAll("/$", "");
    String secret = token.getText().toString().trim();
    connectionExecutor.execute(
            () -> {
              boolean connected = false;
              String message = "";
              try {
                java.net.HttpURLConnection c =
                    (java.net.HttpURLConnection)
                        new java.net.URL(endpoint + "/pair").openConnection();
                c.setRequestProperty("X-Reader-Token", secret);
                c.setConnectTimeout(2500);
                c.setReadTimeout(2500);
                try {
                  int status = c.getResponseCode();
                  if (status != 200) throw new java.io.IOException("HTTP " + status);
                  connected = true;
                } finally {
                  c.disconnect();
                }
              } catch (Exception e) {
                message = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
              }
              boolean result = connected;
              String detail = message;
              runOnUiThread(
                  () -> {
                    if (!connectionVisible || requestGeneration != connectionGeneration) return;
                    connectionChecking = false;
                    if (result)
                      setConnectionStatus("电脑已连接", Color.rgb(20, 135, 75));
                    else
                      setConnectionStatus("电脑未连接：" + detail, Color.rgb(190, 45, 45));
                    connectionHandler.postDelayed(() -> checkConnection(), 5000);
                  });
            });
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
