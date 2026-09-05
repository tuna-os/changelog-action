# Changelog Action Observability Assessment & Stack Guidelines

## Overview

`changelog-action` is a custom GitHub Action written in Python (`changelog.py`) designed to automate changelog generation, commit parsing, release section categorization, and pull request metadata extraction within CI/CD pipelines.

This document outlines the observability posture of `changelog-action`, details environment variables for debugging GitHub Action runs, specifies data privacy boundaries, and provides guidelines for future CI telemetry integrations.

---

## 1. Existing Logging & Diagnostic Subsystem

### 1.1 Logging Facilities
- **GitHub Action Workflow Logging**: Log output is emitted via Python standard output and standard error streams, which are captured directly by the GitHub Actions runner.
- **Workflow Command Annotations**: Output formatting uses GitHub Actions workflow commands (e.g. `::error::`, `::warning::`, `::notice::`) for inline UI annotation in PR and workflow run summaries.

### 1.2 Diagnostic Environment Variables
Execution verbosity and workflow diagnostics can be enabled via standard GitHub Action and Python environment flags:

- **`ACTIONS_STEP_DEBUG`**: Set to `true` in repository secrets or workflow inputs to enable step debug logging output.
- **`ACTIONS_RUNNER_DEBUG`**: Set to `true` to display verbose GitHub Actions runner diagnostic logs.
- **`PYTHONVERBOSE`**: Enables detailed Python module loading and traceback output during action execution.

---

## 2. Telemetry & Data Flow Boundaries

In compliance with operator telemetry policies:
- **No Remote Telemetry Exporters**: No telemetry, analytics, or metric data is exported to third-party endpoints or external services.
- **Local CI Execution Containment**: Diagnostic logs remain strictly contained within the executing GitHub Actions runner environment and workflow run logs.
- **Credential Protection**: GitHub tokens (`GITHUB_TOKEN`), API keys, and repository secrets MUST NOT be written to log outputs or error messages.

---

## 3. Future OpenTelemetry Roadmap & Stack Guidelines

If an operator configures CI/CD telemetry collection (such as OpenTelemetry Collector or GitHub Actions trace exporters) in a future release:

1. **GitHub Actions OpenTelemetry Integration**:
   - Trace changelog parsing steps (git log extraction, release note compilation, file writing) as discrete spans in CI job traces.
2. **Structured Logging Standard**:
   - Utilize standard Python `logging` module with structured JSON formatting behind an opt-in flag.
3. **Explicit Opt-in Configuration**:
   - Any external exporter or telemetry collection backend must remain opt-in and require explicit workflow input parameters.
