"""qoder_sign.py —— Qoder COSY 推理签名与请求体编码模块

逆向自 Qoder 桌面/CLI 官方客户端与社区验证过的参考实现，包含三部分：

1. Qoder 自定义 Base64 变体（请求体编码，encoding.go / reference_impl.py 同构）：
     std = standard_base64(plain)
     a   = len(std) // 3
     rearranged = std[n-a:] + std[a:n-a] + std[:a]      # 尾/中/首 三段轮转
     逐字符按自定义字母表映射，'=' -> '$'

2. 纯标准库密码学（Docker alpine / 精简 Python 均可运行，零第三方依赖）：
     - AES-128-CBC（key == iv，PKCS7 填充），S-box 在导入期由 GF(2^8) 自校验生成
     - RSA PKCS#1 v1.5 公钥加密（Qoder 服务端 RSA 公钥硬编码，用于包裹会话 AES key）

3. COSY 会话与 Bearer 签名：
     tempKey(16) 随机会话密钥
     cosyKey     = base64(RSA_PKCS1v15(tempKey))
     info        = base64(AES-CBC(identity_json_sorted_compact, key=iv=tempKey))
     payload     = base64(sorted-compact {cosyVersion, ideVersion, info, requestId, version})
     date        = unix 秒
     sig         = hex(md5(payload + "\\n" + cosyKey + "\\n" + date + "\\n" + body + "\\n" + path))
     Bearer      = "COSY." + payload + "." + sig
     path        = url.path，去掉 "/algo" 前缀

推理端点固定携带 cosy-* 头（machineid/machinetoken/machinetype/date/key/user/...），
x-model-key 决定上游模型路由。会话按账号缓存，access token 轮换后重建。

已知强度风险（只标注、不单方面加固：现状 / 为什么不能改 / 真要改的前提）：
  1. 会话密钥熵偏低：temp_key = uuid.uuid4().hex[:16]（本文件 540 行附近）只有
     16 个十六进制字符 = 64 bit 熵，字符集仅 [0-9a-f]，再整体作为 UTF-8 字节
     充当 AES-128 的 key/iv（key == iv）。名义 AES-128，有效强度约 64 bit。
     不能单方面改：temp_key 的字节序列必须与服务端侧解包结果逐字节一致——服务端
     用官方 RSA 私钥解出 cosy-key，再以同一串字节解 info；长度或编码一变，info
     直接解不开、请求整体失败（无降级路径）。
     真要改的前提：① 先用真实账号做端到端回归（发一条 chat，观察 200 与 SSE 数据）；
     ② 改用 os.urandom(16) 随机字节（熵 64 -> 128 bit，key 长度仍是 16 字节）并验证
     服务端不依赖 ASCII/hex 形态；③ Lead 批准 + 全量账号灰度 + 保留回滚点。
  2. RSA 包裹密钥过短：SERVER_PUB_PEM（本文件 442 行附近）是官方客户端硬编码公钥，
     实测 _RSA_N.bit_length() == 1024、e == 65537，配 PKCS#1 v1.5 (type 2) 加密。
     1024 位低于当代推荐（NIST 建议不低于 2048 位），且 v1.5 不具备 IND-CCA 安全性；
     但本链路密文只上行、客户端侧不暴露解密预言机，实际可攻击面受限。
     不能单方面改：公钥必须与官方私钥配对，客户端换更长密钥在数学上不可能，
     属协议层决策。
     真要改的前提：官方客户端更换公钥（届时替换 SERVER_PUB_PEM 与相应 cosy 版本）。
  上述两条均源自"与官方协议对齐"，不得为"加固"而单方面偏离兼容性；
  详见 .team/doudou.md 风险 R3 与不变量章节。
"""
import base64
import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from urllib.parse import urlparse

from qoder_fingerprint import derive_id, derive_machine_token, derive_machine_type

# ---------------------------------------------------------------------------
# Qoder 自定义 Base64 变体
# ---------------------------------------------------------------------------
QODER_CUSTOM_ALPHABET = "_doRTgHZBKcGVjlvpC,@aFSx#DPuNJme&i*MzLOEn)sUrthbf%Y^w.(kIQyXqWA!"
QODER_STD_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
QODER_PAD = "$"

