"""Configuração: segredos e valores simples no .env; a loja e o servidor do MACLE em loja.json.

Cada instalação (bot + .env) é de uma loja só: ela só enxerga os próprios defeitos, itens e NFs.

Nenhuma credencial, loja, servidor ou e-mail fica no código. Falta de variável ou arquivo inválido
para a execução logo no início, com mensagem clara, em vez de quebrar no meio da rodada.
"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent


class ConfigInvalida(Exception):
    pass


@dataclass(frozen=True)
class Config:
    macle_usuario: str
    macle_senha: str
    macle_terminal: str
    loja: dict                # {'numero', 'nome', 'razao', 'cnpj', 'servidor'} da loja desta instalação
    gmail_user: str
    gmail_senha: str          # senha de app do Gmail (IMAP)
    telegram_token: str       # token do bot (Bot API)
    telegram_chat_id: str
    intervalo_min: int
    timeout: float
    tentativas: int
    entrada: Path             # pastas dos casos (um caso.json por pasta)
    fornecedores: Path
    saida: Path
    log: Path
    segredos: tuple = field(default=(), repr=False)

    def __repr__(self):  # nunca imprime senha nem token, nem por engano num log
        return f"Config(loja={self.loja['numero']}, gmail={self.gmail_user!r})"


# O login do MACLE não está aqui: o simulador aceita qualquer um, e no MACLE real o erro de login já explica.
OBRIGATORIAS = ('GMAIL_USER', 'GMAIL_APP_PASSWORD', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID')
LOJA_PADRAO = RAIZ / 'data' / 'loja.json'
LOJA_EXEMPLO = RAIZ / 'data' / 'loja.exemplo.json'


def _numero(nome, padrao, tipo=int, minimo=0):
    bruto = os.environ.get(nome, '').strip() or str(padrao)
    try:
        valor = tipo(bruto.replace(',', '.'))
    except ValueError:
        raise ConfigInvalida(f'{nome} precisa ser um número (veio {bruto!r})')
    if valor < minimo:
        raise ConfigInvalida(f'{nome} precisa ser >= {minimo}')
    return valor


def carregar_loja(caminho):
    """Lê e valida o loja.json: número da loja, CNPJ e servidor http(s) do MACLE."""
    try:
        loja = json.loads(Path(caminho).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigInvalida(f'{caminho}: {exc}')
    loja['numero'] = str(loja.get('numero') or '').strip()
    if not loja['numero'].isdigit():
        raise ConfigInvalida(f'{caminho}: "numero" da loja (a empresa no MACLE) ausente ou inválido')
    if not str(loja.get('servidor', '')).startswith(('http://', 'https://')):
        raise ConfigInvalida(f'{caminho}: loja sem "servidor" http(s)')
    if len(''.join(c for c in loja.get('cnpj', '') if c.isdigit())) != 14:
        raise ConfigInvalida(f'{caminho}: loja com CNPJ inválido')
    return loja


def carregar(arquivo_env=None):
    """Lê o .env (sem sobrescrever variáveis já definidas no ambiente) e valida."""
    load_dotenv(arquivo_env or RAIZ / '.env')
    env = lambda nome, padrao='': os.environ.get(nome, padrao).strip()

    faltando = [n for n in OBRIGATORIAS if not env(n)]
    if faltando:
        raise ConfigInvalida('Variáveis faltando no .env: ' + ', '.join(faltando) + ' (veja o .env.example)')

    arquivo_loja = Path(env('LOJA_ARQUIVO') or LOJA_PADRAO)
    if not arquivo_loja.is_absolute():
        arquivo_loja = RAIZ / arquivo_loja
    if not arquivo_loja.exists():
        arquivo_loja = LOJA_EXEMPLO  # sem configuração própria: roda contra o simulador

    gmail_senha = ''.join(env('GMAIL_APP_PASSWORD').split())  # o Google mostra a senha de app com espaços
    return Config(
        macle_usuario=env('MACLE_USUARIO'),
        macle_senha=env('MACLE_SENHA'),
        macle_terminal=env('MACLE_TERMINAL') or '001',
        loja=carregar_loja(arquivo_loja),
        gmail_user=env('GMAIL_USER'),
        gmail_senha=gmail_senha,
        telegram_token=env('TELEGRAM_BOT_TOKEN'),
        telegram_chat_id=env('TELEGRAM_CHAT_ID'),
        intervalo_min=_numero('INTERVALO_MIN', 60, minimo=1),
        timeout=_numero('HTTP_TIMEOUT', 15, minimo=1),
        tentativas=_numero('TENTATIVAS', 3, minimo=1),
        entrada=Path(env('DEFEITOS_ENTRADA') or RAIZ / 'data' / 'entrada'),
        fornecedores=Path(env('DEFEITOS_FORNECEDORES') or RAIZ / 'data' / 'fornecedores.json'),
        saida=Path(env('DEFEITOS_SAIDA') or RAIZ / 'saida'),
        log=Path(env('DEFEITOS_LOG') or RAIZ / 'logs' / 'execucoes.jsonl'),
        segredos=(env('MACLE_SENHA'), gmail_senha, env('TELEGRAM_BOT_TOKEN')),
    )
