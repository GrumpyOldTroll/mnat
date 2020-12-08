import setuptools

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name="mnat-egress",
    version="0.0.1",
    author="Jake Holland",
    author_email="jholland@akamai.com",
    description="The MNAT egress package.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/GrumpyOldTroll/mnat/egress",
    packages=setuptools.find_packages(),
    scripts=['mnat-egress', 'mnat-igmp-monitor', '../common/mnat-translate'],
    install_requires=['h2','twisted','pyOpenSSL', 'service_identity', 'watchdog'],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.6',
)
