#!/bin/bash

# --- Main Download Logic ---
FILE_NAME="livesqlbench-base-lite-dumps.zip"

# Google Drive URL format
FILE="https://drive.usercontent.google.com/download?id=1KABce6czIqL9kMyIX7i-_A0CIQoDnmyW&export=download&authuser=0"

# Download the DB schema
echo "Downloading the DB schema"
curl -L "$FILE" -o "$FILE_NAME"

# Unzip the file
unzip "$FILE_NAME"

# Remove the zipfile after extraction
rm -fr "$FILE_NAME"

# Cloning the dataset repo
echo "Cloning the dataset repo"
git clone https://huggingface.co/datasets/birdsql/bird-interact-lite
