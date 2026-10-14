"""Explicit public sample allowlist; never traverse a private SDK checkout for distribution."""
PARTNER_SAMPLE_FILES = (
    'README.md', 'CMakeLists.txt', 'scale-mock.json', 'sample-frame.hex',
    'python/main.py', 'dotnet/Quickstart.csproj', 'dotnet/Program.cs',
    'swift/ScaleApp.swift', 'c/main.c', 'c/fisica_core.def', 'cpp/main.cpp',
    'flutter/pubspec.yaml', 'flutter/lib/main.dart', 'flutter/assets/scale-mock.json', 'flutter/prepare.py',
    'android/settings.gradle', 'android/build.gradle',
    'android/app-kotlin/build.gradle', 'android/app-java/build.gradle',
    'android/app-kotlin/src/main/AndroidManifest.xml', 'android/app-java/src/main/AndroidManifest.xml',
    'android/app-kotlin/src/main/kotlin/example/fisica/kotlin/MainActivity.kt',
    'android/app-java/src/main/java/example/fisica/java/MainActivity.java',
)
