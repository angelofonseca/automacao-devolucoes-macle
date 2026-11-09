"""Apoio dos testes: simulador do MACLE numa thread e uma Config apontando para pastas temporárias."""
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from defeitos.config import RAIZ, Config
from mock_macle import servidor


class MacleSimulado:
    def __enter__(self):
        servidor.reiniciar('teste', 'senha-teste')
        servidor.Handler.log_message = lambda *a: None  # silencia o log nos testes
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), servidor.Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.http.server_address[1]}/Controle/'
        return self

    def falhar(self, status=None, vezes=1, atraso=0):
        servidor.estado['falha'].update(status=status, vezes=vezes, atraso=atraso)

    def __exit__(self, *exc):
        self.http.shutdown()
        self.http.server_close()


def lojas(url):
    return {'consulta': '6', 'ordemLojas': ['6', '1'], 'lojas': {
        '1': {'nome': 'Centro', 'razao': 'Calçados Exemplo Ltda - Loja Centro', 'cnpj': '00.000.111/0001-01', 'servidor': url},
        '6': {'nome': 'Shopping', 'razao': 'Calçados Exemplo Ltda - Loja Shopping', 'cnpj': '00.000.111/0006-06', 'servidor': url}}}


def config_teste(url, pasta=None, **extra):
    pasta = Path(pasta or tempfile.mkdtemp())
    valores = dict(macle_usuario='teste', macle_senha='senha-teste', macle_terminal='001', lojas=lojas(url),
                   peso_par=0.7, peso_caixa=0.2, gmail_user='', gmail_senha='', telegram_token='', telegram_chat_id='',
                   intervalo_min=60, timeout=2, tentativas=3, entrada=pasta / 'entrada',
                   fornecedores=RAIZ / 'data' / 'fornecedores.json', saida=pasta / 'saida', log=pasta / 'logs' / 'execucoes.jsonl')
    valores.update(extra)
    return Config(**valores)
