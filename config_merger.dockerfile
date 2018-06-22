FROM python:alpine
RUN pip3 install pyyaml
COPY config_merger.py /config_merger/config_merger.py
WORKDIR /config_merger/
ENTRYPOINT [ "python3", "/config_merger/config_merger.py" ]
CMD [ "--help" ]

# docker build -t config_merger --file config_merger.dockerfile .
# docker run --rm -it config_merger
