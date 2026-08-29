#!/bin/bash
set -e

# Setup directories
mkdir -p ~/jdk
mkdir -p ~/Android/Sdk/cmdline-tools

# Download OpenJDK 17
if [ ! -d ~/jdk/jdk-17.0.12+7 ]; then
    echo "Downloading OpenJDK 17..."
    wget -qO ~/jdk/jdk-17.tar.gz https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.12%2B7/OpenJDK17U-jdk_x64_linux_hotspot_17.0.12_7.tar.gz
    tar -xf ~/jdk/jdk-17.tar.gz -C ~/jdk
    rm ~/jdk/jdk-17.tar.gz
fi

export JAVA_HOME=$HOME/jdk/jdk-17.0.12+7
export PATH=$JAVA_HOME/bin:$PATH

# Download Android SDK command line tools
if [ ! -d ~/Android/Sdk/cmdline-tools/latest ]; then
    echo "Downloading Android Cmdline tools..."
    wget -qO ~/Android/cmdline-tools.zip https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
    unzip -q ~/Android/cmdline-tools.zip -d ~/Android/Sdk/cmdline-tools
    mv ~/Android/Sdk/cmdline-tools/cmdline-tools ~/Android/Sdk/cmdline-tools/latest
    rm ~/Android/cmdline-tools.zip
fi

export ANDROID_HOME=$HOME/Android/Sdk
export PATH=$ANDROID_HOME/cmdline-tools/latest/bin:$PATH

# Accept licenses and install platform tools
echo "Accepting licenses and installing SDK..."
yes | sdkmanager --licenses > /dev/null 2>&1 || true
sdkmanager "platform-tools" "platforms;android-34" "build-tools;34.0.0" > /dev/null

echo "Setup complete."
