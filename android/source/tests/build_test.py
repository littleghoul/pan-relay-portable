import pathlib,subprocess,zipfile,shutil
r=pathlib.Path(__file__).resolve().parents[1];b=r/'build/runtime';b.mkdir(exist_ok=True)
for n in ['classes','dex']:(b/n).mkdir(exist_ok=True)
sdk=pathlib.Path.home()/'AppData/Local/Android/Sdk';bt=sdk/'build-tools/35.0.0';jar=sdk/'platforms/android-36/android.jar';jdk=pathlib.Path(shutil.which('java')).parent
def run(*a):subprocess.run([str(x) for x in a],check=True)
run(bt/'aapt2.exe','link','--manifest',r/'tests/AndroidManifest.xml','-I',jar,'-o',b/'raw.apk')
run(jdk/'javac.exe','-encoding','UTF-8','-source','8','-target','8','-classpath',str(jar)+';'+str(r/'build/classes'),'-d',b/'classes',r/'tests/RuntimeChecks.java')
run(jdk/'jar.exe','cf',b/'classes.jar','-C',b/'classes','.')
run(bt/'d8.bat','--lib',jar,'--classpath',r/'build/classes.jar','--min-api','26','--output',b/'dex',b/'classes.jar')
with zipfile.ZipFile(b/'raw.apk','a') as z:z.write(b/'dex/classes.dex','classes.dex')
run(bt/'zipalign.exe','-f','4',b/'raw.apk',b/'aligned.apk')
p=r.parent/'private/pan-relay-portable-signing'
run(bt/'apksigner.bat','sign','--ks',p/'release.jks','--ks-pass','file:'+str(p/'password.txt'),'--out',b/'tests.apk',b/'aligned.apk')
