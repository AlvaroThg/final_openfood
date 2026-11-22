"""Claves RSA de prueba, generadas una vez por sesión (generarlas tarda ~100 ms)."""

from __future__ import annotations

import functools

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@functools.cache
def rsa_private_pem(bits: int = 2048, label: str = "default") -> bytes:
    """`label` permite pedir claves distintas del mismo tamaño (p. ej. la de un atacante)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def public_pem(private_pem: bytes) -> bytes:
    key = serialization.load_pem_private_key(private_pem, password=None)
    return key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
