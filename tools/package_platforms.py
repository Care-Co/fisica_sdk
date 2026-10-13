"""Assemble binary-only mobile/desktop bindings from trusted prebuilt cores."""
import argparse,hashlib,json,shutil,zipfile
from pathlib import Path
from native_build import ROOT,release_metadata
from build_platforms import archive,public_files

def main():
 p=argparse.ArgumentParser();p.add_argument('--input',type=Path,default=ROOT/'.build/platforms');p.add_argument('--output',type=Path,default=ROOT/'.build/multiplatform-releases');a=p.parse_args()
 release=release_metadata();out=a.output/release['sdk_version'];out.mkdir(parents=True,exist_ok=True)
 for file in a.input.rglob('*'):
  if file.suffix in ('.zip','.aar'): shutil.copyfile(file,out/file.name)
 swift=ROOT/'wrappers/swift'
 archive(out/f"fisica-swift-{release['wrappers']['swift']['version']}.zip", public_files(swift)+[(p,'CFisica.xcframework/'+p.relative_to(a.input/'apple/CFisica.xcframework').as_posix()) for p in (a.input/'apple/CFisica.xcframework').rglob('*') if p.is_file()]+[(ROOT/'LICENSE','LICENSE')])
 kotlin=ROOT/'wrappers/kotlin'
 archive(out/f"fisica-kotlin-{release['wrappers']['kotlin']['version']}.zip",public_files(kotlin)+[(ROOT/'LICENSE','LICENSE'),(ROOT/'native/include/fisica_core.h','fisica_core.h')])
 archive(out/f"fisica-dotnet-{release['wrappers']['dotnet']['version']}.zip",public_files(ROOT/'wrappers/dotnet')+[(ROOT/'LICENSE','LICENSE')])
 flutter=ROOT/'wrappers/flutter';files=public_files(flutter)+[(ROOT/'LICENSE','LICENSE')]
 for lib in (a.input/'android/jni').rglob('libfisica_core.so'): files.append((lib,'android/src/main/jniLibs/'+lib.parent.name+'/'+lib.name))
 xc=a.input/'apple/CFisica.xcframework'
 for platform in ['ios','macos']:
  files.extend((f,platform+'/CFisica.xcframework/'+f.relative_to(xc).as_posix()) for f in xc.rglob('*') if f.is_file())
 for target,arch in [('win_amd64','x64'),('win_arm64','arm64')]:
  files.append((a.input/target/'fisica_core.dll',f'windows/lib/{arch}/fisica_core.dll'))
 archive(out/f"fisica-flutter-{release['wrappers']['flutter']['version']}-platforms.zip",files)
 artifacts={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in out.iterdir() if f.is_file() and f.suffix in ('.zip','.aar','.nupkg')}
 (out/'platform-manifest.json').write_text(json.dumps({'sdk_version':release['sdk_version'],'core_version':release['core_version'],'abi_minor':release['abi_minor'],'artifacts':artifacts},indent=2)+'\n')
 print('Platform packages:',out)
if __name__=='__main__':main()
