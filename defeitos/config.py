"""Configuração: segredos e valores simples no .env; lojas e servidores do MACLE em lojas.json.

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
    lojas: dict               # {'consulta', 'ordemLojas', 'lojas': {n: {'nome', 'razao', 'cnpj', 'servidor'}}}
    peso_par: float
    peso_caixa: float
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
    segredos: tuple = field(default=(), repr=False)

    @property
    def gmail_ativo(self):
        return bool(self.gmail_senha)

    @property
    def telegram_ativo(self):
        return bool(self.telegram_token)

    def loja(self, numero):
        loja = self.lojas['lojas'].get(str(numero))
        if not loja:
            raise ConfigInvalida(f'loja {numero} não está no {LOJAS_PADRAO.name}')
        return loja

    def __repr__(self):  # nunca imprime senha nem token, nem por engano num log
        return f"Config(lojas={sorted(self.lojas['lojas'])}, gmail={self.gmail_ativo}, telegram={self.telegram_ativo})"


LOJAS_PADRAO = RAIZ / 'data' / 'lojas.json'
LOJAS_EXEMPLO = RAIZ / 'data' / 'lojas.exemplo.json'


def _numero(nome, padrao, tipo=int, minimo=0):
    bruto = os.environ.get(nome, '').strip() or str(padrao)
    try:
        valor = tipo(bruto.replace(',', '.'))
    except ValueError:
        raise ConfigInvalida(f'{nome} precisa ser um número (veio {bruto!r})')
    if valor < minimo:
        raise ConfigInvalida(f'{nome} precisa ser >= {minimo}')
    return valor


def carregar_lojas(caminho):
    """Lê e valida o lojas.json: cada loja com CNPJ e servidor http(s); consulta e ordem apontando para lojas existentes."""
    try:
        dados = json.loads(Path(caminho).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigInvalida(f'{caminho}: {exc}')
    lojas = dados.get('lojas') or {}
    if not lojas:
        raise ConfigInvalida(f'{caminho}: nenhuma loja cadastrada')
    for n, l in lojas.items():
        if not str(l.get('servidor', '')).startswith(('http://', 'https://')):
            raise ConfigInvalida(f'{caminho}: loja {n} sem "servidor" http(s)')
        if len(''.join(c for c in l.get('cnpj', '') if c.isdigit())) != 14:
            raise ConfigInvalida(f'{caminho}: loja {n} com CNPJ inválido')
    dados.setdefault('consulta', next(iter(lojas)))
    dados.setdefault('ordemLojas', list(lojas))
    for n in [dados['consulta'], *dados['ordemLojas']]:
        if str(n) not in lojas:
            raise ConfigInvalida(f'{caminho}: loja {n} citada em consulta/ordemLojas não está em "lojas"')
    return dados


def carregar(arquivo_env=None):
    """Lê o .env (sem sobrescrever variáveis já definidas no ambiente) e valida."""
    load_dotenv(arquivo_env or RAIZ / '.env')
    env = lambda nome, padrao='': os.environ.get(nome, padrao).strip()

    faltando = [n for n in ('MACLE_USUARIO', 'MACLE_SENHA') if not env(n)]
    if env('GMAIL_APP_PASSWORD') and not env('GMAIL_USER'):
        faltando.append('GMAIL_USER')
    if env('TELEGRAM_BOT_TOKEN') and not env('TELEGRAM_CHAT_ID'):
        faltando.append('TELEGRAM_CHAT_ID')
    if faltando:
        raise ConfigInvalida('Variáveis faltando no .env: ' + ', '.join(faltando) + ' (veja o .env.example)')

    arquivo_lojas = Path(env('LOJAS_ARQUIVO') or LOJAS_PADRAO)
    if not arquivo_lojas.is_absolute():
        arquivo_lojas = RAIZ / arquivo_lojas
    if not arquivo_lojas.exists():
        arquivo_lojas = LOJAS_EXEMPLO  # sem configuração própria: roda contra o simulador

    gmail_senha = ''.join(env('GMAIL_APP_PASSWORD').split())  # o Google mostra a senha de app com espaços
    return Config(
        macle_usuario=env('MACLE_USUARIO'),
        macle_senha=env('MACLE_SENHA'),
        macle_terminal=env('MACLE_TERMINAL') or '001',
        lojas=carregar_lojas(arquivo_lojas),
        peso_par=_numero('PESO_POR_PAR', 0.7, float),
        peso_caixa=_numero('PESO_CAIXA', 0.2, float),
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
