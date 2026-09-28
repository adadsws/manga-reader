package org.local.reader;

// 截图生命周期参考 MangaLens c1eb6606b8ae5ab4af79565d5385ddda6926bbc0
// https://github.com/mkisontop/MangaLens；本地仅实现网络、播放与取消编排。
import android.app.*;
import android.content.*;
import android.content.pm.ServiceInfo;
import android.graphics.*;
import android.hardware.display.*;
import android.media.*;
import android.media.projection.*;
import android.os.*;
import android.text.SpannableString;
import android.text.Spanned;
import android.text.style.RelativeSizeSpan;
import android.util.*;
import android.view.*;
import android.widget.*;
import java.io.*;
import java.net.*;
import java.nio.*;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.*;
import org.json.*;

public class ReaderService extends Service {
  static final String ICON_PLAY = "▶", ICON_PAUSE = "Ⅱ", ICON_STOP = "■", ICON_CLOSE = "×";
  static final long AUTO_FLIP_CAPTURE_DELAY_MS = 120;
  static final long AUTO_FLIP_CHANGE_TIMEOUT_MS = 1800;

  final Handler main = new Handler(Looper.getMainLooper());
  final ExecutorService workers = Executors.newFixedThreadPool(2);
  WindowManager wm;
  LinearLayout overlay;
  TextView status;
  Button mainAction, pause, closeButton, autoButton, errorButton;
  String lastError = "";
  SharedPreferences preferences;
  SharedPreferences.OnSharedPreferenceChangeListener preferenceListener;
  int flipGeneration = 0;
  int speechSettingsGeneration = 0;
  boolean waitingForGap = false;
  long gapRemainingMs, gapDeadline, expectedPlaybackEndAt;
  Runnable gapTask;
  WindowManager.LayoutParams params;
  MediaProjection projection;
  VirtualDisplay display;
  ImageReader reader;
  MediaPlayer player;
  int width, height, density, generation = 0, index = 0;
  boolean capturing = false, running = false, paused = false, automatic = false, destroyed = false;
  long captureAfter, stableAt, captureStarted;
  int[] stableHash, lastPageHash;
  Bitmap pendingFrame;
  Runnable settle;
  String base, token, pageId = "";
  String historyMatchStage = "";
  JSONArray sentences;
  final ConcurrentHashMap<Integer, File> audioFiles = new ConcurrentHashMap<>();
  final java.util.Set<HttpURLConnection> activeConnections = ConcurrentHashMap.newKeySet();
  final java.util.Set<Integer> downloading = ConcurrentHashMap.newKeySet();
  boolean autoFlip, left, asrCheck, parallelVision, eagerFirstAudio, earlyPrefetch, adaptiveCapture, historyReuse, historyPromote, resumeCapture = false;

  public IBinder onBind(Intent i) {
    return null;
  }

