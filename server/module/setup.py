from setuptools import setup, find_packages

# TBD: use_scm_version = True/ setup_requires=["setuptools_scm"]

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name = "jetconf_mnat",
    author = "Jake Holland",
    author_email="jholland@akamai.com",
    description = "MNAT server jetconf backend",
    long_description = long_description,
    long_description_content_type="text/markdown",
    url = "https://github.com/GrumpyOldTroll/mnat/server",
    packages = find_packages(),
    install_requires = ["jetconf"],
    classifiers = [
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Telecommunications Industry",
    ],
    package_data = {
        "": ["yang-library-data.json"]
    }
)
