import com.panrelay.portable.UrlPolicy;
public class UrlPolicyTest {
 public static void main(String[] args){
  if(!UrlPolicy.LAN.isEmpty()||!UrlPolicy.WAN.isEmpty())throw new AssertionError("defaults must be empty");
  UrlPolicy.configure("nas.example.com:5004/","https://remote.example.com/");
  if(!UrlPolicy.LAN.equals("http://nas.example.com:5004")||!UrlPolicy.WAN.equals("https://remote.example.com"))throw new AssertionError("normalize");
  if(!UrlPolicy.trusted("http://nas.example.com:5004/admin")||!UrlPolicy.trusted("https://remote.example.com:443/admin"))throw new AssertionError("valid");
  for(String s:new String[]{"http://nas.example.com.evil:5004/","http://evil@nas.example.com:5004/","javascript:alert(1)","file:///sdcard/a","http://nas.example.com:80/","https://unconfigured.example.com/"})if(UrlPolicy.trusted(s))throw new AssertionError(s);
  for(String s:new String[]{"http://nas.example.com/admin","https://user:pass@nas.example.com","http://nas.example.com:99999","http://nas.example.com?x=1","file:///a"}){try{UrlPolicy.normalize(s);throw new AssertionError(s);}catch(IllegalArgumentException expected){}}
  if(UrlPolicy.sameOrigin(UrlPolicy.WAN,UrlPolicy.LAN))throw new AssertionError("cross host");
  System.out.println("Portable URL tests passed: blank defaults, normalization, selected origins, rejected malformed URLs");
 }
}