  public void onCreate() {
    super.onCreate();
    NotificationManager nm = getSystemService(NotificationManager.class);
    nm.createNotificationChannel(
        new NotificationChannel("reader", "漫画朗读", NotificationManager.IMPORTANCE_LOW));
    Notification n =
        new Notification.Builder(this, "reader")
            .setContentTitle("漫画朗读已启动")
            .setContentText("截图和播放服务运行中，可在应用内停止")
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setOngoing(true)
            .build();
    if (Build.VERSION.SDK_INT >= 29)
      startForeground(
          1,
          n,
          ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION
              | ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
    else startForeground(1, n);
    SharedPreferences p = getSharedPreferences("reader", 0);
    preferences = p;
    base = p.getString("address", ReaderDefaults.ADDRESS);
    token = p.getString("token", ReaderDefaults.TOKEN);
    autoFlip = p.getBoolean("auto", ReaderDefaults.AUTO_FLIP);
    left = p.getBoolean("left", ReaderDefaults.NEXT_PAGE_LEFT);
    asrCheck = p.getBoolean("asrCheck", ReaderDefaults.ASR_CHECK);
    parallelVision = p.getBoolean("parallelVision", ReaderDefaults.PARALLEL_VISION);
    eagerFirstAudio = p.getBoolean("eagerFirstAudio", ReaderDefaults.EAGER_FIRST_AUDIO);
    earlyPrefetch = p.getBoolean("earlyPrefetch", ReaderDefaults.EARLY_PREFETCH);
    adaptiveCapture = p.getBoolean("adaptiveCapture", ReaderDefaults.ADAPTIVE_CAPTURE);
    historyReuse = p.getBoolean("historyReuse", ReaderDefaults.HISTORY_REUSE);
    historyPromote = p.getBoolean("historyPromote", ReaderDefaults.HISTORY_PROMOTE);
    wm = (WindowManager) getSystemService(WINDOW_SERVICE);
    DisplayMetrics m = new DisplayMetrics();
    wm.getDefaultDisplay().getRealMetrics(m);
    width = m.widthPixels;
    height = m.heightPixels;
    density = m.densityDpi;
    overlay = new LinearLayout(this);
    overlay.setOrientation(1);
    overlay.setPadding(12, 6, 12, 6);
    overlay.setBackgroundColor(0xee12324a);
    applyOverlayOpacity();
    LinearLayout statusRow = new LinearLayout(this);
    statusRow.setGravity(Gravity.CENTER_VERTICAL);
    overlay.addView(statusRow);
    status = new TextView(this);
    status.setTextColor(Color.WHITE);
    status.setTextSize(12);
    status.setMaxLines(2);
    status.setText("就绪（拖动此处）");
    statusRow.addView(status, new LinearLayout.LayoutParams(0, -2, 1));
    autoButton = add(statusRow, "", () -> toggleAutoFlip());
    updateAutoButton();
    LinearLayout errorRow = new LinearLayout(this);
    overlay.addView(errorRow);
    errorButton = add(errorRow, "查看完整错误", () -> showErrorDetails());
    errorButton.setContentDescription("查看完整错误");
    errorButton.setVisibility(View.GONE);
    preferenceListener = (prefs, key) -> {
      if ("auto".equals(key)) setAutoFlip(prefs.getBoolean("auto", ReaderDefaults.AUTO_FLIP));
      else if ("asrCheck".equals(key)) {
        asrCheck = prefs.getBoolean("asrCheck", ReaderDefaults.ASR_CHECK);
        debug(asrCheck ? "asr_on" : "asr_off");
      } else if ("overlayOpacityPercent".equals(key)) applyOverlayOpacity();
      else if ("speechSpeedPercent".equals(key)) refreshSpeechSpeed();
      else if ("parallelVision".equals(key)) parallelVision = prefs.getBoolean(key, ReaderDefaults.PARALLEL_VISION);
      else if ("eagerFirstAudio".equals(key)) eagerFirstAudio = prefs.getBoolean(key, ReaderDefaults.EAGER_FIRST_AUDIO);
      else if ("earlyPrefetch".equals(key)) earlyPrefetch = prefs.getBoolean(key, ReaderDefaults.EARLY_PREFETCH);
      else if ("adaptiveCapture".equals(key)) adaptiveCapture = prefs.getBoolean(key, ReaderDefaults.ADAPTIVE_CAPTURE);
      else if ("historyReuse".equals(key)) historyReuse = prefs.getBoolean(key, ReaderDefaults.HISTORY_REUSE);
      else if ("historyPromote".equals(key)) historyPromote = prefs.getBoolean(key, ReaderDefaults.HISTORY_PROMOTE);
    };
    p.registerOnSharedPreferenceChangeListener(preferenceListener);
    LinearLayout row = new LinearLayout(this);
    overlay.addView(row);
    mainAction = addLabeledIcon(
        row,
        ICON_PLAY,
        "重新开始",
        1f,
        () -> {
          if (running) {
            cancel();
            setStatus("已全部停止 · 可重新开始");
          } else {
            automatic = false;
            cancel();
            lastPageHash = null;
            capture();
          }
        });
    pause = addLabeledIcon(row, ICON_PAUSE, "暂停", 1f, () -> togglePause());
    closeButton = addIcon(row, ICON_CLOSE, "关闭浮窗", .55f, () -> stopSelf());
    updateActionButtons();
    params =
        new WindowManager.LayoutParams(
            (int) (250 * m.density),
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
            PixelFormat.TRANSLUCENT);
    params.gravity = Gravity.TOP | Gravity.LEFT;
    params.x = 20;
    params.y = 100;
    wm.addView(overlay, params);
    debug("overlay_ready");
    status.setOnTouchListener(
        new View.OnTouchListener() {
          float x, y;
          int ox, oy;

          public boolean onTouch(View v, android.view.MotionEvent e) {
            if (e.getAction() == 0) {
              x = e.getRawX();
              y = e.getRawY();
              ox = params.x;
              oy = params.y;
            }
            if (e.getAction() == 2) {
              params.x = ox + (int) (e.getRawX() - x);
              params.y = oy + (int) (e.getRawY() - y);
              wm.updateViewLayout(overlay, params);
            }
            return true;
          }
        });
  }

  Button add(LinearLayout row, String text, Runnable action) {
    return configureButton(row, new Button(this), text, action);
  }

  Button configureButton(LinearLayout row, Button b, String text, Runnable action) {
    b.setText(text);
    b.setTextSize(13);
    b.setPadding(0, 0, 0, 0);
    b.setSingleLine(true);
    b.setMinWidth(0);
    b.setMinimumWidth(0);
    row.addView(b, new LinearLayout.LayoutParams(0, -2, 1));
    b.setOnClickListener(v -> action.run());
    return b;
  }

  Button addIcon(LinearLayout row, String icon, String description, float weight, Runnable action) {
    Button button = add(row, icon, action);
    ((LinearLayout.LayoutParams) button.getLayoutParams()).weight = weight;
    button.setTextSize(20);
    button.setTypeface(Typeface.DEFAULT_BOLD);
    button.setGravity(Gravity.CENTER);
    button.setContentDescription(description);
    return button;
  }

  Button addLabeledIcon(LinearLayout row, String icon, String label, float weight, Runnable action) {
    Button button = addIcon(row, icon, label, weight, action);
    setLabeledIcon(button, icon, label);
    return button;
  }

  void setLabeledIcon(Button button, String icon, String label) {
    SpannableString text = new SpannableString(icon + " " + label);
    text.setSpan(new RelativeSizeSpan(.55f), icon.length() + 1, text.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
    button.setText(text);
    button.setContentDescription(label);
  }

  void setRunning(boolean value) {
    running = value;
    updateActionButtons();
  }

  void updateActionButtons() {
    if (mainAction != null)
      setLabeledIcon(mainAction, running ? ICON_STOP : ICON_PLAY, running ? "全部停止" : "重新开始");
    updatePauseButton();
  }

  public int onStartCommand(Intent intent, int flags, int id) {
    if (intent == null || projection != null) return START_NOT_STICKY;
    try {
      projection =
          ((MediaProjectionManager) getSystemService(MEDIA_PROJECTION_SERVICE))
              .getMediaProjection(
                  intent.getIntExtra("code", -1), intent.getParcelableExtra("data"));
      projection.registerCallback(
          new MediaProjection.Callback() {
            public void onStop() {
              stopSelf();
            }

            public void onCapturedContentResize(int w, int h) {
              resizeCapture(w, h);
            }
          },
          main);
      reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 3);
      reader.setOnImageAvailableListener(r -> frame(r), main);
      display =
          projection.createVirtualDisplay(
              "ReaderCapture",
              width,
              height,
              density,
              DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
              reader.getSurface(),
              null,
              main);
    } catch (Exception e) {
      fail("截图授权失败：" + e.getMessage());
    }
    return START_NOT_STICKY;
  }

  void resizeCapture(int w, int h) {
    if (display == null || w == width && h == height) return;
    cancel();
    width = w;
    height = h;
    ImageReader old = reader;
    reader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 3);
    reader.setOnImageAvailableListener(r -> frame(r), main);
    display.resize(width, height, density);
    display.setSurface(reader.getSurface());
    old.close();
    setStatus("屏幕尺寸已变更，请点击重新开始");
  }

