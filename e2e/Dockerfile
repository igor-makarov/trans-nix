FROM buildpack-deps:trixie-curl AS toolchain

ENV MISE_INSTALL_PATH=/usr/local/bin/mise
ENV MISE_PYTHON_GITHUB_ATTESTATIONS=false

RUN curl -fsSL https://mise.run | sh
RUN mise install python@3.14 uv@latest \
    && python_root="$(mise where python@3.14)" \
    && cp -a "$python_root" /opt/e2e-python \
    && basename "$python_root" > /opt/python-version \
    && mise exec uv@latest -- uv venv \
        --python /opt/e2e-python/bin/python3 /opt/e2e-venv \
    && /opt/e2e-venv/bin/python --version

COPY pyproject.toml uv.lock /opt/e2e-project/
RUN UV_PROJECT_ENVIRONMENT=/opt/e2e-venv \
    mise exec uv@latest -- uv sync \
        --locked --project /opt/e2e-project --no-install-project

# The cold image has an absolute Python harness, but no mise-managed Python.
# This lets one test verify the plugin's declared Python dependency ordering.
FROM debian:13-slim AS cold

COPY --from=toolchain /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=toolchain /usr/local/bin/mise /usr/local/bin/mise
COPY --from=toolchain /opt/e2e-python /opt/e2e-python
COPY --from=toolchain /opt/e2e-venv /opt/e2e-venv
COPY --from=toolchain /opt/python-version /opt/python-version

ENV MISE_INSTALL_PATH=/usr/local/bin/mise
ENV MISE_PYTHON_GITHUB_ATTESTATIONS=false
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV TRANS_NIX_EXPECT_NO_PYTHON=1
ENV TRANS_NIX_MISE=/usr/local/bin/mise
ENV TRANS_NIX_REPO=/work
ENV TRANS_NIX_BASE_PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /work
COPY . /work
ENTRYPOINT ["/opt/e2e-venv/bin/python", "-B"]

# Warm tests inherit the identical cold runtime and expose its harness Python as
# a read-only mise seed. Each ephemeral container links that seed into its own
# isolated MISE_DATA_DIR.
FROM cold AS warm

RUN mkdir -p /opt/mise-python \
    && ln -s /opt/e2e-python "/opt/mise-python/$(cat /opt/python-version)"
ENV TRANS_NIX_PYTHON_SEED=/opt/mise-python
