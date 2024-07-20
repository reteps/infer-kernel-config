FROM python:3.11

ADD requirements.txt /root

WORKDIR /root
RUN apt update && apt upgrade -y && apt install -y ripgrep vim coccinelle
RUN python3 -m pip install -r requirements.txt