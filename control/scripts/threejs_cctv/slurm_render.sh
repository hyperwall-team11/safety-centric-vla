#!/bin/bash
#SBATCH -p p02
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --time=01:00:00
#SBATCH --job-name=scv-cctv
#SBATCH --output=%x_%A_%a.log
# 프레임을 작업 배열 개수로 나눠 GPU 하나씩 렌더한다
N=300; K=${SLURM_ARRAY_TASK_COUNT:-1}; I=${SLURM_ARRAY_TASK_ID:-0}
A=$(( N * I / K )); B=$(( N * (I + 1) / K ))
[ -n "$FRAMES" ] && A=${FRAMES%:*} && B=${FRAMES#*:}
cd "${CCTV_DIR:-$HOME/cctv_render}"   # data/, assets/, blender_render.py를 올려 둔 폴더
hostname; nvidia-smi --query-gpu=name --format=csv,noheader
"${BLENDER:-$HOME/apps/blender-5.2.3-linux-x64/blender}" -b -P blender_render.py -- --data data --assets assets --out out --frames $A:$B --samples ${SAMPLES:-128} 2>&1 | grep -E "Cycles|프레임|Error|Traceback|rror"
echo JOB_DONE
