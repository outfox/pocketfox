# pocketfox: a clever personal AI assistant container
# Based on pocketfox by outfox
#
# Build: docker build -t pocketfox .
# Run:   docker compose up -d

# ── Go builder: gogcli + sag ──────────────────────────────────────────
FROM golang:1.26 AS go-builder

# Build gogcli (Google Suite CLI)
RUN git clone https://github.com/steipete/gogcli.git /gogcli && \
    cd /gogcli && git checkout v0.11.0 && make

# Build sag (ElevenLabs TTS CLI) - needs CGO for ALSA
RUN apt-get update && apt-get install -y --no-install-recommends \
    libasound2-dev pkg-config && rm -rf /var/lib/apt/lists/*
ENV CGO_ENABLED=1
RUN go install github.com/steipete/sag/cmd/sag@latest


# ── AWS CLI v2 builder ──────────────────────────────────────────────
FROM python:3.12-slim AS aws-builder
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip && \
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip && \
    unzip -q /tmp/awscliv2.zip -d /tmp && \
    /tmp/aws/install && \
    rm -rf /tmp/awscliv2.zip /tmp/aws


# ── Final image ───────────────────────────────────────────────────────
FROM python:3.12-slim AS final

# Metadata
LABEL maintainer="Tiger Jove <tiger@tiger.pocketfox>"
LABEL description="pocketfox, an autonomous artificial agent"
LABEL version="0.5.0"

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# System dependencies
# - ffmpeg: audio processing for voice messages
# - libasound2: runtime ALSA lib for sag (TTS)
# - git: for potential skill installations
# - curl: for API calls and downloads
# - wget: for downloading binaries
# - bubblewrap: sandbox isolation for exec tool
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ffmpeg \
    libasound2 \
    curl \
    wget \
    ssh \
    gh \
    keepassxc \
    trash-cli \
    ca-certificates \
    bubblewrap \
    && rm -rf /var/lib/apt/lists/*

# Copy Go-built binaries from builder
COPY --from=go-builder /gogcli/bin/gog /usr/local/bin/gog
COPY --from=go-builder /go/bin/sag /usr/local/bin/sag

# Copy AWS CLI v2 from builder
COPY --from=aws-builder /usr/local/aws-cli/ /usr/local/aws-cli/
COPY --from=aws-builder /usr/local/bin/aws /usr/local/bin/aws

# Install uv (Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Node.js for Claude Code CLI (Claude Max subscription support)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Install ImageMagick 7 (AppImage extracted)
RUN wget https://imagemagick.org/archive/binaries/magick -O /tmp/magick.appimage && \
    chmod +x /tmp/magick.appimage && \
    cd /tmp && ./magick.appimage --appimage-extract && \
    mv squashfs-root /opt/imagemagick && \
    ln -s /opt/imagemagick/usr/bin/magick /usr/local/bin/magick && \
    rm /tmp/magick.appimage

# Install Nushell (pinned version)
ENV NUSHELL_VERSION=0.102.0
RUN wget https://github.com/nushell/nushell/releases/download/${NUSHELL_VERSION}/nu-${NUSHELL_VERSION}-x86_64-unknown-linux-musl.tar.gz -O /tmp/nushell.tar.gz && \
    tar -xzf /tmp/nushell.tar.gz -C /tmp && \
    cp /tmp/nu-${NUSHELL_VERSION}-x86_64-unknown-linux-musl/nu /usr/local/bin/ && \
    rm -rf /tmp/nushell.tar.gz /tmp/nu-*

# Create non-root user
RUN useradd -m -u 1000 -s /bin/bash pocketfox

# Create directory structure for sandbox isolation:
# - ~/.pocketfox/workspace: visible in sandbox as /workspace (read-write)
# - ~/.pocketfox/prompt: NOT visible in sandbox (MEMORY.md, SOUL.md, etc.)
# - ~/.config: NOT visible in sandbox (credentials like gog tokens)
# When exec runs with sandbox_dir configured, bwrap isolates commands to only
# see /workspace, preventing access to credentials and prompt files.
RUN mkdir -p /home/pocketfox/.pocketfox/workspace \
             /home/pocketfox/.pocketfox/prompt \
             /home/pocketfox/.config \
             /home/pocketfox/.claude \
             /home/pocketfox/.ssh && \
    chmod 700 /home/pocketfox/.ssh && \
    chown -R pocketfox:pocketfox /home/pocketfox

# SSH known_hosts for root (for git clone during build)
RUN mkdir -p /root/.ssh && \
    ssh-keyscan github.com >> /root/.ssh/known_hosts

# Copy pocketfox from local source (this repo)
COPY . /root/pocketfox

ARG CACHE_BUST=0

RUN --mount=type=ssh \
    git clone --depth 1 git@github.com:outfox/loom /root/loom

RUN --mount=type=ssh \
    git clone --depth 1 git@github.com:outfox/any2any /root/any2any

# Install loom first (dependency), then pocketfox from local source
WORKDIR /root/loom
RUN uv pip install --system --no-cache .

# Install convert-all for file format conversions (e.g. docx to markdown)
WORKDIR /root/any2any
RUN uv pip install --system --no-cache .


# Finally, the main agent code
WORKDIR /root/pocketfox
RUN uv pip install --system --no-cache .

# General environments
ENV CLAUDE_CODE_USE_BEDROCK=0
ENV GOG_KEYRING_BACKEND="file"

# Default port for pocketfox gateway
EXPOSE 18790

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD pocketfox status || exit 1

# Switch to non-root user for runtime
ARG AGENT_NAME=pocketfox

USER ${AGENT_NAME}
ENV HOME=/home/${AGENT_NAME}
ENV AGENT_NAME=${AGENT_NAME}
ENV PATH="/home/${AGENT_NAME}/.${AGENT_NAME}/workspace/scripts:/home/${AGENT_NAME}/.local/bin:/home/${AGENT_NAME}/.cargo/bin:${PATH}"
WORKDIR /home/pocketfox

RUN git config --global user.name "Blue Duval" && \
    git config --global user.email "blue@tiger.blue"

# GitHub known_hosts for pocketfox user
RUN ssh-keyscan github.com >> /home/pocketfox/.ssh/known_hosts

# Default command: run the gateway
CMD ["pocketfox", "gateway"]