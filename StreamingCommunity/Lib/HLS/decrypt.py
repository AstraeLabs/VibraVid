# 03.04.24

import sys
import logging
import importlib.util


# External library
from rich.console import Console


# Cryptodome imports 
from Cryptodome.Cipher import AES
from Cryptodome.Util.Padding import unpad


# Check if Cryptodome module is installed
console = Console()
crypto_spec = importlib.util.find_spec("Cryptodome")
crypto_installed = crypto_spec is not None

if not crypto_installed:
    console.log("[red]pycryptodomex non è installato. Per favore installalo. Leggi readme.md [Requirement].")
    sys.exit(0)
logging.info("[cyan]Decryption use: Cryptodomex")



class M3U8_Decryption:
    def __init__(self, key: bytes, iv: bytes, method: str, pssh: bytes = None) -> None:
        """
        Initialize the M3U8_Decryption object.

        Parameters:
            key (bytes): The encryption key.
            iv (bytes): The initialization vector (IV).
            method (str): The encryption method.
        """
        self.key = key
        self.iv = iv
        if "0x" in str(iv):
            self.iv = bytes.fromhex(iv.replace("0x", ""))
        self.method = method
        self.pssh = pssh

        # Pre-create the cipher based on the encryption method
        if self.method == "AES":
            self.cipher = AES.new(self.key, AES.MODE_ECB)
        elif self.method == "AES-128":
            self.cipher = AES.new(self.key[:16], AES.MODE_CBC, iv=self.iv)
        elif self.method == "AES-128-CTR":
            self.cipher = AES.new(self.key[:16], AES.MODE_CTR, nonce=self.iv)

        message = None
        if self.method is not None:
            message = f"Method: [green]{self.method}"
        if self.key is not None:
            message += f" | Key: [green]{self.key.hex()}"
        if self.iv is not None:
            message += f" | IV: [green]{self.iv.hex()}"
        console.log(f"[cyan]Decryption {message}")

    def decrypt(self, ciphertext: bytes) -> bytes:
        """
        Decrypt the ciphertext using the specified encryption method.

        Parameters:
            ciphertext (bytes): The encrypted content to decrypt.

        Returns:
            bytes: The decrypted content.
        """
        if self.method in {"AES", "AES-128"}:
            decrypted_data = self.cipher.decrypt(ciphertext)
            decrypted_content = unpad(decrypted_data, AES.block_size)
        elif self.method == "AES-128-CTR":
            decrypted_content = self.cipher.decrypt(ciphertext)
        else:
            raise ValueError("Invalid or unsupported method")

        return decrypted_content