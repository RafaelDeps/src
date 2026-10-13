"""Testes do leitor quote-aware de ``.env`` do consumidor ETL.

Cobrem round-trip de valores com ``#``, ``=``, aspas e espaços, além de
fixtures de robustez (BOM, CRLF e ausência de newline final). Offline: apenas
escrevem arquivos temporários e chamam ``carregar_credenciais``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src_etl.etl.gerenciar import carregar_credenciais


def _escrever(tmp_path: Path, conteudo: str) -> Path:
    destino = tmp_path / ".env"
    destino.write_text(conteudo, encoding="utf-8")
    return destino


def test_valor_simples(tmp_path: Path) -> None:
    env = _escrever(tmp_path, "USER=alice\nPASSWORD=s3cr3t\n")
    assert carregar_credenciais(env) == ("alice", "s3cr3t")


def test_aspas_simples_preserva_hash_e_igual(tmp_path: Path) -> None:
    env = _escrever(tmp_path, "USER='a#b'\nPASSWORD='p=w#d'\n")
    assert carregar_credenciais(env) == ("a#b", "p=w#d")


def test_aspas_simples_preserva_espacos_internos(tmp_path: Path) -> None:
    env = _escrever(tmp_path, "USER='  alice  '\nPASSWORD=' s e n h a '\n")
    assert carregar_credenciais(env) == ("  alice  ", " s e n h a ")


def test_aspas_duplas_processa_escapes(tmp_path: Path) -> None:
    env = _escrever(tmp_path, 'USER="us\\"er"\nPASSWORD="p\\nw\\\\x"\n')
    assert carregar_credenciais(env) == ('us"er', "p\nw\\x")


def test_aspas_duplas_ignora_hash_interno(tmp_path: Path) -> None:
    env = _escrever(tmp_path, 'USER="a#b=c"\nPASSWORD="x y z"\n')
    assert carregar_credenciais(env) == ("a#b=c", "x y z")


def test_sem_aspas_descarta_comentario_inline(tmp_path: Path) -> None:
    env = _escrever(tmp_path, "USER=alice # comentário\nPASSWORD=s3cr3t#sobra\n")
    assert carregar_credenciais(env) == ("alice", "s3cr3t")


def test_bom_e_crlf(tmp_path: Path) -> None:
    destino = tmp_path / ".env"
    destino.write_bytes("\ufeffUSER='u#1'\r\nPASSWORD='p#2'\r\n".encode("utf-8"))
    assert carregar_credenciais(destino) == ("u#1", "p#2")


def test_aspas_simples_decodifica_escape_de_apostrofo(tmp_path: Path) -> None:
    env = _escrever(tmp_path, "USER='a\\'b'\nPASSWORD='c\\\\d'\n")
    assert carregar_credenciais(env) == ("a'b", "c\\d")


def test_sem_newline_final(tmp_path: Path) -> None:
    env = _escrever(tmp_path, "USER='u'\nPASSWORD='p'")
    assert carregar_credenciais(env) == ("u", "p")


def test_fallback_src_user_e_src_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRC_USER", "envuser")
    monkeypatch.setenv("SRC_PASS", "envpass")
    env = _escrever(tmp_path, "MISTRAL_KEY=abc\n")
    assert carregar_credenciais(env) == ("envuser", "envpass")


def test_arquivo_prioritario_sobre_ambiente(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRC_USER", "envuser")
    monkeypatch.setenv("SRC_PASS", "envpass")
    env = _escrever(tmp_path, "USER='u1'\nPASSWORD='p1'\n")
    assert carregar_credenciais(env) == ("u1", "p1")
