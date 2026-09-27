# Sandbox image with the dev toolchains preinstalled, so sandboxes need no apt/installer access.
# Build and load with template/build.sh. Everything here downloads at build time, on the host.
FROM docker/sandbox-templates:claude-code-docker

USER root
# The base image (Ubuntu 26.04) already ships git, node, npm, python3, pip and uv; this adds .NET.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      dotnet-sdk-10.0 \
      python3-venv pipx \
      build-essential git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 \
    DOTNET_NOLOGO=1 \
    NUGET_XMLDOC_MODE=skip
USER agent
