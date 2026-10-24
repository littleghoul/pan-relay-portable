package com.panrelay.portable;
import android.app.*;
import android.os.*;
import android.content.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import org.json.JSONTokener;
public class RuntimeChecks extends Instrumentation {
 String primary,secondary;
 public void onCreate(Bundle args){super.onCreate(args);primary=args.getString("primary");secondary=args.getString("secondary");start();}
 String js(MainActivity a,String code)throws Exception{
  CountDownLatch latch=new CountDownLatch(1);AtomicReference<String> r=new AtomicReference<>();
  runOnMainSync(()->a.web.evaluateJavascript(code,v->{r.set(v);latch.countDown();}));
  if(!latch.await(10,TimeUnit.SECONDS))throw new Exception("JS timeout");return r.get();
 }
 void ready(MainActivity a,String base)throws Exception{
  for(int i=0;i<60;i++){String result=js(a,"location.origin==='"+base+"' && document.readyState!=='loading' && !!document.querySelector('input') && document.title.includes('Pan')");if("true".equals(result))return;Thread.sleep(500);}
  throw new Exception("Page failed: "+base+" "+js(a,"JSON.stringify({url:location.href,state:document.readyState,text:document.body&&document.body.innerText.slice(0,200)})"));
 }
 public void onStart(){Bundle result=new Bundle();MainActivity a=null;try{
  getTargetContext().getSharedPreferences("MainActivity",0).edit().putString("lan_url",primary).putString("wan_url",secondary).putBoolean("wan",false).commit();
  a=(MainActivity)startActivitySync(new Intent(getTargetContext(),MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
  MainActivity app=a;
  runOnMainSync(()->app.web.loadUrl(UrlPolicy.LAN+"/"));ready(a,UrlPolicy.LAN);
  runOnMainSync(()->app.web.loadUrl(UrlPolicy.WAN+"/"));ready(a,UrlPolicy.WAN);
  String blob=(String)new JSONTokener(js(a,"(()=>{window.__prDownloadName='测试.xlsx';return URL.createObjectURL(new Blob([new Uint8Array([80,75,3,4,1,2,3,4])],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}));})()" )).nextValue();
  runOnMainSync(()->app.download(blob,""));
  AtomicBoolean ok=new AtomicBoolean(false);
  for(int i=0;i<40;i++){runOnMainSync(()->ok.set(app.pendingBytes!=null&&app.pendingBytes.length==8&&app.pendingBytes[0]==80));if(ok.get())break;Thread.sleep(250);}
  if(!ok.get())throw new Exception("Blob Excel handoff failed");
  runOnMainSync(()->{app.finishActivity(11);app.finish();});
  result.putString("stream","PASS: LAN page, WAN page, Blob extraction and Android save-file handoff\n");finish(Activity.RESULT_OK,result);
 }catch(Throwable e){result.putString("stream","FAIL: "+e.toString()+"\n");if(a!=null){MainActivity app=a;runOnMainSync(()->app.finish());}finish(Activity.RESULT_CANCELED,result);}}
}
