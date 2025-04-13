#!/bin/bash

set -e

BASE_DIR=$(pwd)
WORK_DIR=$BASE_DIR/temp

FIRMWARE=$(basename $(ls $BASE_DIR/assets/*.bin 2>/dev/null | head -n 1) .bin)

cd $WORK_DIR

if [ ! -f "$BASE_DIR/assets/$FIRMWARE.bin" ]; then
    echo "❌ 固件文件不存在，请先下载固件到：$BASE_DIR/assets/"
    exit 1
fi

if [ ! -d "$FIRMWARE" ]; then
    echo "❌ 解压后的固件文件夹不存在，请先提取固件"
    exit 1
fi

SQUASHFS_INFO=$(file $FIRMWARE/root.squashfs)
echo "🚗 原始固件信息: $SQUASHFS_INFO"

COMPRESSION=$(echo "$SQUASHFS_INFO" | grep -o "xz\|gzip\|lzo\|lz4\|zstd compressed" | cut -d' ' -f1)
BLOCKSIZE=$(echo "$SQUASHFS_INFO" | grep -o "blocksize: [0-9]* bytes" | cut -d' ' -f2)

echo "🔥 使用原始参数重新打包固件..."
mksquashfs squashfs-root $FIRMWARE/root-patched.squashfs \
    -comp $COMPRESSION -b $BLOCKSIZE \
    -noappend -all-root -always-use-fragments -no-xattrs -no-exports


# 校验固件大小上限
MODEL=$(cat $BASE_DIR/assets/.model)
IMAGE_MAX_SIZE=0
if [ "$MODEL" = "OH2P" ]; then
    IMAGE_MAX_SIZE=$((0x02800000))
elif [ "$MODEL" = "LX06" ]; then
    IMAGE_MAX_SIZE=$((0x02800000))
fi

SIZE=`stat -L -c %s $FIRMWARE/root-patched.squashfs`
echo "📊 当前固件大小: $SIZE 字节"
if [ "$SIZE" -ge "$IMAGE_MAX_SIZE" ]; then
    echo "❌ 固件大小超过允许的最大值：$IMAGE_MAX_SIZE 字节"
    exit 1
fi

echo "✅ 固件大小检查通过，剩余空间: $((IMAGE_MAX_SIZE - SIZE)) 字节"

cp -rf $FIRMWARE $BASE_DIR/assets/$FIRMWARE

echo "✅ 打包完成，固件文件已复制到 assets 目录..."
echo $BASE_DIR/assets/$FIRMWARE/root-patched.squashfs
