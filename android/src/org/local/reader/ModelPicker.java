package org.local.reader;

// 原生模型树：从电脑动态加载，按目录懒展开并仅在可见角色行下载头像。
import android.app.*;
import android.graphics.*;
import android.graphics.drawable.ColorDrawable;
import android.os.*;
import android.view.*;
import android.widget.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import org.json.*;

public class ModelPicker {
  public interface Listener { void selected(JSONObject model); }
  final Activity activity;
  final String base, token;
  final Listener listener;
  AlertDialog dialog;
  LinearLayout content;
  TextView summary;

  public ModelPicker(Activity activity, String base, String token, Listener listener) {
    this.activity=activity;this.base=base.replaceAll("/$","");this.token=token;this.listener=listener;
  }

  int dp(int value) { return (int)(value*activity.getResources().getDisplayMetrics().density+.5f); }

  public void show() {
    LinearLayout root=new LinearLayout(activity);root.setOrientation(LinearLayout.VERTICAL);root.setPadding(dp(14),dp(8),dp(14),dp(8));
    summary=new TextView(activity);summary.setText("正在读取电脑模型目录…");summary.setTextSize(15);summary.setPadding(0,0,0,dp(8));root.addView(summary);
    Button refresh=new Button(activity);refresh.setText("刷新模型目录");refresh.setContentDescription("刷新模型目录");refresh.setOnClickListener(v->load());root.addView(refresh);
    content=new LinearLayout(activity);content.setOrientation(LinearLayout.VERTICAL);ScrollView scroll=new ScrollView(activity);scroll.addView(content);root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
    dialog=new AlertDialog.Builder(activity).setTitle("选择朗读模型").setView(root).setNegativeButton("关闭",null).create();dialog.show();
    Window window=dialog.getWindow();if(window!=null)window.setLayout(-1,(int)(activity.getResources().getDisplayMetrics().heightPixels*.85f));
    load();
  }

  void load() {
    summary.setText("正在读取电脑模型目录…");content.removeAllViews();
    new Thread(()->{
      try {
        JSONObject result=request("GET","/models",null);
        activity.runOnUiThread(()->render(result));
      } catch(Exception e) { activity.runOnUiThread(()->showError("目录读取失败："+e.getMessage())); }
    }).start();
  }

  void render(JSONObject result) {
    content.removeAllViews();JSONObject counts=result.optJSONObject("counts");
    summary.setText(counts==null?"模型目录已更新":String.format(java.util.Locale.CHINA,"共%d项 · 可用%d · 未验证%d · 不完整%d · 加载失败%d",counts.optInt("total"),counts.optInt("available"),counts.optInt("unverified"),counts.optInt("incomplete"),counts.optInt("failed")));
    JSONArray tree=result.optJSONArray("tree");if(tree==null||tree.length()==0){showError(result.optString("error","没有发现模型"));return;}
    addNodes(content,tree,0);
  }

  void addNodes(LinearLayout parent, JSONArray nodes, int depth) {
    for(int i=0;i<nodes.length();i++) {
      JSONObject node=nodes.optJSONObject(i);if(node==null)continue;
      if("group".equals(node.optString("kind"))) {
        JSONObject only=singleModelChild(node);
        if(only!=null)addModel(parent,only,depth);else addGroup(parent,node,depth);
      } else addModel(parent,node,depth);
    }
  }

  JSONObject singleModelChild(JSONObject group) {
    JSONArray children=group.optJSONArray("children");
    if(children==null||children.length()!=1)return null;
    JSONObject child=children.optJSONObject(0);
    return child!=null&&"model".equals(child.optString("kind"))?child:null;
  }

  int countModels(JSONArray nodes) {
    if(nodes==null)return 0;
    int count=0;
    for(int i=0;i<nodes.length();i++) {
      JSONObject node=nodes.optJSONObject(i);if(node==null)continue;
      if("model".equals(node.optString("kind")))count++;else count+=countModels(node.optJSONArray("children"));
    }
    return count;
  }

  void addGroup(LinearLayout parent, JSONObject node, int depth) {
    LinearLayout box=new LinearLayout(activity);box.setOrientation(LinearLayout.VERTICAL);parent.addView(box);
    Button header=new Button(activity);header.setAllCaps(false);header.setGravity(Gravity.START|Gravity.CENTER_VERTICAL);header.setPadding(dp(10+depth*12),0,dp(8),0);String name=node.optString("name","未命名分组");String label=name+"（"+countModels(node.optJSONArray("children"))+"个模型）";header.setText("▶ "+label);header.setContentDescription("模型分组 "+label);box.addView(header,new LinearLayout.LayoutParams(-1,dp(48)));
    LinearLayout children=new LinearLayout(activity);children.setOrientation(LinearLayout.VERTICAL);children.setVisibility(View.GONE);box.addView(children);final boolean[] built={false};
    header.setOnClickListener(v->{boolean open=children.getVisibility()!=View.VISIBLE;if(open&&!built[0]){addNodes(children,node.optJSONArray("children"),depth+1);built[0]=true;}children.setVisibility(open?View.VISIBLE:View.GONE);header.setText((open?"▼ ":"▶ ")+label);});
  }

