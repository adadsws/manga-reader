package org.local.reader;

// 在真实 Android 主线程执行生产状态机；不截屏、不读取漫画、不合成音频。
import android.app.Instrumentation;
import android.content.ComponentName;
import android.os.Bundle;
import android.widget.*;
import org.json.*;

public class AutoFlipTests extends Instrumentation {
  MockReader service;
  int passed;
  class MockReader extends ReaderService {
    int captures, clears, releases, loads, fetches, plays, turns, lastFetched=-1;
    long loadedAt;
    String message;
    MockReader() {
      attachBaseContext(getTargetContext());
      preferences = getSharedPreferences("reader", 0);
      autoButton = new Button(getTargetContext());
      mainAction = new Button(getTargetContext());
      pause = new Button(getTargetContext());
      closeButton = new Button(getTargetContext());
      closeButton.setContentDescription("关闭浮窗");
      errorButton = new Button(getTargetContext());
      overlay = new LinearLayout(getTargetContext());
      autoFlip = preferences.getBoolean("auto", ReaderDefaults.AUTO_FLIP);
      parallelVision = preferences.getBoolean("parallelVision", ReaderDefaults.PARALLEL_VISION);
      eagerFirstAudio = preferences.getBoolean("eagerFirstAudio", ReaderDefaults.EAGER_FIRST_AUDIO);
      earlyPrefetch = preferences.getBoolean("earlyPrefetch", ReaderDefaults.EARLY_PREFETCH);
      adaptiveCapture = preferences.getBoolean("adaptiveCapture", ReaderDefaults.ADAPTIVE_CAPTURE);
      historyReuse = preferences.getBoolean("historyReuse", ReaderDefaults.HISTORY_REUSE);
      historyHidden = preferences.getBoolean("historyHidden", ReaderDefaults.HISTORY_HIDDEN);
      updatePauseButton();
      updateAutoButton();
    }
    @Override void capture() { captures++; }
    @Override void load(int position, int generation) {
      loads++; loadedAt=android.os.SystemClock.elapsedRealtime();
      java.io.File ready=audioFiles.get(position);
      if (ready!=null) play(ready,generation);
    }
    @Override void clearPage() { super.clearPage(); clears++; }
    @Override void releasePlayer() { releases++; }
    @Override void fetch(int position, int generation) { fetches++; lastFetched=position; }
    @Override void play(java.io.File file, int generation) { if (!waitingForGap) plays++; }
    @Override void setStatus(String text) { message = text; }
    @Override void turnPage(String completedStatus) { turns++; message=completedStatus; }
  }
  void ui(Runnable action) {
    final Throwable[] error = new Throwable[1];
    runOnMainSync(() -> { try { action.run(); } catch (Throwable t) { error[0] = t; } });
    if (error[0] != null) throw new AssertionError(error[0]);
  }
  void check(boolean ok, String message) { if (!ok) throw new AssertionError(message); }
  void fresh() {
    ui(() -> {
      if (service != null) { service.main.removeCallbacksAndMessages(null); service.workers.shutdownNow(); }
      service = new MockReader(); service.setRunning(true); service.setAutoFlip(true);
      service.adaptiveCapture = true;
    });
  }
  Button findButton(android.view.View view, String text) {
    if (view instanceof Button && text.contentEquals(((Button)view).getText())) return (Button)view;
    if (view instanceof android.view.ViewGroup) {
      android.view.ViewGroup group=(android.view.ViewGroup)view;
      for (int i=0;i<group.getChildCount();i++) { Button found=findButton(group.getChildAt(i),text); if(found!=null)return found; }
    }
    return null;
  }  SeekBar findGap(android.view.View view) {
    if (view instanceof SeekBar) return (SeekBar)view;
    if (view instanceof android.view.ViewGroup) {
      android.view.ViewGroup group=(android.view.ViewGroup)view;
      for (int i=0;i<group.getChildCount();i++) { SeekBar found=findGap(group.getChildAt(i)); if (found!=null) return found; }
    }
    return null;
  }
  Button findButtonDescription(android.view.View view, String description) {
    if (view instanceof Button && description.contentEquals(view.getContentDescription())) return (Button)view;
    if (view instanceof android.view.ViewGroup) {
      android.view.ViewGroup group=(android.view.ViewGroup)view;
      for (int i=0;i<group.getChildCount();i++) { Button found=findButtonDescription(group.getChildAt(i),description); if(found!=null)return found; }
    }
    return null;
  }
  SeekBar findSeekBar(android.view.View view, String prefix) {
    if (view instanceof SeekBar && view.getContentDescription()!=null
        && view.getContentDescription().toString().startsWith(prefix)) return (SeekBar)view;
    if (view instanceof android.view.ViewGroup) {
      android.view.ViewGroup group=(android.view.ViewGroup)view;
      for (int i=0;i<group.getChildCount();i++) { SeekBar found=findSeekBar(group.getChildAt(i),prefix); if (found!=null) return found; }
    }
    return null;
  }
  TextView findText(android.view.View view, String prefix) {
    if (view instanceof TextView && ((TextView)view).getText().toString().startsWith(prefix)) return (TextView)view;
    if (view instanceof android.view.ViewGroup) {
      android.view.ViewGroup group=(android.view.ViewGroup)view;
      for (int i=0;i<group.getChildCount();i++) { TextView found=findText(group.getChildAt(i),prefix); if (found!=null) return found; }
    }
    return null;
  }
  android.view.View findDescription(android.view.View view, String description) {
    if (view.getContentDescription()!=null && description.contentEquals(view.getContentDescription())) return view;
    if (view instanceof android.view.ViewGroup) {
      android.view.ViewGroup group=(android.view.ViewGroup)view;
      for (int i=0;i<group.getChildCount();i++) { android.view.View found=findDescription(group.getChildAt(i),description); if(found!=null)return found; }
    }
    return null;
  }
  EditText findEditText(android.view.View view) {
    if (view instanceof EditText) return (EditText)view;
    if (view instanceof android.view.ViewGroup) {
      android.view.ViewGroup group=(android.view.ViewGroup)view;
      for (int i=0;i<group.getChildCount();i++) { EditText found=findEditText(group.getChildAt(i)); if(found!=null)return found; }
    }
    return null;
  }
  int directSettingControls(android.view.View view) {
    int count=(view instanceof Switch || view instanceof SeekBar || view instanceof EditText) ? 1 : 0;
    if (view instanceof android.view.ViewGroup) {
      android.view.ViewGroup group=(android.view.ViewGroup)view;
      for (int i=0;i<group.getChildCount();i++) count+=directSettingControls(group.getChildAt(i));
    }
    return count;
  }
  void waitTurn() throws Exception { Thread.sleep(1000); }
  public void onCreate(Bundle args) { super.onCreate(args); start(); }
  public void onStart() {
    Bundle result = new Bundle();
    boolean original = getTargetContext().getSharedPreferences("reader", 0).getBoolean("auto", false);
    android.content.SharedPreferences saved = getTargetContext().getSharedPreferences("reader", 0);
    boolean hadGap = saved.contains("sentenceGapMs");
    int originalGap = saved.getInt("sentenceGapMs", ReaderDefaults.SENTENCE_GAP_MS);
    boolean hadSpeed = saved.contains("speechSpeedPercent");
    int originalSpeed = saved.getInt("speechSpeedPercent", ReaderDefaults.SPEECH_SPEED_PERCENT);
    boolean hadOpacity = saved.contains("overlayOpacityPercent");
    int originalOpacity = saved.getInt("overlayOpacityPercent", ReaderDefaults.OVERLAY_OPACITY_PERCENT);
    boolean hadVision = saved.contains("parallelVision"), originalVision = saved.getBoolean("parallelVision", true);
    boolean hadEager = saved.contains("eagerFirstAudio"), originalEager = saved.getBoolean("eagerFirstAudio", true);
    boolean hadPrefetch = saved.contains("earlyPrefetch"), originalPrefetch = saved.getBoolean("earlyPrefetch", true);
    boolean hadAdaptive = saved.contains("adaptiveCapture"), originalAdaptive = saved.getBoolean("adaptiveCapture", true);
    boolean hadHistoryReuse = saved.contains("historyReuse"), originalHistoryReuse = saved.getBoolean("historyReuse", ReaderDefaults.HISTORY_REUSE);
    boolean hadHistoryHidden = saved.contains("historyHidden"), originalHistoryHidden = saved.getBoolean("historyHidden", ReaderDefaults.HISTORY_HIDDEN);
    boolean hadHistoryPromote = saved.contains("historyPromote"), originalHistoryPromote = saved.getBoolean("historyPromote", false);
    int outcome = -1;
    try {
      check(TurnService.startX(true,100)<TurnService.endX(true,100) && TurnService.startX(false,100)>TurnService.endX(false,100),"page direction is opposite to finger gesture"); passed++;
      ComponentName turnComponent=new ComponentName("org.local.reader","org.local.reader.TurnService");
      check(TurnService.enabledServicesContain("example/.Other:org.local.reader/.TurnService",turnComponent)
          && TurnService.enabledServicesContain("org.local.reader/org.local.reader.TurnService",turnComponent)
          && !TurnService.enabledServicesContain("example/.Other",turnComponent),
          "accessibility setting parser recognizes short and full service names"); passed++;
      check(ReaderService.turnFailureMessage(TurnService.TURN_DISABLED).startsWith("请开启")
          && ReaderService.turnFailureMessage(TurnService.TURN_NOT_CONNECTED).contains("已设置但未连接")
          && ReaderService.turnFailureMessage(TurnService.TURN_REJECTED).contains("暂时无法执行"),
          "turn errors distinguish disabled, disconnected, and rejected states"); passed++;
      check("开始播放".equals(ReaderService.consoleMessage("play_start"))
          && "本页未识别到文字，尝试继续翻页".equals(ReaderService.consoleMessage("no_text_turn"))
          && "翻页后画面未变化，已停止自动翻页".equals(ReaderService.consoleMessage("page_unchanged"))
          && ReaderService.consoleMessage("unknown")==null
          && !ReaderService.consoleMessage("error").contains("generation"),
          "emulator console uses fixed user messages without internal fields"); passed++;
      check("1/3 · 正在接收语音".equals(ReaderService.statusLine("正在接收语音",0,3))
          && "3/3 · 句间停顿…".equals(ReaderService.statusLine("句间停顿…",9,3))
          && "正在识别".equals(ReaderService.statusLine("正在识别",0,0)),
          "status keeps a stable clamped page progress prefix after OCR"); passed++;
      check("匹配：图片".equals(ReaderService.historyStatusLine("image"))
          && "匹配：OCR结构".equals(ReaderService.historyStatusLine("ocr"))
          && "匹配：朗读文本".equals(ReaderService.historyStatusLine("text"))
          && "匹配：无".equals(ReaderService.historyStatusLine("none"))
          && "匹配：关闭".equals(ReaderService.historyStatusLine("disabled"))
          && "".equals(ReaderService.historyStatusLine("unknown")),
          "history status distinguishes the first exact matching checkpoint"); passed++;
      fresh(); ui(() -> {
        android.text.Spanned text=(android.text.Spanned)service.mainAction.getText();
        android.text.style.RelativeSizeSpan[] spans=text.getSpans(0,text.length(),android.text.style.RelativeSizeSpan.class);
        check(spans.length==1 && Math.abs(spans[0].getSizeChange()-.55f)<.001f
            && "■ 全部停止".equals(text.toString()) && service.pause.isEnabled(),
            "running controls use icon plus small label and enable pause");
      }); passed++;
      fresh(); ui(() -> { service.errorButton.setVisibility(android.view.View.GONE); service.automatic=true; service.stopAfterUnchangedPage(); check(!service.automatic && service.lastError.isEmpty() && service.errorButton.getVisibility()==android.view.View.GONE && "翻页后画面未变化，已停止自动翻页".equals(service.message), "unchanged page is a normal stop without error UI"); }); passed++;
      String complete="语音合成失败：CUDA内存不足，模型返回了完整诊断信息"; String parsed=ReaderService.httpErrorMessage(502,("{\"detail\":\""+complete+"\"}").getBytes(java.nio.charset.StandardCharsets.UTF_8)); fresh(); ui(() -> service.fail("语音失败："+parsed)); check(service.lastError.equals("语音失败：HTTP 502："+complete) && service.errorButton.getVisibility()==android.view.View.VISIBLE,"full speech error remains available in Android"); passed++;
      fresh(); ui(() -> { service.setAutoFlip(false); service.finished(); check("本页朗读完成".equals(service.message), "off finishes current page"); }); passed++;
      fresh(); ui(() -> {
        service.errorButton.setVisibility(android.view.View.GONE);
        service.handleNoText();
        check(service.turns==1 && "本页未识别到文字".equals(service.message) && service.lastError.isEmpty()
            && service.errorButton.getVisibility()!=android.view.View.VISIBLE,
            "empty page with auto flip reuses the normal page completion state machine");
      }); passed++;
      fresh(); ui(() -> {
        service.setAutoFlip(false);
        service.handleNoText();
        check(service.turns==0 && !service.running && "本页未识别到文字".equals(service.message)
            && service.lastError.isEmpty(),
            "empty page without auto flip stops normally and keeps the current page");
      }); passed++;
      fresh(); ui(() -> service.afterTurn(service.generation, service.flipGeneration)); waitTurn();
      ui(() -> check(service.captures == 1, "on captures next page once")); passed++;
      check(ReaderService.AUTO_FLIP_CAPTURE_DELAY_MS < 800
          && ReaderService.AUTO_FLIP_CHANGE_TIMEOUT_MS > ReaderService.AUTO_FLIP_CAPTURE_DELAY_MS,
          "auto flip begins observing early but keeps a bounded slow-animation window"); passed++;
      fresh(); ui(() -> {
        check(service.autoFlipCaptureDelayMs()==ReaderService.AUTO_FLIP_CAPTURE_DELAY_MS,"adaptive capture uses early observation delay");
        service.adaptiveCapture=false;
        check(service.autoFlipCaptureDelayMs()==800,"disabled adaptive capture restores fixed legacy delay");
      }); passed++;
      fresh(); ui(() -> {
        service.automatic=true;
        service.lastPageHash=new int[]{10,20};
        service.stableHash=new int[]{10,20};
        service.captureStarted=android.os.SystemClock.elapsedRealtime()-ReaderService.AUTO_FLIP_CHANGE_TIMEOUT_MS;
        check(service.unchangedPageTimedOut(),"unchanged deadline does not depend on another frame callback");
        service.adaptiveCapture=false;
        check(!service.unchangedPageTimedOut(),"legacy capture does not use adaptive unchanged deadline");
      }); passed++;
      fresh(); ui(() -> { service.afterTurn(service.generation, service.flipGeneration); int generation=service.generation; int releases=service.releases; service.toggleAutoFlip(); check(service.generation==generation && service.releases==releases, "off preserves current audio"); }); waitTurn();
      ui(() -> check(service.captures == 0 && !service.resumeCapture, "off cancels queued capture")); passed++;
      fresh(); ui(() -> { int turn=service.flipGeneration; service.setAutoFlip(false); service.afterTurn(service.generation, turn); check(service.clears==0, "off rejects in-flight gesture callback"); }); passed++;
      fresh(); ui(() -> { int turn=service.flipGeneration; service.setAutoFlip(false); service.setAutoFlip(true); service.afterTurn(service.generation, turn); check(service.clears==0, "off-on cannot revive old callback"); }); passed++;
      fresh(); ui(() -> { service.paused=true; service.afterTurn(service.generation, service.flipGeneration); }); waitTurn();
      ui(() -> { check(service.captures==0 && service.resumeCapture, "pause holds capture"); service.togglePause(); check(service.captures==1, "resume enabled captures"); }); passed++;
      fresh(); ui(() -> { service.paused=true; service.afterTurn(service.generation, service.flipGeneration); service.setAutoFlip(false); service.togglePause(); check(service.captures==0, "resume disabled does not capture"); }); passed++;
      fresh(); ui(() -> { service.afterTurn(service.generation, service.flipGeneration); service.cancel(); }); waitTurn();
      ui(() -> check(service.captures==0, "stop cancels queued capture")); passed++;
      JSONArray fixtureSentences=new JSONArray().put(new JSONObject().put("text","fixture"));
      fresh(); ui(() -> {
        service.sentences=fixtureSentences;
        service.audioFiles.put(0,new java.io.File("unused"));
        service.togglePause();
        check(service.paused && service.sentences!=null && service.audioFiles.containsKey(0)
            && (ReaderService.ICON_PLAY+" 继续").equals(service.pause.getText().toString())
            && "继续".contentEquals(service.pause.getContentDescription()),
            "pause only stops playback while preserving page and cached processing");
        service.cancel();
        check(!service.paused && service.sentences==null && service.audioFiles.isEmpty()
            && (ReaderService.ICON_PAUSE+" 暂停").equals(service.pause.getText().toString())
            && "暂停".contentEquals(service.pause.getContentDescription()) && !service.pause.isEnabled()
            && (ReaderService.ICON_PLAY+" 重新开始").equals(service.mainAction.getText().toString())
            && "重新开始".contentEquals(service.mainAction.getContentDescription()),
            "stop clears all work and changes the combined action to restart");
      }); passed++;
      fresh(); ui(() -> { service.toggleAutoFlip(); check(!service.preferences.getBoolean("auto", true), "off persisted"); check("自动翻页：关".contentEquals(service.autoButton.getText()), "label off"); service.toggleAutoFlip(); check(service.preferences.getBoolean("auto", false), "on persisted"); MockReader restarted=new MockReader(); check(restarted.autoFlip && "自动翻页：开".contentEquals(restarted.autoButton.getText()), "restart remembers on"); restarted.workers.shutdownNow(); }); passed++;
      fresh(); ui(() -> { java.io.StringWriter text=new java.io.StringWriter(); service.dump(null,new java.io.PrintWriter(text),new String[0]); check(text.toString().contains("autoFlip=true") && !text.toString().contains("unavailable"), "dump on main thread does not block"); }); passed++;
      check(ReaderService.remainingGapAfterPlayback(1400,1500,500)==400
          && ReaderService.remainingGapAfterPlayback(1400,2000,500)==0
          && ReaderService.remainingGapAfterPlayback(0,2000,500)==500,
          "minimum gap accounts for previous playback end and completion callback delay"); passed++;
      fresh(); ui(() -> { service.preferences.edit().putInt("sentenceGapMs",400).commit(); long started=android.os.SystemClock.elapsedRealtime(); service.gapDeadline=started; service.beginSentenceGap(service.generation); check(service.loads==1 && service.waitingForGap,"gap starts next audio processing immediately"); }); waitTurn();
      ui(() -> { check(service.loads==2 && !service.waitingForGap && service.loadedAt>=service.gapDeadline,"gap retries ready check once after minimum interval"); }); passed++;
      fresh(); ui(() -> { service.preferences.edit().putInt("sentenceGapMs",400).commit(); service.beginSentenceGap(service.generation); service.acceptDownloadedAudio(0,new java.io.File("unused"),service.generation); check(service.plays==0 && service.waitingForGap,"fast processing cannot bypass minimum gap"); }); waitTurn();
      ui(() -> check(service.plays==1 && !service.waitingForGap,"ready audio plays when minimum gap ends")); passed++;
      fresh(); ui(() -> { service.preferences.edit().putInt("sentenceGapMs",200).commit(); service.beginSentenceGap(service.generation); }); waitTurn();
      ui(() -> { int loads=service.loads; service.acceptDownloadedAudio(0,new java.io.File("unused"),service.generation); check(service.plays==1 && service.loads==loads && !service.waitingForGap,"processing longer than gap plays immediately without a second pause"); }); passed++;
      fresh(); ui(() -> { service.preferences.edit().putInt("sentenceGapMs",400).commit(); service.beginSentenceGap(service.generation); service.togglePause(); }); waitTurn();
      ui(() -> { check(service.loads==1 && service.waitingForGap,"pause freezes gap while processing continues"); service.togglePause(); check(service.loads==1,"resume retains remaining gap"); }); waitTurn();
      ui(() -> check(service.loads==2,"resume eventually rechecks audio once")); passed++;
      fresh(); ui(() -> { service.beginSentenceGap(service.generation); service.cancel(); }); waitTurn();
      ui(() -> check(service.loads==1 && !service.waitingForGap,"cancel removes gap without a later retry")); passed++;
      fresh(); ui(() -> { service.beginSentenceGap(service.generation); service.generation++; }); waitTurn();
      ui(() -> check(service.loads==1,"old gap callback cannot load new generation")); passed++;
      fresh(); ui(() -> { service.preferences.edit().putInt("sentenceGapMs",0).commit(); service.beginSentenceGap(service.generation); }); waitTurn();
      ui(() -> check(service.loads==2 && !service.waitingForGap,"zero gap starts processing and releases immediately")); passed++;
      fresh(); ui(() -> { service.preferences.edit().remove("sentenceGapMs").commit(); check(service.sentenceGapMs()==ReaderDefaults.SENTENCE_GAP_MS,"current gap is the shared default"); service.preferences.edit().putInt("sentenceGapMs",1200).commit(); check(service.sentenceGapMs()==1200,"live preference read"); MockReader restarted=new MockReader(); check(restarted.sentenceGapMs()==1200,"gap persisted"); restarted.workers.shutdownNow(); service.preferences.edit().putInt("sentenceGapMs",99999).commit(); check(service.sentenceGapMs()==3000,"upper clamp"); service.preferences.edit().putInt("sentenceGapMs",-1).commit(); check(service.sentenceGapMs()==0,"lower clamp"); }); passed++;
      saved.edit().putInt("sentenceGapMs",ReaderDefaults.SENTENCE_GAP_MS).commit();
      saved.edit().putInt("speechSpeedPercent",ReaderDefaults.SPEECH_SPEED_PERCENT).putInt("overlayOpacityPercent",ReaderDefaults.OVERLAY_OPACITY_PERCENT).putBoolean("adaptiveCapture",ReaderDefaults.ADAPTIVE_CAPTURE).putBoolean("historyReuse",ReaderDefaults.HISTORY_REUSE).putBoolean("historyHidden",ReaderDefaults.HISTORY_HIDDEN).putBoolean("historyPromote",true).commit();
      MainActivity activity=(MainActivity)startActivitySync(new android.content.Intent(getTargetContext(),MainActivity.class).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK));
      ui(() -> {
        TextView title=findText(activity.getWindow().getDecorView(),"电脑连接与模型");
        TextView addressTitle=findText(activity.getWindow().getDecorView(),"电脑服务地址");
        android.view.View addressRow=findDescription(activity.getWindow().getDecorView(),"设置项 电脑服务地址");
        check(title.getCurrentTextColor()==MainActivity.SECTION_HEADING_TEXT
            && title.getBackground()==null && addressRow!=null
            && addressRow.getHeight()==MainActivity.SETTING_ROW_HEIGHT
            && addressTitle!=null && activity.addressStatus.getHeight()==56
            && activity.address.getParent()==null
            && directSettingControls(activity.getWindow().getDecorView())==0,
            "settings uses equal two-line rows and keeps direct controls out of the list");
      }); passed++;
      ui(() -> findDescription(activity.getWindow().getDecorView(),"设置项 配对口令").performClick());
      ui(() -> {
        EditText input=findEditText(activity.activeDialog.getWindow().getDecorView());
        int variation=input.getInputType() & android.text.InputType.TYPE_MASK_VARIATION;
        check(activity.token.getText().toString().contentEquals(activity.tokenStatus.getText())
            && activity.token.getText().toString().contentEquals(input.getText())
            && variation==android.text.InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD,
            "pairing token is fully visible in both the row and editor");
        activity.activeDialog.getButton(android.app.AlertDialog.BUTTON_NEGATIVE).performClick();
      }); passed++;
      MainActivity.ConnectionVersionState same=MainActivity.connectionVersionState("{\"version\":4}");
      MainActivity.ConnectionVersionState different=MainActivity.connectionVersionState("{\"version\":3}");
      MainActivity.ConnectionVersionState missing=MainActivity.connectionVersionState("{\"status\":\"ok\"}");
      check(same.color==MainActivity.CONNECTED_TEXT && same.text.equals("电脑已连接 · 版本 4")
          && different.color==MainActivity.WARNING_TEXT && different.text.contains("安卓 4，电脑 3")
          && missing.color==MainActivity.WARNING_TEXT && missing.text.contains("未报告有效版本"),
          "pair version states distinguish matching, different, and missing versions without disconnecting"); passed++;
      if (android.os.Build.VERSION.SDK_INT>=34) {
        android.content.Intent capture=activity.screenCaptureIntent();
        android.os.Bundle extras=capture.getExtras();
        boolean entireScreen=false;
        if (extras!=null) for (String key:extras.keySet()) {
          Object value=extras.get(key);
          if (value instanceof android.media.projection.MediaProjectionConfig
              && value.equals(android.media.projection.MediaProjectionConfig.createConfigForDefaultDisplay())) entireScreen=true;
        }
        check(entireScreen,"Android 14+ projection is fixed to the entire screen"); passed++;
      }
      ui(() -> {
        android.view.View advanced=findDescription(activity.getWindow().getDecorView(),"进入高级选项");
        check(advanced!=null && activity.advancedOptions.getVisibility()==android.view.View.GONE
            && findText(activity.getWindow().getDecorView(),"OCR 与分镜并行")==null,
            "advanced rows stay out of the overview hierarchy");
        advanced.performClick();
        check(activity.advancedOptions.getVisibility()==android.view.View.VISIBLE
            && activity.advancedScroll.isShown()
            && findButtonDescription(activity.getWindow().getDecorView(),"返回常用设置")!=null
            && findText(activity.getWindow().getDecorView(),"OCR 与分镜并行")!=null
            && directSettingControls(activity.getWindow().getDecorView())==0,
            "advanced settings open as a two-line child screen without direct controls");
        findButtonDescription(activity.getWindow().getDecorView(),"返回常用设置").performClick();
        check(activity.advancedOptions.getVisibility()==android.view.View.GONE
            && activity.mainScroll.isShown(),"advanced settings return to the overview screen");
      }); passed++;
      ui(() -> {
        check(findGap(activity.getWindow().getDecorView())==null,"gap slider is absent until its row is opened");
        findDescription(activity.getWindow().getDecorView(),"设置项 句间停顿").performClick();
      });
      ui(() -> {
        SeekBar bar=findGap(activity.activeDialog.getWindow().getDecorView());
        check(bar!=null && bar.getMax()==30 && bar.getProgress()==1,"gap row opens its slider at the current value");
        bar.setProgress(2);
        activity.activeDialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).performClick();
        check(saved.getInt("sentenceGapMs",0)==200 && "0.2 秒".contentEquals(activity.gapStatus.getText())
            && activity.gapStatus.getCurrentTextColor()==MainActivity.CHANGED_TEXT,
            "gap dialog saves and refreshes the status line: value="+saved.getInt("sentenceGapMs",0)
                +" status="+activity.gapStatus.getText()+" color="+activity.gapStatus.getCurrentTextColor());
      });
      ui(() -> findDescription(activity.getWindow().getDecorView(),"设置项 句间停顿").performClick());
      ui(() -> {
        findGap(activity.activeDialog.getWindow().getDecorView()).setProgress(1);
        activity.activeDialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).performClick();
      }); passed++;
      ui(() -> findDescription(activity.getWindow().getDecorView(),"设置项 朗读速度").performClick());
      ui(() -> {
        SeekBar speed=findSeekBar(activity.activeDialog.getWindow().getDecorView(),"朗读速度 ");
        check(speed!=null && speed.getMax()==20 && speed.getProgress()==8,"speed row opens the current 0.90x slider");
        speed.setProgress(9);
        activity.activeDialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).performClick();
        check(saved.getInt("speechSpeedPercent",0)==95 && "0.95×".contentEquals(activity.speedStatus.getText()),"speed dialog saves in five-percent steps");
        findDescription(activity.getWindow().getDecorView(),"进入高级选项").performClick();
        findDescription(activity.getWindow().getDecorView(),"设置项 浮窗透明度").performClick();
      });
      ui(() -> {
        SeekBar opacity=findSeekBar(activity.activeDialog.getWindow().getDecorView(),"浮窗透明度 ");
        check(opacity!=null && opacity.getMax()==14 && opacity.getProgress()==13,"opacity row opens the 30% to 100% slider");
        opacity.setProgress(12);
        activity.activeDialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).performClick();
        check(saved.getInt("overlayOpacityPercent",0)==90 && "90%".contentEquals(activity.opacityStatus.getText()),"opacity dialog saves and refreshes its status");
        findButtonDescription(activity.getWindow().getDecorView(),"返回常用设置").performClick();
      }); passed++;
      fresh(); ui(() -> {
        service.preferences.edit().putInt("speechSpeedPercent",999).putInt("overlayOpacityPercent",20).commit();
        service.applyOverlayOpacity();
        check(service.speechSpeedPercent()==150,"speed preference clamps at upper boundary");
        check(service.overlayOpacityPercent()==30 && Math.abs(service.overlay.getAlpha()-0.30f)<0.001f,"overlay applies clamped opacity");
      }); passed++;
      fresh(); ui(() -> { service.index=2; service.earlyPrefetch=true; service.acceptDownloadedAudio(2,new java.io.File("unused"),service.generation); check(service.plays==1 && service.fetches==1 && service.lastFetched==3,"early prefetch starts when current audio is cached"); }); passed++;
      fresh(); ui(() -> { service.index=2; service.earlyPrefetch=false; service.acceptDownloadedAudio(2,new java.io.File("unused"),service.generation); check(service.plays==1 && service.fetches==0,"legacy prefetch waits for player prepared"); }); passed++;
      fresh(); ui(() -> {
        service.index=1;
        service.audioFiles.put(2,new java.io.File(getTargetContext().getCacheDir(),"stale-speed.wav"));
        service.downloading.add(2);
        int before=service.speechSettingsGeneration;
        service.refreshSpeechSpeed();
        check(service.speechSettingsGeneration==before+1 && !service.audioFiles.containsKey(2)
            && !service.downloading.contains(2),"speed change discards prefetched future audio");
      }); passed++;
      final String[] originalAddress={null};
      ui(() -> {
        originalAddress[0]=activity.address.getText().toString();
        findDescription(activity.getWindow().getDecorView(),"设置项 电脑服务地址").performClick();
      });
      ui(() -> {
        EditText input=findEditText(activity.activeDialog.getWindow().getDecorView());
        input.setText("http://10.0.2.2:9999/");
        activity.activeDialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).performClick();
        check("http://10.0.2.2:9999".equals(saved.getString("address",""))
            && "http://10.0.2.2:9999".contentEquals(activity.addressStatus.getText()),
            "text setting opens an editor, normalizes and refreshes the row");
        activity.address.setText(originalAddress[0]);
        check(findDescription(activity.getWindow().getDecorView(),"设置项 朗读模型")!=null
            && findButton(activity.getWindow().getDecorView(),"选择朗读模型")==null,
            "model selection is one two-line row without a second button");
      }); passed++;
      ui(() -> findDescription(activity.getWindow().getDecorView(),"设置项 读完一页后自动翻页").performClick());
      ui(() -> {
        android.widget.ListView choices=activity.activeDialog.getListView();
        choices.performItemClick(choices.getChildAt(1),1,choices.getItemIdAtPosition(1));
        activity.activeDialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).performClick();
        check(!activity.auto.isChecked() && !saved.getBoolean("auto",true)
            && "已关闭".contentEquals(activity.autoStatus.getText()),
            "boolean setting changes only through its opened choice dialog");
        activity.auto.setChecked(ReaderDefaults.AUTO_FLIP);
      }); passed++;
      ui(() -> {
        findDescription(activity.getWindow().getDecorView(),"进入高级选项").performClick();
        check(findDescription(activity.getWindow().getDecorView(),"设置项 低等待优化总开关")!=null
            && directSettingControls(activity.getWindow().getDecorView())==0,"optimization settings are two-line rows without switches");
        activity.lowLatency.setChecked(false);
        check(!activity.parallelVision.isChecked() && !activity.eagerFirstAudio.isChecked() && !activity.earlyPrefetch.isChecked()
            && !saved.getBoolean("parallelVision",true) && !saved.getBoolean("eagerFirstAudio",true)
            && !saved.getBoolean("earlyPrefetch",true),"master switch disables and persists legacy pipeline");
        activity.parallelVision.setChecked(true);
        check(!activity.lowLatency.isChecked() && saved.getBoolean("parallelVision",false),"individual optimization persists without forcing others");
        activity.lowLatency.setChecked(true);
        check(activity.parallelVision.isChecked() && activity.eagerFirstAudio.isChecked() && activity.earlyPrefetch.isChecked()
            && activity.lowLatency.isChecked(),"master switch enables all Android optimizations");
      }); passed++;
      ui(() -> {
        check(activity.adaptiveCapture.isChecked() && "已开启 · 画面稳定后截图".contentEquals(activity.adaptiveCaptureStatus.getText()),
            "adaptive capture row exposes the enabled default");
        activity.adaptiveCapture.setChecked(false);
        check(!saved.getBoolean("adaptiveCapture",true)
            && activity.adaptiveCaptureStatus.getCurrentTextColor()==MainActivity.CHANGED_TEXT,
            "adaptive capture persists and refreshes its status line");
        activity.adaptiveCapture.setChecked(true);
      }); passed++;
      final int[] originalSwipe={0};
      ui(() -> {
        originalSwipe[0]=Integer.parseInt(activity.swipeY.getText().toString());
        findDescription(activity.getWindow().getDecorView(),"设置项 翻页滑动高度").performClick();
      });
      ui(() -> {
        EditText input=findEditText(activity.activeDialog.getWindow().getDecorView());
        input.setText("60");
        activity.activeDialog.getButton(android.app.AlertDialog.BUTTON_POSITIVE).performClick();
        check(saved.getInt("swipeY",0)==60 && "60%".contentEquals(activity.swipeYStatus.getText()),
            "bounded number dialog saves and refreshes the row");
        activity.swipeY.setText(String.valueOf(originalSwipe[0]));
      }); passed++;
      ui(() -> {
        check(activity.historyReuse.isChecked() && !activity.historyHidden.isChecked()
            && !saved.contains("historyPromote"),
            "history controls expose the speed-first defaults");
        activity.historyReuse.setChecked(false); activity.historyHidden.setChecked(true);
        check(!saved.getBoolean("historyReuse",true) && saved.getBoolean("historyHidden",false),
            "history reuse and hidden choices persist independently");
        findButtonDescription(activity.getWindow().getDecorView(),"返回常用设置").performClick();
      }); passed++;
      ui(() -> { check(activity.connectionStatus!=null && !activity.connectionStatus.getText().toString().startsWith("●") && activity.connectionStatus.getContentDescription().toString().startsWith("电脑连接状态 ") && findButton(activity.getWindow().getDecorView(),"检测电脑连接")==null,"connection is a two-line read-only state without a manual test button"); }); passed++;
      ModelPicker picker=new ModelPicker(activity,"http://127.0.0.1", "token", model -> {}); check("可用".equals(picker.stateName("available")) && "未验证".equals(picker.stateName("unverified")) && "文件不完整".equals(picker.stateName("incomplete")) && "加载失败".equals(picker.stateName("failed")),"model states are explicit"); JSONObject leaf=new JSONObject().put("kind","model"); JSONObject single=new JSONObject().put("kind","group").put("children",new JSONArray().put(leaf)); JSONObject nested=new JSONObject().put("kind","group").put("children",new JSONArray().put(new JSONObject().put("kind","model"))); JSONArray branches=new JSONArray().put(single).put(nested); check(picker.singleModelChild(single)==leaf,"single-model folder is flattened"); check(picker.countModels(branches)==2,"folder count includes descendant models"); ui(activity::finish); passed++;
      result.putString("stream", "PASS: " + passed + " Android playback state tests\n");
      result.putInt("passed", passed);

    } catch (Throwable e) {
      result.putString("stream", "FAIL after " + passed + ": " + e.toString()); outcome = 0;
    } finally {
      ui(() -> { if (service != null) { service.main.removeCallbacksAndMessages(null); service.workers.shutdownNow(); } getTargetContext().getSharedPreferences("reader", 0).edit().putBoolean("auto", original).commit(); });
    }
    if (hadGap) saved.edit().putInt("sentenceGapMs", originalGap).commit();
    else saved.edit().remove("sentenceGapMs").commit();
    if (hadSpeed) saved.edit().putInt("speechSpeedPercent", originalSpeed).commit();
    else saved.edit().remove("speechSpeedPercent").commit();
    if (hadOpacity) saved.edit().putInt("overlayOpacityPercent", originalOpacity).commit();
    else saved.edit().remove("overlayOpacityPercent").commit();
    android.content.SharedPreferences.Editor restoreOptimizations=saved.edit();
    if (hadVision) restoreOptimizations.putBoolean("parallelVision",originalVision); else restoreOptimizations.remove("parallelVision");
    if (hadEager) restoreOptimizations.putBoolean("eagerFirstAudio",originalEager); else restoreOptimizations.remove("eagerFirstAudio");
    if (hadPrefetch) restoreOptimizations.putBoolean("earlyPrefetch",originalPrefetch); else restoreOptimizations.remove("earlyPrefetch");
    if (hadAdaptive) restoreOptimizations.putBoolean("adaptiveCapture",originalAdaptive); else restoreOptimizations.remove("adaptiveCapture");
    if (hadHistoryReuse) restoreOptimizations.putBoolean("historyReuse",originalHistoryReuse); else restoreOptimizations.remove("historyReuse");
    if (hadHistoryHidden) restoreOptimizations.putBoolean("historyHidden",originalHistoryHidden); else restoreOptimizations.remove("historyHidden");
    if (hadHistoryPromote) restoreOptimizations.putBoolean("historyPromote",originalHistoryPromote); else restoreOptimizations.remove("historyPromote");
    restoreOptimizations.commit();
    finish(outcome, result);
  }
}
