import os
import re
from setuptools import setup, find_packages

def read_readme():
    with open("README.md", "r", encoding="utf-8") as fh:
        return fh.read()
    
with open(os.path.join(os.path.dirname(__file__), "requirements.txt"), "r", encoding="utf-8-sig") as f:
    required_packages = f.read().splitlines()

def get_version():
    try:
        import pkg_resources
        return pkg_resources.get_distribution('StreamingCommunity').version
    
    except Exception:
        version_file_path = os.path.join(os.path.dirname(__file__), "StreamingCommunity", "Upload", "version.py")
        with open(version_file_path, "r", encoding="utf-8") as f:
            version_match = re.search(r"^__version__\s*=\s*['\"]([^'\"]*)['\"]", f.read(), re.M)
        if version_match:
            return version_match.group(1)
        raise RuntimeError("Unable to find version string in StreamingCommunity/Upload/version.py.")

setup(
    name="StreamingCommunity",
    version=get_version(),
    long_description=read_readme(),
    long_description_content_type="text/markdown",
    author="Arrowar",
    url="https://github.com/Arrowar/StreamingCommunity",
    packages=find_packages(include=["StreamingCommunity", "StreamingCommunity.*"]),
    install_requires=required_packages,
    python_requires='>=3.8',
    entry_points={
        "console_scripts": [
            "streamingcommunity=StreamingCommunity.run:main",
        ],
    },
    include_package_data=True,
    keywords="streaming community",
    project_urls={
        "Bug Reports": "https://github.com/Arrowar/StreamingCommunity/issues",
        "Source": "https://github.com/Arrowar/StreamingCommunity",
    }
)