  void addModel(LinearLayout parent, JSONObject model, int depth) {
    LinearLayout row=new LinearLayout(activity);row.setGravity(Gravity.CENTER_VERTICAL);row.setPadding(dp(10+depth*12),dp(6),dp(4),dp(6));
    ImageView avatar=new ImageView(activity);avatar.setContentDescription("头像 "+model.optString("name"));avatar.setScaleType(ImageView.ScaleType.CENTER_CROP);avatar.setImageResource(android.R.drawable.sym_def_app_icon);row.addView(avatar,new LinearLayout.LayoutParams(dp(52),dp(52)));
    LinearLayout text=new LinearLayout(activity);text.setOrientation(LinearLayout.VERTICAL);text.setPadding(dp(10),0,dp(6),0);row.addView(text,new LinearLayout.LayoutParams(0,-2,1));
    String state=model.optString("state");String stateText=stateName(state);String title=model.optString("name")+" · "+model.optString("version")+(model.optBoolean("active")?"（当前）":"");
    TextView first=new TextView(activity);first.setText(title);first.setTextSize(16);first.setTextColor(Color.rgb(25,40,55));text.addView(first);
    TextView second=new TextView(activity);second.setText(model.optString("checkpoint")+" · "+stateText);second.setTextSize(13);second.setTextColor(stateColor(state));text.addView(second);
    JSONArray errors=model.optJSONArray("errors");if(errors!=null&&errors.length()>0){TextView detail=new TextView(activity);detail.setText(join(errors));detail.setTextSize(12);detail.setTextColor(Color.rgb(170,45,35));text.addView(detail);}
    row.setContentDescription("模型 "+title+" "+stateText);boolean selectable=model.optBoolean("selectable");row.setEnabled(selectable);row.setAlpha(selectable?1f:.55f);if(selectable)row.setOnClickListener(v->select(model));parent.addView(row);
    String avatarUrl=model.optString("avatar_url","");if(!avatarUrl.isEmpty())loadAvatar(avatar,avatarUrl);
  }

  String stateName(String state) {
    if("available".equals(state))return "可用";if("unverified".equals(state))return "未验证";if("incomplete".equals(state))return "文件不完整";if("failed".equals(state))return "加载失败";return "未知状态";
  }
  int stateColor(String state) { return "available".equals(state)?Color.rgb(25,125,65):("unverified".equals(state)?Color.rgb(165,105,0):Color.rgb(170,45,35)); }
  String join(JSONArray values) { StringBuilder text=new StringBuilder();for(int i=0;i<values.length();i++){if(i>0)text.append("；");text.append(values.optString(i));}return text.toString(); }

  void select(JSONObject model) {
    final ProgressDialog progress=ProgressDialog.show(activity,"加载模型",model.optString("name")+" · "+model.optString("version"),true,false);
    new Thread(()->{
      try {
        JSONObject result=request("POST","/models/select",new JSONObject().put("model_id",model.getString("id")));
        JSONObject selected=result.getJSONObject("selected");
        activity.runOnUiThread(()->{progress.dismiss();listener.selected(selected);if(dialog!=null)dialog.dismiss();Toast.makeText(activity,"模型已切换",Toast.LENGTH_SHORT).show();});
      } catch(Exception e) { activity.runOnUiThread(()->{progress.dismiss();new AlertDialog.Builder(activity).setTitle("模型加载失败").setMessage(e.getMessage()).setPositiveButton("确定",null).show();load();}); }
    }).start();
  }

  void loadAvatar(ImageView view,String path) {
    new Thread(()->{try{HttpURLConnection connection=open(path,"GET");try{if(connection.getResponseCode()!=200)return;Bitmap bitmap=BitmapFactory.decodeStream(connection.getInputStream());if(bitmap!=null)activity.runOnUiThread(()->view.setImageBitmap(bitmap));}finally{connection.disconnect();}}catch(Exception ignored){}}).start();
  }

  void showError(String message) { summary.setText(message); }

  HttpURLConnection open(String path,String method) throws Exception {
    HttpURLConnection connection=(HttpURLConnection)new URL(base+path).openConnection();connection.setRequestMethod(method);connection.setRequestProperty("X-Reader-Token",token);connection.setConnectTimeout(8000);connection.setReadTimeout(300000);return connection;
  }

  JSONObject request(String method,String path,JSONObject body) throws Exception {
    HttpURLConnection connection=open(path,method);
    try {
      if(body!=null){connection.setDoOutput(true);connection.setRequestProperty("Content-Type","application/json; charset=utf-8");try(OutputStream output=connection.getOutputStream()){output.write(body.toString().getBytes(StandardCharsets.UTF_8));}}
      int status=connection.getResponseCode();InputStream stream=status>=400?connection.getErrorStream():connection.getInputStream();String value=read(stream);JSONObject result=value.isEmpty()?new JSONObject():new JSONObject(value);
      if(status>=400)throw new IOException(result.optString("detail","HTTP "+status));return result;
    } finally { connection.disconnect(); }
  }

  String read(InputStream stream) throws Exception { if(stream==null)return "";try(BufferedReader reader=new BufferedReader(new InputStreamReader(stream,StandardCharsets.UTF_8))){StringBuilder value=new StringBuilder();String line;while((line=reader.readLine())!=null)value.append(line);return value.toString();} }
}
