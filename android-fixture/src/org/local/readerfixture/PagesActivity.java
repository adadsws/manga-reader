package org.local.readerfixture;

// 独立测试 APK：展示用户提供的页面并响应右滑，不与朗读服务共享数据。
import android.app.*;
import android.graphics.*;
import android.os.*;
import android.view.*;
import android.widget.*;

public class PagesActivity extends Activity {
  String[] pages = {"0012.jpg", "0009.jpg", "0041.jpg"};
  int index = 0;
  boolean blankFirst;
  float start;
  ImageView image;

  public void onCreate(Bundle b) {
    super.onCreate(b);
    getWindow().setFlags(1024, 1024);
    getWindow().getDecorView().setSystemUiVisibility(5894);
    image = new ImageView(this);
    image.setBackgroundColor(Color.WHITE);
    image.setScaleType(ImageView.ScaleType.FIT_CENTER);
    blankFirst = getIntent().getBooleanExtra("blank_first", false);
    setContentView(image);
    show();
    image.setOnTouchListener(
        (v, e) -> {
          if (e.getAction() == 0) start = e.getX();
          if (e.getAction() == 1 && Math.abs(e.getX() - start) > 80) {
            index = Math.min(index + 1, blankFirst ? pages.length : pages.length - 1);
            show();
          }
          return true;
        });
  }

  void show() {
    if (blankFirst && index == 0) {
      image.setImageDrawable(null);
      android.util.Log.i("ReaderFixture", "PAGE 0 BLANK");
      return;
    }
    int pageIndex = blankFirst ? index - 1 : index;
    try (java.io.InputStream s = getAssets().open(pages[pageIndex])) {
      image.setImageBitmap(BitmapFactory.decodeStream(s));
      android.util.Log.i("ReaderFixture", "PAGE " + index + " " + pages[pageIndex]);
    } catch (Exception e) {
      throw new RuntimeException(e);
    }
  }
}
