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
FROM python:3.13-slim AS aws-builder
RUN apt-get update && apt-get install -y --no-install-recommends curl unzip && \
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip && \
    unzip -q /tmp/awscliv2.zip -d /tmp && \
    /tmp/aws/install && \
    rm -rf /tmp/awscliv2.zip /tmp/aws


# ── Final image ───────────────────────────────────────────────────────
FROM python:3.13-slim AS final

# Main parameters
ARG AGENT_NAME=pocketfox

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
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# TZ is intentionally NOT set at build time — inject via docker-compose.yml or
# host environment at runtime (e.g. TZ=Europe/Berlin in .env).
# tzdata is installed above so /usr/share/zoneinfo/* is available for zoneinfo.
# Python, croniter, and modern tools read TZ from the environment directly.

# Copy Go-built binaries from builder
COPY --from=go-builder /gogcli/bin/gog /usr/local/bin/gog
COPY --from=go-builder /go/bin/sag /usr/local/bin/sag

# Copy AWS CLI v2 from builder
COPY --from=aws-builder /usr/local/aws-cli/ /usr/local/aws-cli/
COPY --from=aws-builder /usr/local/bin/aws /usr/local/bin/aws

# Install uv (Python package manager)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Node.js 24 LTS — required for qmd and Claude Code CLI
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# qmd — hybrid semantic search engine for agent memory
# Models (~2GB) are downloaded on first use to ~/.cache/qmd/models/
# build-essential is required to compile better-sqlite3 (native addon);
# installed BEFORE npm install, purged afterwards to keep the image lean.
RUN <<EOF
apt-get update
apt-get install -y --no-install-recommends build-essential
npm install -g @tobilu/qmd
apt-get purge -y --auto-remove build-essential
rm -rf /var/lib/apt/lists/*
EOF

# Install ImageMagick 7 (AppImage extracted)
RUN wget https://imagemagick.org/archive/binaries/magick -O /tmp/magick.appimage && \
    chmod +x /tmp/magick.appimage && \
    cd /tmp && ./magick.appimage --appimage-extract && \
    mv squashfs-root /opt/imagemagick && \
    ln -s /opt/imagemagick/usr/bin/magick /usr/local/bin/magick && \
    rm /tmp/magick.appimage

# Install supercronic (container-friendly cron)
ENV SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.41/supercronic-linux-amd64 \
    SUPERCRONIC_SHA1SUM=f70ad28d0d739a96dc9e2087ae370c257e79b8d7 \
    SUPERCRONIC=supercronic-linux-amd64
RUN curl -fsSLO "$SUPERCRONIC_URL" \
    && echo "${SUPERCRONIC_SHA1SUM} ${SUPERCRONIC}" | sha1sum -c - \
    && chmod +x "$SUPERCRONIC" \
    && mv "$SUPERCRONIC" "/usr/local/bin/${SUPERCRONIC}" \
    && ln -s "/usr/local/bin/${SUPERCRONIC}" /usr/local/bin/supercronic

# Install Nushell (pinned version)
ENV NUSHELL_VERSION=0.102.0
RUN wget https://github.com/nushell/nushell/releases/download/${NUSHELL_VERSION}/nu-${NUSHELL_VERSION}-x86_64-unknown-linux-musl.tar.gz -O /tmp/nushell.tar.gz && \
    tar -xzf /tmp/nushell.tar.gz -C /tmp && \
    cp /tmp/nu-${NUSHELL_VERSION}-x86_64-unknown-linux-musl/nu /usr/local/bin/ && \
    rm -rf /tmp/nushell.tar.gz /tmp/nu-*

# Create non-root user
RUN useradd -m -u 1000 -s /bin/bash ${AGENT_NAME}

# Create directory structure for sandbox isolation:
# - ~/.pocketfox/workspace: visible in sandbox as /workspace (read-write)
# - ~/.pocketfox/prompt: NOT visible in sandbox (MEMORY.md, SOUL.md, etc.)
# - ~/.config: NOT visible in sandbox (credentials like gog tokens)
# When exec runs with sandbox_dir configured, bwrap isolates commands to only
# see /workspace, preventing access to credentials and prompt files.
RUN mkdir -p /home/${AGENT_NAME}/workspace \
             /home/${AGENT_NAME}/.config \
             /home/${AGENT_NAME}/.claude \
             /home/${AGENT_NAME}/.ssh && \
    chmod 700 /home/${AGENT_NAME}/.ssh && \
    chown -R ${AGENT_NAME}:${AGENT_NAME} /home/${AGENT_NAME}

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
ENV HOME=/home/${AGENT_NAME}
ENV AGENT_NAME=${AGENT_NAME}
ENV PATH="/home/${AGENT_NAME}/workspace/scripts:/home/${AGENT_NAME}/.local/bin:/home/${AGENT_NAME}/.cargo/bin:${PATH}"
WORKDIR /home/${AGENT_NAME}

USER ${AGENT_NAME}

RUN git config --global user.name "Blue Duval" && \
    git config --global user.email "blue@tiger.blue" && \
    ssh-keyscan github.com >> /home/${AGENT_NAME}/.ssh/known_hosts

# Scheduled tasks (supercronic crontab)
RUN mkdir -p /home/${AGENT_NAME}/.config/pocketfox
COPY --chown=${AGENT_NAME}:${AGENT_NAME} crontab /home/${AGENT_NAME}/.config/pocketfox/crontab

# Entrypoint: supercronic + pocketfox gateway
COPY --chown=${AGENT_NAME}:${AGENT_NAME} entrypoint.sh /home/${AGENT_NAME}/entrypoint.sh
RUN chmod +x /home/${AGENT_NAME}/entrypoint.sh

CMD ["/bin/bash", "-c", "exec ${HOME}/entrypoint.sh"]
