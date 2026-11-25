"""Apoio dos testes: simulador do MACLE numa thread, Config em pastas temporárias e Gmail/Telegram falsos."""
import json
import shutil
import struct
import tempfile
import threading
import zlib
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from defeitos import casos
from defeitos.config import RAIZ, Config
from mock_macle import servidor

EXEMPLOS = Path(__file__).parent / 'fixtures' / 'casos.json'


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


def loja(url):
    return {'numero': '1', 'nome': 'Centro', 'razao': 'Calçados Exemplo Ltda - Loja Centro', 'cnpj': '00.000.111/0001-01',
            'servidor': url}


def config_teste(url, pasta=None, **extra):
    pasta = Path(pasta or tempfile.mkdtemp())
    valores = dict(macle_usuario='teste', macle_senha='senha-teste', macle_terminal='001', loja=loja(url),
                   gmail_user='loja@teste.example', gmail_senha='senha-app-teste', telegram_token='123:TOKEN-TESTE',
                   telegram_chat_id='1', intervalo_min=60, timeout=2, tentativas=3, entrada=pasta / 'entrada',
                   fornecedores=RAIZ / 'data' / 'fornecedores.json', saida=pasta / 'saida', log=pasta / 'logs' / 'execucoes.jsonl')
    valores.update(extra)
    return Config(**valores)


def png_exemplo(largura=120, altura=80, cor=(200, 60, 60)):
    """PNG simples de uma cor só, gerado sem biblioteca externa (a foto do par nos testes)."""
    def bloco(tipo, dados):
        return struct.pack('>I', len(dados)) + tipo + dados + struct.pack('>I', zlib.crc32(tipo + dados))
    linha = b'\x00' + bytes(cor) * largura
    return (b'\x89PNG\r\n\x1a\n' + bloco(b'IHDR', struct.pack('>IIBBBBB', largura, altura, 8, 2, 0, 0, 0))
            + bloco(b'IDAT', zlib.compress(linha * altura)) + bloco(b'IEND', b''))


def criar_caso(entrada, loja, codigo, quantidade, defeito):
    """casos.criar com uma foto de exemplo (no uso real, a foto vem do Telegram)."""
    with tempfile.TemporaryDirectory() as tmp:
        foto = Path(tmp) / 'foto.png'
        foto.write_bytes(png_exemplo())
        return casos.criar(entrada, loja, codigo, quantidade, defeito, foto)


def resetar(entrada):
    """Recria os casos de tests/fixtures/casos.json (status novo) numa pasta vazia."""
    if Path(entrada).exists():
        shutil.rmtree(entrada)
    return [criar_caso(entrada, **e) for e in json.loads(EXEMPLOS.read_text(encoding='utf-8'))['casos']]


@contextmanager
def integracoes_falsas():
    """Gmail e Telegram sem rede: guarda os rascunhos (EmailMessage) e os textos enviados ao chat."""
    saida = {'rascunhos': [], 'telegram': []}

    def gravar(cfg, msg):
        saida['rascunhos'].append(msg)
        return {'modo': 'gmail', 'messageId': msg['Message-ID'], 'jaExistia': False}

    def post(url, json=None, **kw):
        saida['telegram'].append(json['text'])
        return mock.Mock(status_code=200, json=lambda: {'ok': True, 'result': {'message_id': len(saida['telegram'])}})

    with mock.patch('defeitos.gmail._gravar_imap', gravar), mock.patch('defeitos.telegram.requests.post', post):
        yield saida