_STD2CUSTOM = {ord(QODER_STD_ALPHABET[i]): QODER_CUSTOM_ALPHABET[i] for i in range(64)}
_STD2CUSTOM[ord("=")] = QODER_PAD
_CUSTOM2STD = {ord(QODER_CUSTOM_ALPHABET[i]): QODER_STD_ALPHABET[i] for i in range(64)}
_CUSTOM2STD[ord(QODER_PAD)] = "="

assert len(QODER_CUSTOM_ALPHABET) == 64 and len(set(QODER_CUSTOM_ALPHABET)) == 64, "bad alphabet"


def qoder_encode(plain: bytes) -> str:
    """把明文编码成 Qoder 自定义 Base64 变体（出站请求体格式）。"""
    std = base64.b64encode(plain).decode("ascii")
    n = len(std)
    a = n // 3
    rearranged = std[n - a:] + std[a:n - a] + std[:a]
    return rearranged.translate(_STD2CUSTOM)


def qoder_decode(encoded: str) -> bytes:
    """qoder_encode 的逆运算（测试与调试用）。

    正向: R = S[n-a:] + S[a:n-a] + S[:a]   （尾/中/首 三段）
    逆向: S = R3 + R2 + R1
    """
    std = encoded.translate(_CUSTOM2STD)
    n = len(std)
    a = n // 3
    r1, r2, r3 = std[:a], std[a:n - a], std[n - a:]
    original = r3 + r2 + r1
    return base64.b64decode(original.encode("ascii"))


# ---------------------------------------------------------------------------
# 纯标准库 AES-128（加密方向，S-box 导入期由有限域自校验生成）
# ---------------------------------------------------------------------------
def _gmul(a: int, b: int) -> int:
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


def _rotl8(x: int, k: int) -> int:
    return ((x << k) | (x >> (8 - k))) & 0xFF


def _build_sbox() -> list:
    sbox = []
    for i in range(256):
        inv = 0
        if i:
            for x in range(1, 256):
                if _gmul(i, x) == 1:
                    inv = x
                    break
        s = inv ^ _rotl8(inv, 1) ^ _rotl8(inv, 2) ^ _rotl8(inv, 3) ^ _rotl8(inv, 4) ^ 0x63
        sbox.append(s)
    return sbox


_SBOX = _build_sbox()
assert _SBOX[0x53] == 0xED and _SBOX[0x00] == 0x63, "AES S-box self-check failed"

_RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36]


