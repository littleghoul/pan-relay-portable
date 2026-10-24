package com.panrelay.portable;

import android.app.*;
import android.os.*;
import android.content.*;
import android.graphics.Color;
import android.net.Uri;
import android.view.*;
import android.webkit.*;
import android.widget.*;
import android.util.Base64;
import org.json.*;
import java.io.*;
import java.net.URL;
import java.net.HttpURLConnection;
import java.util.concurrent.*;

public class MainActivity extends Activity {
    WebView web;
    TextView address, error;
    Button lan, wan;
    ProgressBar progress;
    String base;
    final Handler handler=new Handler(Looper.getMainLooper());
    final ExecutorService io=Executors.newSingleThreadExecutor();
    byte[] pendingBytes;
    boolean downloading=false;
    ValueCallback<Uri[]> fileCallback;
    static final int SAVE=11, PICK=12, MAX=8*1024*1024;
    static final String XLSX="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

    @Override public void onCreate(Bundle state){
        super.onCreate(state);
        UrlPolicy.configure(getPreferences(MODE_PRIVATE).getString("lan_url",""),getPreferences(MODE_PRIVATE).getString("wan_url",""));
        base=getPreferences(MODE_PRIVATE).getBoolean("wan",false)&&!UrlPolicy.WAN.isEmpty()?UrlPolicy.WAN:UrlPolicy.LAN;
        LinearLayout root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setBackgroundColor(Color.rgb(248,250,252));
        root.setOnApplyWindowInsetsListener((v,insets)->{v.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());return insets;});
        LinearLayout toolbar=new LinearLayout(this);toolbar.setPadding(dp(8),dp(4),dp(8),0);toolbar.setGravity(Gravity.CENTER_VERTICAL);
        lan=button("内网",v->switchServer(false));wan=button("外网",v->switchServer(true));
        toolbar.addView(lan,new LinearLayout.LayoutParams(0,dp(46),1));toolbar.addView(wan,new LinearLayout.LayoutParams(0,dp(46),1));
        toolbar.addView(button("首页",v->{if(base.isEmpty())configureServers();else web.loadUrl(base+"/");}),new LinearLayout.LayoutParams(0,dp(46),1));
        toolbar.addView(button("刷新",v->{if(base.isEmpty())configureServers();else web.reload();}),new LinearLayout.LayoutParams(0,dp(46),1));toolbar.addView(button("设置",v->configureServers()),new LinearLayout.LayoutParams(0,dp(46),1));root.addView(toolbar);
        address=new TextView(this);address.setTextSize(11);address.setPadding(dp(16),dp(2),dp(16),dp(7));address.setTextColor(Color.rgb(100,116,139));root.addView(address);
        progress=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal);root.addView(progress,new LinearLayout.LayoutParams(-1,dp(2)));
        error=new TextView(this);error.setTextSize(14);error.setTextColor(Color.rgb(185,28,28));error.setPadding(dp(16),dp(12),dp(16),dp(12));error.setVisibility(View.GONE);root.addView(error);
        web=new WebView(this);root.addView(web,new LinearLayout.LayoutParams(-1,0,1));setContentView(root);
        WebSettings s=web.getSettings();s.setJavaScriptEnabled(true);s.setDomStorageEnabled(true);s.setAllowFileAccess(false);s.setAllowContentAccess(false);s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        s.setSupportMultipleWindows(true);s.setJavaScriptCanOpenWindowsAutomatically(false);s.setUserAgentString(s.getUserAgentString()+" PanRelayAndroid/1.0");
        CookieManager.getInstance().setAcceptCookie(true);CookieManager.getInstance().setAcceptThirdPartyCookies(web,false);
        web.setWebViewClient(new WebViewClient(){
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest r){return route(r.getUrl().toString());}
            @Override public void onPageStarted(WebView view,String url,android.graphics.Bitmap icon){error.setVisibility(View.GONE);progress.setVisibility(View.VISIBLE);}
            @Override public void onPageFinished(WebView view,String url){
                CookieManager.getInstance().flush();progress.setVisibility(View.GONE);
                if(UrlPolicy.trusted(url)){
                    address.setText((url.startsWith(UrlPolicy.LAN)?"局域网 · ":"外网 · ")+url);
                    // Remember the download attribute without exposing a native JavaScript bridge.
                    web.evaluateJavascript("if(!window.__prNameHook){window.__prNameHook=true;document.addEventListener('click',function(e){var a=e.target.closest&&e.target.closest('a[download]');if(a)window.__prDownloadName=a.download;},true);}",null);
                }
            }
            @Override public void onReceivedError(WebView view,WebResourceRequest r,WebResourceError e){if(r.isForMainFrame())showError("连接失败。内网需连接家中 Wi-Fi；也可以点上方“外网”切换，再点刷新。");}
            @Override public void onReceivedHttpError(WebView view,WebResourceRequest r,WebResourceResponse response){if(r.isForMainFrame())showError("服务器返回 "+response.getStatusCode()+"，请稍后刷新。");}
        });
        web.setWebChromeClient(new WebChromeClient(){
            @Override public void onProgressChanged(WebView v,int value){progress.setProgress(value);}
            @Override public boolean onCreateWindow(WebView view,boolean dialog,boolean gesture,Message result){
                if(!gesture)return false;
                WebView child=new WebView(MainActivity.this);
                child.setWebViewClient(new WebViewClient(){@Override public boolean shouldOverrideUrlLoading(WebView v,WebResourceRequest r){String url=r.getUrl().toString();if(UrlPolicy.trusted(url))web.loadUrl(url);else external(url);v.destroy();return true;}});
                ((WebView.WebViewTransport)result.obj).setWebView(child);result.sendToTarget();return true;
            }
            @Override public boolean onShowFileChooser(WebView v,ValueCallback<Uri[]> callback,FileChooserParams params){
                if(fileCallback!=null)fileCallback.onReceiveValue(null);fileCallback=callback;
                try{startActivityForResult(params.createIntent(),PICK);return true;}catch(Exception e){fileCallback=null;return false;}
            }
            @Override public boolean onJsAlert(WebView v,String url,String message,JsResult result){new AlertDialog.Builder(MainActivity.this).setMessage(message).setPositiveButton("确定",(d,w)->result.confirm()).setOnCancelListener(d->result.cancel()).show();return true;}
            @Override public boolean onJsConfirm(WebView v,String url,String message,JsResult result){new AlertDialog.Builder(MainActivity.this).setMessage(message).setPositiveButton("确定",(d,w)->result.confirm()).setNegativeButton("取消",(d,w)->result.cancel()).setOnCancelListener(d->result.cancel()).show();return true;}
        });
        web.setDownloadListener((url,ua,disposition,mime,length)->download(url,disposition));
        updateServer();if(base.isEmpty())configureServers();else if(state==null||web.restoreState(state)==null)web.loadUrl(base+"/");
    }
    Button button(String label,View.OnClickListener click){Button b=new Button(this);b.setText(label);b.setTextSize(14);b.setAllCaps(false);b.setOnClickListener(click);return b;}
    int dp(int n){return Math.round(n*getResources().getDisplayMetrics().density);}
    void updateServer(){lan.setTextColor(base.equals(UrlPolicy.LAN)?Color.rgb(37,99,235):Color.DKGRAY);wan.setTextColor(base.equals(UrlPolicy.WAN)?Color.rgb(37,99,235):Color.DKGRAY);address.setText(base.isEmpty()?"请先设置您自己的 NAS 服务器地址":base);}
    void switchServer(boolean useWan){
        if(downloading){toast("请先完成或取消当前文件保存。");return;}
        String next=useWan?UrlPolicy.WAN:UrlPolicy.LAN;if(next.isEmpty()){configureServers();return;}if(next.equals(base))return;
        new AlertDialog.Builder(this).setTitle("切换到"+(useWan?"外网":"内网")).setMessage("未提交的输入不会保留，NAS 上已提交的任务会继续运行。").setPositiveButton("切换",(d,w)->{base=next;getPreferences(MODE_PRIVATE).edit().putBoolean("wan",useWan).apply();web.stopLoading();web.clearHistory();updateServer();web.loadUrl(base+"/");}).setNegativeButton("取消",null).show();
    }
    void configureServers(){
        if(downloading){toast("请先完成或取消当前文件保存。");return;}
        LinearLayout form=new LinearLayout(this);form.setOrientation(LinearLayout.VERTICAL);form.setPadding(dp(20),dp(8),dp(20),dp(8));
        TextView hint=new TextView(this);hint.setText("填写您自己的 NAS 地址。只填写根地址和端口，不加 /admin。修改地址会离开当前页面，未提交的输入不会保留。");form.addView(hint);
        EditText local=new EditText(this);local.setSingleLine(true);local.setInputType(17);local.setHint("主要地址（必填）");local.setText(UrlPolicy.LAN);form.addView(local);
        EditText remote=new EditText(this);remote.setSingleLine(true);remote.setInputType(17);remote.setHint("外网地址（可选）");remote.setText(UrlPolicy.WAN);form.addView(remote);
        TextView example=new TextView(this);example.setText("例如 http://NAS的IP:5004 或 https://nas.example.com。地址只保存在本机。");form.addView(example);
        AlertDialog dialog=new AlertDialog.Builder(this).setTitle("设置服务器地址").setView(form).setPositiveButton("保存并打开",null).setNegativeButton(base.isEmpty()?"退出":"取消",(d,w)->{if(base.isEmpty())finish();}).create();
        dialog.setCanceledOnTouchOutside(false);dialog.setOnCancelListener(d->{if(base.isEmpty())finish();});dialog.setOnShowListener(d->dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v->{
            try{String l=UrlPolicy.normalize(local.getText().toString()),r=UrlPolicy.normalize(remote.getText().toString());if(l.isEmpty()){local.setError("请填写主要地址");return;}
                UrlPolicy.configure(l,r);getPreferences(MODE_PRIVATE).edit().putString("lan_url",l).putString("wan_url",r).putBoolean("wan",false).apply();base=l;web.stopLoading();web.clearHistory();updateServer();dialog.dismiss();web.loadUrl(base+"/");
            }catch(IllegalArgumentException e){local.setError(e.getMessage());}
        }));dialog.show();
    }
    boolean route(String url){if(UrlPolicy.trusted(url))return false;if(url.startsWith("blob:")){download(url,"");return true;}external(url);return true;}
    void external(String url){
        Uri u=Uri.parse(url);if(!"https".equals(u.getScheme())&&!"http".equals(u.getScheme())){toast("请在系统浏览器中打开此链接。");return;}
        try{startActivity(new Intent(Intent.ACTION_VIEW,u).addCategory(Intent.CATEGORY_BROWSABLE));}catch(Exception e){toast("未找到可打开链接的浏览器。");}
    }
    void showError(String message){error.setText(message);error.setVisibility(View.VISIBLE);progress.setVisibility(View.GONE);}
    void toast(String message){Toast.makeText(this,message,Toast.LENGTH_LONG).show();}
    void failDownload(){downloading=false;pendingBytes=null;toast("下载失败，请刷新后重试；需要先登录，文件不能超过 8 MB。");}
    void download(String url,String disposition){
        if(downloading){toast("正在准备上一份文件，请稍后再试。");return;}
        String page=web.getUrl();if(!UrlPolicy.trusted(page))return;
        downloading=true;
        if(url.startsWith("blob:")){
            if(!UrlPolicy.sameOrigin(url.substring(5),page)){failDownload();return;}
            String script="window.__prBlob={state:'waiting'};fetch("+JSONObject.quote(url)+").then(r=>r.blob()).then(b=>{if(b.size>8388608)throw Error('large');let f=new FileReader();f.onload=()=>{window.__prBlob={state:'ready',data:f.result.split(',')[1],name:window.__prDownloadName||'网盘分享.xlsx'};};f.onerror=()=>window.__prBlob={state:'error'};f.readAsDataURL(b);}).catch(()=>window.__prBlob={state:'error'});";
            web.evaluateJavascript(script,v->pollBlob(page,0));
        }else if(UrlPolicy.sameOrigin(url,page)){
            String cookie=CookieManager.getInstance().getCookie(url);String name=URLUtil.guessFileName(url,disposition,XLSX);
            io.execute(()->{try{HttpURLConnection c=(HttpURLConnection)new URL(url).openConnection();try{
                c.setConnectTimeout(15000);c.setReadTimeout(30000);c.setInstanceFollowRedirects(false);if(cookie!=null)c.setRequestProperty("Cookie",cookie);
                if(c.getResponseCode()!=200)throw new IOException();String type=c.getContentType();if(type==null||!type.contains("spreadsheetml"))throw new IOException();
                ByteArrayOutputStream out=new ByteArrayOutputStream();try(InputStream in=c.getInputStream()){byte[] buf=new byte[16384];int n;while((n=in.read(buf))!=-1){if(out.size()+n>MAX)throw new IOException();out.write(buf,0,n);}}byte[] data=out.toByteArray();runOnUiThread(()->saveDialog(data,name));
            }finally{c.disconnect();}}catch(Exception e){runOnUiThread(()->failDownload());}});
        }else failDownload();
    }
    void pollBlob(String origin,int tries){
        if(isFinishing()||!UrlPolicy.sameOrigin(origin,web.getUrl())){failDownload();return;}
        web.evaluateJavascript("JSON.stringify({state:window.__prBlob&&window.__prBlob.state,length:window.__prBlob&&window.__prBlob.data?window.__prBlob.data.length:0,name:window.__prBlob&&window.__prBlob.name})",raw->{
            try{JSONObject info=new JSONObject((String)new JSONTokener(raw).nextValue());String state=info.optString("state");if("ready".equals(state)){int length=info.getInt("length");if(length>MAX*4/3+4)throw new IOException();readChunk(origin,new StringBuilder(),length,info.optString("name","网盘分享.xlsx"));}else if("error".equals(state)||tries>100)failDownload();else handler.postDelayed(()->pollBlob(origin,tries+1),200);}catch(Exception e){failDownload();}
        });
    }
    void readChunk(String origin,StringBuilder data,int length,String name){
        if(!UrlPolicy.sameOrigin(origin,web.getUrl())){failDownload();return;}
        int offset=data.length();web.evaluateJavascript("window.__prBlob.data.slice("+offset+","+Math.min(offset+65536,length)+")",raw->{
            try{String chunk=(String)new JSONTokener(raw).nextValue();if(chunk.isEmpty()&&data.length()<length)throw new IOException();data.append(chunk);if(data.length()<length)readChunk(origin,data,length,name);else{web.evaluateJavascript("delete window.__prBlob",null);saveDialog(Base64.decode(data.toString(),Base64.DEFAULT),name);}}catch(Exception e){failDownload();}
        });
    }
    void saveDialog(byte[] data,String name){
        if(data.length<4||data[0]!='P'||data[1]!='K'){failDownload();return;}
        pendingBytes=data;name=name.replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]","_");if(!name.endsWith(".xlsx"))name+=".xlsx";
        Intent i=new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType(XLSX).putExtra(Intent.EXTRA_TITLE,name);
        try{startActivityForResult(i,SAVE);}catch(Exception e){failDownload();}
    }
    @Override protected void onActivityResult(int request,int result,Intent intent){super.onActivityResult(request,result,intent);
        if(request==PICK&&fileCallback!=null){fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result,intent));fileCallback=null;}
        if(request==SAVE){byte[] bytes=pendingBytes;pendingBytes=null;if(result!=RESULT_OK||intent==null||bytes==null){downloading=false;toast("未保存，可再次点击下载。");return;}Uri uri=intent.getData();io.execute(()->{try(OutputStream out=getContentResolver().openOutputStream(uri)){if(out==null)throw new IOException();out.write(bytes);runOnUiThread(()->{downloading=false;toast("Excel 已保存，分享链接可直接点击。");});}catch(Exception e){runOnUiThread(()->failDownload());}});}
    }
    @Override public void onBackPressed(){if(web.canGoBack())web.goBack();else new AlertDialog.Builder(this).setMessage("退出 Pan Relay？NAS 上的任务仍会继续。").setPositiveButton("退出",(d,w)->finish()).setNegativeButton("取消",null).show();}
    @Override protected void onSaveInstanceState(Bundle out){super.onSaveInstanceState(out);web.saveState(out);}
    @Override protected void onPause(){super.onPause();CookieManager.getInstance().flush();web.onPause();}
    @Override protected void onResume(){super.onResume();if(web!=null)web.onResume();}
    @Override protected void onDestroy(){handler.removeCallbacksAndMessages(null);web.destroy();io.shutdown();super.onDestroy();}
}
