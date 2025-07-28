FROM python:3.12-slim

RUN apt-get update && apt-get -y upgrade
RUN apt-get install make
RUN apt-get -y install libicu-dev pkg-config build-essential

COPY . /opt/servicelayer
WORKDIR /opt/servicelayer
RUN pip3 install --upgrade pip setuptools
RUN pip install --no-binary=:pyicu: pyicu
RUN make dev

CMD /bin/bash