def _expand_key(key: bytes) -> list:
    """AES 密钥扩展 -> (Nr+1) 个 16 字节轮密钥。支持 AES-128 (16B) / AES-256 (32B)。"""
    if len(key) == 16:
        w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
        for i in range(4, 44):
            t = list(w[i - 1])
            if i % 4 == 0:
                t = t[1:] + t[:1]                     # RotWord
                t = [_SBOX[b] for b in t]             # SubWord
                t[0] ^= _RCON[i // 4 - 1]
            w.append([w[i - 4][j] ^ t[j] for j in range(4)])
        n_words = 44
    elif len(key) == 32:
        # FIPS-197 AES-256: Nk=8, 每 8 字轮 Sub+Rcon，第 4 字轮仅 Sub
        w = [list(key[i:i + 4]) for i in range(0, 32, 4)]
        for i in range(8, 60):
            t = list(w[i - 1])
            if i % 8 == 0:
                t = t[1:] + t[:1]
                t = [_SBOX[b] for b in t]
                t[0] ^= _RCON[i // 8 - 1]
            elif i % 8 == 4:
                t = [_SBOX[b] for b in t]
            w.append([w[i - 8][j] ^ t[j] for j in range(4)])
        n_words = 60
    else:
        raise ValueError("aes key must be 16 or 32 bytes, got %d" % len(key))
    round_keys = []
    for r in range(n_words // 4):
        rk = []
        for c in range(4):
            rk.extend(w[r * 4 + c])
        round_keys.append(rk)
    return round_keys


def _encrypt_block(block: bytes, round_keys: list) -> bytes:
    nr = len(round_keys) - 1          # AES-128: 10, AES-256: 14
    state = list(block)
    rk0 = round_keys[0]
    state = [state[i] ^ rk0[i] for i in range(16)]

    def sub_shift(s):
        out = [0] * 16
        # 列优先状态: s[r + 4c]
        for c in range(4):
            for r in range(4):
                out[r + 4 * c] = _SBOX[s[r + 4 * ((c + r) % 4)]]
        return out

    def mix_col(s):
        out = [0] * 16
        for c in range(4):
            a = s[4 * c:4 * c + 4]
            out[4 * c + 0] = _gmul(a[0], 2) ^ _gmul(a[1], 3) ^ a[2] ^ a[3]
            out[4 * c + 1] = a[0] ^ _gmul(a[1], 2) ^ _gmul(a[2], 3) ^ a[3]
            out[4 * c + 2] = a[0] ^ a[1] ^ _gmul(a[2], 2) ^ _gmul(a[3], 3)
            out[4 * c + 3] = _gmul(a[0], 3) ^ a[1] ^ a[2] ^ _gmul(a[3], 2)
        return out

    for rnd in range(1, nr):
        state = sub_shift(state)
        state = mix_col(state)
        rk = round_keys[rnd]
        state = [state[i] ^ rk[i] for i in range(16)]
    state = sub_shift(state)
    rk = round_keys[nr]
    state = [state[i] ^ rk[i] for i in range(16)]
    return bytes(state)


def aes_cbc_encrypt(plain: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-128-CBC 加密（PKCS7 填充）。Qoder info 加密使用 key == iv。"""
    if len(key) != 16 or len(iv) != 16:
        raise ValueError("aes key/iv must be 16 bytes")
    pad = 16 - len(plain) % 16
    data = plain + bytes([pad] * pad)
    round_keys = _expand_key(key)
    out = bytearray()
    prev = iv
    for off in range(0, len(data), 16):
        blk = bytes(a ^ b for a, b in zip(data[off:off + 16], prev))
        enc = _encrypt_block(blk, round_keys)
        out.extend(enc)
        prev = enc
    return bytes(out)


_INV_SBOX = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV_SBOX[_v] = _i


def _xtimes(a: int, n: int) -> int:
    """GF(2^8) 乘以常数 n（InvMixColumns 用 9/11/13/14）。"""
    r = 0
    for _ in range(8):
        if n & 1:
            r ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        n >>= 1
    return r


def _decrypt_block(block: bytes, round_keys: list) -> bytes:
    """AES 单块解密（与本文件的正向实现严格互逆；轮数由密钥长度决定）。

    正向轮结构: SubShift -> MixColumns -> AddRoundKey(r)
    逆向轮结构: InvSubShift -> AddRoundKey(r) -> InvMixColumns
    （每轮内顺序为 SP800-38A 标准逆密码）
    """
    nr = len(round_keys) - 1
    state = [block[i] ^ round_keys[nr][i] for i in range(16)]

    def inv_sub_shift(s):
        # 正向: O[r][c] = S(I[r][(c+r)%4])  ->  逆向: I[r][k] = INV[O[r][(k-r)%4]]
        out = [0] * 16
        for c in range(4):
            for r in range(4):
                out[r + 4 * ((c + r) % 4)] = _INV_SBOX[s[r + 4 * c]]
        return out

    def inv_mix_col(s):
        out = [0] * 16
        for c in range(4):
            a = s[4 * c:4 * c + 4]
            out[4 * c + 0] = (_xtimes(a[0], 14) ^ _xtimes(a[1], 11)
                              ^ _xtimes(a[2], 13) ^ _xtimes(a[3], 9))
            out[4 * c + 1] = (_xtimes(a[0], 9) ^ _xtimes(a[1], 14)
                              ^ _xtimes(a[2], 11) ^ _xtimes(a[3], 13))
            out[4 * c + 2] = (_xtimes(a[0], 13) ^ _xtimes(a[1], 9)
                              ^ _xtimes(a[2], 14) ^ _xtimes(a[3], 11))
            out[4 * c + 3] = (_xtimes(a[0], 11) ^ _xtimes(a[1], 13)
                              ^ _xtimes(a[2], 9) ^ _xtimes(a[3], 14))
        return out

    for rnd in range(nr - 1, 0, -1):
        state = inv_sub_shift(state)
        rk = round_keys[rnd]
        state = [state[i] ^ rk[i] for i in range(16)]
        state = inv_mix_col(state)
    state = inv_sub_shift(state)
    return bytes(state[i] ^ round_keys[0][i] for i in range(16))


def aes_cbc_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-128-CBC 解密 + 严格 PKCS7 校验。用于本地凭证文件读取。"""
    if len(key) != 16 or len(iv) != 16:
        raise ValueError("aes key/iv must be 16 bytes")
    if not data or len(data) % 16:
        raise ValueError("ciphertext length %d not block aligned" % len(data))
    round_keys = _expand_key(key)
    out = bytearray()
    prev = iv
    for off in range(0, len(data), 16):
        blk = data[off:off + 16]
        dec = _decrypt_block(blk, round_keys)
        out.extend(a ^ b for a, b in zip(dec, prev))
        prev = blk
    pad = out[-1]
    if pad < 1 or pad > 16 or bytes(out[-pad:]) != bytes([pad] * pad):
        raise ValueError("bad PKCS7 padding")
    return bytes(out[:-pad])


# ---------------------------------------------------------------------------
# QMC v1 模型目录解密（HKDF-SHA256 + AES-256-GCM，逆向自官方客户端）
#   envelope = base64( "QMC\x01" + nonce[12] + ciphertext||tag )
#   key      = HKDF(ikm=uid, salt="qoder-model-cache-enc",
#                   info="model-cache-v1", L=32)
# ---------------------------------------------------------------------------
QMC_MAGIC = b"QMC\x01"


def _hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    """RFC 5869 HKDF。"""
    if not salt:
        salt = b"\x00" * 32
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    out, t, i = b"", b"", 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        out += t
        i += 1
    return out[:length]


def _gf128_mul(x: int, y: int) -> int:
    """GHASH 域乘法（SP800-38D）。"""
    z, v = 0, y
    for i in range(127, -1, -1):
        if (x >> i) & 1:
            z ^= v
        if v & 1:
            v = (v >> 1) ^ 0xE1000000000000000000000000000000
        else:
            v >>= 1
    return z


def _aes_ecb_block(block: bytes, round_keys: list) -> bytes:
    return _encrypt_block(block, round_keys)


def qmc_decrypt(blob: str, uid: str) -> bytes:
    """解密 QMC v1 模型目录（官方 .models/<uid>/catalog-v6）。"""
    envelope = base64.b64decode(blob.strip())
    if len(envelope) < 4 + 12 + 16 or envelope[:4] != QMC_MAGIC:
        raise ValueError("model cache envelope magic/version unsupported")
    nonce = envelope[4:16]
    sealed = envelope[16:]
    ct, tag = sealed[:-16], sealed[-16:]
    key = _hkdf_sha256(uid.encode("utf-8"),
                       b"qoder-model-cache-enc", b"model-cache-v1", 32)
    rks = _expand_key(key)
    # CTR：J0 = nonce||1，密钥流从 inc32(J0) 开始（对 128 位块的低 32 位递增）
    base = nonce + b"\x00\x00\x00\x01"
    high = int.from_bytes(base, "big") & ~0xFFFFFFFF
    low = (int.from_bytes(base[-4:], "big") + 1) & 0xFFFFFFFF
    out = bytearray()
    for off in range(0, len(ct), 16):
        ks = _aes_ecb_block((high | low).to_bytes(16, "big"), rks)
        chunk = ct[off:off + 16]
        out.extend(a ^ b for a, b in zip(chunk, ks))
        low = (low + 1) & 0xFFFFFFFF
    # GHASH 校验 tag
    h = _aes_ecb_block(b"\x00" * 16, rks)
    y = 0
    hh = int.from_bytes(h, "big")
    for off in range(0, len(ct), 16):
        blk = ct[off:off + 16].ljust(16, b"\x00")
        y = _gf128_mul(y ^ int.from_bytes(blk, "big"), hh)
    lens = (0 << 64) | (len(ct) * 8)
    y = _gf128_mul(y ^ lens, hh)
    j0_enc = _aes_ecb_block((nonce + b"\x00\x00\x00\x01"), rks)
    expect = bytes(a ^ b for a, b in zip(y.to_bytes(16, "big"), j0_enc))
    if not hmac.compare_digest(expect, tag):
        raise ValueError("model cache authentication failed (bad uid?)")
    return bytes(out)


def aes_gcm_decrypt(key: bytes, nonce: bytes, sealed: bytes) -> bytes:
    """AES-GCM 解密（key 支持 16/24/32 字节），带 tag 严格校验。

    用于桌面端 auth.v1.dat（Chromium os_crypt "v10" 布局）。
    """
    if len(sealed) < 16:
        raise ValueError("gcm payload too short")
    if len(nonce) != 12:
        raise ValueError("gcm nonce must be 12 bytes")
    if len(key) not in (16, 24, 32):
        raise ValueError("gcm key must be 16/24/32 bytes")
    if len(key) == 24:
        raise ValueError("AES-192 not supported by the pure implementation")
    ct, tag = sealed[:-16], sealed[-16:]
    rks = _expand_key(key)
    base = nonce + b"\x00\x00\x00\x01"           # J0 = nonce||1
    high = int.from_bytes(base, "big") & ~0xFFFFFFFF
    low = (int.from_bytes(base[-4:], "big") + 1) & 0xFFFFFFFF
    out = bytearray()
    for off in range(0, len(ct), 16):
        ks = _aes_ecb_block((high | low).to_bytes(16, "big"), rks)
        chunk = ct[off:off + 16]
        out.extend(a ^ b for a, b in zip(chunk, ks))
        low = (low + 1) & 0xFFFFFFFF
    h = _aes_ecb_block(b"\x00" * 16, rks)
    y, hh = 0, int.from_bytes(h, "big")
    for off in range(0, len(ct), 16):
        blk = ct[off:off + 16].ljust(16, b"\x00")
        y = _gf128_mul(y ^ int.from_bytes(blk, "big"), hh)
    y = _gf128_mul(y ^ (len(ct) * 8), hh)        # lenA=0
    j0_enc = _aes_ecb_block(base, rks)
    expect = bytes(a ^ b for a, b in zip(y.to_bytes(16, "big"), j0_enc))
    if not hmac.compare_digest(expect, tag):
        raise ValueError("GCM authentication failed")
    return bytes(out)


def dpapi_unprotect(data: bytes) -> bytes:
    """Windows DPAPI CryptUnprotectData（读取 os_crypt 保护的对称密钥用）。"""
    if os.name != "nt":
        raise OSError("DPAPI only available on Windows")
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_byte))]

    crypt32 = ctypes.windll.crypt32
    fun = crypt32.CryptUnprotectData
    fun.argtypes = [ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR,
                    ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
                    wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    fun.restype = wintypes.BOOL
    in_blob = DATA_BLOB(len(data),
                        (ctypes.c_byte * len(data)).from_buffer_copy(data))
    out_blob = DATA_BLOB()
    if not fun(ctypes.byref(in_blob), None, None, None, None, 0,
               ctypes.byref(out_blob)):
        raise OSError("CryptUnprotectData failed: %d" % ctypes.get_last_error())
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def chromium_decrypt_v10(blob: bytes, key: bytes) -> bytes:
    """解 Chromium/Electron os_crypt "v10" 载荷：v10 + nonce12 + AES-256-GCM。

    `key` 为从 Local State 的 os_crypt.encrypted_key 解出（DPAPI 前缀剥离 +
    dpapi_unprotect）的 32 字节对称密钥。
    """
    if blob[:3] != b"v10":
        raise ValueError("not a v10 payload")
    return aes_gcm_decrypt(key, blob[3:15], blob[15:])


# ---------------------------------------------------------------------------
# RSA PKCS#1 v1.5 公钥加密（Qoder 服务端公钥，硬编码自官方客户端）
# ---------------------------------------------------------------------------
SERVER_PUB_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDA8iMH5c02LilrsERw9t6Pv5Nc
4k6Pz1EaDicBMpdpxKduSZu5OANqUq8er4GM95omAGIOPOh+Nx0spthYA2BqGz+l
6HRkPJ7S236FZz73In/KVuLnwI8JJ2CbuJap8kvheCCZpmAWpb/cPx/3Vr/J6I17
XcW+ML9FoCI6AOvOzwIDAQAB
-----END PUBLIC KEY-----"""


def _parse_rsa_pub(pem: str):
    """从 SPKI PEM 解出 (n, e)。只走公钥结构，纯标准库。"""
    body = "".join(l for l in pem.splitlines() if "BEGIN" not in l and "END" not in l)
    der = base64.b64decode(body)

    def read_tlv(buf, i):
        tag = buf[i]
        ln = buf[i + 1]
        j = i + 2
        if ln & 0x80:
            n = ln & 0x7F
            ln = int.from_bytes(buf[j:j + n], "big")
            j += n
        return tag, buf[j:j + ln], j + ln

    # SubjectPublicKeyInfo ::= SEQUENCE { AlgorithmIdentifier, BIT STRING }
    tag, seq, _ = read_tlv(der, 0)
    i = 0
    tag, alg, i = read_tlv(seq, i)          # AlgorithmIdentifier
    tag, bits, i = read_tlv(seq, i)         # BIT STRING
    bits = bits[1:]                          # 去掉 unused-bits 字节
    tag, rsaseq, _ = read_tlv(bits, 0)      # RSAPublicKey ::= SEQUENCE { n, e }
    j = 0
    tag, nbuf, j = read_tlv(rsaseq, j)
    tag, ebuf, j = read_tlv(rsaseq, j)
    return int.from_bytes(nbuf, "big"), int.from_bytes(ebuf, "big")


_RSA_N, _RSA_E = _parse_rsa_pub(SERVER_PUB_PEM)


def rsa_pkcs1v15_encrypt(plain: bytes, n: int = None, e: int = None) -> bytes:
    """RSA PKCS#1 v1.5 (type 2) 加密。公钥运算，纯 Python 模幂。"""
    n = n or _RSA_N
    e = e or _RSA_E
    k = (n.bit_length() + 7) // 8
    if len(plain) > k - 11:
        raise ValueError("message too long for RSA block")
    ps_len = k - 3 - len(plain)
    ps = bytearray()
    while len(ps) < ps_len:
        b = os.urandom(ps_len - len(ps))
        ps.extend(x for x in b if x != 0)
    em = b"\x00\x02" + bytes(ps[:ps_len]) + b"\x00" + plain
    m = int.from_bytes(em, "big")
    c = pow(m, e, n)
    return c.to_bytes(k, "big")


# ---------------------------------------------------------------------------
# COSY 会话与签名
# ---------------------------------------------------------------------------
# COSY 协议版本：官方 0.4.3 客户端（qoder-agent-sdk / qoder-cn-agent-sdk）
# 内置常量为 "1.1.64"（SDK 里 `s5="1.1.64"`，cosy-version 头与 payload 共用），
# 旧值 "0.1.43" 来自更早的 CLI 版本。实测 1.1.64 可用于模型列表与推理。
COSY_VERSION = "1.1.64"
DEFAULT_USER_TYPE = "personal_professional_trial"


def json_sorted_compact(mapping: dict) -> bytes:
    """键排序 + 无空白的紧凑 JSON（服务端签名字节与此强绑定）。"""
    parts = []
    for k in sorted(mapping.keys()):
        parts.append(json.dumps(str(k), ensure_ascii=False) + ":" +
                     json.dumps(mapping[k] if mapping[k] is not None else "",
                                ensure_ascii=False))
    return ("{" + ",".join(parts) + "}").encode("utf-8")


class CosySession(object):
    """单账号的 COSY 签名会话。

    machine 系列字段按账号 UID 稳定派生（同一账号永远来自同一台虚拟设备）；
    tempKey/cosyKey/info 在会话创建时生成，access token 轮换后整体重建。
    """

    def __init__(self, uid, nickname="", access_token="", refresh_token="",
                 user_type=DEFAULT_USER_TYPE, org_id="", org_name=""):
        self.uid = uid or ""
        # 稳定设备指纹（防多号关联 / 防机器码漂移）
        self.machine_id = derive_id(self.uid, "machine")
        self.machine_token = derive_machine_token(self.uid)
        self.machine_type = derive_machine_type(self.uid)
        self.access_token = access_token or ""

        self.temp_key = uuid.uuid4().hex[:16]                 # 16 ASCII -> AES-128
        self.cosy_key = base64.b64encode(
            rsa_pkcs1v15_encrypt(self.temp_key.encode("utf-8"))
        ).decode("ascii")
        # 身份体 9 键（唯一真源）：此前另有模块级 _IDENTITY_KEYS 常量副本，
        # 全仓零引用且存在双源漂移风险，已在本轮清理；增删键必须同步服务端预期。
        identity = {
            "name": nickname or "",
            "aid": self.uid,
            "uid": self.uid,
            "yx_uid": "",
            "organization_id": org_id or "",
            "organization_name": org_name or "",
            "user_type": user_type or DEFAULT_USER_TYPE,
            "security_oauth_token": access_token or "",
            "refresh_token": refresh_token or "",
        }
        plain = json_sorted_compact(identity)
        self.info = base64.b64encode(
            aes_cbc_encrypt(plain, self.temp_key.encode("utf-8"),
                            self.temp_key.encode("utf-8"))
        ).decode("ascii")

    def bearer(self, body: str, raw_url: str):
        """返回 (payload_b64, date, bearer)。签名覆盖 body 与去 /algo 的 path。"""
        path = urlparse(raw_url).path or "/"
        if path.startswith("/algo"):
            path = path[len("/algo"):]
        payload = {
            "cosyVersion": COSY_VERSION,
            "ideVersion": "",
            "info": self.info,
            "requestId": str(uuid.uuid4()),
            "version": "v1",
        }
        payload_b64 = base64.b64encode(json_sorted_compact(payload)).decode("ascii")
        date = str(int(time.time()))
        raw = "\n".join([payload_b64, self.cosy_key, date, body, path])
        sig = hashlib.md5(raw.encode("utf-8")).hexdigest()
        return payload_b64, date, "Bearer COSY." + payload_b64 + "." + sig

    def headers(self, body: str, raw_url: str, model_key: str = "",
                sse: bool = True, accept: str = "text/event-stream") -> dict:
        """构造一次推理/模型接口的完整 COSY 签名头。"""
        _, date, bearer = self.bearer(body, raw_url)
        h = {
            "cosy-data-policy": "AGREE",
            "content-type": "application/json",
            "cosy-machinetype": self.machine_type,
            "cosy-clienttype": "5",
            "cosy-date": date,
            "cosy-user": self.uid,
            "cosy-key": self.cosy_key,
            "accept": accept,
            "cosy-clientip": "169.254.198.161",
            "authorization": bearer,
            "accept-encoding": "identity",
            "cosy-version": COSY_VERSION,
            "cosy-machineid": self.machine_id,
            "cosy-machinetoken": self.machine_token,
            "login-version": "v2",
            "user-agent": "Go-http-client/2.0",
        }
        if sse:
            h["cache-control"] = "no-cache"
        if model_key:
            h["x-model-key"] = model_key
            h["x-model-source"] = "system"
        return h


class CosySessionCache(object):
    """账号 UID -> CosySession 的进程级缓存；access token 变化即失效。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries = {}

    def get(self, account):
        token = getattr(account, "access_token", "") or ""
        if not token:
            raise ValueError("cosy: empty access token")
        key = account.uid or token[:16]
        with self._lock:
            hit = self._entries.get(key)
            if hit and hit[0] == token:
                return hit[1]
        sess = CosySession(
            uid=account.uid,
            nickname=getattr(account, "nickname", ""),
            access_token=token,
            refresh_token=getattr(account, "refresh_token", ""),
            user_type=getattr(account, "user_type", "") or DEFAULT_USER_TYPE,
            org_id=getattr(account, "organization_id", ""),
            org_name=getattr(account, "organization_name", ""),
        )
        with self._lock:
            self._entries[key] = (token, sess)
        return sess

    def invalidate(self, uid):
        with self._lock:
            self._entries.pop(uid, None)


SESSIONS = CosySessionCache()
