# setup.py
from setuptools import setup, find_packages

setup(
    name='fisica_sdk',
    version='0.1.0',
    description='Care&Co Foot pressure analysis and visualization SDK',
    author='Care&Co',
    author_email='carencoinc@carenco.kr',
    url='https://github.com/Care-Co/',
    packages=find_packages(),
    install_requires=[
        'numpy>=1.21',
        'opencv-python>=4.5',
        'requests>=2.25',
        'packaging>=21.0',
        'Pillow>=8.0',
        'pyserial>=3.5',
        'importlib-metadata>=4.0; python_version<"3.8"',  # 하위 호환
        'PyQt5>=5.15',
    ],
    python_requires='>=3.8',
    classifiers=[
        'Programming Language :: Python :: 3.8',
        'License :: Care&Co. Inc',
        'Operating System :: OS Independent',
    ],
)