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
  float start;
  ImageView image;

  public void onCreate(Bundle b) {
    super.onCreate(b);
    getWindow().setFlags(1024, 1024);
    getWindow().getDecorView().setSystemUiVisibility(5894);
    image = new ImageView(this);
    image.setBackgroundColor(Color.WHITE);
    image.setScaleType(ImageView.ScaleType.FIT_CENTER);
    setContentView(image);
    show();
    image.setOnTouchListener(
        (v, e) -> {
          if (e.getAction() == 0) start = e.getX();
          if (e.getAction() == 1 && Math.abs(e.getX() - start) > 80) {
            index = Math.min(index + 1, pages.length - 1);
            show();
          }
          return true;
        });
  }

  void show() {
    try (java.io.InputStream s = getAssets().open(pages[index])) {
      image.setImageBitmap(BitmapFactory.decodeStream(s));
      android.util.Log.i("ReaderFixture", "PAGE " + index + " " + pages[index]);
    } catch (Exception e) {
      throw new RuntimeException(e);
    }
  }
}
