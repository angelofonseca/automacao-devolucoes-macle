"""Configuração lida do .env (nenhuma credencial fica no código).

Falta de variável obrigatória para a execução logo no início, com mensagem clara,
em vez de quebrar no meio da rodada.
"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent


class ConfigInvalida(Exception):
    pass


@dataclass(frozen=True)
class Config:
    erp_url: str
    erp_usuario: str
    erp_senha: str
    gmail_user: str
    gmail_senha: str          # vazio = rascunhos vão para saida/rascunhos/*.eml
    telegram_token: str       # vazio = mensagens vão para saida/telegram.log
    telegram_chat_id: str
    intervalo_min: int
    timeout: float
    tentativas: int
    entrada: Path             # pastas dos casos (um caso.json por pasta)
    fornecedores: Path
    saida: Path
    log: Path

    @property
    def gmail_ativo(self):
        return bool(self.gmail_senha)

    @property
    def telegram_ativo(self):
        return bool(self.telegram_token)

    def __repr__(self):  # nunca imprime senha nem token, nem por engano num log
        return f'Config(erp_url={self.erp_url!r}, gmail={self.gmail_ativo}, telegram={self.telegram_ativo})'


def _inteiro(nome, padrao, minimo=1):
    bruto = os.environ.get(nome, str(padrao)).strip() or str(padrao)
    try:
        valor = int(bruto)
    except ValueError:
        raise ConfigInvalida(f'{nome} precisa ser um número inteiro (veio {bruto!r})')
    if valor < minimo:
        raise ConfigInvalida(f'{nome} precisa ser >= {minimo}')
    return valor


def carregar(arquivo_env=None):
    """Lê o .env (sem sobrescrever variáveis já definidas no ambiente) e valida."""
    load_dotenv(arquivo_env or RAIZ / '.env')
    env = lambda nome, padrao='': os.environ.get(nome, padrao).strip()

    faltando = [n for n in ('ERP_URL', 'ERP_USUARIO', 'ERP_SENHA') if not env(n)]
    if env('GMAIL_APP_PASSWORD') and not env('GMAIL_USER'):
        faltando.append('GMAIL_USER')
    if env('TELEGRAM_BOT_TOKEN') and not env('TELEGRAM_CHAT_ID'):
        faltando.append('TELEGRAM_CHAT_ID')
    if faltando:
        raise ConfigInvalida('Variáveis faltando no .env: ' + ', '.join(faltando) + ' (veja o .env.example)')

    erp_url = env('ERP_URL').rstrip('/')
    if not erp_url.startswith(('http://', 'https://')):
        raise ConfigInvalida('ERP_URL precisa começar com http:// ou https://')

    return Config(
        erp_url=erp_url,
        erp_usuario=env('ERP_USUARIO'),
        erp_senha=env('ERP_SENHA'),
        gmail_user=env('GMAIL_USER'),
        gmail_senha=''.join(env('GMAIL_APP_PASSWORD').split()),  # o Google mostra a senha de app com espaços
        telegram_token=env('TELEGRAM_BOT_TOKEN'),
        telegram_chat_id=env('TELEGRAM_CHAT_ID'),
        intervalo_min=_inteiro('INTERVALO_MIN', 60),
        timeout=_inteiro('HTTP_TIMEOUT', 10),
        tentativas=_inteiro('TENTATIVAS', 3),
        entrada=Path(env('DEFEITOS_ENTRADA') or RAIZ / 'data' / 'entrada'),
        fornecedores=Path(env('DEFEITOS_FORNECEDORES') or RAIZ / 'data' / 'fornecedores.json'),
        saida=Path(env('DEFEITOS_SAIDA') or RAIZ / 'saida'),
        log=Path(env('DEFEITOS_LOG') or RAIZ / 'logs' / 'execucoes.jsonl'),
    )
