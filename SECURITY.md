# Security Policy

## Reporting Security Issues

If you discover a security vulnerability in the changelog-action, please report it responsibly:

1. **Do not** open a public GitHub issue for security vulnerabilities
2. Use GitHub's private vulnerability reporting: navigate to the **Security** tab, then **Advisories**, and click **"Report a vulnerability"**
3. Or email maintainers with details including: affected versions, impact description, and proof of concept if available

We take security seriously and will:
- Acknowledge receipt within 48 hours
- Provide updates every 7 days or when significant progress is made
- Work with you to understand and remediate the issue
- Credit you in security advisories (unless you prefer anonymity)

## Security Considerations

- Base container image (`fedora@sha256:...`) is digest-pinned for reproducibility and supply-chain integrity
- Critical tools (cosign, oras) are checksum-verified on download against upstream release checksums
- The action runs untrusted attestation verification logic (`cosign verify-attestation`) that should be kept current
- All dependencies in the Dockerfile are explicitly managed and pinned

## Supported Versions

Security updates are applied to the master branch (and main once the rename completes). Users are encouraged to use `@master` or pin to specific commit SHAs for reproducibility.

## Dependency Management

- Cosign and oras releases are tracked by Renovate and security updates are prioritized
- Base image updates are monitored for security patches
- Python dependencies in test environments are managed via pip with explicit versions
