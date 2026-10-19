import unittest

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from src.media.m3u8.aes128 import decrypt_aes128_segment, parse_iv, sequence_iv


class AES128Tests(unittest.TestCase):
    def test_decrypts_aes_cbc_segment_with_pkcs7_padding(self):
        key = bytes(range(16))
        iv = bytes(range(16, 32))
        plaintext = b'\x47' * 188 * 3
        padder = PKCS7(algorithms.AES.block_size).padder()
        padded = padder.update(plaintext) + padder.finalize()
        encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        ciphertext = encryptor.update(padded) + encryptor.finalize()

        self.assertEqual(decrypt_aes128_segment(ciphertext, key, '0x' + iv.hex()), plaintext)

    def test_iv_helpers_use_128_bit_big_endian_values(self):
        self.assertEqual(parse_iv('0x1'), b'\0' * 15 + b'\1')
        self.assertEqual(sequence_iv(256), '0x' + (b'\0' * 14 + b'\1\0').hex())
        with self.assertRaises(ValueError):
            parse_iv('1' * 33)

    def test_rejects_invalid_key_ciphertext_and_padding(self):
        iv = '0x' + '00' * 16
        with self.assertRaisesRegex(ValueError, '密钥长度'):
            decrypt_aes128_segment(b'\0' * 16, b'short', iv)
        with self.assertRaisesRegex(ValueError, '密文长度'):
            decrypt_aes128_segment(b'\0' * 15, b'\0' * 16, iv)
        with self.assertRaisesRegex(ValueError, '填充无效'):
            decrypt_aes128_segment(b'\0' * 16, b'\0' * 16, iv)


if __name__ == '__main__':
    unittest.main()
