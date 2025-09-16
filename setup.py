from setuptools import setup, find_packages

# README 파일 읽기
with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name='fisica',
    version='1.0.0',
    description='Care&Co Foot pressure analysis and visualization SDK',
    long_description=long_description,
    long_description_content_type="text/markdown",
    author='Care&Co',
    author_email='carencoinc@carenco.kr',
    url='https://github.com/Care-Co/',
    project_urls={
        "Bug Tracker": "https://github.com/Care-Co/fisica_sdk/issues",
        "Documentation": "https://fisica-sdk.readthedocs.io/",
        "Source Code": "https://github.com/Care-Co/fisica_sdk",
    },
    packages=find_packages(),
    install_requires=[
        'numpy>=1.21',
        'opencv-python>=4.5',
        'requests>=2.25',
        'packaging>=21.0',
        'Pillow>=8.0',
        'pyserial>=3.5',
        'PyQt5>=5.15',
        'bleak>=0.22.3'
    ],
    python_requires='>=3.8',
    keywords='foot pressure analysis medical sensor visualization',
    license='MIT',  # classifiers와 일치
    classifiers=[
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Intended Audience :: Healthcare Industry',
        'Topic :: Scientific/Engineering :: Medical Science Apps.',
        'Topic :: Scientific/Engineering :: Human Machine Interfaces',
        'Topic :: Software Development :: Libraries :: Python Modules',
    ],
    include_package_data=True,
    zip_safe=False,
)