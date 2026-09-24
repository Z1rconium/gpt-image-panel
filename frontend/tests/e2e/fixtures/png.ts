import { deflateSync } from 'node:zlib';

/**
 * Minimal PNG writer for test fixtures: the browser tests need specific pixel
 * sizes (and, for the mask editor's edge work, specific content), which is far
 * cheaper to synthesize than to ship as base64 blobs.
 */

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(buffer: Buffer): number {
  let crc = 0xffffffff;
  for (let index = 0; index < buffer.length; index += 1) {
    crc = CRC_TABLE[(crc ^ buffer[index]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type: string, data: Buffer): Buffer {
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const body = Buffer.concat([Buffer.from(type, 'latin1'), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body), 0);
  return Buffer.concat([length, body, crc]);
}

function encodeRgb(width: number, height: number, shade: (x: number, y: number) => number): Buffer {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 2;
  const raw = Buffer.alloc((1 + width * 3) * height);
  let offset = 0;
  for (let y = 0; y < height; y += 1) {
    raw[offset] = 0;
    offset += 1;
    for (let x = 0; x < width; x += 1) {
      const value = shade(x, y);
      raw[offset] = value;
      raw[offset + 1] = value;
      raw[offset + 2] = value;
      offset += 3;
    }
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', deflateSync(raw)),
    pngChunk('IEND', Buffer.alloc(0))
  ]);
}

/** A solid mid-gray RGB PNG; used to exercise large-image editor sizing. */
export function solidPng(width: number, height: number): Buffer {
  return encodeRgb(width, height, () => 128);
}

/** A light canvas with a dark square in it, for the object-edge tests. */
export function blockPng(
  width: number,
  height: number,
  block: { x: number; y: number; size: number }
): Buffer {
  return encodeRgb(width, height, (x, y) =>
    x >= block.x && x < block.x + block.size && y >= block.y && y < block.y + block.size ? 40 : 220
  );
}

/** A 64x64 RGB PNG with a hard black/white vertical step at x = 32. */
export function twoTonePng(width: number, height: number): Buffer {
  return encodeRgb(width, height, (x) => (x < width / 2 ? 0 : 255));
}

export function blackWhiteMaskPng(width: number, height: number): Buffer {
  return encodeRgb(width, height, (x, y) =>
    x >= width / 4 && x < 3 * width / 4 && y >= height / 4 && y < 3 * height / 4 ? 255 : 0
  );
}

export function opaqueRgbaBlackWhiteMaskPng(width: number, height: number): Buffer {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  const raw = Buffer.alloc((1 + width * 4) * height);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const offset = y * (1 + width * 4) + 1 + x * 4;
      const shade = x >= width / 4 && x < 3 * width / 4 && y >= height / 4 && y < 3 * height / 4 ? 255 : 0;
      raw.fill(shade, offset, offset + 3);
      raw[offset + 3] = 255;
    }
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', deflateSync(raw)),
    pngChunk('IEND', Buffer.alloc(0))
  ]);
}

export function softAlphaMaskPng(width: number, height: number): Buffer {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  const raw = Buffer.alloc((1 + width * 4) * height);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const offset = y * (1 + width * 4) + 1 + x * 4;
      raw[offset + 3] = x < width / 2 ? 64 : 192;
    }
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    pngChunk('IHDR', ihdr),
    pngChunk('IDAT', deflateSync(raw)),
    pngChunk('IEND', Buffer.alloc(0))
  ]);
}
