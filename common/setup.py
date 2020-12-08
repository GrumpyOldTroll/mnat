from setuptools import find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

# TBD: use_scm_version = True/ setup_requires=["setuptools_scm"]

setuptools.setup(
    name="mnat",
    version="0.0.1",
    author="Jake Holland",
    author_email="jholland@akamai.com",
    description="The mnat.common_client package (a sub-component, used by both mnat-ingress and mnat-egress).  It includes the mnat-translate script and the base protocol handler.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/GrumpyOldTroll/mnat/common",
    packages=find_packages(),
    scripts=['mnat-translate'],
    setup_requires=[
        'cython>=0.22',
        'setuptools>18.0',
        'setuptools-scm>1.5.4'
    ],
    install_requires=['Cython','python-libpcap','h2','twisted','pyOpenSSL','service_identity'],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Telecommunications Industry",
    ],
    python_requires='>=3.6',
)