  // 工具读取 ReaderDebug；模拟器控制台只显示固定中文摘要，不转发正文和口令。
  static String consoleMessage(String event) {
    switch (event) {
      case "overlay_ready": return "朗读浮窗已启动";
      case "overlay_closed": return "朗读浮窗已关闭";
      case "capture_start": return "正在截取当前页面";
      case "upload_start": return "截图已发送，电脑正在识别";
      case "ocr_received": return "识别完成，准备生成语音";
      case "audio_download": return "正在接收语音";
      case "audio_prepared": return "语音已准备完成";
      case "play_start": return "开始播放";
      case "sentence_gap_start": return "进入句间停顿";
      case "sentence_gap_end": return "句间停顿结束";
      case "play_complete": return "本页播放完成";
      case "turn_start": return "开始自动翻页";
      case "turn_complete": return "自动翻页完成";
      case "page_unchanged": return "翻页后画面未变化，已停止自动翻页";
      case "auto_on": return "自动翻页已开启";
      case "auto_off": return "自动翻页已关闭";
      case "pause": return "朗读已暂停";
      case "resume": return "继续朗读";
      case "asr_on": return "语音完整性检查已开启";
      case "asr_off": return "语音完整性检查已关闭";
      case "cancel": return "当前朗读已停止";
      case "error": return "安卓操作失败，请点浮窗错误按钮查看完整原因";
      default: return null;
    }
  }

  void debug(String event) {
    Log.i("ReaderDebug", "event=" + event + " generation=" + generation + " auto=" + autoFlip);
    String message = consoleMessage(event);
    if (message != null) Log.i("ReaderConsole", message);
  }

