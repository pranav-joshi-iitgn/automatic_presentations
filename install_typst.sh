#!/bin/bash

# Exit immediately if any command fails
set -e

# Define version and filenames
VERSION="v0.14.2"
ARCHIVE="typst-x86_64-unknown-linux-musl.tar.xz"
DIR_NAME="typst-x86_64-unknown-linux-musl"
URL="https://github.com{VERSION}/${ARCHIVE}"

echo "Starting Typst ${VERSION} installation..."

# 1. Clean up any leftover artifacts from prior attempts
rm -f "${ARCHIVE}"
rm -rf "${DIR_NAME}"

# 2. Download the official release archive
echo "Downloading archive from GitHub..."
wget -q --show-progress "${URL}"

# 3. Extract the archive
echo "Extracting files..."
tar -xf "${ARCHIVE}"

# 4. Move binary to the system execution path
echo "Installing binary to /usr/local/bin (requires sudo)..."
sudo cp "${DIR_NAME}/typst" /usr/local/bin/

# 5. Clean up temporary directories and archive files
echo "Cleaning up installer files..."
rm -rf "${DIR_NAME}" "${ARCHIVE}"

# 6. Verify the installation
echo "Checking installed version:"
typst --version

echo "Typst installation completed successfully!"
