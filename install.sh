#!/usr/bin/env sh
set -eu

REPO="loopsmark/corbit"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/bin}"

main() {
    echo "Installing Corbit..."

    # Detect OS
    OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
    case "$OS" in
        linux)  OS="linux" ;;
        darwin) OS="darwin" ;;
        *)      echo "Unsupported OS: $OS"; exit 1 ;;
    esac

    # Detect architecture
    ARCH="$(uname -m)"
    case "$ARCH" in
        x86_64|amd64)  ARCH="x86_64" ;;
        arm64|aarch64) ARCH="arm64" ;;
        *)             echo "Unsupported architecture: $ARCH"; exit 1 ;;
    esac

    echo "Detected: $OS-$ARCH"

    # Get latest release tag
    if command -v gh >/dev/null 2>&1; then
        VERSION=$(gh api "repos/$REPO/releases/latest" --jq '.tag_name' 2>/dev/null || true)
    fi
    if [ -z "${VERSION:-}" ]; then
        VERSION=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" | grep '"tag_name"' | head -1 | sed 's/.*"tag_name": *"//;s/".*//')
    fi
    if [ -z "${VERSION:-}" ]; then
        echo "Error: Could not determine latest version"
        exit 1
    fi

    echo "Latest version: $VERSION"

    # Build download URL
    BINARY_NAME="corbit-${VERSION#v}-${OS}-${ARCH}"
    DOWNLOAD_URL="https://github.com/$REPO/releases/download/$VERSION/$BINARY_NAME"

    # Create install directory
    mkdir -p "$INSTALL_DIR"

    # Download binary
    echo "Downloading $DOWNLOAD_URL..."
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$DOWNLOAD_URL" -o "$INSTALL_DIR/corbit"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$INSTALL_DIR/corbit" "$DOWNLOAD_URL"
    else
        echo "Error: curl or wget required"
        exit 1
    fi

    chmod +x "$INSTALL_DIR/corbit"

    # Verify installation
    if "$INSTALL_DIR/corbit" version >/dev/null 2>&1; then
        echo ""
        echo "Corbit installed successfully to $INSTALL_DIR/corbit"
    else
        echo ""
        echo "Binary downloaded to $INSTALL_DIR/corbit"
        echo "Warning: Could not verify binary. You may need to check compatibility."
    fi

    # Check PATH
    case ":$PATH:" in
        *":$INSTALL_DIR:"*) ;;
        *)
            echo ""
            echo "Add to your PATH:"
            echo "  export PATH=\"$INSTALL_DIR:\$PATH\""
            ;;
    esac

    echo ""
    echo "Get started:"
    echo "  corbit run --issue <number>"
}

main
