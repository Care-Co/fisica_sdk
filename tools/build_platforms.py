"""Trusted multi-platform binary builder. Never compiles on a customer's device."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import zipfile
from native_build import ROOT,build_native,prepare_release,release_metadata
from audit_artifacts import audit_archive

def run(args): subprocess.run([str(a) for a in args],check=True)
def archive(output,files):
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        for file,name in files: z.write(file,name)
    audit_archive(output)
def public_files(directory):
    return [(p,p.relative_to(directory).as_posix()) for p in directory.rglob('*') if p.is_file() and p.name != 'pubspec.lock' and
            not any(part in ('Tests','tests','test','.build','obj','bin','.dart_tool','build','CFisica.xcframework','src/test') for part in p.relative_to(directory).parts)]
def apple(output):
    output.mkdir(parents=True,exist_ok=True)
    generated=output/'generated';prepare_release(generated)
    xc=output/'CFisica.xcframework';shutil.rmtree(xc,ignore_errors=True);xc.mkdir()
    developer=Path(os.environ.get('FISICA_XCODE_DEVELOPER','/Applications/Xcode.app/Contents/Developer'))
    macsdk=Path(subprocess.check_output(['xcrun','--sdk','macosx','--show-sdk-path'],text=True).strip())
    variants=[('macos-arm64_x86_64','macos',None,macsdk,['arm64-apple-macos11','x86_64-apple-macos11']),
              ('ios-arm64','ios',None,developer/'Platforms/iPhoneOS.platform/Developer/SDKs/iPhoneOS.sdk',['arm64-apple-ios13']),
              ('ios-arm64_x86_64-simulator','ios','simulator',developer/'Platforms/iPhoneSimulator.platform/Developer/SDKs/iPhoneSimulator.sdk',['arm64-apple-ios13-simulator','x86_64-apple-ios13-simulator'])]
    libraries=[]
    for identifier,platform,variant,sdk,targets in variants:
        if not sdk.is_dir(): raise RuntimeError(f'Apple SDK missing: {sdk}')
        directory=xc/identifier;directory.mkdir();thin=[]
        for target in targets:
            objects=[]
            for source in sorted((ROOT/'native/src').glob('*.cpp')):
                obj=generated/(identifier+'-'+target.split('-')[0]+'-'+source.stem+'.o')
                run(['c++','-std=c++17','-O2','-ffp-contract=off','-fvisibility=hidden','-fvisibility-inlines-hidden','-DFISICA_CORE_STATIC','-DFISICA_CORE_BUILD','-target',target,'-isysroot',sdk,'-I'+str(ROOT/'native/include'),'-I'+str(generated),'-c',source,'-o',obj]);objects.append(obj)
            lib=generated/(identifier+'-'+target.split('-')[0]+'.a');run(['libtool','-static','-o',lib,*objects]);thin.append(lib)
        dest=directory/'libfisica_core.a'
        if len(thin)>1: run(['lipo','-create',*thin,'-output',dest])
        else: shutil.copyfile(thin[0],dest)
        headers=directory/'Headers';headers.mkdir();shutil.copyfile(ROOT/'native/include/fisica_core.h',headers/'fisica_core.h')
        (headers/'module.modulemap').write_text('module CFisica { header "fisica_core.h" export * }\n')
        item={'LibraryIdentifier':identifier,'LibraryPath':dest.name,'HeadersPath':'Headers','SupportedArchitectures':[t.split('-')[0] for t in targets],'SupportedPlatform':platform}
        if variant: item['SupportedPlatformVariant']=variant
        libraries.append(item)
    (xc/'Info.plist').write_bytes(plistlib.dumps({'CFBundlePackageType':'XFWK','XCFrameworkFormatVersion':'1.0','AvailableLibraries':libraries}))
    wrapper=ROOT/'wrappers/swift';shutil.rmtree(wrapper/'CFisica.xcframework',ignore_errors=True);shutil.copytree(xc,wrapper/'CFisica.xcframework','src/test')
    files=public_files(wrapper)+[(p,'CFisica.xcframework/'+p.relative_to(xc).as_posix()) for p in xc.rglob('*') if p.is_file()]+[(ROOT/'LICENSE','LICENSE')]
    archive(output/('fisica-swift-'+release_metadata()['wrappers']['swift']['version']+'.zip'),files)
    return xc

def android(output,ndk):
    output.mkdir(parents=True,exist_ok=True)
    generated=output/'generated';prepare_release(generated)
    compiler=next((ndk/'toolchains/llvm/prebuilt').iterdir())/'bin'
    abis={'arm64-v8a':'aarch64-linux-android23','armeabi-v7a':'armv7a-linux-androideabi23','x86_64':'x86_64-linux-android23'}
    libs=output/'jni';sources=sorted((ROOT/'native/src').glob('*.cpp'))
    for abi,target in abis.items():
        directory=libs/abi;directory.mkdir(parents=True,exist_ok=True)
        common=[compiler/(target+'-clang++'),'-std=c++17','-O2','-shared','-fPIC','-fvisibility=hidden','-ffp-contract=off','-static-libstdc++','-Wl,--exclude-libs,ALL','-I'+str(ROOT/'native/include')]
        run([*common,'-DFISICA_CORE_BUILD','-I'+str(generated),*sources,'-Wl,-soname,libfisica_core.so','-o',directory/'libfisica_core.so'])
        run([*common,ROOT/'wrappers/kotlin/bridge/fisica_jni.cpp','-L'+str(directory),'-lfisica_core','-Wl,-soname,libfisica_jni.so','-o',directory/'libfisica_jni.so'])
    # Compile public Kotlin to a classes.jar without shipping a build toolchain.
    gradle=Path(os.environ.get('FISICA_GRADLE_LIB','/opt/homebrew/opt/gradle/libexec/lib'))
    stdlib=next(gradle.glob('kotlin-stdlib-*.jar'))
    jar=output/'classes.jar'
    kotlin_sources=sorted((ROOT/'wrappers/kotlin/src/main/kotlin').rglob('*.kt'))
    androidsdk=Path(os.environ.get('ANDROID_SDK_ROOT',str(Path.home()/'Library/Android/sdk')))
    androidjar=androidsdk/'platforms/android-35/android.jar'
    run(['java','-cp',str(gradle/'*'),'org.jetbrains.kotlin.cli.jvm.K2JVMCompiler','-no-stdlib','-no-reflect','-classpath',str(stdlib)+os.pathsep+str(androidjar),'-jvm-target','1.8','-d',jar,*kotlin_sources])
    manifest=ROOT/'wrappers/kotlin/AndroidManifest.xml'
    files=[(jar,'classes.jar'),(manifest,'AndroidManifest.xml'),(ROOT/'LICENSE','LICENSE'),(generated/'release.json','release.json'),(ROOT/'wrappers/kotlin/consumer-rules.pro','proguard.txt')]
    files += [(p,'jni/'+p.relative_to(libs).as_posix()) for p in libs.rglob('*.so')]
    archive(output/('fisica-scale-'+release_metadata()['wrappers']['kotlin']['version']+'.aar'),files)
    archive(output/('fisica-kotlin-'+release_metadata()['wrappers']['kotlin']['version']+'.zip'),public_files(ROOT/'wrappers/kotlin')+[(ROOT/'LICENSE','LICENSE'),(ROOT/'native/include/fisica_core.h','fisica_core.h')])
    return libs

def desktop(output,target):
    previous=os.environ.get('FISICA_BUILD_PLATFORM');os.environ['FISICA_BUILD_PLATFORM']=target
    try:
        core=build_native(output/target)
        files=[(core,core.name),(core.parent/'release.json','release.json'),(ROOT/'native/include/fisica_core.h','fisica_core.h'),(ROOT/'LICENSE','LICENSE')]
        archive(output/f'fisica-core-{release_metadata()["core_version"]}-{target}.zip',files)
    finally:
        if previous is None: os.environ.pop('FISICA_BUILD_PLATFORM',None)
        else: os.environ['FISICA_BUILD_PLATFORM']=previous

def artifact_paths(output,targets):
    # Names come from release metadata so CI never hardcodes a version.
    release=release_metadata();wrappers=release['wrappers'];paths=[]
    for target in targets:
        if target=='apple': paths.append(output/'apple'/f"fisica-swift-{wrappers['swift']['version']}.zip")
        elif target=='android': paths += [output/'android'/f"fisica-scale-{wrappers['kotlin']['version']}.aar",output/'android'/f"fisica-kotlin-{wrappers['kotlin']['version']}.zip"]
        else: paths.append(output/f"fisica-core-{release['core_version']}-{target}.zip")
    return paths

def main():
    targets=['apple','android','macosx_11_0_universal2','win_arm64','win_amd64']
    p=argparse.ArgumentParser();group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--target',choices=targets);group.add_argument('--print-artifacts',nargs='+',choices=targets,metavar='TARGET')
    p.add_argument('--output',type=Path,default=ROOT/'.build/platforms');p.add_argument('--ndk',type=Path,default=Path.home()/'Library/Android/sdk/ndk/26.3.11579264');args=p.parse_args()
    if args.print_artifacts:
        print(' '.join(path.relative_to(Path.cwd()).as_posix() if path.is_relative_to(Path.cwd()) else str(path) for path in artifact_paths(args.output,args.print_artifacts)));return
    args.output.mkdir(parents=True,exist_ok=True)
    if args.target=='apple': apple(args.output/'apple')
    elif args.target=='android': android(args.output/'android',args.ndk)
    else: desktop(args.output,args.target)
if __name__=='__main__':main()
