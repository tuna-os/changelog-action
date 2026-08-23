FROM fedora:45

RUN dnf install -y python3 python3-zstandard zstd skopeo git curl && dnf clean all

# Install Cosign.
#
# Pinned and checksum-verified. changelog.py's whole job is to run
# `cosign verify-attestation` and trust its exit status, so a cosign binary
# fetched from `latest/download` with no integrity check would make every
# verification in this action worth exactly nothing — and a subverted verifier
# fails silently, by design.
#
# Both tools are verified against the checksum file their own release
# publishes, so bumping the version below is the only maintenance step; there
# is no locally maintained digest to keep in sync. Renovate understands these
# ARG pins.
ARG COSIGN_VERSION=3.1.3
RUN set -eux; \
    cd /tmp; \
    curl -fsSLO "https://github.com/sigstore/cosign/releases/download/v${COSIGN_VERSION}/cosign-linux-amd64"; \
    curl -fsSLO "https://github.com/sigstore/cosign/releases/download/v${COSIGN_VERSION}/cosign_checksums.txt"; \
    sha256sum --check --ignore-missing cosign_checksums.txt; \
    install -m 0755 cosign-linux-amd64 /usr/local/bin/cosign; \
    rm -f cosign-linux-amd64 cosign_checksums.txt

# Install ORAS (for fetching SBOMs stored as OCI referrers via oras attach).
#
# Same treatment. This previously resolved the version from the GitHub API at
# build time and extracted the tarball unverified, so the binary that fetches
# every SBOM blob was whatever the network handed over.
ARG ORAS_VERSION=1.3.3
RUN set -eux; \
    cd /tmp; \
    curl -fsSLO "https://github.com/oras-project/oras/releases/download/v${ORAS_VERSION}/oras_${ORAS_VERSION}_linux_amd64.tar.gz"; \
    curl -fsSLO "https://github.com/oras-project/oras/releases/download/v${ORAS_VERSION}/oras_${ORAS_VERSION}_checksums.txt"; \
    sha256sum --check --ignore-missing "oras_${ORAS_VERSION}_checksums.txt"; \
    tar -xzf "oras_${ORAS_VERSION}_linux_amd64.tar.gz" oras; \
    install -m 0755 oras /usr/local/bin/oras; \
    rm -f oras "oras_${ORAS_VERSION}_linux_amd64.tar.gz" "oras_${ORAS_VERSION}_checksums.txt"

COPY changelog.py /changelog.py
RUN chmod +x /changelog.py

ENTRYPOINT ["/changelog.py"]
