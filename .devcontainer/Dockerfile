FROM ghcr.io/astral-sh/uv:0.12.13@sha256:b485bd65cc2cf1c9a93b3554012c9c3778cf7b1b5fd3d3096ce9e1226c97e1e6 AS uv
FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04
COPY --from=uv /uv /uvx /usr/local/bin/
