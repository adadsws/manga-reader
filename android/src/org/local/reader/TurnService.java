package org.local.reader;

// 仅执行用户开启的翻页手势，使用 Android 官方 dispatchGesture API。
import android.accessibilityservice.*;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.graphics.Path;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.TextUtils;
import android.view.accessibility.AccessibilityEvent;

public class TurnService extends AccessibilityService {
  static final int TURN_DISABLED = 1;
  static final int TURN_NOT_CONNECTED = 2;
  static final int TURN_REJECTED = 3;
  static final int RETRY_COUNT = 10;
  static final long RETRY_DELAY_MS = 150;
  static volatile TurnService current;

  interface ActiveCheck {
    boolean isActive();
  }

  interface FailureCallback {
    void onFailure(int reason);
  }

  protected void onServiceConnected() {
    super.onServiceConnected();
    current = this;
  }

  public boolean onUnbind(Intent intent) {
    if (current == this) current = null;
    return super.onUnbind(intent);
  }

  public void onDestroy() {
    // 旧实例可能在新实例已经连上后才销毁，不能清掉新连接。
    if (current == this) current = null;
    super.onDestroy();
  }

  public void onAccessibilityEvent(AccessibilityEvent e) {}

  public void onInterrupt() {}

  static float startX(boolean pageLeft, int width) { return width * (pageLeft ? .2f : .8f); }

  static float endX(boolean pageLeft, int width) { return width * (pageLeft ? .8f : .2f); }

  static boolean enabledServicesContain(String setting, ComponentName target) {
    if (TextUtils.isEmpty(setting)) return false;
    TextUtils.SimpleStringSplitter splitter = new TextUtils.SimpleStringSplitter(':');
    splitter.setString(setting);
    while (splitter.hasNext()) {
      ComponentName enabled = ComponentName.unflattenFromString(splitter.next());
      if (target.equals(enabled)) return true;
    }
    return false;
  }

  static boolean isConfigured(Context context) {
    String setting =
        Settings.Secure.getString(
            context.getContentResolver(), Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
    return enabledServicesContain(setting, new ComponentName(context, TurnService.class));
  }

  static void turn(
      Context context,
      boolean left,
      int w,
      int h,
      ActiveCheck active,
      Runnable waiting,
      Runnable success,
      Runnable cancelled,
      FailureCallback failed) {
    attemptTurn(
        context.getApplicationContext(),
        left,
        w,
        h,
        active,
        waiting,
        success,
        cancelled,
        failed,
        0);
  }

  static void attemptTurn(
      Context context,
      boolean left,
      int w,
      int h,
      ActiveCheck active,
      Runnable waiting,
      Runnable success,
      Runnable cancelled,
      FailureCallback failed,
      int attempt) {
    if (!active.isActive()) return;
    TurnService service = current;
    if (service == null) {
      if (!isConfigured(context)) {
        failed.onFailure(TURN_DISABLED);
        return;
      }
      if (attempt >= RETRY_COUNT) {
        failed.onFailure(TURN_NOT_CONNECTED);
        return;
      }
      if (attempt == 0) waiting.run();
      retry(context, left, w, h, active, waiting, success, cancelled, failed, attempt);
      return;
    }
    android.content.SharedPreferences prefs = service.getSharedPreferences("reader", 0);
    float y = prefs.getInt("swipeY", ReaderDefaults.SWIPE_Y_PERCENT) / 100f;
    int duration = prefs.getInt("swipeMs", ReaderDefaults.SWIPE_DURATION_MS);
    Path path = new Path();
    // 设置描述页面翻向；手指手势与页面移动方向相反。
    path.moveTo(startX(left, w), h * y);
    path.lineTo(endX(left, w), h * y);
    boolean sent = false;
    try {
      sent =
          service.dispatchGesture(
              new GestureDescription.Builder()
                  .addStroke(new GestureDescription.StrokeDescription(path, 0, duration))
                  .build(),
              new GestureResultCallback() {
                public void onCompleted(GestureDescription d) {
                  success.run();
                }

                public void onCancelled(GestureDescription d) {
                  cancelled.run();
                }
              },
              null);
    } catch (RuntimeException ignored) {
      // 系统正解绑服务时 dispatchGesture 可能抛错；进入同一短重试窗口。
    }
    if (sent) return;
    if (attempt >= RETRY_COUNT) {
      failed.onFailure(TURN_REJECTED);
      return;
    }
    if (attempt == 0) waiting.run();
    retry(context, left, w, h, active, waiting, success, cancelled, failed, attempt);
  }

  static void retry(
      Context context,
      boolean left,
      int w,
      int h,
      ActiveCheck active,
      Runnable waiting,
      Runnable success,
      Runnable cancelled,
      FailureCallback failed,
      int attempt) {
    new Handler(Looper.getMainLooper())
        .postDelayed(
            () ->
                attemptTurn(
                    context,
                    left,
                    w,
                    h,
                    active,
                    waiting,
                    success,
                    cancelled,
                    failed,
                    attempt + 1),
            RETRY_DELAY_MS);
  }
}
