from setuptools import setup, find_packages

with open("README.md") as f:
    long_description = f.read()


setup(
    name="openaleph-servicelayer",
    version="1.23.2",
    description="Basic remote service functions for openaleph components",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
    ],
    keywords="storage files s3",
    author="Data and Research Center, forked from OCCRP.org",
    author_email="alex@dataresearchcenter.org",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="http://github.com/alephdata/servicelayer",
    license="MIT",
    packages=find_packages(exclude=["ez_setup", "examples", "test"]),
    namespace_packages=[],
    include_package_data=True,
    zip_safe=True,
    install_requires=[
        "banal >= 1.0.6, < 2.0.0",
        "normality >= 3.0.1, < 4.0.0",
        "fakeredis >= 2.22.0, < 3.0.0",
        "sqlalchemy >= 2.0.4, < 3.0.0",
        "structlog >= 24.1.0, < 25.0.0",
        "colorama >= 0.4.6, < 1.0.0",
        "prometheus-client >= 0.20.0, < 0.21.0",
    ],
    extras_require={
        "amazon": ["boto3 >= 1.11.9, <2.0.0"],
        "google": [
            "grpcio >= 1.32.0, <2.0.0",
            "google-cloud-storage >= 1.31.0, < 3.0.0",
        ],
        "dev": [
            "twine",
            "moto < 5",
            "boto3 >= 1.11.9, <2.0.0",
            "pytest >= 3.6",
            "coverage",
            "pytest-cov",
            "time-machine>=2.14.1, <3.0.0",
        ],
    },
    test_suite="tests",
    entry_points={
        "servicelayer.test": ["test = servicelayer.extensions:get_extensions"]
    },
)
