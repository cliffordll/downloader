"""HLS AES-128 CBC segment decryption."""

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


def parse_iv(value: str) -> bytes:
    """Parse an HLS hexadecimal IV into its 16-byte network representation."""
    text = value.removeprefix('0x').removeprefix('0X')
    try:
        number = int(text, 16)
    except ValueError:
        raise ValueError('AES-128 IV 必须是十六进制数。') from None
    if number < 0 or number >= 1 << 128:
        raise ValueError('AES-128 IV 超出 128 位范围。')
    return number.to_bytes(16, 'big')


def sequence_iv(sequence: int) -> str:
    """HLS implicit IV: the media sequence number encoded as a 128-bit value."""
    if sequence < 0 or sequence >= 1 << 128:
        raise ValueError('M3U8 媒体序号超出 AES-128 IV 范围。')
    return '0x' + sequence.to_bytes(16, 'big').hex()


def decrypt_aes128_segment(ciphertext: bytes, key: bytes, iv: str) -> bytes:
    if len(key) != 16:
        raise ValueError(f'AES-128 密钥长度必须为 16 字节，实际为 {len(key)} 字节。')
    if not ciphertext or len(ciphertext) % 16:
        raise ValueError('AES-128 TS 密文长度无效，必须是 16 字节的整数倍。')
    decryptor = Cipher(algorithms.AES(key), modes.CBC(parse_iv(iv))).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = PKCS7(algorithms.AES.block_size).unpadder()
    try:
        return unpadder.update(padded) + unpadder.finalize()
    except ValueError:
        raise ValueError('AES-128 解密失败：TS 填充无效，请检查密钥和 IV。') from None
