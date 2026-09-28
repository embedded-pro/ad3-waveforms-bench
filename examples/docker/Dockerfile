# A bench container that uses the AD3 and the device terminal of the host through ad3-bench-server.
# No WaveForms runtime and no USB access are needed in here.
FROM python:3.12-slim

RUN pip install --no-cache-dir ad3-waveforms-bench

WORKDIR /bench
COPY . /bench

CMD ["pytest", "-v"]
