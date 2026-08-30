FROM debian:13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

ENV MISE_INSTALL_PATH=/usr/local/bin/mise
RUN curl -fsSL https://mise.run | sh

ENV TRANS_NIX_EXPECT_NO_PYTHON=1
WORKDIR /work
COPY . /work
ENTRYPOINT ["bash", "tests/e2e.sh"]
