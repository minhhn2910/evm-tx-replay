#!/bin/bash

START_BLOCK=${1:?usage: collect_blocks.sh START_BLOCK NUM_BLOCKS [extra main.py args]}
NUM_BLOCKS=${2:?}
shift 2

for ((i = 0; i < NUM_BLOCKS; i++)); do
    python main.py --block "$((START_BLOCK + i))" "$@"
done