  void setStatus(String s) {
    if (destroyed) return;
    String line = statusLine(s, index, sentences == null ? 0 : sentences.length());
    line = line.length() > 48 ? line.substring(0, 48) + "…" : line;
    String historyLine = historyStatusLine(historyMatchStage);
    status.setText(historyLine.isEmpty() ? line : line + "\n" + historyLine);
    Log.i("Reader", s);
  }

  static String statusLine(String message, int index, int total) {
    if (total <= 0) return message;
    int current = Math.max(1, Math.min(index + 1, total));
    return current + "/" + total + " · " + message;
  }

  static String historyStatusLine(String stage) {
    if ("image".equals(stage)) return "匹配：图片";
    if ("ocr".equals(stage)) return "匹配：OCR结构";
    if ("text".equals(stage)) return "匹配：朗读文本";
    if ("none".equals(stage)) return "匹配：无";
    if ("disabled".equals(stage)) return "匹配：关闭";
    return "";
  }

  void setHistoryMatchStage(String stage) {
    historyMatchStage = stage == null ? "" : stage;
    Log.i("ReaderDebug", "event=history_match stage=" + historyMatchStage);
  }

  void fail(String s) {
    debug("error");    if (destroyed) return;
    cancel();
    lastError = s == null || s.trim().isEmpty() ? "未知错误" : s;
    if (errorButton != null) errorButton.setVisibility(View.VISIBLE);
    setStatus(lastError);
  }

  int speechSpeedPercent() {
    return Math.max(50, Math.min(150, preferences.getInt("speechSpeedPercent", ReaderDefaults.SPEECH_SPEED_PERCENT)));
  }

  int overlayOpacityPercent() {
    return Math.max(30, Math.min(100, preferences.getInt("overlayOpacityPercent", ReaderDefaults.OVERLAY_OPACITY_PERCENT)));
  }

  void applyOverlayOpacity() {
    if (overlay != null) overlay.setAlpha(overlayOpacityPercent() / 100.0f);
  }

  void refreshSpeechSpeed() {
    speechSettingsGeneration++;
    for (HttpURLConnection connection : activeConnections) connection.disconnect();
    for (Integer position : new java.util.ArrayList<>(audioFiles.keySet())) {
      if (position > index) {
        File stale = audioFiles.remove(position);
        if (stale != null) stale.delete();
      }
    }
    downloading.removeIf(position -> position > index);
    debug("speech_speed_changed");
  }

  void stopAfterUnchangedPage() {
    cancel();
    debug("page_unchanged");
    setStatus("翻页后画面未变化，已停止自动翻页");
  }

  void clearError() {
    lastError = "";
    if (errorButton != null) errorButton.setVisibility(View.GONE);
    if (status != null) status.setMaxLines(3);
  }

