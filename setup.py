"""Trusted source build. Customer distribution is binary wheels only."""
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
from setuptools import Distribution, setup
from setuptools.command.build_py import build_py
from setuptools.command.build import build
from setuptools.command.sdist import sdist
from setuptools.errors import SetupError
from wheel.bdist_wheel import bdist_wheel

ROOT = Path(__file__).resolve().parent
builder = runpy.run_path(str(ROOT / 'tools' / 'native_build.py'))
# A wheel is a customer package: its core trusts production keys only. tools/build_release.py
# --validation sets FISICA_ENTITLEMENT_KEYS=validation for QA wheels that also trust the test key.
os.environ.setdefault(builder['KEY_MODE'], 'release')
release = builder['release_metadata']()
if sys.platform == 'darwin':
    os.environ.setdefault('MACOSX_DEPLOYMENT_TARGET', '11.0')


class BinaryDistribution(Distribution):
    def has_ext_modules(self):
        return True


class PlatformBuild(build):
    def finalize_options(self):
        super().finalize_options()
        # Keep DLLs and dylibs in separate trees, including trusted cross-builds.
        self.build_lib = str(ROOT / '.build' / 'python' / builder['platform_tag']())

    def run(self):
        # The wheel takes everything in build_lib: start empty, so modules removed from
        # fisica_sdk/ (or a core from an earlier build) cannot reach it.
        shutil.rmtree(self.build_lib, ignore_errors=True)
        super().run()


class BuildPythonWrapper(build_py):
    def run(self):
        super().run()
        package = Path(self.build_lib) / 'fisica_sdk'
        builder['build_native'](package / '_native')
        (package / '_release.json').write_text(json.dumps(release, indent=2) + '\n')


class BinaryWheel(bdist_wheel):
    def get_tag(self):
        return 'py3', 'none', builder['platform_tag']()


class NoSourceDistribution(sdist):
    def run(self):
        raise SetupError('Source distributions are disabled to protect the native implementation. Build with python -m build --wheel.')


setup(
    name='fisica',
    # A validation (QA) wheel, whose core trusts the test key, is version <v>+validation:
    # fisica-<v>+validation-py3-none-<platform>.whl, shown by pip list, refused by PyPI.
    version=builder['wheel_version'](release),
    description='Care&Co Fisica Scale Python wrapper with a shared native core',
    long_description=(ROOT / 'Readme.md').read_text(encoding='utf-8'),
    long_description_content_type='text/markdown',
    author='Care&Co',
    author_email='carencoinc@carenco.kr',
    url='https://github.com/Care-Co/',
    packages=['fisica_sdk'],
    install_requires=['numpy>=1.21', 'packaging>=21.0', 'pyserial>=3.5', 'bleak>=0.22.3',
                      # bleak's PyObjC 12 has no Python 3.9 wheels; its source build fails with current Xcode.
                      'pyobjc-core<12; platform_system=="Darwin" and python_version<"3.10"'],
    extras_require={
        'visualization': ['opencv-python>=4.5', 'Pillow>=8.0'],
    },
    python_requires='>=3.9',
    license_expression='LicenseRef-Fisica-Scale-SDK',
    license_files=['LICENSE'],
    include_package_data=False,
    zip_safe=False,
    distclass=BinaryDistribution,
    cmdclass={'build': PlatformBuild, 'build_py': BuildPythonWrapper, 'bdist_wheel': BinaryWheel, 'sdist': NoSourceDistribution},
)
