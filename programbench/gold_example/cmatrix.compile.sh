#!/bin/bash
set -e
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j"$(nproc)"
cp build/cmatrix executable
