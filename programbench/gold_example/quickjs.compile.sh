#!/bin/bash
set -e
make -j"$(nproc)" qjs
cp qjs executable
