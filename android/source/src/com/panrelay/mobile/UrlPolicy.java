package com.panrelay.portable;
import java.net.URI;
public final class UrlPolicy {
    public static String LAN="",WAN="";
    public static String normalize(String value){
        if(value==null||value.trim().isEmpty())return "";
        value=value.trim();if(!value.contains("://"))value="http://"+value;
        try{
            URI u=new URI(value);String scheme=u.getScheme().toLowerCase(java.util.Locale.ROOT);
            if((!scheme.equals("http")&&!scheme.equals("https"))||u.getHost()==null||u.getRawUserInfo()!=null||u.getRawQuery()!=null||u.getRawFragment()!=null||u.getPort()>65535||u.getPort()==0||(!u.getPath().isEmpty()&&!u.getPath().equals("/")))throw new Exception();
            int port=u.getPort();if((scheme.equals("http")&&port==80)||(scheme.equals("https")&&port==443))port=-1;
            return new URI(scheme,null,u.getHost().toLowerCase(java.util.Locale.ROOT),port,null,null,null).toASCIIString();
        }catch(Exception e){throw new IllegalArgumentException("请填写服务器根地址，例如 https://nas.example.com:5004，不带页面路径、账号或密码。");}
    }
    public static void configure(String lan,String wan){LAN=normalize(lan);WAN=normalize(wan);}
    public static String origin(String value){
        try{URI u=new URI(value);if(u.getRawUserInfo()!=null)return "";return normalize(new URI(u.getScheme(),null,u.getHost(),u.getPort(),null,null,null).toString());}catch(Exception e){return "";}
    }
    public static boolean trusted(String value){String o=origin(value);return !o.isEmpty()&&(o.equals(LAN)||o.equals(WAN));}
    public static boolean sameOrigin(String a,String b){return trusted(a)&&trusted(b)&&origin(a).equals(origin(b));}
}