  void showErrorDetails() {
    if (lastError.isEmpty()) return;
    TextView details = new TextView(this);
    details.setText(lastError);
    details.setTextSize(15);
    details.setTextIsSelectable(true);
    details.setPadding(36, 20, 36, 20);
    ScrollView scroll = new ScrollView(this);
    scroll.addView(details);
    try {
      AlertDialog dialog = new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Light_Dialog_Alert)
          .setTitle("朗读错误详情").setView(scroll).setPositiveButton("关闭", null).create();
      Window window = dialog.getWindow();
      if (window != null) window.setType(WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY);
      dialog.show();
    } catch (Exception ignored) {
      status.setMaxLines(Integer.MAX_VALUE);
      status.setText(lastError);
    }
  }

  void capture() {
    clearError();
    debug("capture_start");    if (projection == null) {
      fail("请重新启动并授权整个屏幕");
      return;
    }
    paused = false;
    setRunning(true);
    stableHash = null;
    captureStarted = SystemClock.elapsedRealtime();
    captureAfter = System.nanoTime();
    Image old = reader.acquireLatestImage();
    if (old != null) old.close();
    capturing = true;
    overlay.setVisibility(View.INVISIBLE);
    int g = generation;
    main.postDelayed(
        () -> {
          if (g == generation && capturing && unchangedPageTimedOut()) stopAfterUnchangedPage();
        },
        AUTO_FLIP_CHANGE_TIMEOUT_MS);
    main.postDelayed(
        () -> {
          if (g == generation && capturing) fail("屏幕未稳定或无法截图，请重试");
        },
        6000);
  }

  int[] hash(Bitmap b) {
    Bitmap tiny = Bitmap.createScaledBitmap(b, 32, 32, true);
    int[] p = new int[1024];
    tiny.getPixels(p, 0, 32, 0, 0, 32, 32);
    tiny.recycle();
    for (int i = 0; i < p.length; i++)
      p[i] = (Color.red(p[i]) + Color.green(p[i]) + Color.blue(p[i])) / 3;
    return p;
  }

  double difference(int[] a, int[] b) {
    if (a == null || b == null) return 255;
    long d = 0;
    for (int i = 0; i < a.length; i++) d += Math.abs(a[i] - b[i]);
    return d / (double) a.length;
  }

  boolean unchangedPageTimedOut() {
    return adaptiveCapture && automatic && SystemClock.elapsedRealtime() - captureStarted >= AUTO_FLIP_CHANGE_TIMEOUT_MS
        && difference(lastPageHash, stableHash) < 3;
  }

  void frame(ImageReader r) {
    Image image = null;
    Bitmap bitmap = null;
    try {
      image = r.acquireLatestImage();
      if (image == null || !capturing) return;
      Image.Plane plane = image.getPlanes()[0];
      int stride = plane.getRowStride() / plane.getPixelStride();
      Bitmap padded = Bitmap.createBitmap(stride, height, Bitmap.Config.ARGB_8888);
      padded.copyPixelsFromBuffer(plane.getBuffer());
      bitmap = Bitmap.createBitmap(padded, 0, 0, width, height);
      if (padded != bitmap) padded.recycle();
      int[] current = hash(bitmap);
      boolean changed = difference(stableHash, current) > 3;
      stableHash = current;
      if (pendingFrame != null) pendingFrame.recycle();
      pendingFrame = bitmap;
      bitmap = null;
      if (settle == null || changed) {
        if (settle != null) main.removeCallbacks(settle);
        int g = generation;
        settle =
            () -> {
              settle = null;
              if (!capturing || g != generation || pendingFrame == null) return;
              Bitmap ready = pendingFrame;
              pendingFrame = null;
              try {
                if (automatic && difference(lastPageHash, stableHash) < 3) {
                  if (adaptiveCapture && !unchangedPageTimedOut()) {
                    // 翻页动画较慢时继续观察；一旦出现新画面，仍需稳定 300ms 才上传。
                    return;
                  }
                  capturing = false;
                  overlay.setVisibility(View.VISIBLE);
                  stopAfterUnchangedPage();
                  return;
                }
                capturing = false;
                overlay.setVisibility(View.VISIBLE);
                lastPageHash = stableHash;
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                ready.compress(Bitmap.CompressFormat.JPEG, 96, out);
                upload(out.toByteArray(), g);
              } finally {
                ready.recycle();
              }
            };
        main.postDelayed(settle, 300);
      }
    } catch (Exception e) {
      fail("截图失败：" + e.getMessage());
    } finally {
      if (image != null) image.close();
      if (bitmap != null) bitmap.recycle();
    }
  }

  static String httpErrorMessage(int code, byte[] body) {
    String raw = new String(body, StandardCharsets.UTF_8).trim();
    String reason = raw;
    try {
      String detail = new JSONObject(raw).optString("detail", "").trim();
      if (!detail.isEmpty()) reason = detail;
    } catch (Exception ignored) {}
    if (reason.isEmpty()) reason = "电脑没有返回错误正文";
    return "HTTP " + code + "：" + reason;
  }
  byte[] request(String method, String path, byte[] body) throws Exception {
    HttpURLConnection c = (HttpURLConnection) new URL(base + path).openConnection();
    activeConnections.add(c);
    c.setRequestMethod(method);
    c.setConnectTimeout(12000);
    c.setReadTimeout(240000);
    c.setRequestProperty("X-Reader-Token", token);
    c.setRequestProperty("X-Reader-ASR-Check", asrCheck ? "all" : "off");
    c.setRequestProperty("X-Reader-Parallel-Vision", parallelVision ? "on" : "off");
    c.setRequestProperty("X-Reader-Eager-First-Audio", eagerFirstAudio ? "on" : "off");
    c.setRequestProperty("X-Reader-Full-Page-Prefetch", earlyPrefetch ? "on" : "off");
    c.setRequestProperty("X-Reader-History-Reuse", historyReuse ? "on" : "off");
    c.setRequestProperty("X-Reader-History-Promote", historyPromote ? "on" : "off");
    c.setRequestProperty(
        "X-Reader-Speech-Speed",
        String.format(java.util.Locale.US, "%.2f", speechSpeedPercent() / 100.0));
    try {
      if (body != null) {
        c.setDoOutput(true);
        c.setRequestProperty(
            "Content-Type", "image/jpeg");
        c.setFixedLengthStreamingMode(body.length);
        try (OutputStream o = c.getOutputStream()) {
          o.write(body);
        }
      }
      int code = c.getResponseCode();
      InputStream in = code < 400 ? c.getInputStream() : c.getErrorStream();
      ByteArrayOutputStream out = new ByteArrayOutputStream();
      if (in != null)
        try (InputStream s = in) {
          byte[] buffer = new byte[16384];
          int n;
          while ((n = s.read(buffer)) != -1) {
            out.write(buffer, 0, n);
            if (out.size() > 30000000) throw new IOException("响应过大");
          }
        }
      if (code >= 400) throw new IOException(httpErrorMessage(code, out.toByteArray()));
      return out.toByteArray();
    } finally {
      activeConnections.remove(c);
      c.disconnect();
    }
  }

  void upload(byte[] body, int g) {
    debug("upload_start");    setStatus("电脑正在识别和排序…");
    workers.submit(
        () -> {
          try {
            JSONObject response =
                new JSONObject(new String(request("POST", "/pages", body), "UTF-8"));
            main.post(
                () -> {
                  if (g != generation || destroyed) return;
                  try {
                    pageId = response.getString("page_id");
                    sentences = response.getJSONArray("sentences");
                    setHistoryMatchStage(response.optString("match_stage", ""));
                    debug("ocr_received");
                    index = 0;
                    if (sentences.length() == 0) {
                      fail("此页未识别到文字，已停止");
                      return;
                    }
                    setStatus("整页已识别，准备语音…");
                    load(index, g);
                  } catch (Exception e) {
                    fail(e.getMessage());
                  }
                });
          } catch (Exception e) {
            main.post(
                () -> {
                  if (g == generation) fail("连接失败：" + e.getMessage());
                });
          }
        });
  }

  void load(int position, int g) {
    if (g != generation || destroyed) return;
    File ready = audioFiles.get(position);
    if (ready != null) {
      play(ready, g);
      return;
    }
    fetch(position, g);
  }

  void fetch(int position, int g) {
    if (sentences == null || position >= sentences.length() || !downloading.add(position)) return;
    debug("audio_download");
    String id = pageId;
    int speedGeneration = speechSettingsGeneration;
    workers.submit(
        () -> {
          try {
            byte[] data = request("GET", "/pages/" + id + "/audio/" + position, null);
            File f = new File(getCacheDir(), "voice-" + g + "-" + position + ".wav");
            try (FileOutputStream o = new FileOutputStream(f)) {
              o.write(data);
            }
            main.post(
                () -> {
                  downloading.remove(position);
                  if (g != generation || destroyed || speedGeneration != speechSettingsGeneration) {
                    f.delete();
                    return;
                  }
                  acceptDownloadedAudio(position, f, g);
                });
          } catch (Exception e) {
            main.post(
                () -> {
                  downloading.remove(position);
                  if (g == generation && speedGeneration == speechSettingsGeneration)
                    fail("语音失败：" + e.getMessage());
                });
          }
        });
  }

  // 当前段一落盘就让播放器准备，并按开关并行请求下一段。
  void acceptDownloadedAudio(int position, File file, int g) {
    audioFiles.put(position, file);
    if (position != index) return;
    play(file, g);
    if (earlyPrefetch) fetch(position + 1, g);
  }

  void play(File file, int g) {
    if (g != generation || destroyed || waitingForGap) return;
    releasePlayer();
    try {
      player = new MediaPlayer();
      player.setAudioAttributes(
          new AudioAttributes.Builder()
              .setUsage(AudioAttributes.USAGE_MEDIA)
              .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
              .build());
      player.setWakeMode(this, PowerManager.PARTIAL_WAKE_LOCK);
      player.setDataSource(file.getAbsolutePath());
      player.setOnPreparedListener(
          p -> {
            if (g != generation) return;
            try {
              setStatus(sentences.getJSONObject(index).getString("text"));
            } catch (Exception ignored) {
            }
            debug("audio_prepared");
            if (!paused) startPlayback(p);
            if (!earlyPrefetch) fetch(index + 1, g);
          });
      player.setOnCompletionListener(
          p -> {
            if (g != generation) return;
            long completedAt = SystemClock.elapsedRealtime();
            long remainingGap = remainingGapAfterPlayback(
                expectedPlaybackEndAt, completedAt, sentenceGapMs());
            expectedPlaybackEndAt = 0;
            File done = audioFiles.remove(index);
            if (done != null) done.delete();
            index++;
            if (index < sentences.length()) beginSentenceGap(g, remainingGap);
            else finished();
          });
      player.setOnErrorListener(
          (p, w, e) -> {
            fail("音频播放失败 " + w);
            return true;
          });
      player.prepareAsync();
    } catch (Exception e) {
      fail(e.getMessage());
    }
  }

  // 下一句处理与最小句间隔并行；播放须同时等到音频就绪和间隔结束。
  int sentenceGapMs() {
    return Math.max(0, Math.min(3000, preferences.getInt("sentenceGapMs", ReaderDefaults.SENTENCE_GAP_MS)));
  }

  static long remainingGapAfterPlayback(long expectedEndAt, long completedAt, int gapMs) {
    long audioEndedAt = expectedEndAt > 0 ? Math.min(expectedEndAt, completedAt) : completedAt;
    return Math.max(0, audioEndedAt + gapMs - completedAt);
  }

  void startPlayback(MediaPlayer activePlayer) {
    activePlayer.start();
    int remaining = Math.max(0, activePlayer.getDuration() - activePlayer.getCurrentPosition());
    expectedPlaybackEndAt = remaining > 0 ? SystemClock.elapsedRealtime() + remaining : 0;
    debug("play_start");
  }

  void beginSentenceGap(int g) {
    beginSentenceGap(g, sentenceGapMs());
  }

  void beginSentenceGap(int g, long remainingGapMs) {
    if (g != generation || destroyed) return;
    releasePlayer();
    clearSentenceGap();
    waitingForGap = true;
    gapRemainingMs = Math.max(0, remainingGapMs);
    debug("sentence_gap_start");
    setStatus("句间停顿…");
    load(index, g);
    if (!paused) scheduleSentenceGap(g);
  }

  void scheduleSentenceGap(int g) {
    gapDeadline = SystemClock.elapsedRealtime() + gapRemainingMs;
    gapTask = () -> {
      if (g != generation || destroyed || paused || !waitingForGap) return;
      waitingForGap = false;
      gapTask = null;
      gapRemainingMs = 0;
      debug("sentence_gap_end");
      if (!audioFiles.containsKey(index)) setStatus("准备下一句…");
      load(index, g);
    };
    main.postDelayed(gapTask, gapRemainingMs);
  }

  void clearSentenceGap() {
    if (gapTask != null) main.removeCallbacks(gapTask);
    gapTask = null;
    waitingForGap = false;
    gapRemainingMs = 0;
  }

  void finished() {
    debug("play_complete");    releasePlayer();
    if (!autoFlip) {
      setRunning(false);
      setStatus("本页朗读完成");
      return;
    }
    setStatus("本页完成，正在向" + (left ? "左" : "右") + "翻页…");
    int g = generation;
    debug("turn_start");
    int turn = flipGeneration;
    TurnService.ActiveCheck active =
        () -> !destroyed && g == generation && turn == flipGeneration && autoFlip;
    TurnService.turn(
        this,
        left,
        width,
        height,
        active,
        () -> {
          if (active.isActive()) {
            debug("turn_wait_accessibility");
            setStatus("本页完成，正在等待翻页无障碍连接…");
          }
        },
        () -> afterTurn(g, turn),
        () -> {
          if (active.isActive()) fail("翻页手势被取消");
        },
        reason -> {
          if (active.isActive()) fail(turnFailureMessage(reason));
        });
  }

  static String turnFailureMessage(int reason) {
    if (reason == TurnService.TURN_DISABLED) return "请开启漫画朗读的翻页无障碍服务";
    if (reason == TurnService.TURN_NOT_CONNECTED)
      return "翻页无障碍已设置但未连接，请在系统设置中关闭后重新开启";
    return "翻页无障碍暂时无法执行手势，请稍后重试";
  }

  // 开关改变后让旧手势/延迟截图失效，但不打断当前页音频。
  void toggleAutoFlip() {
    setAutoFlip(!autoFlip);
    preferences.edit().putBoolean("auto", autoFlip).apply();
  }

  void setAutoFlip(boolean enabled) {
    if (autoFlip != enabled) {
      autoFlip = enabled;
      debug(enabled ? "auto_on" : "auto_off");
      flipGeneration++;
      if (!enabled) resumeCapture = false;
    }
    updateAutoButton();
  }

  void updateAutoButton() {
    autoButton.setText("自动翻页：" + (autoFlip ? "开" : "关"));
    autoButton.setContentDescription(autoButton.getText());
    autoButton.setSelected(autoFlip);
  }

  void updatePauseButton() {
    if (pause == null) return;
    setLabeledIcon(pause, paused ? ICON_PLAY : ICON_PAUSE, paused ? "继续" : "暂停");
    pause.setEnabled(running);
  }

  void afterTurn(int g, int turn) {
    if (destroyed || g != generation || turn != flipGeneration || !autoFlip) return;
    debug("turn_complete");
    automatic = true;
    clearPage();
    resumeCapture = true;
    main.postDelayed(
        () -> {
          if (!destroyed && g == generation && turn == flipGeneration
              && autoFlip && !paused && resumeCapture) {
            resumeCapture = false;
            capture();
          }
        },
        autoFlipCaptureDelayMs());
  }

  long autoFlipCaptureDelayMs() {
    return adaptiveCapture ? AUTO_FLIP_CAPTURE_DELAY_MS : 800;
  }

  void togglePause() {
    if (!running) return;
    paused = !paused;
    updatePauseButton();
    debug(paused ? "pause" : "resume");
    if (!paused && autoFlip && resumeCapture) {
      resumeCapture = false;
      capture();
      return;
    }
    if (waitingForGap) {
      if (paused && gapTask != null) {
        gapRemainingMs = Math.max(0, gapDeadline - SystemClock.elapsedRealtime());
        main.removeCallbacks(gapTask);
        gapTask = null;
      } else if (!paused) scheduleSentenceGap(generation);
      return;
    }
    if (player != null) {
      try {
        if (paused && player.isPlaying()) {
          player.pause();
          expectedPlaybackEndAt = 0;
        } else if (!paused) startPlayback(player);
      } catch (IllegalStateException ignored) {
      }
    }
  }

  void releasePlayer() {
    if (player != null) {
      player.release();
      player = null;
    }
    expectedPlaybackEndAt = 0;
  }

  void clearPage() {
    clearSentenceGap();
    String id = pageId;
    pageId = "";
    historyMatchStage = "";
    if (!id.isEmpty())
      workers.submit(
          () -> {
            try {
              request("DELETE", "/pages/" + id, null);
            } catch (Exception ignored) {
            }
          });
    for (File f : audioFiles.values()) f.delete();
    audioFiles.clear();
    downloading.clear();
  }

  void cancel() {
    debug("cancel");    generation++;
    clearSentenceGap();
    flipGeneration++;
    for (HttpURLConnection c : activeConnections) c.disconnect();
    activeConnections.clear();
    automatic = false;
    resumeCapture = false;
    capturing = false;
    if (settle != null) {
      main.removeCallbacks(settle);
      settle = null;
    }
    if (pendingFrame != null) {
      pendingFrame.recycle();
      pendingFrame = null;
    }
    paused = false;
    releasePlayer();
    clearPage();
    sentences = null;
    overlay.setVisibility(View.VISIBLE);
    setRunning(false);
  }

  // adb dumpsys 提供控制状态和按钮边界；不输出截图、文本或配对信息。
  String buttonState(Button button) {
    int[] xy = new int[2];
    button.getLocationOnScreen(xy);
    return button.getContentDescription() + "@" + xy[0] + "," + xy[1] + ","
        + (xy[0] + button.getWidth()) + "," + (xy[1] + button.getHeight());
  }

  @Override protected void dump(FileDescriptor fd, PrintWriter out, String[] args) {
    java.util.concurrent.atomic.AtomicReference<String> snapshot =
        new java.util.concurrent.atomic.AtomicReference<>("reader_state=unavailable");
    CountDownLatch ready = new CountDownLatch(1);
    Runnable collect = () -> {
      int[] xy = new int[2];
      autoButton.getLocationOnScreen(xy);
      snapshot.set("autoFlip=" + autoFlip + " asrCheck=" + asrCheck + " paused=" + paused
          + " running=" + running + " pauseEnabled=" + pause.isEnabled()
          + " resumeCapture=" + resumeCapture + " autoBounds="
          + xy[0] + "," + xy[1] + "," + (xy[0] + autoButton.getWidth())
          + "," + (xy[1] + autoButton.getHeight()) + " controls="
          + buttonState(mainAction) + ";" + buttonState(pause) + ";" + buttonState(closeButton));
      ready.countDown();
    };
    if (Looper.myLooper() == main.getLooper()) collect.run();
    else main.post(collect);
    try { ready.await(2, TimeUnit.SECONDS); }
    catch (InterruptedException e) { Thread.currentThread().interrupt(); }
    out.println(snapshot.get());
  }

  public void onDestroy() {
    debug("overlay_closed");    if (preferences != null && preferenceListener != null)
      preferences.unregisterOnSharedPreferenceChangeListener(preferenceListener);
    cancel();
    destroyed = true;
    main.removeCallbacksAndMessages(null);
    if (display != null) display.release();
    if (reader != null) reader.close();
    if (projection != null) {
      MediaProjection old = projection;
      projection = null;
      old.stop();
    }
    if (overlay != null) wm.removeView(overlay);
    workers.shutdownNow();
    super.onDestroy();
  }
}
