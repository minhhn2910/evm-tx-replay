#!/usr/bin/env python3
"""Setup script for CuEVM Data Collection."""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="evm-tx-replay",
    version="1.0.0",
    author="evm-tx-replay Contributors",
    description="A tool for collecting Ethereum transaction data from mainnet",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/minhhn2910/evm-tx-replay",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "License :: OSI Approved :: MIT License",
    ],
    python_requires=">=3.8",
    install_requires=[
        "web3>=6.0.0",
        "hexbytes>=0.3.0",
        "matplotlib>=3.5.0",
    ],
    entry_points={
        "console_scripts": [
            "cuevm-collect=main:main",
        ],
    },
    include_package_data=True,
    zip_safe=False,
)
