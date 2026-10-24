"""Offline Android SDK build; keeps the signing key separate from distributable files."""
import os, pathlib, subprocess, zipfile, secrets, shutil
root=pathlib.Path(__file__).resolve().parent
sdk=pathlib.Path(os.environ.get('ANDROID_HOME',str(pathlib.Path.home()/'AppData/Local/Android/Sdk')))
bt=sdk/'build-tools/35.0.0'; android=sdk/'platforms/android-36/android.jar'
jdk=pathlib.Path(shutil.which('java')).parent
out=root/'build';out.mkdir(exist_ok=True)
for name in ['gen','classes','dex']: (out/name).mkdir(exist_ok=True)
def run(*args):
    subprocess.run([str(a) for a in args],check=True,cwd=root)
run(bt/'aapt2.exe','compile','--dir',root/'res','-o',out/'resources.zip')
run(bt/'aapt2.exe','link','-o',out/'unsigned.apk','--manifest',root/'AndroidManifest.xml','-I',android,'--java',out/'gen',out/'resources.zip')
sources=list((root/'src').rglob('*.java'))+list((out/'gen').rglob('*.java'))
run(jdk/'javac.exe','-encoding','UTF-8','-source','8','-target','8','-classpath',android,'-d',out/'classes',*sources)
run(jdk/'jar.exe','cf',out/'classes.jar','-C',out/'classes','.')
run(bt/'d8.bat','--lib',android,'--min-api','26','--output',out/'dex',out/'classes.jar')
with zipfile.ZipFile(out/'unsigned.apk','a',zipfile.ZIP_DEFLATED) as z:
    for p in (out/'dex').glob('*.dex'):z.write(p,p.name)
run(bt/'zipalign.exe','-f','4',out/'unsigned.apk',out/'aligned.apk')
private=root.parent/'private/pan-relay-portable-signing';private.mkdir(parents=True,exist_ok=True)
password=private/'password.txt';key=private/'release.jks'
if not key.exists():
    password.write_text(secrets.token_urlsafe(32),encoding='ascii')
    run(jdk/'keytool.exe','-genkeypair','-keystore',key,'-alias','panrelay','-keyalg','RSA','-keysize','3072','-validity','10000','-dname','CN=Pan Relay, O=Personal Apps','-storepass:file',password,'-keypass:file',password)
dest=root.parents[1]/'outputs/PanRelay-Portable-1.0.0.apk';dest.parent.mkdir(exist_ok=True)
run(bt/'apksigner.bat','sign','--ks',key,'--ks-key-alias','panrelay','--ks-pass','file:'+str(password),'--out',dest,out/'aligned.apk')
run(bt/'apksigner.bat','verify','--verbose',dest)
run(bt/'aapt.exe','dump','badging',dest)
print('APK:',dest)
