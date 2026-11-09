"""@xvdp """
from setuptools import setup, find_packages
name = "giddy"
def _set_version(version):
    with open('giddy/version.py', 'w', encoding='utf8') as _fi:
        _fi.write("version='"+version+"'")
        return version
    
subs = find_packages(where=name)
packages = [name] + [f'{name}.{p}' for p in subs]

setup(
    name="giddy",
    packages=packages,
    entry_points={
        'console_scripts': [
            'giddy=giddy.__main__:main',
        ]},
    author='xvdp',
    python_requires='>=3.9',
    package_dir={name: name,},
    version=_set_version(version='0.0.2')
)
