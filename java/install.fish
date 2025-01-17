#!/usr/bin/env fish

set JAVA_VERSION 21
set JVM_HOME /Library/Java/JavaVirtualMachines
set JDK_HOME "$JVM_HOME/openjdk-$JAVA_VERSION.jdk"

brew install "openjdk@$JAVA_VERSION"
sudo ln -sfn "/opt/homebrew/opt/openjdk@$JAVA_VERSION/libexec/openjdk.jdk" $JDK_HOME
set -Ux JAVA_HOME "$JDK_HOME/Contents/Home"
