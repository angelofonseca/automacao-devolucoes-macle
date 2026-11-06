"""Retry com backoff, classificação de erros HTTP e log de execução em JSONL.

Regra:
- falha temporária (timeout, conexão recusada, HTTP 5xx ou 429) -> tenta de novo, esperando 1 s, 2 s, 4 s...
- falha permanente (HTTP 4xx: credencial errada, produto inexistente) -> não adianta repetir, falha na hora.
"""
import json
import socket
import ssl
import time
from datetime import datetime, timezone

import requests


class ErroIntegracao(Exception):
    """Falha ao falar com um serviço externo. `temporaria` diz se vale tentar de novo."""

    def __init__(self, servico, detalhe, temporaria, status=None):
        super().__init__(f'{servico}: {detalhe}')
        self.servico = servico
        self.detalhe = detalhe
        self.temporaria = temporaria
        self.status = status


# Exceções de rede que indicam falha passageira (o serviço pode voltar na próxima tentativa)
REDE = (requests.Timeout, requests.ConnectionError, socket.timeout, ConnectionError, ssl.SSLError)


def checar_status(resposta, servico):
    """Converte o status HTTP em exceção: 5xx/429 = temporária, outros 4xx = permanente."""
    codigo = resposta.status_code
    if codigo < 400:
        return resposta
    try:
        detalhe = resposta.json().get('erro') or resposta.json().get('description') or ''
    except ValueError:
        detalhe = resposta.text[:120]
    temporaria = codigo >= 500 or codigo == 429
    raise ErroIntegracao(servico, f'HTTP {codigo} {detalhe}'.strip(), temporaria, status=codigo)


def com_retry(funcao, servico, tentativas=3, espera_base=1.0, dormir=None, ao_falhar=None):
    """Chama funcao() até `tentativas` vezes enquanto a falha for temporária.

    ao_falhar(tentativa, erro) é chamado a cada falha (usado para registrar no log).
    Devolve o resultado de funcao() ou levanta ErroIntegracao com a última falha.
    """
    for tentativa in range(1, tentativas + 1):
        try:
            return funcao()
        except ErroIntegracao as exc:
            erro = exc
        except REDE as exc:
            erro = ErroIntegracao(servico, f'{type(exc).__name__}: falha de rede', temporaria=True)
        if ao_falhar:
            ao_falhar(tentativa, erro)
        if not erro.temporaria or tentativa == tentativas:
            raise erro
        (dormir or time.sleep)(espera_base * 2 ** (tentativa - 1))  # backoff exponencial: 1 s, 2 s, 4 s...


def esconder(texto, *segredos):
    """Tira senhas e tokens de uma mensagem de erro antes de logar ou mandar no Telegram."""
    for s in segredos:
        if s:
            texto = texto.replace(s, '***')
    return texto


def registrar(caminho, **campos):
    """Acrescenta uma linha JSON ao log de execuções (um evento por linha)."""
    caminho.parent.mkdir(parents=True, exist_ok=True)
    linha = {'em': datetime.now(timezone.utc).isoformat(timespec='seconds'), **campos}
    with open(caminho, 'a', encoding='utf-8') as f:
        f.write(json.dumps(linha, ensure_ascii=False) + '\n')
