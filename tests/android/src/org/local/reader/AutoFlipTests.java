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
    int captures, clears, releases, loads, fetches, plays, lastFetched=-1;
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
      historyPromote = preferences.getBoolean("historyPromote", ReaderDefaults.HISTORY_PROMOTE);
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
    boolean hadHistoryPromote = saved.contains("historyPromote"), originalHistoryPromote = saved.getBoolean("historyPromote", ReaderDefaults.HISTORY_PROMOTE);
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
      saved.edit().putInt("speechSpeedPercent",ReaderDefaults.SPEECH_SPEED_PERCENT).putInt("overlayOpacityPercent",ReaderDefaults.OVERLAY_OPACITY_PERCENT).putBoolean("adaptiveCapture",ReaderDefaults.ADAPTIVE_CAPTURE).putBoolean("historyReuse",ReaderDefaults.HISTORY_REUSE).putBoolean("historyPromote",ReaderDefaults.HISTORY_PROMOTE).commit();
      MainActivity activity=(MainActivity)startActivitySync(new android.content.Intent(getTargetContext(),MainActivity.class).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK));
      ui(() -> {
        Button advanced=findButton(activity.getWindow().getDecorView(),"高级选项");
        CheckBox vision=(CheckBox)findButton(activity.getWindow().getDecorView(),"OCR 与分镜并行");
        check(advanced!=null && activity.advancedOptions.getVisibility()==android.view.View.GONE
            && vision!=null && !vision.isShown(),"advanced settings start collapsed");
        advanced.performClick();
        check(activity.advancedOptions.getVisibility()==android.view.View.VISIBLE
            && "收起高级选项".contentEquals(advanced.getText()),"advanced settings expand as one group");
      }); passed++;
      ui(() -> { SeekBar bar=findGap(activity.getWindow().getDecorView()); TextView label=findText(activity.getWindow().getDecorView(),"句间停顿："); check(bar!=null && bar.getMax()==30 && bar.getProgress()==1 && label.getCurrentTextColor()==MainActivity.NORMAL_TEXT,"settings slider reflects current default without highlight"); bar.requestFocus(); bar.dispatchKeyEvent(new android.view.KeyEvent(android.view.KeyEvent.ACTION_DOWN,android.view.KeyEvent.KEYCODE_DPAD_RIGHT)); bar.dispatchKeyEvent(new android.view.KeyEvent(android.view.KeyEvent.ACTION_UP,android.view.KeyEvent.KEYCODE_DPAD_RIGHT)); check(saved.getInt("sentenceGapMs",0)==bar.getProgress()*100 && bar.getProgress()>1 && label.getCurrentTextColor()==MainActivity.CHANGED_TEXT,"changed gap saves immediately and turns blue"); bar.setProgress(1); check(label.getCurrentTextColor()==MainActivity.NORMAL_TEXT,"restoring default clears blue highlight"); }); passed++;
      ui(() -> {
        SeekBar speed=findSeekBar(activity.getWindow().getDecorView(),"朗读速度：");
        SeekBar opacity=findSeekBar(activity.getWindow().getDecorView(),"浮窗透明度：");
        check(speed!=null && speed.getMax()==20 && speed.getProgress()==8,"speed slider exposes current 0.90x default");
        check(opacity!=null && opacity.getMax()==14 && opacity.getProgress()==13,"opacity slider exposes 30% to 100%");
        speed.requestFocus(); speed.dispatchKeyEvent(new android.view.KeyEvent(android.view.KeyEvent.ACTION_DOWN,android.view.KeyEvent.KEYCODE_DPAD_RIGHT)); speed.dispatchKeyEvent(new android.view.KeyEvent(android.view.KeyEvent.ACTION_UP,android.view.KeyEvent.KEYCODE_DPAD_RIGHT));
        check(saved.getInt("speechSpeedPercent",0)==95,"speed slider saves in five-percent steps");
        opacity.requestFocus(); opacity.dispatchKeyEvent(new android.view.KeyEvent(android.view.KeyEvent.ACTION_DOWN,android.view.KeyEvent.KEYCODE_DPAD_LEFT)); opacity.dispatchKeyEvent(new android.view.KeyEvent(android.view.KeyEvent.ACTION_UP,android.view.KeyEvent.KEYCODE_DPAD_LEFT));
        check(saved.getInt("overlayOpacityPercent",0)==90,"opacity slider saves and is adjustable");
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
      ui(() -> { Button models=findButton(activity.getWindow().getDecorView(),"选择朗读模型"); check(models!=null && "选择朗读模型".contentEquals(models.getContentDescription()),"settings exposes host model picker"); }); passed++;
      ui(() -> {
        CheckBox master=(CheckBox)findButton(activity.getWindow().getDecorView(),"全部开启/关闭");
        CheckBox vision=(CheckBox)findButton(activity.getWindow().getDecorView(),"OCR 与分镜并行");
        CheckBox eager=(CheckBox)findButton(activity.getWindow().getDecorView(),"首段语音提前生成");
        CheckBox prefetch=(CheckBox)findButton(activity.getWindow().getDecorView(),"按顺序预取本页全部语音");
        check(master!=null && vision!=null && eager!=null && prefetch!=null,"optimization switches are visible");
        master.setChecked(false);
        check(!vision.isChecked() && !eager.isChecked() && !prefetch.isChecked()
            && !saved.getBoolean("parallelVision",true) && !saved.getBoolean("eagerFirstAudio",true)
            && !saved.getBoolean("earlyPrefetch",true),"master switch disables and persists legacy pipeline");
        vision.setChecked(true);
        check(!master.isChecked() && saved.getBoolean("parallelVision",false),"individual optimization persists without forcing others");
        master.setChecked(true);
        check(vision.isChecked() && eager.isChecked() && prefetch.isChecked() && master.isChecked(),"master switch enables all Android optimizations");
      }); passed++;
      ui(() -> {
        CheckBox adaptive=(CheckBox)findButton(activity.getWindow().getDecorView(),"翻页后自适应抓帧");
        check(adaptive!=null && adaptive.isChecked() && adaptive.getCurrentTextColor()==MainActivity.NORMAL_TEXT,
            "adaptive capture is visible and enabled by default");
        adaptive.setChecked(false);
        check(!saved.getBoolean("adaptiveCapture",true) && adaptive.getCurrentTextColor()==MainActivity.CHANGED_TEXT,
            "adaptive capture can be disabled independently and persists");
        adaptive.setChecked(true);
      }); passed++;
      ui(() -> {
        CheckBox reuse=(CheckBox)findButton(activity.getWindow().getDecorView(),"匹配历史结果时跳过处理");
        CheckBox promote=(CheckBox)findButton(activity.getWindow().getDecorView(),"将本次新结果设为后续默认");
        check(reuse!=null && reuse.isChecked() && promote!=null && !promote.isChecked(),
            "history controls expose the speed-first defaults");
        reuse.setChecked(false); promote.setChecked(true);
        check(!saved.getBoolean("historyReuse",true) && saved.getBoolean("historyPromote",false),
            "history reuse and promotion choices persist independently");
      }); passed++;
      ui(() -> { check(activity.connectionStatus!=null && activity.connectionStatus.getText().toString().startsWith("●") && activity.connectionStatus.getContentDescription().toString().startsWith("电脑连接状态 ") && findButton(activity.getWindow().getDecorView(),"检测电脑连接")==null,"settings continuously shows connection state without a manual test button"); }); passed++;
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
    if (hadHistoryPromote) restoreOptimizations.putBoolean("historyPromote",originalHistoryPromote); else restoreOptimizations.remove("historyPromote");
    restoreOptimizations.commit();
    finish(outcome, result);
  }
}
