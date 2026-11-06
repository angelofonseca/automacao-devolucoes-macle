"""Apoio dos testes: ERP simulado numa thread e uma Config apontando para pastas temporárias."""
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from defeitos.config import RAIZ, Config
from mock_erp import servidor


class ERPSimulado:
    def __enter__(self):
        servidor.USUARIO, servidor.SENHA = 'teste', 'senha-teste'
        servidor.sessoes.clear()
        servidor.falha.update(status=None, atraso=0, vezes=0)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), servidor.Handler)
        servidor.Handler.log_message = lambda *a: None  # silencia o log nos testes
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.http.server_address[1]}'
        return self

    def falhar(self, status=None, vezes=1, atraso=0):
        servidor.falha.update(status=status, vezes=vezes, atraso=atraso)

    def __exit__(self, *exc):
        self.http.shutdown()
        self.http.server_close()


def config_teste(url, pasta=None, **extra):
    pasta = Path(pasta or tempfile.mkdtemp())
    valores = dict(erp_url=url, erp_usuario='teste', erp_senha='senha-teste', gmail_user='', gmail_senha='',
                   telegram_token='', telegram_chat_id='', intervalo_min=60, timeout=2, tentativas=3,
                   entrada=pasta / 'entrada', fornecedores=RAIZ / 'data' / 'fornecedores.json',
                   saida=pasta / 'saida', log=pasta / 'logs' / 'execucoes.jsonl')
    valores.update(extra)
    return Config(**valores)
